"""The status board, derived from the snapshot: text for chat, JSON for the pane, HTML for a browser."""

import datetime as dt
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import graph  # noqa: E402
import i18n  # noqa: E402
import octicons  # noqa: E402

# Fixed top-level groups, in this order on every surface. 팀 대기 is waiting_others; a closed unit lands in one of
# the last four (or in 팀 대기 while a PR of it is still open), by unit_state.
SECTION_KEYS = ("in_progress", "waiting_owner", "waiting_others", "queue", "release_wait", "released", "done",
                "cancelled")
ARCHIVABLE = ("released", "done", "cancelled")  # never 팀 대기 or release_wait: something still moves there


def sections():
    """(key, label) per board section; labels resolve per call because ui.language can change between calls."""
    return tuple((k, i18n.t(f"board.section_{k}")) for k in SECTION_KEYS)


def _column(u):
    # A held unit is not running whatever its last status said: it sits in the queue with its reason.
    if u.get("held") and u["status"] in ("queue", "in_progress"):
        return "queue"
    # Statuses without a section of their own (graph.py) sit next to their nearest one.
    return {"parked": "queue", "blocked": "waiting_others"}.get(u["status"], u["status"])


def state_ctx(snap=None):
    """What unit_state reads once per board: prefs (release.none, release.watch_days, board.archive_days), the repo
    registry and the snapshot (the release units' recorded tags)."""
    import gitpolicy
    import prefs
    return {"p": prefs.load(), "rs": gitpolicy.repos(), "now": cl.now(), "snap": snap}


def _pr_short(x):
    return f"#{x.get('number')}"


def unit_state(u, ctx):
    """The one board state of a unit: {group, label, detail}. The section a row sits in and the state cell the
    chat board, the side pane and board.html print all come from here. An open unit is its status's group (the
    surfaces add its wait). A closed unit's cell names only where its PRs stand, never a wait, phase or deadline
    left over from when it ran: cancelled → 취소; a done release unit → 릴리스 완료; a PR still open → 팀 대기; a PR
    merged in a repo with a release step and in no release tag yet → 머지됨·릴리스 대기; a released PR → 릴리스 완료;
    else 완료."""
    import factory
    import release
    st = u["status"]
    if st not in graph.CLOSED:
        return {"group": _column(u), "label": status_label(st), "detail": None}
    by = {}
    for x in u.get("prs") or []:
        if isinstance(x, dict):
            by.setdefault(factory.pr_release_state(x, ctx["p"], ctx["rs"], ctx.get("snap"), ctx["now"]), []).append(x)
    tags = [x["released"] for x in by.get("released") or [] if isinstance(x.get("released"), str)
            and not cl.parse_ts(x["released"])]
    dep = u.get("deployed") or {}
    tags += [d.get("tag") for env in ("prod", "dev") for d in [dep.get(env)] if isinstance(d, dict) and d.get("tag")]
    if st == "cancelled":
        g, detail = "cancelled", None
    elif release.is_release_unit(u):
        g, detail = "released", tags[-1] if tags else None
    elif by.get("open"):
        g, detail = "waiting_others", " · ".join("PR " + _pr_short(x) for x in by["open"])
    elif by.get("release-wait"):
        g, detail = "release_wait", " · ".join(_pr_short(x) for x in by["release-wait"])
    elif by.get("released"):
        g, detail = "released", ", ".join(dict.fromkeys(tags)) or None
    else:
        g, detail = "done", None
    return {"group": g, "label": i18n.t(f"board.section_{g}"), "detail": detail}


def archived(u, state, ctx):
    """Whether a closed unit is in the archive view: recorded by `foundry board archive`, or 릴리스 완료 / 완료 / 취소
    for longer than prefs board.archive_days (0: never by age). 팀 대기 and release-wait units never are."""
    if state["group"] not in ARCHIVABLE:
        return False
    if u.get("archived"):
        return True
    days = ((ctx["p"].get("board") or {}).get("archive_days")) or 0
    t = cl.parse_ts(u.get("closed") or u.get("updated"))
    return bool(days and t) and (ctx["now"] - t).total_seconds() >= days * 86400


def _approval(a):
    """An unanswered request. `queued`: the asking hook gave up waiting and denied; the request stays answerable,
    and an answer is applied once when the session asks the same thing again (approve.py `late`)."""
    out = {k: a.get(k) for k in ("rid", "unit", "role", "tool", "summary", "tier", "asked", "session", "why",
                                  "evidence", "advisor")} | {"queued": bool(a.get("gave_up"))}
    asked = cl.parse_ts(a.get("asked"))
    if a.get("wait_s") and asked and not a.get("gave_up"):  # when the hook denies and queues it
        out["auto_deny"] = cl.iso(asked + dt.timedelta(seconds=a["wait_s"]))
    return out


def open_approvals(snap):
    """Every request the owner or the foreman can still answer, queued ones included, newest first. A queued
    request of a finished unit is left out: nothing will ask it again."""
    import approve
    units = snap.get("units") or {}
    done = {k for k, u in units.items() if u.get("status") in graph.CLOSED}
    ttl, now, prs = approve.queued_ttl_hours(), cl.now(), approve._pr_states(snap)
    return sorted((a for a in (snap.get("approvals") or {}).values()
                   if a.get("answer") is None and not (a.get("gave_up") and a.get("unit") in done)
                   and not approve.stale_reason(a, snap, ttl, now, prs)),
                  key=lambda a: a.get("asked") or "", reverse=True)


VIEWS = ("status", "repo")
SPARK = "▁▂▃▄▅▆▇█"
ROW_DEFAULTS = {"blocked_by": [], "chain": None, "stall": None, "cost_usd": None, "sync_lag_min": None,
                "sync_text": None, "mode": "full", "hands": [], "mismatch": None, "pr_items": [], "branches": [],
                "unwatched": None}


def model(snap, view="status", repo=None, archived_view=False):
    m = _model_core(snap, archived_view)
    for mod in cl.modules():
        if hasattr(mod, "board"):
            mod.board(snap, m)
    _finish(snap, m)
    m = apply_view(m, view, repo)
    m["summary"] = summary(m)
    return m


def summary(m):
    """The one set of counts every surface shows: the text and HTML band, the board's section headers and the
    mod's band pills. Section counts are the rows drawn; 결정 필요 is the open decision cards plus
    the open `decision.ask` questions; attention is anything waiting on the owner."""
    items = owner_items(m)
    for sec in m["sections"]:
        sec["count"] = len(sec["rows"])
        if sec["key"] == "waiting_owner":
            sec["items"], sec["count"] = items, len(items)
    counts = {sec["key"]: sec["count"] for sec in m["sections"]}
    m["counts"] = counts
    decisions = len(m["cards"]) + len(m["asks"])
    m["cards_open"] = decisions
    owner = len(items)
    attention = bool(decisions or m["approvals"] or m["halts"] or m["foreman_down"] or owner)
    return {"counts": counts, "decisions": decisions, "approvals": len(m["approvals"]), "attention": attention,
            "owner_wait": owner}


def owner_wait(m):
    return len(owner_items(m))


def owner_items(m):
    """오너 대기, one row each: every open owner to-do, and each waiting_owner unit with no open decision. Decisions
    are 결정 필요's alone (one item, one section): a unit waiting on its own card is shown there, never here.
    `state` says what is wanted: 할 일: <todo text>, or 오너 확인. The 오너 대기 section and summary.owner_wait
    are both this list."""
    rows = {r.get("id"): r for sec in m["sections"] for r in sec["rows"]}
    title = lambda uid: (rows.get(uid) or {}).get("title") or ""  # noqa: E731
    out = []
    for t in m.get("owner_todos") or []:
        what = t.get("title") or t.get("text") or f"{t.get('kind') or ''} {t.get('target') or ''}".strip()
        out.append({"kind": "todo", "ref": t.get("tid"), "unit": t.get("unit"), "title": title(t.get("unit")),
                    "state": i18n.t("board.state_todo", text=what), "text": what})
    covered = {x["unit"] for x in out if x["unit"]} | {d.get("unit") for d in m["cards"] + m["asks"] if d.get("unit")}
    check = i18n.t("board.status_owner_check")
    for sec in m["sections"]:
        if sec["key"] == "waiting_owner":
            out += [{"kind": "unit", "ref": r["id"], "unit": r["id"], "title": r.get("title") or "",
                     "state": check, "text": r.get("summary") or ""} for r in sec["rows"] if r["id"] not in covered]
    repo = m.get("repo_filter")
    if repo and repo != "all":  # the band follows the filtered rows: keep what belongs to a shown unit
        out = [x for x in out if x["unit"] in rows]
    return out


def _repos_of(u, r):
    """Every repo a unit touches: its own, its worktree's, its PRs' and branches'. Multi-repo units list all."""
    out = []
    cands = list(u.get("repos") or []) + [u.get("repo"), (u.get("worktree") or {}).get("repo")]
    cands += [p.get("repo") for p in r.get("pr_items") or [] if isinstance(p, dict)]
    cands += [b.get("repo") for b in r.get("branches") or [] if isinstance(b, dict)]
    cands += [ref.rsplit("#", 1)[0] for ref in r.get("prs") or [] if "#" in ref]
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out


def _canonical_repos(m):
    """One repo, one name: a short name (`webapp`) becomes owner/repo as the inventory's groups do, so the repo
    filter, the repo view and the cards' repo all use the same name."""
    rows = [r for sec in m["sections"] for r in sec["rows"]]
    names = {x for r in rows for x in r["repos"]} | {c.get("repo") for c in m.get("cards") or [] if c.get("repo")}
    if all("/" in x for x in names):
        return
    import factory
    import gitpolicy
    rs = gitpolicy.repos()
    known = {x for x in names if "/" in x} | {k for k in (m.get("inventory") or {}).get("repos") or {} if "/" in k}
    known |= {k for k in rs if "/" in k}
    canon = {x: factory.canonical_repo(x, known, rs) for x in names}
    for r in rows:
        r["repos"] = list(dict.fromkeys(canon[x] for x in r["repos"]))
    for c in m.get("cards") or []:
        if c.get("repo"):
            c["repo"] = canon[c["repo"]]


def _finish(snap, m):
    """Defaults for every key a feature module may add, so renderers never meet a missing one."""
    for sec in m["sections"]:
        for r in sec["rows"]:
            for k, v in ROW_DEFAULTS.items():
                if r.get(k) is None:
                    r[k] = v
            r["repos"] = _repos_of(snap["units"].get(r["id"]) or {}, r)
    _canonical_repos(m)
    halts = m.get("halts") if "halts" in m else snap.get("halts")
    if isinstance(halts, dict):
        halts = [dict(h, scope=h.get("scope") or k) for k, h in halts.items() if not h.get("resumed")]
    m["halts"] = list(halts or [])
    m.setdefault("next", [])
    m.setdefault("cards", [])
    carded = {c.get("key") or c.get("id") for c in m["cards"]}
    import views
    m["asks"] = [{"key": d["key"], "unit": d.get("unit"), "question": d.get("question") or d["key"], "asked": d.get("asked"),
                  "headline": views.headline({"unit": d.get("unit"), "question": d.get("question") or d["key"]}, snap),
                  "outline": views.outline(d.get("question") or d["key"]),
                  "detail_cmd": f"foundry decide show {d['key']}"}
                 for d in sorted(snap["decisions"].values(), key=lambda d: d.get("asked") or "")
                 if d.get("answer") is None and d["key"] not in carded]
    m["cards_open"] = len(m["cards"]) + len(m["asks"])
    m.setdefault("inventory", {"repos": {}, "reviewers": []})
    m.setdefault("monitor", None)
    m.setdefault("foreman", None)
    m.setdefault("handover", None)
    m.setdefault("night", False)
    m.setdefault("glyphs", "emoji")
    m.setdefault("sync_lagging", [])
    m.setdefault("stale_foremen", 0)
    for sec in m["sections"]:
        for r in sec["rows"]:
            if not r["sync_text"] and r["id"] in m["sync_lagging"]:
                r["sync_text"] = i18n.t("board.sync_lag",
                                        min=r["sync_lag_min"] if r["sync_lag_min"] is not None else "?")
    m["foreman_restart"] = bool(watchers_mod().foreman_drift(snap))
    m["foreman_down"] = bool((m["foreman"] and m["foreman"].get("alive") is False)
                             or (m["monitor"] and m["monitor"].get("collecting") is False))
    views.board(snap, m)  # last: the owner-facing views read every row and card in their final shape


def apply_view(m, view="status", repo=None):
    """Status view (sections by status, repo column) or repo view (groups by repo, status column); repo filters."""
    view = view if view in VIEWS else "status"
    rows = [r for sec in m["sections"] for r in sec["rows"]]
    m["repo_names"] = sorted({x for r in rows for x in r["repos"]})
    repo = repo if repo and repo != "all" else None
    if repo:
        for sec in m["sections"]:
            sec["rows"] = [r for r in sec["rows"] if repo in r["repos"]]
            sec["count"] = len(sec["rows"])
    groups = {}
    for sec in m["sections"]:
        for r in sec["rows"]:
            for x in r["repos"] or ["-"]:
                if not repo or x == repo:
                    groups.setdefault(x, []).append(r["id"])
    m["repo_groups"] = [{"repo": k, "units": v} for k, v in sorted(groups.items())]
    m["view"], m["repo_filter"] = view, repo or "all"
    return m


def spark(values):
    nums = [v for v in values if isinstance(v, (int, float))]
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or 1
    return "".join(SPARK[int((v - lo) / span * (len(SPARK) - 1))] if isinstance(v, (int, float)) else " "
                   for v in values)


# Every window is drawn in the same 24 buckets, so a sparkline fits a fixed-width tile (3h: 7.5 min, 6h: 15, 24h: 60).
BUCKETS = 24


def scaled(values, lo, hi):
    """Sparkline on a fixed scale, so a bar's height means the same value in every bucket and every refresh."""
    span = (hi - lo) or 1
    return "".join(SPARK[max(0, min(len(SPARK) - 1, round((v - lo) / span * (len(SPARK) - 1))))]
                   if isinstance(v, (int, float)) else " " for v in values)


def watchers_mod():
    import watchers
    return watchers


def metrics(hours, now=None):
    """The pane's PC 부하 graph, read only from metrics.jsonl: the last `hours` in BUCKETS fixed buckets, each the worst sample in it (max for load/docker, min for memory/disk) with that value's
    own pressure level, plus the current value and the window's worst value with its time."""
    import watchers
    now = now or cl.now()
    pp = watchers._p()["pressure"]
    n = BUCKETS
    step = hours * 3600 / n
    start = now.timestamp() - n * step
    rows = [((cl.parse_ts(r.get("ts")) or now).timestamp(), r)
            for r in watchers.read_metrics(dt.datetime.fromtimestamp(start, now.tzinfo))]
    last = rows[-1][1] if rows else {}
    tiles = []
    for name, key, unit, worse in watchers.tiles_for([r for _, r in rows]):
        pick = max if worse == "high" else min
        buckets = [None] * n
        vals = [(t, r[key]) for t, r in rows if isinstance(r.get(key), (int, float))]
        for t, v in vals:
            i = min(n - 1, int((t - start) // step))
            buckets[i] = v if buckets[i] is None else pick(buckets[i], v)
        peak = pick(vals, key=lambda x: x[1]) if vals else None
        limits = [x for x in (pp.get(f"{key}_warn"), pp.get(f"{key}_danger")) if x is not None]
        hi = 100 if unit == "%" else max([v for _, v in vals] + limits + [0])
        tiles.append({"name": name, "key": key, "value": last.get(key), "unit": unit, "worse": worse,
                      "status": watchers.level_of(key, last.get(key), pp),
                      "warn": pp.get(f"{key}_warn"), "danger": pp.get(f"{key}_danger"),
                      "peak": peak[1] if peak else None,
                      "peak_ts": cl.iso(dt.datetime.fromtimestamp(peak[0], now.tzinfo)) if peak else None,
                      "spark": buckets, "levels": [watchers.level_of(key, v, pp) for v in buckets],
                      "glyphs": scaled(buckets, 0, hi),
                      **({"extra": {k: last.get(k) for k in watchers.TILE_EXTRA[key]}}
                         if key in watchers.TILE_EXTRA else {})})
    return {"hours": hours, "bucket_min": step / 60 if step % 60 else int(step // 60), "samples": len(rows), "tiles": tiles}


def cached_metrics(hours):
    """metrics() as JSON, reused while metrics.jsonl is unchanged: the pane refreshes every 30 s, a sample lands
    every 60 s, so half the refreshes find nothing new and do no work."""
    try:
        st = os.stat(cl.path("metrics.jsonl"))
        key = [st.st_mtime_ns, st.st_size, hours]
    except OSError:
        key = None
    cache = cl.path("metrics-cache.json")
    try:
        with open(cache) as f:
            hit = json.load(f)
        if key and hit.get("key") == key:
            return hit["out"]
    except (OSError, ValueError, AttributeError):
        pass
    out = json.dumps(metrics(hours), ensure_ascii=False)
    if key:
        cl.write_atomic(cache, json.dumps({"key": key, "out": out}, ensure_ascii=False))
    return out


def _model_core(snap, archived_view=False):
    import watch
    apps = watch.known_apps(snap)
    rows = {k: [] for k in SECTION_KEYS}
    ctx, n_archived = state_ctx(snap), 0
    import housekeep
    reclaim = housekeep.reclaim_notes()
    sessions = snap.get("sessions") or {}
    approvals = open_approvals(snap)
    for u in snap["units"].values():
        if u["kind"] == "foreman":
            continue
        state = unit_state(u, ctx)
        arch = archived(u, state, ctx)
        n_archived += arch
        if arch != archived_view:
            continue
        agent = snap["agents"].get(u.get("agent") or "", {})
        sess = sessions.get(u.get("session") or "")
        decisions = [d for d in snap["decisions"].values() if d.get("unit") == u["id"]]
        open_dec = [d for d in decisions if d["answer"] is None]
        waits = u["waits"]
        last_note = u["notes"][-1]["text"] if u["notes"] else ""
        held = u.get("held")
        if u["status"] in graph.CLOSED:
            summary = state["detail"] or ""  # a finished unit's last wait or note is history, not its state
        elif held:
            summary = "held: " + (held.get("reason") or "")
        elif u["status"] == "waiting_owner" and open_dec:
            summary = open_dec[0]["question"] or ""
        elif u["status"] == "waiting_others" and waits:
            summary = "; ".join(f"{w['kind']} {w.get('ref') or w.get('until') or ''}".strip() for w in waits)
        else:
            summary = last_note
        rows[state["group"]].append({
            "id": u["id"], "title": u["title"], "kind": u["kind"], "status": u["status"], "repo": u.get("repo"),
            "group": state["group"], "state": state, "closed": u["status"] in graph.CLOSED,
            "lead": u.get("lead"), "summary": summary[:160], "updated": u["updated"],
            "held": held,
            "session": {k: sess.get(k) for k in ("name", "state", "role", "bg_id", "model", "updated")} if sess else None,
            "agent": {"id": u.get("agent"), "status": agent.get("status")},
            "tracker": u.get("tracker"), "worktree": u.get("worktree"), "reclaim": reclaim.get(u["id"]),
            "keys": u["keys"], "tickets": u["keys"]["ticket"], "prs": u["keys"]["pr"],
            "note": last_note[:300],
            "waits": waits,
            "unwatched": watch.unwatched(snap, u, apps),
            "inbox": [i for i in u["inbox"] if not i["delivered"] and not i.get("superseded")],
            "decisions": decisions,
            "open_decisions": open_dec,
            "approvals": [_approval(a) for a in approvals if a.get("unit") == u["id"]],
            "todos": list(u["todos"].values()),
            "notes": u["notes"][-10:],
        })
    for k in rows:
        rows[k].sort(key=lambda r: r["updated"] or "", reverse=True)
    hk = snap["housekeeping"]
    events = [e for e in snap["events"] if not e["consumed"]]
    # toasts: everything new the owner may want to hear about. escalations (the section): only what no other
    # section shows. An open decision is 결정 필요, a tier-3 approval is 승인 대기, and a report is a note on its
    # unit's timeline with nothing for the owner to do: one item, one section.
    toasts = []
    for d in snap["decisions"].values():
        if d.get("answer") is None:
            toasts.append({"id": f"decision:{d['key']}", "kind": "decision", "unit": d.get("unit"),
                           "text": d.get("question") or d["key"], "ts": d.get("asked")})
    for a in approvals:
        if (a.get("tier") or 0) >= 3:
            toasts.append({"id": f"approval:{a['rid']}", "kind": "approval", "unit": a.get("unit"),
                           "text": f"{a.get('tool') or '?'}: {a.get('summary') or ''}", "ts": a.get("asked")})
    for e in events:
        if e.get("kind") in ("report", "escalate"):
            toasts.append({"id": f"event:{e['eid']}", "kind": e["kind"], "unit": e.get("unit"),
                           "text": e.get("summary") or "", "ts": e.get("ts")})
    toasts.sort(key=lambda x: x["ts"] or "", reverse=True)
    escalations = [x for x in toasts if x["kind"] == "escalate"]
    pressure = snap.get("pressure") or {"level": "ok", "reasons": [], "ts": None}
    return {"generated": cl.iso(),
            "sections": [{"key": k, "label": label, "count": len(rows[k]), "rows": rows[k]} for k, label in sections()],
            "counts": {k: len(rows[k]) for k in SECTION_KEYS},
            "archived_view": archived_view, "archived_count": n_archived,
            "approvals": [_approval(a) for a in approvals],
            "escalations": escalations, "toasts": toasts,
            "pressure": {"level": pressure.get("level") or "ok", "reasons": pressure.get("reasons") or []},
            "sessions": [{k: x.get(k) for k in ("name", "unit", "role", "state", "bg_id", "model", "updated")}
                         for x in sorted(sessions.values(), key=lambda x: x.get("updated") or "", reverse=True)],
            "proposals": [p for p in hk["plans"] if p.get("verdict") == "propose"][-30:],
            "events": events}


def _hl(c):
    """A decision's one-line headline (views.py) after its unit (the text board has no 단위 column); a model built
    without a snapshot still gets one."""
    import views
    return f"{c.get('unit') or '-'} · {c.get('headline') or views.headline(c, {})}"


def _refs(r):
    return " ".join(r["tickets"] + r["prs"])


def flags(r):
    """Short markers a row carries: stall, sync lag, mismatch, plan mode, blockers. A closed row carries none."""
    out = []
    if r.get("closed"):
        return out
    if r.get("stall"):
        out.append(i18n.t("board.flag_stall", text=r["stall"].get("text") or r["stall"].get("kind") or ""))
    if r.get("sync_text"):
        out.append(r["sync_text"])
    if r.get("mismatch"):
        out.append(i18n.t("board.flag_mismatch", text=str(r["mismatch"])))
    if r.get("mode") == "plan":
        out.append("plan")
    if r.get("blocked_by"):
        out.append("blocked by " + ", ".join(map(str, r["blocked_by"])))
    return [f.strip() for f in out if f]


def chain_text(c):
    if not c:
        return ""
    if isinstance(c, dict):
        up, down = c.get("upstream") or c.get("up") or [], c.get("downstream") or c.get("down") or []
        return " -> ".join(filter(None, [", ".join(map(str, up)), "(this)", ", ".join(map(str, down))]))
    return " -> ".join(map(str, c))


def band(m):
    sm = m.get("summary") or summary(m)
    c = sm["counts"]
    parts = ["  ".join(f"{sec['label']} {c[sec['key']]}" for sec in m["sections"] if sec["key"] != "waiting_owner"),
             i18n.t("board.band_owner_wait", n=sm["owner_wait"]), i18n.t("board.band_decisions", n=sm["decisions"]),
             i18n.t("board.band_approvals", n=sm["approvals"]),
             i18n.t("board.band_pressure", level=m["pressure"].get("level") or "ok")]
    if sm["attention"]:
        parts.insert(0, i18n.t("board.attention"))
    stalled = sum(1 for sec in m["sections"] for r in sec["rows"] if r.get("stall"))
    if stalled:
        parts.insert(0, i18n.t("board.band_stalls", n=stalled))
    if m["foreman_down"]:
        parts.append("foreman down")
    if m.get("foreman_restart"):
        parts.append(i18n.t("board.foreman_restart"))
    if m.get("stale_foremen"):
        parts.append(i18n.t("board.stale_foremen", n=m["stale_foremen"]))
    if m["halts"]:
        parts.insert(0, f"ANDON {len(m['halts'])}")
    return " | ".join(parts)


def _row_text(r, col):
    sess = f" [{r['session']['state']}]" if r["session"] else ""
    inbox = f" [inbox {len(r['inbox'])}]" if r["inbox"] else ""
    appr = f" [approvals {len(r['approvals'])}]" if r["approvals"] else ""
    refs = f" ({_refs(r)})" if _refs(r) else ""
    fl = "".join(f" <{f}>" for f in flags(r))
    return f"  {r['id']:<24} {col[:22]:<22} {r['title'][:36]:<36} {r['summary'][:50]}{refs}{fl}{inbox}{appr}{sess}"


def render_text(snap, view="status", repo=None):
    return text_of(model(snap, view, repo))


def _outline_text(d):
    """A decision's outline under its headline, `- ` bullets; nothing when it is one line (the headline says it)."""
    ol = d.get("outline") or []
    return [f"    {'  ' * x['depth']}- {x['text']}" for x in ol] if len(ol) > 1 else []


def _outline_html(d, fallback):
    """A decision's outline as nested lists, or the text in a div when it is one line."""
    e = html.escape
    ol = d.get("outline") or []
    if len(ol) <= 1:
        return f"<div>{e(fallback or '')}</div>"
    lis = "".join(f"<li class=d{x['depth']}>{e(x['text'])}</li>" for x in ol)
    return f"<ul class=outline>{lis}</ul>"


def text_of(m):
    import views
    repo = m["repo_filter"]
    out = [f"foundry board  ({m['generated']})  view {m['view']}{' / archived' if m.get('archived_view') else ''}"
           f" / repo {m['repo_filter']}", band(m)]
    if m.get("archived_count") and not m.get("archived_view"):
        out.append(i18n.t("board.archived_n", n=m["archived_count"]))
    for h in m["halts"]:
        out.append(f"!! ANDON {h.get('scope') or '-'}: {h.get('reason') or ''} ({h.get('by') or '-'})")
    if m["cards"] or m["asks"]:
        out.append("\n## " + i18n.t("board.h_decisions", n=m["cards_open"]))
        # One headline each; the full question, options and reason are `foundry decide show <id>`.
        for d in m["asks"]:
            out.append(f"  {_hl(d)}  [{d['key']}]")
            out.extend(_outline_text(d))
        for cd in m["cards"]:
            keys = "/".join(str(o.get("key")) for o in cd.get("options") or [])
            out.append(f"  {_hl(cd)}  [{cd.get('id')} {keys}]")
            out.extend(_outline_text(cd))
        out.append("  " + i18n.t("board.decide_detail"))
    if m.get("releases"):
        out.append("\n## " + i18n.t("board.h_releases", n=len(m["releases"])))
        for x in m["releases"]:
            out.append(f"  {x['unit']}  {x['stage_ko']}  PR {len(x['prs'])}  {x.get('tag') or '-'}")
    if m["next"]:
        out.append("\n## " + i18n.t("board.h_next", n=len(m["next"])))
        for n in m["next"]:
            out.append(f"  {n.get('unit')} {n.get('repo') or '-'} {n.get('title') or ''} — {n.get('why') or ''}")
    if m["view"] == "repo":
        by_id = {r["id"]: (r, sec["label"]) for sec in m["sections"] for r in sec["rows"]}
        for g in m["repo_groups"]:
            out.append(f"\n## {g['repo']} ({len(g['units'])})")
            for uid in g["units"]:
                r, label = by_id[uid]
                out.append(_row_text(r, label))
    else:
        for sec in m["sections"]:
            out.append(f"\n## {sec['label']} ({sec['count']})")
            if "items" in sec:
                out.extend(f"  {x['unit'] or '-':<24} {x['state'][:60]:<60} {x['title'][:36]}" for x in sec["items"])
                if not sec["items"]:
                    out.append("  -")
                continue
            if not sec["rows"]:
                out.append("  -")
            for r in sec["rows"]:
                out.append(_row_text(r, ", ".join(r["repos"]) or "-"))
    if m["approvals"]:
        out.append("\n## " + i18n.t("board.h_approvals", n=len(m["approvals"])))
        queued = i18n.t("board.queued")
        for a in m["approvals"]:
            out.append(f"  {a['rid']} tier {a['tier']} {a['unit'] or '-'} {a['tool'] or ''}: {(a['summary'] or '')[:80]}"
                       + (f" ({queued})" if a.get("queued") else ""))
    if m["escalations"]:
        out.append("\n## " + i18n.t("board.h_escalations", n=len(m["escalations"])))
        for x in m["escalations"]:
            out.append(f"  {x['kind']:<9} {x['unit'] or '-'}: {x['text'][:90]}")
    inv = m["inventory"]
    blocks = [b for b in m.get("repo_blocks") or [] if b["branch"] or b["worktree"]]
    if m.get("pr_count") or blocks:
        out.append("\n## " + i18n.t("board.inventory"))
        if m.get("pr_count"):  # PRs live on the PR tab: one count line here
            out.append(f"  {m['pr_summary']['text']}  → foundry board --tab pr")
        kinds = {"branch": i18n.t("board.kind_branch"), "worktree": i18n.t("board.kind_worktree")}
        stale = " " + i18n.t("board.stale")
        for b in blocks:
            if repo and repo != "all" and b["repo"] != repo:
                continue
            out.append(f"\n  {b['repo']}  {b['counts']}")
            for it in (inv.get("repos") or {}).get(b["repo"]) or []:
                if it.get("kind") == "pr":
                    continue
                age = f"{it.get('age_h')}h" if it.get("age_h") is not None else "-"
                kind = kinds.get(it.get("kind"), it.get("kind"))
                out.append(f"    {kind:<5} {str(it.get('ref'))[:48]:<48} {age:>6}  {views.inv_state(it)[0]}"
                           + (stale if it.get("stale") else "") + (f" -> {it['action']}" if it.get("action") else ""))
    if m.get("cost_usd"):
        out.append("\n" + i18n.t("board.cost", usd=f"{m['cost_usd']:.2f}"))
    mon = m["monitor"]
    if mon:
        out.append("\n## " + i18n.t("board.pc_load", status=mon.get("status")) + f"  {mon.get('footer') or ''}")
        for t in mon.get("tiles") or []:
            out.append(f"  {t.get('name'):<7} {t.get('value')}{t.get('unit') or ''}  {spark(t.get('spark') or [])}")
    if m["pressure"]["level"] != "ok":
        out.append(f"\npressure {m['pressure']['level']}: {'; '.join(m['pressure']['reasons'])}")
    if m.get("kaizen"):
        out.append("\n## " + i18n.t("board.h_kaizen", at=m["kaizen"].get("at") or "-"))
        out += [f"  {x['text']}" if x["head"] else f"    - {x['text']}" for x in m["kaizen"]["lines"]]
    if (m["handover"] or {}).get("text"):
        out.append("\n## " + handover_head(m["handover"]))
        for sec in handover_sections(m["handover"]):
            out.append(f"  {sec['title']} ({sec['count']})")
            out += [f"    {x['unit'] + ' — ' if x['unit'] else ''}{x['line']}" for x in sec["items"]]
            if sec.get("link") == "pr":
                link = i18n.t("board.pr_tab_all_cmd")
                out.append("    " + (i18n.t("board.more_then", n=sec["more"], link=link) if sec.get("more") else link))
    if m["events"]:
        out.append("\n" + i18n.t("board.events_pending", n=len(m["events"])))
    if m.get("intake_status"):
        out.append("\n" + i18n.t("board.intake_line", **m["intake_status"]))
    if m["proposals"]:
        out.append(i18n.t("board.proposals_pending", n=len(m["proposals"])))
    out.append("\n" + i18n.t("board.legend"))
    return "\n".join(out)


status_label = i18n.status_word  # a unit status's badge label in the UI language; an unknown status as itself


def pane_words(language=None):
    """The pane's catalog with the unit status words (`status_<status>`) from i18n.status_word."""
    return {**i18n.catalog("pane", language), **{f"status_{s}": i18n.status_word(s, language) for s in i18n.STATUS_KEYS}}


GH_ICON = {"open": "git-pull-request", "draft": "git-pull-request-draft", "merged": "git-merge",
           "closed": "git-pull-request-closed"}
REVIEW_MARK = {"approved": "✓", "changes": "✗", "pending": "●"}
# GitHub's own colours (Primer), as CSS variables so light and dark each keep ≥ 4.5:1 on the page background.
GH_LIGHT = {"open": "#1a7f37", "draft": "#59636e", "merged": "#8250df", "closed": "#d1242f",
            "approved": "#1a7f37", "changes": "#d1242f", "pending": "#9a6700"}
GH_DARK = {"open": "#3fb950", "draft": "#9198a1", "merged": "#a371f7", "closed": "#f85149",
           "approved": "#3fb950", "changes": "#f85149", "pending": "#d29922"}


def gh_html(gh, review=None, label=None):
    """A PR's state the way GitHub draws it: the state's icon and word in its colour, then the review accent."""
    import views
    gh = gh if gh in GH_ICON else "open"
    mark = (f" <span class='rv rv-{review}' title='{views.GH_REVIEW_KO[review]}'>{REVIEW_MARK[review]} "
            f"{views.GH_REVIEW_KO[review]}</span>") if review in REVIEW_MARK else ""
    return (f"<span class='gh gh-{gh}'>{octicons.svg(GH_ICON[gh], views.GH_KO[gh])} {html.escape(label or views.GH_KO[gh])}</span>"
            f"{mark}")


def _gh_css():
    light = ";".join(f"--gh-{k}:{v}" for k, v in GH_LIGHT.items())
    dark = ";".join(f"--gh-{k}:{v}" for k, v in GH_DARK.items())
    cls = " ".join(f".gh-{k},.rv-{k}{{color:var(--gh-{k})}}" for k in GH_LIGHT)
    return (f":root{{{light}}} @media (prefers-color-scheme:dark){{:root{{{dark}}}}} {cls} "
            ".gh,.rv{font-weight:600;white-space:nowrap} .rv{margin-left:6px;font-size:12px}")


def _badge(status, label=None):
    return f"<span class='badge s-{html.escape(str(status))}'>{html.escape(label or status_label(status))}</span>"


def _tagged(gist):
    """A timeline gist with its leading `[report]` / `[lead]` / `intake` tag in the tag's color."""
    m = re.match(r"\[([\w-]+)\]\s*|(intake)\b:?\s*", gist or "", re.I)
    if not m:
        return html.escape(gist or "")
    kind = (m[1] or m[2]).lower()
    shown = f"[{kind}]" if m[1] else kind
    return f"<b class='tag tag-{html.escape(kind)}'>{html.escape(shown)}</b> {html.escape(gist[m.end():])}"


def _detail(r):
    e = html.escape
    tr = lambda k, **kw: i18n.t("board." + k, **kw)  # noqa: E731
    v = r.get("view") or {}
    parts = [f"<div class='dhead s-{e(r['status'])}'><b>{e(r['id'])}</b> {_badge(r['status'])} {e(r['title'])}</div>",
             f"<dl><dt>{tr('label_status')}</dt><dd><b>{e(v.get('status_line') or r['status'])}</b></dd>",
             f"<dt>{tr('label_next')}</dt><dd>{e(v.get('next') or '-')} <span class=dim>({e(v.get('who_ko') or '-')})</span></dd>"]
    if v.get("blocked"):
        parts.append(f"<dt>{tr('label_blocked')}</dt><dd class='warn'>{e(' / '.join(v['blocked']))}</dd>")
    parts.append("</dl>")
    facts = [("repo", ", ".join(r["repos"]) or "-"), (tr("label_ticket"), v.get("tracker") or "-")]
    if r.get("session"):
        facts.append((tr("label_session"), f"{r['session'].get('name')} ({r['session'].get('state')})"))
    facts.append(("PR", ", ".join(p["ref"] for p in v.get("prs") or []) or ", ".join(r.get("prs") or []) or "-"))
    icon = {"repo": "repo", "PR": "git-pull-request"}
    parts.append("<dl class=facts>" + "".join(
        f"<dt>{k}</dt><dd class=ico>{octicons.svg(icon[k]) + ' ' if k in icon and x != '-' else ''}{e(x)}</dd>"
        for k, x in facts) + "</dl><dl>")
    if v.get("branch") or v.get("worktree"):
        parts.append(f"<dt>branch</dt><dd class=ico>{octicons.svg('git-branch')} {e(v.get('branch') or '-')} "
                     f"<span class=dim>{e(v.get('worktree') or '')}</span></dd>")
    if v.get("worktree_note"):
        parts.append(f"<dt>worktree</dt><dd class=dim>{e(v['worktree_note'])}</dd>")
    if v.get("chain"):
        parts.append(f"<dt>{tr('label_chain')}</dt><dd>{e(v['chain'])}</dd>")
    if r["held"]:
        parts.append(f"<dt>{tr('status_parked')}</dt><dd>{e(r['held'].get('reason') or '')}</dd>")
    parts.append("</dl>")

    def lst(title, items):
        if items:
            parts.append(f"<h4>{title}</h4><ul>" + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>")
    if v.get("prs"):
        parts.append("<h4>PR</h4><ul>" + "".join(
            f"<li>{gh_html(p.get('gh'), p.get('gh_review'))} {e(p['ref'])} <span class=dim>head {e(p.get('head') or '-')}"
            f" · CI {e(p.get('ci') or '-')} · {tr('label_merge')} {e(p.get('merge_state') or '-')}</span> {e(p.get('title') or '')}"
            + (f"<div class=dim>{e(p['pick'])}</div>" if p.get("pick") else "") + "</li>" for p in v["prs"]) + "</ul>")
    lst(tr("h_waits"), [f"{w['text']} ({w['age_h'] if w['age_h'] is not None else '-'}h) {w['note']}"
                        + (f" · stale: {w['stale_text']}" if w["stale"] else "") for w in v.get("waits") or []])
    lst(tr("h_approval_requests"), [f"T{a['tier']} {a['command']} · " + (a["why"] or tr("ready"))
                                    for a in v.get("approvals") or []])
    lst(tr("h_owner_todos"), v.get("owner_todos") or [])
    lst(tr("h_dispatcher"), v.get("dispatcher") or [])
    decs = r["decisions"]
    if decs:
        opened = tr("answer_open")
        lis = "".join(
            (f"<li><details><summary>{e(d['key'])}: {e(d['question'][:100])}…</summary><div>{e(d['question'])}</div></details>"
             if len(d["question"] or "") > 120 else f"<li>{e(d['key'])}: {e(d['question'] or '')}")
            + f" <b>→ {e(str(d['answer'])) if d['answer'] is not None else opened}</b></li>" for d in decs)
        if r["status"] in graph.CLOSED and all(d["answer"] is not None for d in decs):
            parts.append(f"<details><summary><b>{tr('h_decision')}</b> <span class=dim>{tr('answered', n=len(decs))}</span>"
                         f"</summary><ul>{lis}</ul></details>")
        else:
            parts.append(f"<h4>{tr('h_decision')}</h4><ul>{lis}</ul>")
    lst(tr("h_hands_running"), [f"{h.get('name')} ({h.get('role') or '-'})" for h in v.get("hands_running") or []])
    if v.get("hands_stopped"):
        parts.append(f"<div class=dim>{tr('hands_stopped', n=v['hands_stopped'])}</div>")
    lst(tr("h_inbox"), [f"{i['ts'][5:16]} {i['text']}" for i in r["inbox"]])
    lst(tr("stage"), [f"[{t['status']}] {t['subject']}" for t in r["todos"]])
    tl = v.get("timeline") or []
    if tl:
        parts.append(f"<h4>{tr('h_timeline')}</h4><ul>" + "".join(
            f"<li><details><summary><span class=dim>{e((x['ts'] or '')[5:16].replace('T', ' '))}</span> {_tagged(x['gist'])}</summary>"
            f"<div class=dim>{e(x['text'])}</div></details></li>" for x in tl) + "</ul>")
    return "".join(parts)


def _age_html(h):
    import views
    tone = views.age_tone(h)
    return f"<span{' class=' + tone if tone else ''}>{html.escape(str(h) + 'h' if h is not None else '-')}</span>"


def _inv_html(it):
    """A 재고 row: a branch says its PR's state in GitHub's colours (or 「PR 없음」) and how far ahead it is, a
    worktree its unit's status (or its reclaim state) in the status colours; 경과 is coloured by age."""
    import views
    e = html.escape
    text, tone = views.inv_state(it)
    if it.get("kind") == "branch":
        st = (gh_html(it.get("gh"), it.get("gh_review"), text) + f" <span class=dim>{e(views.short_ref(it.get('pr')))}</span>"
              if it.get("gh") else f"<span class=dim>{e(text)}</span>")
        return f"{octicons.svg('git-branch', i18n.t('board.kind_branch'))} {e(str(it.get('ref')))} {st} {_age_html(it.get('age_h'))}"
    return (f"{octicons.svg('repo', i18n.t('board.kind_worktree'))} {e(str(it.get('ref')))} {_badge(tone or 'queue', text)} "
            f"{_age_html(it.get('age_h'))}")


def _repo_head(b, tag="h3"):
    """A repo block's header: the repo icon, the name in bold on its accent, then its counts."""
    e = html.escape
    a = b["accent"]
    return (f"<{tag} class=repohead><span class=chip style='background:{a['bg']};color:{a['fg']}'>{octicons.svg('repo')} "
            f"<b>{e(b['short'])}</b></span> <span class=dim>{e(b['counts'])}</span></{tag}>")


def _link(url, label):
    e = html.escape
    return f"<a href='{e(url)}' target=_blank rel=noopener>{e(label)}</a>" if url else e(label)


def _reviewer_load_html(inv):
    e = html.escape
    revs = ", ".join(
        f"<span{' class=warn' if x.get('warn') else ''}><b>{e(x['login'])}</b> "
        + i18n.t("board.reviewer_mine", n=x.get("mine", x["pending"]))
        + " <span class=dim>· "
        + i18n.t("board.reviewer_others", n=e(str(x["others"]) if x.get("others") is not None else "-"))
        + "</span></span>"
        for x in inv.get("reviewers") or [])
    return f"<div>{i18n.t('board.reviewer_load', list=revs)}</div>" if revs else ""


def pr_tab_html(m):
    """The PR tab, the one place PRs are listed: stage counts, reviewer load, then one block per repo."""
    e = html.escape
    rows = m.get("pr_tab") or []
    sm = m.get("pr_summary") or {"text": "PR 0", "buckets": []}
    tr = lambda k, **kw: i18n.t("board." + k, **kw)  # noqa: E731
    head = (f"<div class=prsum><b>{e(sm['text'])}</b>"
            + (f" <span class=warn>· {tr('stale_n', n=sm['stale'])}</span>" if sm.get("stale") else "") + "</div>"
            + _reviewer_load_html(m.get("inventory") or {}))
    stale = tr("stale")
    blocks = []
    for b in m.get("repo_blocks") or []:
        mine = [x for x in rows if x.get("repo") == b["repo"]]
        if not mine:
            continue
        trs = "".join(
            f"<tr{' class=stale' if x['stale'] else ''}><td class=ico>{_link(x.get('url'), x.get('short') or x['ref'])}"
            f"{' <b class=stalebadge>' + stale + '</b>' if x['stale'] else ''}</td><td>{e(x['title'])}</td>"
            f"<td>{_link(x.get('unit_url'), str(x['unit'] or '-'))}</td>"
            f"<td>{gh_html(x.get('gh'), x.get('gh_review'), x['state_ko'])}</td><td><b>{e(x['reviewer'])}</b>"
            + (f" <span class=dim>{e(', '.join(x['reviewer_auto']))}</span>" if x.get("reviewer_auto") else "")
            + f"{'<div class=dim>' + e(x['pick']) + '</div>' if x.get('pick') else ''}</td>"
            f"<td>{_age_html(x['age_h'])}</td><td class=one>{e(x['action'])}</td><td class=dim>{e(x['who_ko'])}</td></tr>"
            for x in mine)
        blocks.append(f"<div class=repoblock>{_repo_head(b)}<table class=prs><thead><tr><th>PR</th><th>{tr('th_title')}</th>"
                      f"<th>unit</th><th>{tr('label_status')}</th><th>{tr('th_reviewer')}</th><th>{tr('th_age')}</th>"
                      f"<th>{tr('label_next')}</th><th>{tr('th_who')}</th></tr></thead>"
                      f"<tbody>{trs}</tbody></table></div>")
    return head + ("".join(blocks) or f"<div class=dim>{tr('none')}</div>")


def _extra_html(m):
    """Andon, decision cards, next, inventory, PC load and handover, compact."""
    e = html.escape
    out = []
    for h in m["halts"]:
        out.append(f"<div class='andon'>ANDON {e(str(h.get('scope') or '-'))}: {e(h.get('reason') or '')}</div>")
    cards = []
    for cd in m["cards"]:
        opts = "".join(
            f"<li{' class=rec' if o.get('key') == cd.get('recommended') else ''}><b>{e(str(o.get('key')))}</b> "
            f"{e(o.get('label') or '')} <span class=dim>{e(o.get('description') or '')}</span></li>"
            for o in cd.get("options") or [])
        cards.append(f"<li><details><summary>{e(_hl(cd))}</summary>"
                     f"{_outline_html(cd, cd.get('question'))}<ul>{opts}</ul>"
                     f"<span class=dim>chat: {e(str(cd.get('id')))} &lt;option&gt; · {e(cd.get('detail_cmd') or '')}</span>"
                     f"</details></li>")
    cards += [f"<li><details><summary>{e(_hl(d))}</summary>{_outline_html(d, d['question'])}"
              f"</details></li>" for d in m["asks"]]
    tr = lambda k, **kw: i18n.t("board." + k, **kw)  # noqa: E731
    none = tr("none")
    out.append(f"<section class='waiting_owner'><h2>{tr('decisions')} <span>{len(cards)}</span></h2>"
               f"<ul>{''.join(cards) or '<li class=dim>' + none + '</li>'}</ul></section>")
    rels = m.get("releases") or []
    if rels:
        import views
        trs = "".join(f"<tr><td title='{e(x['unit'])}'>{e(views.short_unit(x['unit']))}</td><td>{e(x['stage_ko'])}</td>"
                      f"<td>{len(x['prs'])}</td><td>{e(x.get('tag') or '-')}</td></tr>" for x in rels)
        out.append(f"<section class='in_progress'><h2>{tr('releases')} <span>{len(rels)}</span></h2><table><thead><tr>"
                   f"<th>{tr('th_release_unit')}</th><th>{tr('stage')}</th><th>PR</th><th>{tr('th_tag')}</th></tr></thead>"
                   f"<tbody>{trs}</tbody></table></section>")
    if m["next"]:
        nx = "".join(f"<li>{e(str(n.get('unit')))} {e(n.get('repo') or '-')} {e(n.get('title') or '')} <span class=dim>{e(n.get('why') or '')}</span></li>"
                     for n in m["next"])
        out.append(f"<section><h2>{tr('next_work')} <span>{len(m['next'])}</span></h2><ul>{nx}</ul></section>")
    inv = m["inventory"]
    blocks = [b for b in m.get("repo_blocks") or [] if b["branch"] or b["worktree"]]
    if m.get("pr_count") or blocks:
        line = (f"<div class=prsum>{e(m['pr_summary']['text'])} · <a href='#' onclick=\"tab('pr');return false\">"
                f"{tr('pr_tab_view')}</a></div>" if m.get("pr_count") else "")
        body = "".join(
            f"<div class=repoblock>{_repo_head(b)}<ul>" + "".join(
                f"<li>{_inv_html(it)}</li>" for it in (inv.get("repos") or {}).get(b["repo"]) or []
                if it.get("kind") != "pr") + "</ul></div>" for b in blocks)
        out.append(f"<section><h2>{tr('inventory')}</h2>{line}{body}</section>")
    mon = m["monitor"]
    if mon:
        tiles = " ".join(f"{e(t.get('name'))} {e(str(t.get('value')))}{e(t.get('unit') or '')} {spark(t.get('spark') or [])}"
                         for t in mon.get("tiles") or [])
        out.append(f"<details><summary>{tr('pc_load', status=e(str(mon.get('status'))))} {e(mon.get('footer') or '')}"
                   f"</summary><div>{tiles}</div></details>")
    if m.get("cost_usd"):
        out.append(f"<div class=dim>{tr('cost', usd=format(m['cost_usd'], '.2f'))}</div>")
    if m.get("intake_status"):
        out.append(f"<div class=dim>{e(i18n.t('board.intake_line', **m['intake_status']))}</div>")
    if m.get("kaizen"):
        kz = "".join(f"<li><b>{e(x['text'])}</b></li>" if x["head"] else f"<li class=d1>{e(x['text'])}</li>"
                     for x in m["kaizen"]["lines"])
        out.append(f"<section class='done'><h2>{tr('kaizen')} <span>{e(m['kaizen'].get('at') or '-')}</span></h2>"
                   f"<ul class=outline>{kz}</ul></section>")
    if (m["handover"] or {}).get("text"):
        h = m["handover"]
        link = f"<a href='#' onclick=\"tab('pr');return false\">{tr('pr_tab_all')}</a>"
        body = "".join(
            f"<h3>{e(sec['title'])} <span class=dim>{sec['count']}</span></h3>"
            + ("<ul class=ho>" + "".join(f"<li>{'<b>' + e(x['unit']) + '</b> — ' if x['unit'] else ''}{e(x['line'])}</li>" for x in sec["items"])
               + "</ul>" if sec["items"] else f"<div class=dim>{none}</div>")
            + (f"<div>{tr('more_then', n=sec['more'], link=link) if sec.get('more') else link}</div>"
               if sec.get("link") == "pr" else "")
            for sec in handover_sections(h))
        out.append(f"<section><h2>{tr('handover')} <span>{e(_handover_meta(h))}</span></h2>{body}</section>")
    return "".join(out)


def _handover_meta(h):
    """The handover's as-of time and change count after its title, empty when the model carries neither."""
    parts = [i18n.t("board.handover_at", at=h["at"])] if h.get("at") else []
    if h.get("changes"):
        parts.append(i18n.t("board.handover_changes", n=h["changes"]))
    return " · ".join(parts)


def handover_head(h):
    """인계 · 08:00 기준 · 이후 변경 N건: the snapshot's time and how much moved since."""
    meta = _handover_meta(h)
    return " · ".join([i18n.t("board.handover")] + ([meta] if meta else []))


def handover_sections(h):
    """The structured handover; a model from before 0.1.30 carries only the text, which gets no sections."""
    return h.get("sections") or []


def pr_tab_text(m):
    """`foundry board --tab pr`: the counts line, reviewer load, then one table per repo block."""
    import views
    stale = m["pr_summary"]["stale"]
    out = [f"foundry PR  {m['pr_summary']['text']}" + (" · " + i18n.t("board.stale_n", n=stale) if stale else "")
           + (" · " + i18n.t("board.ignored_n", n=m["pr_ignored"]) if m.get("pr_ignored") else "")]
    revs = (m.get("inventory") or {}).get("reviewers") or []
    if revs:
        out.append(i18n.t("board.reviewer_load", list=", ".join(
            f"{x['login']} {x.get('text') or x['pending']}{'!' if x.get('warn') else ''}" for x in revs)))
    for b in m.get("repo_blocks") or []:
        rows = [x for x in m["pr_tab"] if x.get("repo") == b["repo"]]
        if rows:
            out += ["", f"{b['repo']}  {b['counts']}", views.pr_table_text(rows)]
    return "\n".join(out)


def render_html(snap):
    return html_of(model(snap))


def html_of(m):
    import views
    e = html.escape
    tr = lambda k, **kw: i18n.t("board." + k, **kw)  # noqa: E731
    none = tr("none")
    empty = f"<tr><td colspan='5' class='empty'>{none}</td></tr>"
    secs = []
    for sec in m["sections"]:
        if "items" in sec:
            rows = "".join(f"<tr class='row'><td title='{e(x['unit'] or '')}'>{e(views.short_unit(x['unit']) or '-')}</td><td>{e(x['title'])}</td><td>{e(x['state'])}</td>"
                           f"<td colspan='2' class='dim'>{e(x['text'][:120])}</td></tr>" for x in sec["items"]) \
                or empty
            secs.append(f"<section class='{sec['key']}'><h2>{e(sec['label'])} <span>{sec['count']}</span></h2>"
                        f"<table><thead><tr><th>{tr('th_unit')}</th><th>{tr('th_title')}</th><th>{tr('label_status')}</th>"
                        f"<th colspan='2'>{tr('th_content')}</th></tr></thead>"
                        f"<tbody>{rows}</tbody></table></section>")
            continue
        rows = "".join(
            f"<tr class='row' onclick=\"toggle('{e(r['id'])}')\"><td title='{e(r['id'])}'>{e(views.short_unit(r['id']))} {_badge(r['status'])}</td><td>{e(r['title'])}</td>"
            f"<td>{e(r['summary'])}</td><td>{e((r['updated'] or '')[5:16].replace('T', ' '))}</td>"
            f"<td>{e(r['session']['state'] if r['session'] else '')}</td></tr>"
            f"<tr id='d-{e(r['id'])}' class='detail' hidden><td colspan='5'>{_detail(r)}</td></tr>"
            for r in sec["rows"]) or empty
        secs.append(f"<section class='{sec['key']}'><h2>{e(sec['label'])} <span>{sec['count']}</span></h2>"
                    f"<table><thead><tr><th>{tr('th_unit')}</th><th>{tr('th_title')}</th><th>{tr('th_now')}</th>"
                    f"<th>{tr('th_updated')}</th><th>{tr('label_session')}</th></tr></thead>"
                    f"<tbody>{rows}</tbody></table></section>")
    queued = tr("queued")
    appr = "".join(f"<li>{e(a['rid'])} tier {e(str(a['tier']))} {e(a['unit'] or '-')} {e(a['tool'] or '')}: {e(a['summary'] or '')}"
                   + (f" <span class=dim>({queued})</span>" if a.get("queued") else "") + "</li>"
                   for a in m["approvals"])
    esc = "".join(f"<li>{e(x['kind'])} {e(x['unit'] or '-')}: {e(x['text'])}</li>" for x in m["escalations"])
    pr = m["pressure"]
    props = "".join(f"<li>{e(p['cls'])}: {e(p['target'])} ({e(p.get('reason') or '')})"
                    + (f" · {e(tr('in_use'))}" if p.get("in_use") else "")
                    + (f" · {e(tr('nowhere'))}: {e(p['nowhere'])}" if p.get("nowhere") else "") + "</li>"
                    for p in m["proposals"])
    pr_tab = pr_tab_html(m)
    gen = tr("gen", at=e(m["generated"]), level=e(pr["level"]), reasons=e("; ".join(pr["reasons"])))
    if m.get("archived_count"):
        gen += " · " + e(tr("archived_n", n=m["archived_count"]))
    no_li = f"<li class=dim>{none}</li>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>foundry board</title>
<meta http-equiv="refresh" content="60">
<style>
body{{font:14px -apple-system,system-ui,sans-serif;margin:24px;color:#1f2328;background:#fff}}
@media (prefers-color-scheme:dark){{body{{color:#e6edf3;background:#0d1117}} tr.row:hover{{background:#161b22!important}} th{{background:#161b22!important}}}}
h1{{font-size:18px;margin:0 0 4px}} .gen{{color:#8b949e;font-size:12px;margin-bottom:16px}}
section{{margin-bottom:20px}} h2{{font-size:14px;margin:0 0 6px;border-left:4px solid #8b949e;padding-left:8px}}
h2 span{{color:#8b949e;font-weight:normal}} h3{{font-size:13px;margin:10px 0 4px}} ul.ho{{margin:0;padding-left:18px}}
section.waiting_owner h2{{border-color:#d29922}} section.in_progress h2{{border-color:#2f81f7}}
section.waiting_others h2{{border-color:#a371f7}} section.done h2,section.released h2{{border-color:#3fb950}}
section.release_wait h2{{border-color:#8250df}} section.cancelled h2{{border-color:#8b949e}}
table{{border-collapse:collapse;width:100%;table-layout:fixed}} th:nth-child(1){{width:18%}} th:nth-child(2){{width:24%}} th:nth-child(4){{width:11%}} th:nth-child(5){{width:8%}} th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid #8b949e33;vertical-align:top}}
th{{background:#f6f8fa;font-weight:600;font-size:12px}} tr.row{{cursor:pointer}} tr.row:hover{{background:#f6f8fa}}
td.empty{{color:#8b949e}} .andon{{background:#da3633;color:#fff;padding:6px 10px;font-weight:600;margin-bottom:8px}}
.warn{{color:#d29922}} .dim{{color:#8b949e}} li.rec{{font-weight:600}} .detail td{{padding:8px 24px}} dl{{display:grid;grid-template-columns:120px 1fr;margin:0}}
.oct{{vertical-align:-2px;opacity:.8}} dt{{color:#8b949e}} dd{{margin:0;font-weight:600}}
dt,dd{{padding:3px 0;border-bottom:1px solid #8b949e22}} dl.facts{{grid-template-columns:120px 1fr 120px 1fr;margin:4px 0}}
@media (max-width:760px){{dl.facts{{grid-template-columns:120px 1fr}}}}
:root{{--done-bg:#E4F2CF;--done-fg:#3E7010;--run-bg:#DFE9F6;--run-fg:#2B5E9E;--wait-bg:#F6ECD2;--wait-fg:#8A5A0C;
--block-bg:#F8E1DE;--block-fg:#A8291F;--mute-bg:#E4E7EA;--mute-fg:#475260;--pro-fg:#6A3FC2}}
@media (prefers-color-scheme:dark){{:root{{--done-bg:#1C3315;--done-fg:#A6E08A;--run-bg:#12294A;--run-fg:#9CC7FF;
--wait-bg:#3A2A08;--wait-fg:#F2C661;--block-bg:#41140F;--block-fg:#FFA198;--mute-bg:#262C33;--mute-fg:#C1C9D2;--pro-fg:#C9A8FF}}}}
.badge{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;white-space:nowrap}}
.s-done,.s-p-merge-wait{{background:var(--done-bg);color:var(--done-fg)}} .s-in_progress,.s-p-approved{{background:var(--run-bg);color:var(--run-fg)}}
.s-waiting_owner,.s-waiting_others,.s-p-review{{background:var(--wait-bg);color:var(--wait-fg)}} .s-blocked{{background:var(--block-bg);color:var(--block-fg)}}
.s-queue,.s-parked,.s-cancelled,.s-p-draft,.s-p-release-wait{{background:var(--mute-bg);color:var(--mute-fg)}}
.dhead{{padding:6px 10px;border-radius:6px;margin-bottom:6px;font-size:15px}} .dhead .badge{{background:transparent;border:1px solid currentColor}}
.tag-report{{color:var(--done-fg)}} .tag-lead{{color:var(--run-fg)}} .tag-intake{{color:var(--pro-fg)}} .tag-owner{{color:var(--wait-fg)}}
a{{color:var(--run-fg)}} .detail li{{margin:4px 0}} ul.outline li.d1{{margin-left:18px;list-style:circle}} ul.outline li.d2{{margin-left:36px;list-style:square}} ul.outline li.d3{{margin-left:54px;list-style:square}} td.one{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} nav.tabs{{margin:0 0 12px}} nav.tabs a{{margin-right:12px}} table.prs th:nth-child(n){{width:auto}} h4{{margin:10px 0 2px;font-size:12px}} ul{{margin:0;padding-left:18px}}
.repohead{{font-size:13px;margin:12px 0 4px}} .chip{{display:inline-block;padding:1px 8px;border-radius:6px}} .repoblock{{margin-bottom:14px}}
.prsum{{margin:4px 0 8px}} .stalebadge{{background:var(--wait-bg);color:var(--wait-fg);border-radius:8px;padding:0 6px;font-size:11px}}
{_gh_css()}
</style></head><body><h1>foundry board</h1><div class="gen">{gen}</div>
<div class="gen">{e(band(m))}</div>
<nav class=tabs><a href='#' id=t-main onclick="tab('main');return false">{tr('tab_board')}</a> <a href='#' id=t-pr onclick="tab('pr');return false">PR ({m.get('pr_count') or 0})</a></nav>
<div id=tab-pr hidden>{pr_tab}</div><div id=tab-main>{_extra_html(m)}
<section><h2>{tr('escalations')} <span>{len(m['escalations'])}</span></h2><ul>{esc or no_li}</ul></section>
<section><h2>{tr('approvals')} <span>{len(m['approvals'])}</span></h2><ul>{appr or no_li}</ul></section>
{''.join(secs)}
<section><h2>{tr('proposals')} <span>{len(m['proposals'])}</span></h2><ul>{props or no_li}</ul></section></div>
<script>function toggle(id){{const r=document.getElementById('d-'+id);if(r)r.hidden=!r.hidden}}
function tab(t){{for(const k of ['main','pr']){{document.getElementById('tab-'+k).hidden=k!==t;document.getElementById('t-'+k).style.fontWeight=k===t?'600':'normal'}}}}
if(location.hash==='#pr')tab('pr');else{{tab('main');if(location.hash)toggle(decodeURIComponent(location.hash.slice(1)))}}</script>
</body></html>"""


def write_all(snap):
    # tmp + rename: the pane and the browser poll these files and must never read a half-written one.
    cl.write_atomic(cl.path("board.json"), json.dumps(model(snap), ensure_ascii=False))
    p = cl.path("board.html")
    cl.write_atomic(p, render_html(snap))
    return p


def _age_days(text):
    """`3d`, `12h` or a bare number of days as days; None when it does not parse."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([dh]?)\s*", str(text or ""))
    return None if not m else float(m.group(1)) / (24 if m.group(2) == "h" else 1)


def archive_candidates(snap, older_days=None, all_done=False, ctx=None):
    """Units `foundry board archive` would move: 릴리스 완료 / 완료 / 취소 by unit_state, not archived yet, closed at
    least older_days ago (any age with all_done)."""
    ctx = ctx or state_ctx(snap)
    out = []
    for u in snap["units"].values():
        if u["kind"] == "foreman" or u.get("archived"):
            continue
        st = unit_state(u, ctx)
        if st["group"] not in ARCHIVABLE:
            continue
        t = cl.parse_ts(u.get("closed") or u.get("updated"))
        if all_done or (t and (ctx["now"] - t).total_seconds() >= older_days * 86400):
            out.append({"id": u["id"], "group": st["group"], "closed": u.get("closed") or u.get("updated")})
    return sorted(out, key=lambda x: x["closed"] or "")


def cmd_archive(a):
    """`foundry board archive --older 3d | --all-done [--dry-run]`: record finished units as archived (the board's
    archive view, `foundry board --archived`). It deletes nothing. The owner, or the foreman relaying the owner's
    words with --quote (「정리해줘」)."""
    import graph
    older = _age_days(a.older) if a.older else None
    if not a.all_done and older is None:
        return graph.fail("refused: give --older <N>d|<N>h or --all-done")
    snap = cl.load()
    cands = archive_candidates(snap, older, a.all_done)
    if a.dry_run:
        return graph.emit({"dry_run": True, "would_archive": cands}) or 0
    by, err = graph.check_by(a.by, "archiving board units")
    if err:
        return graph.fail(err)
    quote = cl.relay_quote(by, a.quote)
    if by != "owner" and not quote:
        return graph.fail("refused: the owner archives, or the foreman with --quote \"<the owner's exact words>\"")
    if cands:
        cl.append("unit.archive", ids=[x["id"] for x in cands], by=by, older=a.older, all_done=bool(a.all_done),
                  **({"quote": quote} if quote else {}))
    return graph.emit({"archived": [x["id"] for x in cands], "by": by, "view": "foundry board --archived"}) or 0


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="board.py")
    p.add_argument("fmt", nargs="?", default="text", choices=["text", "json", "html", "metrics"])
    p.add_argument("--view", choices=VIEWS, default="status")
    p.add_argument("--repo")
    p.add_argument("--hours", type=int, choices=[3, 6, 24], default=3)
    p.add_argument("--tab", choices=["main", "pr"], default="main", help="pr: the PR tab (text only)")
    p.add_argument("--archived", action="store_true", help="the archive view: finished units moved off the board")
    a = p.parse_args(argv)
    if a.fmt == "metrics":
        print(cached_metrics(a.hours))
        return 0
    s = cl.load()
    if a.fmt == "text" and a.tab == "pr":
        print(pr_tab_text(model(s, a.view, a.repo)))
    elif a.fmt == "text":
        print(text_of(model(s, a.view, a.repo, a.archived)))
    elif a.fmt == "json":
        m = model(s, a.view, a.repo, a.archived)
        # The pane's fixed words in prefs ui.language (hooks/board-pane.tsx falls back to Korean without them).
        m["i18n"] = {"lang": i18n.lang(), "pane": pane_words()}
        print(json.dumps(m, ensure_ascii=False))
    else:
        print(write_all(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
