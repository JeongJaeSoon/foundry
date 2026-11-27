"""Domain watchers for the foreman's streams and `watch.py poll` (sessions, worktrees, docker, PC pressure) and `foundry sweep`.

No model runs here. Pressure is recorded on change (warn lets one more launch through, danger none) and,
at danger, the safe classes are cleaned up first. New candidates that need a verdict become one
`housekeeping` event for the foreman, who spawns the sweeper judge (foundry-housekeeping skill).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import graph  # noqa: E402
import housekeep  # noqa: E402


def notify_mac(msg):
    cl.desktop_notify("foundry", msg)


def check_pressure(notify=False, cands=None):
    """Record the level when it changes; at danger, run the safe cleanup before anything else.

    One sampler and one vocabulary (ok|warn|danger, prefs `pressure` thresholds) with the foreman's
    pressure stream (watchers.cycle_pressure), so the two never flip-flop the recorded level."""
    import prefs
    import watchers
    s = watchers.sample()
    level, rs = watchers.band(s, prefs.load()["pressure"])
    if level != cl.pressure_level(cl.load()):
        cl.append("pressure", level=level, reasons=rs, sample=s)
        if notify and level == "danger":
            notify_mac("foundry: PC pressure danger, launches held: " + "; ".join(rs))
    cleaned = []
    if level == "danger":
        cands = housekeep.scan() if cands is None else cands
        cleaned = housekeep.safe_cleanup(cands)
    return {"level": level, "reasons": rs, "cleanup": cleaned}


def needs_verdict(c, snap):
    """Worth waking the judge for: actionable, and not judged since it was last acted on."""
    if c["cls"] == "subagent" or not (c.get("safe") or c.get("suggest") in ("safe", "propose")):
        return False  # finished subagents get a scripted verdict (scripted_verdicts), never a judge
    hk = snap["housekeeping"]
    plans = [p["ts"] for p in hk["plans"] if p["hid"] == c["hid"]]
    done = [d["ts"] for d in hk["done"] if d["hid"] == c["hid"]]
    return not plans or (bool(done) and max(done) >= max(plans))


AGENT_PREFIXES = ("lead-", "boss-")


def _started(e):
    v = e.get("startedAt")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v / 1000 if v > 1e11 else v
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


RM_RETRY = 24 * 3600  # seconds before a failed `claude rm` of one row is tried again


def _op_rm_failed(s, r, ts):
    s.setdefault("agent_rm_failed", {})[r["id"]] = {"name": r.get("name"), "error": r.get("error"), "ts": ts}


def _op_pruned(s, r, ts):
    for rid in r.get("ids") or []:
        (s.get("agent_rm_failed") or {}).pop(rid, None)


OPS = {"session.rm_failed": _op_rm_failed, "session.pruned": _op_pruned}
STATE = {"agent_rm_failed": {}}


def board(snap, m):
    """`claude rm` failures backing off: shown only while there are some."""
    t = cl.now()
    m["agent_rm_failed"] = sum(1 for x in (snap.get("agent_rm_failed") or {}).values()
                               if (when := cl.parse_ts(x.get("ts"))) and (t - when).total_seconds() < RM_RETRY)


def _finished(e):
    import launcher
    return launcher.classify(e)[0] in ("done", "stopped") or str(e.get("status") or e.get("state") or "").lower() == "failed"


def _rid(e):
    return str(e.get("id") or e.get("bgId") or e.get("shortId") or "")


def _lead_rows(snap, entries):
    """[(entry, name, unit id, reason)] for finished lead/boss rows foundry may remove: the name is a session foundry
    recorded, and its unit is done or cancelled, or a newer row of the name exists and it is not the recorded one
    (an open unit keeps its newest row and the one foundry resumes)."""
    import launcher
    by_name = {}
    for e in entries:
        if str(e.get("name") or "").startswith(AGENT_PREFIXES) and e["name"] in snap["sessions"]:
            by_name.setdefault(e["name"], []).append(e)
    out = []
    for name, rows in sorted(by_name.items()):
        sess = snap["sessions"][name]
        u = snap["units"].get(sess.get("unit") or "") or {}
        closed = u.get("status") in graph.CLOSED
        newest = max((_started(e) or 0) for e in rows)
        for e in rows:
            if not _finished(e):
                continue
            mine = _rid(e) == str(sess.get("bg_id") or "") or sess.get("session_id") in launcher.entry_ids(e)
            if closed:
                out.append((e, name, sess.get("unit"), f"unit {u.get('status')}"))
            elif (_started(e) or 0) < newest and not mine:
                out.append((e, name, sess.get("unit"), "superseded by a newer session of the unit"))
    return out


def _foreman_rows(snap, entries):
    """[(entry, name, None, reason)] for foreman rows foundry started that are not the current foreman: finished, or
    stopped by `foundry foreman prune` (or found gone) and still listed with no live process."""
    import launcher
    cur = (snap.get("foreman_current") or {}).get("session")
    if not cur:
        return []
    ours = set(snap.get("foreman_sessions") or {}) | {
        sid for sid, x in (snap.get("foundry_sessions") or {}).items() if x.get("role") == "foreman" and not x.get("unit")}
    stopped = set(snap.get("foreman_stopped") or {}) | set(snap.get("foreman_gone") or {}) | {
        x.get("session") for x in ((snap.get("foreman_prune") or {}).get("stopped") or [])}
    out = []
    for e in entries:
        sid = e.get("sessionId")
        if e.get("name") != launcher.FOREMAN_NAME or not sid or sid == cur or sid not in ours:
            continue
        if _finished(e):
            out.append((e, e["name"], None, "foreman session finished"))
        elif sid in stopped and (e.get("pid") in (None, "") or not launcher.pid_alive(e["pid"])):
            out.append((e, e["name"], None, "foreman session stopped, still listed"))
    return out


def prune_agent_sessions(dry_run=False, now=None):
    """`claude rm` (plain: never --discard-unpushed or --force-remove-worktree) the finished foundry rows that would
    otherwise pile up in `claude agents`: lead/boss rows of a done or cancelled unit and older duplicates of an open
    unit's (_lead_rows), and stopped foreman rows that are not the current one (_foreman_rows). Never a row of another
    name, a working or waiting row with a live process, one with a worktree of Claude's own (rm would delete it), or
    one started less than prefs `sweep.agent_session_ttl_minutes` ago. A failed listing removes nothing. Each removal
    is journaled as `session.pruned`; a failed rm once as `session.rm_failed`, and not tried again for RM_RETRY."""
    import launcher
    import prefs
    ttl = float(((prefs.load().get("sweep") or {}).get("agent_session_ttl_minutes", 30))) * 60
    now = cl.now().timestamp() if now is None else now
    entries = launcher.list_agents(all_=True)
    if entries is None:
        return {"error": "claude agents --json --all failed; nothing removed"}
    snap = cl.load()
    failed = snap.get("agent_rm_failed") or {}
    out, backing = [], []
    for e, name, unit, reason in _lead_rows(snap, entries) + _foreman_rows(snap, entries):
        rid, t = _rid(e), _started(e)
        if not rid or t is None or now - t < ttl or e.get("worktree") or e.get("worktreePath"):
            continue
        last = cl.parse_ts((failed.get(rid) or {}).get("ts"))
        if last and now - last.timestamp() < RM_RETRY:
            backing.append(rid)
            continue
        rec = {"name": name, "unit": unit, "id": rid, "reason": reason}
        if not dry_run:
            rc, out_, err = launcher.run([*launcher.claude_bin(), "rm", rid])
            if rc:
                rec["error"] = (err or out_ or "").strip()[-200:] or f"exit {rc}"
                cl.append("session.rm_failed", id=rid, name=name, error=rec["error"])
            else:
                cl.append("session.pruned", name=name, ids=[rid], unit=unit, reason=reason)
        out.append(rec)
    res = {"removed" if not dry_run else "would_remove": out}
    if backing:
        res["backing_off"] = backing
    return res


def announce(cands):
    """One event per newly seen set of candidates needing a verdict; the same set is not announced twice. Finished
    subagents with nothing attached get their scripted verdict first, so they never wake the judge."""
    housekeep.scripted_verdicts()
    try:  # a finished unit's worktree goes in the same pass; the rest stay with a one-line reason on the board
        gone = {os.path.realpath(r["path"]) for r in housekeep.reclaim(cands).values() if "result" in r}
        cands = [c for c in cands if not (c["cls"] == "worktree" and os.path.realpath(c["target"]) in gone)]
    except Exception as ex:
        cl.append_log("watch.log", {"stream": "sessions", "error": f"worktree reclaim: {ex}"})
    try:
        prune_agent_sessions()
    except Exception as ex:
        cl.append_log("watch.log", {"stream": "sessions", "error": f"agent session prune: {ex}"})
    try:
        import aging
        aging.triage()
    except Exception as ex:
        cl.append_log("watch.log", {"stream": "sessions", "error": f"nearmiss triage: {ex}"})
    snap = cl.load()
    wake = sorted(c["hid"] for c in cands if needs_verdict(c, snap))
    seen_path = cl.path("sweep-announced.json")
    try:
        with open(seen_path) as f:
            seen = set(json.load(f))
    except (OSError, ValueError):
        seen = set()
    cl.write_atomic(seen_path, json.dumps(wake))
    new = [h for h in wake if h not in seen]
    if not new:
        return None
    ev = {"eid": cl.new_id("ev", "_foreman", "housekeeping", *new), "unit": "_foreman", "wid": None,
          "kind": "housekeeping", "ref": None,
          "summary": f"{len(wake)} housekeeping candidate(s) need a verdict: run the foundry-housekeeping skill"}
    cl.append("event", **ev)
    return ev


def cycle(notify=False):
    cands = housekeep.scan()
    pr = check_pressure(notify, cands)
    removed = {r["hid"] for r in pr["cleanup"] if r["result"] == "removed"}
    ev = announce([c for c in cands if c["hid"] not in removed])
    return pr | {"event": ev}


def cmd_sweep(a):
    if a.sub == "scan":
        out = housekeep.scan()
    elif a.sub == "run":
        out = housekeep.execute(a.dry_run)
        out["agent_sessions"] = prune_agent_sessions(a.dry_run)
    elif a.sub == "agents":
        out = prune_agent_sessions(a.dry_run)
    elif a.sub == "remove-worktree":
        res = housekeep.remove_worktree(os.path.abspath(os.path.expanduser(a.path)), a.by, a.reason)
        if res == "removed":
            cl.append("hk.done", hid=f"worktree:{a.path}", cls="worktree", target=a.path,
                      result=f"removed ({a.by}: {a.reason})")
        out = {"path": a.path, "result": res}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if res == "removed" else 1
    else:
        out = check_pressure(a.notify)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def add_cli(sp):
    s = sp.add_parser("sweep", help="housekeeping watchers: scan, run the executor, check PC pressure")
    sub = s.add_subparsers(dest="sub", required=True)
    sub.add_parser("scan").set_defaults(func=cmd_sweep)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_sweep)
    g = sub.add_parser("agents", help="claude rm finished foundry rows: lead/boss rows of closed units or "
                                         "superseded sessions, stopped non-current foreman rows")
    g.add_argument("--dry-run", action="store_true")
    g.set_defaults(func=cmd_sweep)
    w = sub.add_parser("remove-worktree", help="remove one git worktree (an owner-approved proposal): refused "
                                               "while a process uses it as its cwd, never --force")
    w.add_argument("path")
    w.add_argument("--by", required=True, choices=["owner", "foreman"])
    w.add_argument("--reason", required=True)
    w.set_defaults(func=cmd_sweep)
    p = sub.add_parser("pressure")
    p.add_argument("--notify", action="store_true")
    p.set_defaults(func=cmd_sweep)
