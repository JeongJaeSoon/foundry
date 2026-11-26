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


def prune_agent_sessions(dry_run=False, now=None):
    """`claude rm` finished lead/boss rows that would otherwise pile up in `claude agents`.

    A row goes only when all hold: its name is a foundry session we recorded and its cwd is that session's;
    state done or stopped (never working or awaiting input); no worktree of Claude's own (rm would delete it);
    started more than prefs `sweep.agent_session_ttl_minutes` ago; and its unit is done or cancelled, or a newer
    row of the same name exists and it is not the recorded session. So an open unit keeps its newest row and the
    one foundry resumes. A failed listing removes nothing. Each removal is journaled as `session.pruned`."""
    import launcher
    import prefs
    ttl = float(((prefs.load().get("sweep") or {}).get("agent_session_ttl_minutes", 30))) * 60
    now = cl.now().timestamp() if now is None else now
    entries = launcher.list_agents(all_=True)
    if entries is None:
        return {"error": "claude agents --json --all failed; nothing removed"}
    snap = cl.load()
    by_name = {}
    for e in entries:
        if str(e.get("name") or "").startswith(AGENT_PREFIXES):
            by_name.setdefault(e["name"], []).append(e)
    out = []
    for name, rows in sorted(by_name.items()):
        sess = snap["sessions"].get(name)
        if not sess:
            continue
        u = snap["units"].get(sess.get("unit") or "") or {}
        closed = u.get("status") in ("done", "cancelled")
        where = {os.path.realpath(x) for x in (sess.get("cwd"), (u.get("worktree") or {}).get("path")) if x}
        newest = max((_started(e) or 0) for e in rows)
        for e in rows:
            rid = str(e.get("id") or e.get("bgId") or e.get("shortId") or "")
            t = _started(e)
            if not rid or t is None or now - t < ttl:
                continue
            if launcher.classify(e)[0] not in ("done", "stopped") or e.get("worktree") or e.get("worktreePath"):
                continue
            if os.path.realpath(e.get("cwd") or "/nonexistent") not in where:
                continue
            superseded = t < newest and rid != str(sess.get("bg_id") or "") and sess.get("session_id") not in launcher.entry_ids(e)
            if not (closed or superseded):
                continue
            reason = f"unit {u.get('status')}" if closed else "superseded by a newer session of the unit"
            rec = {"name": name, "unit": sess.get("unit"), "id": rid, "reason": reason}
            if not dry_run:
                rc, _, err = launcher.run([*launcher.claude_bin(), "rm", rid])
                if rc:
                    rec["error"] = (err or "").strip()[-200:] or f"exit {rc}"
                else:
                    cl.append("session.pruned", name=name, ids=[rid], unit=sess.get("unit"), reason=reason)
            out.append(rec)
    return {"removed" if not dry_run else "would_remove": out}


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
    g = sub.add_parser("agents", help="claude rm finished lead/boss rows of done units or superseded sessions")
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
