"""Lists that must drain, not only grow: near-misses and waiting PRs.

Near-misses: every entry gets a pattern (kind + the call shape that tripped). `triage` (run from housekeeping,
`sweep.announce`) maps each open pattern to an outcome: a plugin fix, a known guard false positive, or
acknowledged; a pattern with no known outcome that recurs `nearmiss.recur_min` times becomes one owner card.
An entry is resolved when its pattern was resolved at or after it; resolved entries show as a count only and
expire after `nearmiss.expire_days`. A fixed pattern that recurs is open again.

Waiting PRs: `pr_cycle` (the github stream) files one reminder in the PR's existing ask thread after
`review.remind_business_days` without review, opens one owner card (close / reassign / keep waiting) after
`review.escalate_business_days`, and asks the owner to adopt or drop an open PR no live unit owns. Dropped PRs
leave the board with their reason; a drop key `owner/repo#*` or `owner/*#*` drops every PR it matches (`is_dropped`).
"""

import fnmatch
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

STATE = {"nearmiss_patterns": {}, "pr_dropped": {}, "pr_undropped": {}}
REPO_PATTERN = re.compile(r"^[\w.-]+/(?:[\w.-]+|\*)$")  # owner/repo or owner/*: never a bare `*`

# (shape, test on the denied command or the false-positive description, outcome, note). First match wins.
SHAPES = (
    ("heredoc", re.compile(r"<<-?\s*['\"]?\w|heredoc", re.I), "fixed",
     "0.1.25: prompts write files with Write/Edit, never heredocs"),
    ("multiline-message", re.compile(r"\bcommit\b.*(?:\s-[a-zA-Z]*m\b|message)", re.I), "fixed",
     "0.1.25: prompts and the hook reason say Write the message, then `git -C <wt> commit -F <file>`"),
    ("inline-body", re.compile(r"\bgh\s+(?:pr|issue)\b.*--body\b(?!-file)|--body\s", re.I), "fixed",
     "0.1.25: prompts say Write the body, then `--body-file <file>`"),
    ("loop", re.compile(r"\b(?:for|while|until)\b[^;]*;\s*do\b", re.I), "fixed",
     "0.1.25: prompts say one literal call per item, no shell loops"),
    ("variable", re.compile(r"\$\{?\w|through a variable", re.I), "ack",
     "the hook refused a variable-built call as designed (one-command rule in every prompt)"),
    ("redirect", re.compile(r"\s\d?>>?\s*\S|>&"), "fixed",
     "0.1.25: prompts say read whole output, write files with Write"),
    ("compound", re.compile(r";|&&|\|\||\|"), "ack",
     "the hook refused a compound call as designed and handed back the split form"),
    ("force-push", re.compile(r"--force", re.I), "ack",
     "the push policy refused a force push outside the unit branch as designed"),
)
OUTCOMES = ("fixed", "guard-fp", "ack")


def _p():
    import prefs
    return prefs.load()


def pattern(n):
    """(pattern, shape): `kind:shape`, the shape read off the denied command (or the false-positive text)."""
    kind = n.get("kind") or "unknown"
    detail = str(n.get("detail") or "")
    if kind == "stall":
        return f"stall:{detail.split(':', 1)[0].strip() or 'stall'}", "stall"
    m = re.match(r"after deny of `([^`]*)`", detail)
    text = m.group(1) if m else detail
    shape = next((s for s, rx, _, _ in SHAPES if rx.search(text)), "other")
    if kind == "guard-false-positive" and "agent-guard" in detail.lower():
        shape = "agent-guard:" + shape
    return f"{kind}:{shape}", shape


def known_outcome(kind, shape, p):
    """(outcome, ref, note) for a pattern the plugin already answers, else None."""
    refs = (p.get("nearmiss") or {}).get("guard_refs") or {}
    if kind == "stall":
        return "ack", None, "stalls have their own lifecycle (stall.recover, stall.cleared)"
    if shape.startswith("agent-guard:"):
        base = shape.split(":", 1)[1]
        return "guard-fp", refs.get(base) or refs.get("*"), f"known agent-guard false positive ({base}); " \
            "the prompts avoid the call shape"
    hit = next((s for s in SHAPES if s[0] == shape), None)
    return (hit[2], None, hit[3]) if hit else None


def _resolved(n, pats):
    r = pats.get(n.get("pattern") or pattern(n)[0])
    return bool(r) and (r.get("ts") or "") >= (n.get("ts") or "")


def split(snap):
    """(open, resolved) near-miss entries."""
    pats = snap.get("nearmiss_patterns") or {}
    op, done = [], []
    for n in snap.get("nearmiss") or []:
        (done if _resolved(n, pats) else op).append(n)
    return op, done


def groups(entries):
    """One row per pattern: count, units, last time, one sample detail."""
    out = {}
    for n in entries:
        pat = n.get("pattern") or pattern(n)[0]
        g = out.setdefault(pat, {"pattern": pat, "kind": n.get("kind"), "count": 0, "units": [], "last": None,
                                 "sample": None})
        g["count"] += 1
        if n.get("unit") and n["unit"] not in g["units"]:
            g["units"].append(n["unit"])
        if (n.get("ts") or "") >= (g["last"] or ""):
            g["last"], g["sample"] = n.get("ts"), (n.get("detail") or "")[:200]
    return sorted(out.values(), key=lambda g: (-g["count"], g["pattern"]))


# ---------- reducers ----------

def _op_nearmiss(s, r, ts):
    """Runs after factory's reducer appended the entry: stamp its pattern."""
    if s.get("nearmiss"):
        n = s["nearmiss"][-1]
        n.setdefault("pattern", r.get("pattern") or pattern(n)[0])  # triage reads the full denied command


def _op_resolve(s, r, ts):
    s.setdefault("nearmiss_patterns", {})[r["pattern"]] = {
        "outcome": r.get("outcome"), "ref": r.get("ref"), "note": r.get("note"), "by": r.get("by"), "ts": ts}


def _op_expire(s, r, ts):
    pats = s.get("nearmiss_patterns") or {}
    cut = r.get("before") or ""
    s["nearmiss"] = [n for n in s.get("nearmiss") or [] if not (_resolved(n, pats) and (n.get("ts") or "") < cut)]


def _op_pr_dropped(s, r, ts):
    s.setdefault("pr_dropped", {})[r["ref"]] = {"reason": r.get("reason"), "by": r.get("by"), "ts": ts}


def _op_pr_undropped(s, r, ts):
    (s.get("pr_dropped") or {}).pop(r["ref"], None)
    s.setdefault("pr_undropped", {})[r["ref"]] = ts  # an older drop answer on a card does not drop it again


def is_dropped(snap, ref):
    """The pr_dropped key that hides `ref` (exact, or a `owner/repo#*` / `owner/*#*` pattern), else None."""
    dropped = snap.get("pr_dropped") or {}
    if ref in dropped:
        return ref
    return next((k for k in dropped if "*" in k and fnmatch.fnmatchcase(ref, k)), None)


def _op_wait_repin(s, r, ts):
    u = (s.get("units") or {}).get(r.get("id")) or {}
    for w in u.get("waits") or []:
        if w.get("wid") == r.get("wid"):
            w["note"] = r.get("note")


OPS = {"nearmiss": _op_nearmiss, "nearmiss.resolve": _op_resolve, "nearmiss.expire": _op_expire,
       "pr.dropped": _op_pr_dropped, "pr.undropped": _op_pr_undropped, "wait.repin": _op_wait_repin}


# ---------- waits that drifted from their PR ----------

HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
WAKE_ON = ("waiting_others", "waiting_owner", "blocked")


def wait_repairs(snap, viewed):
    """([records], [lines]) for the github stream: a unit parked on an open PR that names reviewers gets the
    pr-review wait it lacks (registered here, not by a lead's hand-typed command), and a wait whose note pins a
    sha that is no longer the PR head is re-pinned to the head."""
    recs, lines = [], []
    for ref, (uid, pr) in sorted(viewed.items()):
        u = (snap.get("units") or {}).get(uid) or {}
        if u.get("status") not in WAKE_ON or pr.get("state") != "OPEN" or pr.get("isDraft"):
            continue
        waits = u.get("waits") or []
        revs = [x.get("login") for x in pr.get("reviewRequests") or [] if x.get("login")]
        if revs and str(pr.get("reviewDecision") or "").upper() != "APPROVED" and not any(
                w.get("kind") == "pr-review" and w.get("ref") == ref for w in waits):
            recs.append(("wait.add", {"id": uid, "wid": f"pr-review:{ref}", "kind": "pr-review", "ref": ref,
                                      "until": None, "note": f"review by {', '.join(revs)}", "fingerprint": None}))
            lines.append(f"wait {uid}: pr-review on {ref} registered (review by {', '.join(revs)})")
        head = pr.get("headRefOid") or ""
        for w in waits:
            note = w.get("note") or ""
            old = [h for h in HEX.findall(note) if not h.isdigit() and not head.startswith(h[:7])]
            if w.get("ref") != ref or not head or not old:
                continue
            for h in old:
                note = note.replace(h, head[:7])
            recs.append(("wait.repin", {"id": uid, "wid": w["wid"], "note": note}))
            lines.append(f"wait {uid}: {w['wid']} re-pinned {old[0][:7]} -> {head[:7]}")
    return recs, lines


# ---------- near-miss triage ----------

def _card_rec(cards, opened, **f):
    import decide
    taken = set(cards) | set(opened)
    cid = decide._next_id(taken)
    opened.append(cid)
    return {k: f.get(k) for k in decide.FIELDS} | {"id": cid, "asked_by": "foundry"}


def _card(snap, key):
    return next((c for c in (snap.get("cards") or {}).values() if c.get("key") == key), None)


def triage(p=None):
    """Resolve every open pattern the plugin can answer, apply answered pattern cards, open one card per
    recurring unknown pattern, and expire resolved entries past `nearmiss.expire_days`."""
    import datetime as dt
    p = p or _p()
    nm = p.get("nearmiss") or {}

    def decide_(snap):
        t = cl.now()
        recs, report, opened = [], {"resolved": [], "cards": [], "open": [], "expired": 0}, []
        op, _ = split(snap)
        for g in groups(op):
            kind, shape = g["pattern"].split(":", 1)
            known = known_outcome(kind, shape, p)
            card = _card(snap, f"nearmiss:{g['pattern']}")
            if not known and card and card.get("answer") in OUTCOMES:
                known = (card["answer"], None, f"owner answered {card['id']}")
            if known:
                outcome, ref, note = known
                recs.append(("nearmiss.resolve", {"pattern": g["pattern"], "outcome": outcome, "ref": ref,
                                                  "note": note, "by": "triage"}))
                report["resolved"].append({"pattern": g["pattern"], "count": g["count"], "outcome": outcome})
            elif g["count"] >= nm.get("recur_min", 3) and not card:
                rec = _card_rec(snap.get("cards") or {}, opened, key=f"nearmiss:{g['pattern']}",
                                question=(f"같은 near-miss 패턴 `{g['pattern']}` 이 {g['count']}번 반복됐습니다 "
                                          f"({', '.join(g['units'][:5]) or '-'}). 예: {(g['sample'] or '')[:160]} "
                                          "플러그인에서 고칠까요, 알려진 guard 오탐으로 둘까요, 확인만 할까요?"),
                                options=[{"key": "fixed", "label": "플러그인 수정 (foundry unit 이 고침)"},
                                         {"key": "guard-fp", "label": "알려진 guard 오탐"},
                                         {"key": "ack", "label": "확인만, 변경 없음"}],
                                recommended="fixed", reason="반복되는 형태는 재시도보다 막는 편이 싸다")
                recs.append(("decide.open", rec))
                report["cards"].append(rec["id"])
            else:
                report["open"].append({"pattern": g["pattern"], "count": g["count"]})
        cut = cl.iso(t - dt.timedelta(days=nm.get("expire_days", 7)))
        pats = dict(snap.get("nearmiss_patterns") or {})
        pats.update({r["pattern"]: {"ts": cl.iso(t)} for op_, r in recs if op_ == "nearmiss.resolve"})
        report["expired"] = sum(1 for n in snap.get("nearmiss") or [] if _resolved(n, pats) and (n.get("ts") or "") < cut)
        if report["expired"]:
            recs.append(("nearmiss.expire", {"before": cut}))
        return report, recs

    res, _ = cl.transact(decide_)
    return res


# ---------- waiting PRs ----------

def _asks(snap, ref, kinds=("review-ask", "review-remind")):
    return [o for o in (snap.get("outbox") or {}).values()
            if o.get("kind") in kinds and ref in (o.get("prs") or [])]


def _ask_thread(snap, ref):
    """(channel, thread ts, recipients) of the latest review ask or reminder about ref that is out in Slack, with a
    real message ts as the thread parent (never a scheduled-message id), else None."""
    import outbox
    sent = [o for o in _asks(snap, ref) if outbox.posted_at(o)]
    if not sent:
        return None
    o = max(sent, key=lambda o: outbox.posted_at(o))
    thread = o.get("thread") if outbox.THREAD_RE.match(str(o.get("thread") or "")) else o.get("sent_ts")
    return (o.get("channel"), thread, list(o.get("to") or [])) if thread else None


def ask_pending(snap, ref):
    """A review ask about ref is filed or scheduled but not out in Slack yet: nothing to remind about."""
    import outbox
    return any(o["state"] == "pending" or outbox.scheduled_pending(o) for o in _asks(snap, ref, ("review-ask",)))


def due_reviewers(snap, ref, t, zone, days):
    """Recipients whose own latest posted ask about ref is `days` business days old and who were not reminded since.
    A team-only review request on GitHub starts no one's clock: only an ask sent to that person does."""
    import factory
    import outbox
    last = {}
    for o in _asks(snap, ref):
        at = outbox.posted_at(o)
        for who in o.get("to") or [""]:  # "": an ask posted to the channel with no one named (a top-level ask)
            if at and (who not in last or at > last[who][0]):
                last[who] = (at, o.get("kind"))
    return sorted(who for who, (at, kind) in last.items()
                  if kind == "review-ask" and factory.business_hours(at, t, zone) / 24 >= days)


def _reminded(snap, ref):
    return any(o.get("kind") == "review-remind" and o.get("state") == "pending" for o in _asks(snap, ref))


def actions(snap, t=None, p=None, inv=None):
    """{ref: action} for every PR row: remind | reminded | no-ask-thread | owner-decision | adopt-or-drop | None."""
    import factory
    p = p or _p()
    t = t or cl.now()
    rv = p.get("review") or {}
    zone = factory._zone(p)
    inv = inv or factory.inventory(snap, t, p)
    out = {}
    for rows in inv["repos"].values():
        for r in rows:
            if r["kind"] != "pr":
                continue
            u = (snap["units"].get(r.get("unit")) or {})
            # An owner PR no unit tracks (factory.collect_prs: owner_prs) ages like a unit's: no unit to adopt it into.
            owned = not r.get("unit") and r["ref"] in (snap.get("owner_prs") or {})
            if (not u and not owned) or u.get("status") == "done":
                if r["stage"] in (*factory.REVIEW_STAGES, "approved", "draft"):  # a merged PR has nothing to adopt
                    out[r["ref"]] = "adopt-or-drop"
                continue
            if not factory.in_review(r["stage"]):
                continue
            bd = factory.business_hours(cl.parse_ts(r.get("since")), t, zone) / 24
            if bd >= rv.get("escalate_business_days", 5):
                out[r["ref"]] = "owner-decision"
            elif ask_pending(snap, r["ref"]):
                out[r["ref"]] = "ask-pending"  # scheduled or queued, not out yet: no reminder on it
            elif _reminded(snap, r["ref"]):
                out[r["ref"]] = "reminded"
            elif not _ask_thread(snap, r["ref"]):
                if bd >= rv.get("remind_business_days", 1):
                    out[r["ref"]] = "no-ask-thread"
            elif due_reviewers(snap, r["ref"], t, zone, rv.get("remind_business_days", 1)):
                out[r["ref"]] = "remind"
    return out


def pr_cycle(p=None, now=None):
    """One pass: file due reminders, open due owner cards, apply answered drop cards. Returns stream lines."""
    import decide
    import factory
    import outbox
    p = p or _p()
    owned = factory.owner_open_prs()  # read-only, outside the lock; None (gh down) keeps the last list
    if owned is not None and owned != [{k: v for k, v in x.items() if k != "seen"}
                                       for x in (cl.load().get("owner_prs") or {}).values()]:
        cl.append("pr.owner_open", prs=owned)
    live = {}  # cards hinge on PRs no unit tracks: read their state (read-only) outside the journal lock
    for ref in decide.untracked_pr_refs(cl.load()):
        repo, num = ref.rsplit("#", 1)
        v = factory.gh("pr", "view", num, "-R", repo, "--json", "state")
        if isinstance(v, dict) and v.get("state"):
            live[ref] = str(v["state"]).upper()

    def decide_(snap):
        t = now or cl.now()
        inv = factory.inventory(snap, t, p)
        rows = {r["ref"]: r for rs in inv["repos"].values() for r in rs if r["kind"] == "pr"}
        recs, lines, opened = [], [], []
        for ref, act in sorted(actions(snap, t, p, inv).items()):
            r = rows[ref]
            uid = r.get("unit")
            if act == "remind":
                channel, thread, _ = _ask_thread(snap, ref)
                to = due_reviewers(snap, ref, t, factory._zone(p), p["review"].get("remind_business_days", 1))
                rec = outbox.record(uid, channel, thread, "review-remind",
                                    f"{ref} のレビューのリマインドです。お手すきの際にご確認をお願いします。", [ref],
                                    [w for w in to if w])
                if rec["oid"] not in (snap.get("outbox") or {}):
                    recs.append(("outbox.add", rec))
                    lines.append(f"pr {ref} ({uid}): no review for {p['review'].get('remind_business_days', 1)} "
                                 "business day(s): reminder filed in the ask thread")
            elif act == "ask-pending":
                late = [o for o in _asks(snap, ref, ("review-ask",)) if outbox.scheduled_pending(o)
                        and (cl.parse_ts(o.get("post_at")) or t) < t]
                for o in late:
                    lines.append(f"pr {ref} ({uid}): scheduled ask {o['oid']} should be out since {o.get('post_at')}: "
                                 f"find its message ts and run `foundry outbox posted {o['oid']} --ts <ts>`")
            elif act == "owner-decision" and not _card(snap, f"pr-aging:{ref}"):
                rec = _card_rec(snap.get("cards") or {}, opened, key=f"pr-aging:{ref}", unit=None, repo=r.get("repo"),
                                question=(f"{ref} ({uid}) 가 영업일 {p['review'].get('escalate_business_days', 5)}일 "
                                          f"넘게 리뷰를 기다리고 있습니다 ({r.get('waits_on')}). PR 을 닫을까요, "
                                          "리뷰어를 바꿀까요, 계속 기다릴까요?"),
                                options=[{"key": "close", "label": "PR 닫기"},
                                         {"key": "reassign", "label": "리뷰어 교체"},
                                         {"key": "wait", "label": "계속 대기"},
                                         {"key": "drop", "label": "보드에서 제외 (내 담당 아님)"}],
                                recommended="reassign", reason="리마인드를 이미 보냈는데 리뷰가 없다")
                recs.append(("decide.open", rec))
                lines.append(f"pr {ref} ({uid}): owner card {rec['id']} (close / reassign / keep waiting)")
            elif act == "adopt-or-drop":
                card = _card(snap, f"pr-orphan:{ref}")
                if not card and r.get("known_open"):
                    rec = _card_rec(snap.get("cards") or {}, opened, key=f"pr-orphan:{ref}", unit=None,
                                    repo=r.get("repo"),
                                    question=(f"{ref} 는 열려 있지만 맡은 unit 이 없습니다 (unit {uid or '-'} "
                                              f"{'완료' if uid else '없음'}). unit 에 편입할까요, 보드에서 뺄까요?"),
                                    options=[{"key": "adopt", "label": "unit 에 편입 (foreman 이 `foundry pr adopt` 실행)"},
                                             {"key": "drop", "label": "보드에서 제외"}],
                                    recommended="drop" if uid else "adopt", reason="주인 없는 대기는 움직이지 않는다")
                    recs.append(("decide.open", rec))
                    lines.append(f"pr {ref}: no live unit: owner card {rec['id']} (adopt / drop)")
        for ref, cid in card_drops(snap):
            recs.append(("pr.dropped", {"ref": ref, "reason": f"owner answered {cid}: drop", "by": "owner"}))
            lines.append(f"pr {ref}: owner answered {cid}: drop: off the board")
        for rid, ref, st in moot_merges(snap):
            recs.append(("approval.answer", {"rid": rid, "behavior": "deny", "by": "github",
                                             "reason": f"{ref} 는 이미 {'머지됨' if st == 'MERGED' else '닫힘'}: merge 요청 자동 철회"}))
            lines.append(f"approval {rid}: {ref} is {st}: merge request withdrawn")
        import approve
        for op_, r in approve.stale_records(snap, p, t):
            recs.append((op_, r))
            lines.append(f"approval {r['rid']}: stale ({r['reason']}): off the board")
        for op_, r in decide.moot_records(snap, live):  # settled cards close as foundry, never the owner's chore
            recs.append((op_, r))
            lines.append(f"card {r.get('id') or r.get('key')}: 자동 종료 ({r['moot']})")
        return lines, recs

    lines, _ = cl.transact(decide_)
    if any(x.startswith("card ") for x in lines):
        import graph
        graph.apply_resolve()
    return lines


def card_drops(snap):
    """[(ref, card id)] for pr-aging / pr-orphan cards answered `drop` whose PR is not dropped yet, unless the
    owner undropped it after that answer."""
    import decide
    out = []
    for cid, c in sorted((snap.get("cards") or {}).items()):
        key = str(c.get("key") or "")
        pre = next((x for x in decide.KEY_PR_PREFIXES if key.startswith(x)), None)
        if not pre or c.get("answer") != "drop":
            continue
        ref = key[len(pre):]
        undone = (snap.get("pr_undropped") or {}).get(ref)
        answered = cl.parse_ts(c.get("answered"))
        if not is_dropped(snap, ref) and not (undone and answered and cl.parse_ts(undone) >= answered):
            out.append((ref, cid))
    return out


def _merge_ref(summary):
    """`repo#n` of a `gh pr merge <n> -R <repo>` request, else None."""
    m = re.search(r"\bgh pr merge\b([^;&|\n]*)", summary or "")
    if not m:
        return None
    toks = m[1].split()
    num = next((x for x in toks if x.isdigit()), None)
    repo = next((toks[i + 1] for i, x in enumerate(toks[:-1]) if x in ("-R", "--repo")), None)
    return f"{repo}#{num}" if num and repo else None


def moot_merges(snap):
    """[(rid, ref, state)] for unanswered merge requests whose PR the github stream saw MERGED or CLOSED."""
    states = {}
    for u in snap["units"].values():
        for pr in u.get("prs") or []:
            if isinstance(pr, dict) and pr.get("repo") and pr.get("number") is not None:
                st = "MERGED" if pr.get("merged") else str(pr.get("state") or "").upper()
                states[f"{pr['repo']}#{pr['number']}"] = st
    out = []
    for rid, a in sorted((snap.get("approvals") or {}).items()):
        ref = None if a.get("answer") else _merge_ref(a.get("summary"))
        if ref and states.get(ref) in ("MERGED", "CLOSED"):
            out.append((rid, ref, states[ref]))
    return out


# ---------- CLI and board ----------

def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return 0


def cmd_nearmiss(a):
    if a.sub == "triage":
        return _emit(triage())
    if a.outcome not in OUTCOMES:
        return _emit({"error": f"--outcome must be one of {OUTCOMES}"}) or 2
    cl.append("nearmiss.resolve", pattern=a.pattern, outcome=a.outcome, ref=a.ref, note=a.note, by="foreman")
    return _emit({"resolved": a.pattern, "outcome": a.outcome})


def cmd_pr(a):
    snap = cl.load()
    if a.sub == "age":
        return _emit({"actions": actions(snap)} if a.dry_run else {"lines": pr_cycle()})
    if a.sub in ("drop", "undrop"):
        if bool(a.ref) == bool(a.repo):
            print("refused: give a PR ref or --repo, not both or neither", file=sys.stderr)
            return 2
        if a.repo and not REPO_PATTERN.match(a.repo):
            print(f"refused: --repo {a.repo!r} is not owner/repo or owner/*", file=sys.stderr)
            return 2
        ref = f"{a.repo}#*" if a.repo else a.ref
        if a.sub == "drop":
            cl.append("pr.dropped", ref=ref, reason=a.reason, by="foreman")
            return _emit({"dropped": ref, "reason": a.reason})
        if ref not in (snap.get("pr_dropped") or {}):
            cover = is_dropped(snap, ref)
            print(f"refused: {ref} is not a drop key" + (f"; it is hidden by {cover}: undrop that" if cover else ""),
                  file=sys.stderr)
            return 2
        cl.append("pr.undropped", ref=ref, by="foreman")
        return _emit({"undropped": ref})
    if a.unit not in snap["units"] or snap["units"][a.unit].get("status") == "done":
        print(f"refused: {a.unit} is not a live unit", file=sys.stderr)
        return 2
    cl.append("unit.keys", unit=a.unit, id=a.unit, pr=[a.ref])
    return _emit({"adopted": a.ref, "unit": a.unit})


def add_cli(sp):
    import factory  # `nearmiss` is factory's parser: add the lifecycle verbs to it
    n = factory.NEARMISS_PARSER
    tr = n.add_parser("triage", help="resolve known patterns, open a card per recurring unknown one, expire old")
    rs = n.add_parser("resolve", help="record an outcome for a pattern (`foundry nearmiss list --open`)")
    rs.add_argument("pattern"); rs.add_argument("--outcome", required=True, choices=OUTCOMES)
    rs.add_argument("--ref"); rs.add_argument("--note")
    for x in (tr, rs):
        x.set_defaults(func=cmd_nearmiss)
    s = sp.add_parser("pr", help="waiting-PR lifecycle: age (remind / owner card), adopt, drop, undrop").add_subparsers(
        dest="sub", required=True)
    ag = s.add_parser("age"); ag.add_argument("--dry-run", action="store_true")
    ad = s.add_parser("adopt"); ad.add_argument("ref"); ad.add_argument("--unit", required=True)
    dr = s.add_parser("drop", help="take a PR (or with --repo every PR of owner/repo or owner/*) off the board")
    dr.add_argument("ref", nargs="?"); dr.add_argument("--repo"); dr.add_argument("--reason", required=True)
    ud = s.add_parser("undrop", help="put a dropped PR (or --repo pattern) back on the board")
    ud.add_argument("ref", nargs="?"); ud.add_argument("--repo")
    for x in s.choices.values():
        x.set_defaults(func=cmd_pr)


def board(snap, m):
    op, done = split(snap)
    m["nearmiss_count"] = len(op)
    m["nearmiss_resolved"] = len(done)
    m["nearmiss_groups"] = groups(op)
    acts = actions(snap, inv=m.get("inventory") or None) if m.get("inventory") else {}
    for rows in (m.get("inventory") or {}).get("repos", {}).values():
        for r in rows:
            if r["kind"] == "pr":
                r["action"] = acts.get(r["ref"])
