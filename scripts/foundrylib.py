"""Foundry state: an append-only JSONL journal plus a derived snapshot.

Only scripts write state. Every mutation is one journal line, appended under an
exclusive flock; the snapshot is a pure function of the journal (`replay`), so a
crash between the append and the snapshot write loses nothing: the next reader
rebuilds it.
"""

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import re
import subprocess
import sys
import time

JST = dt.timezone(dt.timedelta(hours=9))  # kept for callers and tests that name it; the clock follows zone()
_ZONE = {}
STATUSES = ("queue", "in_progress", "waiting_others", "waiting_owner", "done", "parked", "blocked", "cancelled")
LEAD_TYPES = ("foundry:line-lead", "foundry:shop-boss")
SESSION_STATES = ("launching", "running", "idle", "done", "blocked", "stopped", "lost")


def state_dir():
    d = os.environ.get("FOUNDRY_STATE_DIR") or os.path.expanduser("~/.local/state/foundry")
    os.makedirs(d, exist_ok=True)
    return d


def path(name):
    return os.path.join(state_dir(), name)


def desktop_notify(title, text):
    """A desktop notification: FOUNDRY_NOTIFY_CMD, else macOS osascript or Linux notify-send; silent elsewhere."""
    import json as _json
    import shlex
    cmd = os.environ.get("FOUNDRY_NOTIFY_CMD")
    if cmd:
        argv = shlex.split(cmd) + [text]
    elif sys.platform == "darwin":
        argv = ["osascript", "-e", f"display notification {_json.dumps(text)} with title {_json.dumps(title)}"]
    elif shutil.which("notify-send"):
        argv = ["notify-send", title, text]
    else:
        return
    try:
        subprocess.run(argv, timeout=5, capture_output=True)
    except Exception:
        pass


def zone():
    """prefs `timezone` as a tzinfo, read once per prefs file version: the clock, naive times and every display."""
    p = path("prefs.json")
    try:
        stamp = (p, os.stat(p).st_mtime_ns)
    except OSError:
        stamp = (p, None)
    if stamp not in _ZONE:
        import prefs  # prefs imports this module: load it only when the zone is first needed
        _ZONE.clear()
        _ZONE[stamp] = prefs.tz(prefs.load())
    return _ZONE[stamp]


def now():
    override = os.environ.get("FOUNDRY_NOW")
    if override:
        return parse_ts(override)
    return dt.datetime.now(zone())


def parse_ts(text):
    """An aware datetime (a naive one is read in prefs `timezone`), or None when the text is not a timestamp."""
    try:
        t = dt.datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=zone())


def iso(t=None):
    return (t or now()).isoformat(timespec="seconds")


@contextlib.contextmanager
def locked():
    with open(path(".lock"), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_journal():
    p = path("journal.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn last line from a crash mid-append: skip it, keep the rest.
                continue
    return out


def write_atomic(p, text):
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, p)


_write_atomic = write_atomic


def _commit(records):
    """Append records and refresh the snapshot. Caller holds the lock."""
    j = path("journal.jsonl")
    with open(j, "ab+") as f:
        # A torn last line from a crash would otherwise swallow the first record written here.
        if f.seek(0, os.SEEK_END) > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
        for op, fields in records:
            f.write((json.dumps({"ts": iso(), "op": op, **fields}, ensure_ascii=False) + "\n").encode())
        f.flush()
        os.fsync(f.fileno())
    snap = replay(read_journal())
    if _needs_compaction(j):
        snap = _compact_locked(snap)
    else:
        write_atomic(path("snapshot.json"), json.dumps(snap, ensure_ascii=False, indent=1))
    return snap


def append(op, **fields):
    """Append one event and refresh the snapshot. Returns the new snapshot."""
    with locked():
        return _commit([(op, fields)])


def append_many(records):
    with locked():
        return _commit(records)


def transact(decide):
    """Check and record in one critical section: decide(snapshot) -> (result, records)."""
    with locked():
        result, records = decide(load())
        snap = _commit(records) if records else None
        return result, snap


def append_log(name, rec):
    """A JSONL side log, rotated to <name>.1 once it passes FOUNDRY_LOG_MAX_BYTES (1 MB)."""
    p = path(name)
    limit = int(os.environ.get("FOUNDRY_LOG_MAX_BYTES") or 1_000_000)
    try:
        if os.path.getsize(p) >= limit:
            os.replace(p, p + ".1")
    except OSError:
        pass
    with open(p, "a") as f:
        f.write(json.dumps({"ts": iso(), **rec}, ensure_ascii=False) + "\n")


# ---------- compaction and retention ----------

def _needs_compaction(j):
    limit = int(os.environ.get("FOUNDRY_COMPACT_BYTES") or 4_000_000)
    try:
        size = os.path.getsize(j)
        if size <= limit:
            return False
        with open(j, "rb") as f:
            head = len(f.readline())
        return size > 2 * head  # not just one big snapshot line: compacting again would not shrink it
    except OSError:
        return False


def send_window(rec, cfg):
    """How long a recorded send blocks an identical one."""
    state = rec.get("state")
    if state == "pending":
        return dt.timedelta(minutes=5)
    if state == "unknown":
        return dt.timedelta(minutes=cfg["send_unknown_ttl_minutes"])
    if state == "sent":
        if rec.get("kind") == "review-ask-rule":
            return dt.timedelta(hours=cfg["review_ask_ttl_hours"])
        ttl = cfg["send_ttl_hours"]
        return dt.timedelta(hours=ttl.get(rec.get("kind"), ttl.get("default", 24)))
    return dt.timedelta(0)


def prune(s, t=None, cfg=None):
    """Drop what no rule reads any more: expired sends, old consumed events, old housekeeping and agents."""
    t = t or now()
    cfg = cfg or load_config()
    keep = cfg["retention_days"]

    def older(ts, days):
        when = parse_ts(ts)
        return when is not None and t - when > dt.timedelta(days=days)

    def expired_send(r):  # a scheduled send counts from its post_at, as the review-ask rule judges it
        when = parse_ts(r.get("ts"))
        post = parse_ts(r.get("post_at")) if r.get("state") == "sent" else None
        when = max(when, post) if when and post else when
        return when is None or t - when > send_window(r, cfg)

    s["sends"] = {k: v for k, v in s["sends"].items() if not expired_send(v)}
    s["send_overrides"] = [o for o in s["send_overrides"] if (parse_ts(o.get("until")) or t) > t]
    s["events"] = [e for e in s["events"] if not (e["consumed"] and older(e["consumed"], keep["events"]))]
    hk = s["housekeeping"]
    for k in ("plans", "proposals", "done"):
        hk[k] = [x for x in hk[k] if not older(x.get("ts"), keep["housekeeping"])]
    current = {u.get("agent") for u in s["units"].values()}
    s["agents"] = {k: a for k, a in s["agents"].items()
                   if a["status"] == "running" or k in current or not older(a.get("stopped") or a.get("started"), keep["agents"])}
    s["pending_spawns"] = [p for p in s["pending_spawns"] if not p.get("bound")]
    return s


def _compact_locked(snap):
    """Rewrite the journal as one `snapshot` record of the pruned state. Caller holds the lock."""
    snap = prune(json.loads(json.dumps(snap)))
    j = path("journal.jsonl")
    if os.path.exists(j):
        shutil.copy2(j, j + ".1")  # one generation kept for inspection; the snapshot line carries everything
    write_atomic(j, json.dumps({"ts": iso(), "op": "snapshot", "state": snap}, ensure_ascii=False) + "\n")
    write_atomic(path("snapshot.json"), json.dumps(snap, ensure_ascii=False, indent=1))
    return snap


def compact():
    with locked():
        return _compact_locked(replay(read_journal()))


def load():
    """The snapshot, rebuilt from the journal when the cached file is older than it."""
    j, s = path("journal.jsonl"), path("snapshot.json")
    try:
        if os.path.getmtime(s) >= os.path.getmtime(j):
            with open(s) as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    return replay(read_journal())


def rebuild():
    with locked():
        snap = replay(read_journal())
        _write_atomic(path("snapshot.json"), json.dumps(snap, ensure_ascii=False, indent=1))
        return snap


# Feature modules, each optional, each in its own file. A module may define:
#   OPS          {op: reducer(s, r, ts)}       journal ops it owns
#   STATE        {key: default}                top-level snapshot keys it owns
#   UNIT_FIELDS  {key: default}                per-unit fields it owns
#   add_cli(sp), add_unit_cli(unit_subparsers) CLI
#   pre_tool_use(inp, snap, ctx) -> None | str (deny reason) | ("warn", text)
#   post_tool_use(inp, snap, ctx)
#   board(snap, model)                         adds its sections to the board model
# ctx = {"caller": role, "unit": unit id or None, "tool": name, "input": tool_input}
MODULES = ("launcher", "approve", "sweep", "prefs", "graph", "decide", "gitpolicy", "intake", "triage",
           "sync", "factory", "watchers", "release", "review", "hands", "outbox", "glyphs", "aging", "report", "firstrun", "mcpmap")
_MODS = None


def modules():
    """The feature modules that exist, imported once. A missing file is skipped; a broken one raises."""
    global _MODS
    if _MODS is None:
        import importlib
        import importlib.util
        found = []
        for name in MODULES:
            if importlib.util.find_spec(name) is None:
                continue
            found.append(importlib.import_module(name))
        _MODS = found
    return _MODS


def _ext_ops(op):
    """Every module reducer for op, in MODULES order: a module may observe another module's op."""
    return [m.OPS[op] for m in modules() if op in getattr(m, "OPS", {})]


def empty():
    s = _empty_core()
    for m in modules():
        for k, v in getattr(m, "STATE", {}).items():
            s.setdefault(k, json.loads(json.dumps(v)))
    return s


def _empty_core():
    return {
        "units": {},
        "agents": {},
        "pending_spawns": [],
        "decisions": {},
        "sends": {},
        "send_overrides": [],
        "events": [],
        "foundry_sessions": {},
        "housekeeping": {"plans": [], "proposals": [], "done": []},
        # Lead sessions by name (`lead-<unit>` / `boss-<unit>`): one live session per unit.
        "sessions": {},
        # PermissionRequest records by request id, and remembered answers by request key (tier 1).
        "approvals": {},
        "approval_ledger": {},
        "pressure": {"level": "ok", "reasons": [], "ts": None},
        # .claude.json trust keys foundry added (trust.py), by path.
        "trust": {},
    }


def _unit(s, uid):
    return s["units"].setdefault(uid, {
        "id": uid, "kind": "project", "title": uid, "repo": None, "lead": None,
        "status": "queue", "keys": {"ticket": [], "pr": [], "topic": []},
        "waits": [], "inbox": [], "todos": {}, "notes": [], "agent": None,
        "session": None, "worktree": None, "tracker": None, "held": None,
        "created": None, "updated": None,
        "handoff": {"goal": None, "next": None, "files": [], "ts": None, "snapshots": 0, "last_trigger": None},
        **{k: json.loads(json.dumps(v)) for m in modules() for k, v in getattr(m, "UNIT_FIELDS", {}).items()},
    })


REPLAY_ERRORS_MAX = 50


def _record_replay_errors(errors):
    """Keep the last REPLAY_ERRORS_MAX skipped records in replay.errors, one entry per distinct record."""
    p = path("replay.errors")
    try:
        with open(p) as f:
            seen = json.load(f)
        if not isinstance(seen, dict):
            seen = {}
    except (OSError, ValueError):
        seen = {}
    new = {k: v for k, v in errors.items() if k not in seen}
    if not new:
        return
    seen.update(new)
    keep = dict(list(seen.items())[-REPLAY_ERRORS_MAX:])
    try:
        write_atomic(p, json.dumps(keep, ensure_ascii=False, indent=1))
    except OSError:
        pass


def replay(journal):
    """The snapshot from journal records. A record a reducer cannot apply is skipped and logged, never fatal."""
    s = empty()
    errors = {}
    for r in journal:
        try:
            s = _apply(s, r)
        except Exception as ex:  # one bad record must not brick every reader
            line = json.dumps(r, ensure_ascii=False, sort_keys=True, default=str)
            sig = hashlib.sha1(line.encode()).hexdigest()[:12]
            errors[sig] = {"ts": r.get("ts") if isinstance(r, dict) else None,
                           "op": r.get("op") if isinstance(r, dict) else None,
                           "error": f"{type(ex).__name__}: {ex}"[:300], "record": line[:500]}
    if errors:
        _record_replay_errors(errors)
    s["pending_spawns"] = s["pending_spawns"][-200:]
    return s


def _apply(s, r):
    """Apply one journal record to s; returns s (a `snapshot` record replaces it)."""
    op = r.get("op")
    ts = r.get("ts")
    if op == "snapshot":
        if not isinstance(r.get("state"), dict):
            raise ValueError("snapshot record without a state object")
        s = empty() | json.loads(json.dumps(r["state"]))
    elif op == "unit.new":
        u = _unit(s, r["id"])
        for k in ("kind", "title", "repo", "lead"):
            if r.get(k) is not None:
                u[k] = r[k]
        for k in ("ticket", "pr", "topic"):
            for v in r.get(k) or []:
                if v not in u["keys"][k]:
                    u["keys"][k].append(v)
        if r.get("parent") and r["parent"] in s["units"] and r["parent"] != r["id"]:
            u["parent"] = r["parent"]
            kids = s["units"][r["parent"]].setdefault("children", [])
            if r["id"] not in kids:
                kids.append(r["id"])
        u["created"] = u["created"] or ts
        u["updated"] = ts
    elif op == "unit.keys":
        u = _unit(s, r["id"])
        for k in ("ticket", "pr", "topic"):
            for v in r.get(k) or []:
                if v not in u["keys"][k]:
                    u["keys"][k].append(v)
        u["updated"] = ts
    elif op == "unit.status":
        u = _unit(s, r["id"])
        u["status"] = r["status"]
        if r.get("note"):
            u["notes"].append({"ts": ts, "text": r["note"]})
        u["updated"] = ts
    elif op == "unit.checkpoint":
        u = _unit(s, r["id"])
        u["notes"].append({"ts": ts, "text": r["note"]})
        u["updated"] = ts
    elif op == "wait.add":
        u = _unit(s, r["id"])
        u["waits"] = [w for w in u["waits"] if w["wid"] != r["wid"]]
        u["waits"].append({k: r.get(k) for k in ("wid", "kind", "ref", "until", "note", "fingerprint")} | {"since": ts})
        if u["status"] not in ("waiting_owner", "done"):
            u["status"] = "waiting_others"
        u["updated"] = ts
    elif op == "wait.clear":
        u = _unit(s, r["id"])
        u["waits"] = [w for w in u["waits"] if r.get("wid") and w["wid"] != r["wid"]]
        u["updated"] = ts
    elif op == "wait.seen":
        u = _unit(s, r["id"])
        for w in u["waits"]:
            if w["wid"] == r["wid"]:
                w["fingerprint"] = r["fingerprint"]
    elif op == "inbox.add":
        u = _unit(s, r["id"])
        u["inbox"].append({"iid": r["iid"], "ts": ts, "text": r["text"], "from": r.get("from", "owner"), "delivered": None})
        u["updated"] = ts
    elif op == "inbox.delivered":
        u = _unit(s, r["id"])
        for i in u["inbox"]:
            if i["iid"] in r["iids"]:
                i["delivered"] = ts
    elif op == "inbox.supersede":  # a stale instruction: never injected again, never shown as 받은 지시
        u = _unit(s, r["id"])
        for i in u["inbox"]:
            if i["iid"] in r["iids"]:
                i["superseded"] = {"ts": ts, "by": r.get("by"), "reason": r.get("reason")}
                i["delivered"] = i.get("delivered") or ts
    elif op == "decision.ask":
        d = s["decisions"].setdefault(r["key"], {"key": r["key"], "answer": None, "answered": None})
        d.update({"unit": r.get("unit"), "question": r["question"], "asked": d.get("asked") or ts})
        if r.get("refs"):
            d["refs"] = sorted(set(d.get("refs") or []) | set(r["refs"]))
        if r.get("unit"):
            _unit(s, r["unit"])["status"] = "waiting_owner"
    elif op == "decision.answer":
        d = s["decisions"].setdefault(r["key"], {"key": r["key"], "question": None, "asked": None, "unit": r.get("unit")})
        d.update({"answer": r["answer"], "answered": ts})
        d.update({k: r[k] for k in ("by", "quote") if r.get(k)})
        if r.get("withdrawn"):
            d["withdrawn"] = r["withdrawn"]
        if r.get("moot"):
            d["moot"] = r["moot"]
        uid = d.get("unit")
        if uid and uid in s["units"]:
            u = s["units"][uid]
            still = [k for k, v in s["decisions"].items() if v.get("unit") == uid and v.get("answer") is None]
            if not still and u["status"] == "waiting_owner":
                u["status"] = "waiting_others" if u["waits"] else "in_progress"
    elif op == "send.record":
        s["sends"][r["key"]] = {k: r.get(k) for k in ("key", "state", "channel", "thread", "kind", "tool", "post_at")} | {"ts": ts}
    elif op == "send.override":
        s["send_overrides"].append({"channel": r.get("channel"), "until": r["until"], "reason": r.get("reason")})
    elif op == "agent.spawn":
        s["pending_spawns"].append({k: r.get(k) for k in ("session", "caller", "caller_type", "target_type", "unit", "tool_use_id",
                                                          "name", "asked")} | {"ts": ts})
    elif op == "agent.resume" or (op == "agent.start" and r["agent_id"] in s["agents"]):
        a = s["agents"].get(r["agent_id"])
        if a:
            a.update({"status": "running", "stopped": None, "resumed": ts, "resumes": (a.get("resumes") or 0) + 1})
    elif op == "agent.start":
        parent, unit, typ, name, asked = None, None, r["type"], None, None
        # Oldest unbound spawn first: starts follow their PreToolUse calls in order. A named spawn starts
        # with agent_type = its name; it keeps the foundry type it was spawned as.
        for p in s["pending_spawns"]:
            if p["session"] == r.get("session") and r["type"] in (p["target_type"], p.get("name")) and not p.get("bound"):
                p["bound"] = r["agent_id"]
                parent, unit, typ, name, asked = p["caller"], p["unit"], p["target_type"], p.get("name"), p.get("asked")
                break
        s["agents"][r["agent_id"]] = {"agent_id": r["agent_id"], "type": typ, "name": name, "asked": asked,
                                      "session": r.get("session"), "parent": parent, "unit": unit, "status": "running",
                                      "started": ts, "stopped": None}
        if unit and unit in s["units"]:
            u = s["units"][unit]
            u["agent"] = r["agent_id"]
            if u["status"] == "queue":
                u["status"] = "in_progress"
    elif op == "agent.stop":
        a = s["agents"].get(r["agent_id"])
        if a:
            a["status"] = "stopped"
            a["stopped"] = ts
    elif op == "agent.lost":
        for aid in r["agent_ids"]:
            a = s["agents"].get(aid)
            if not a or a["status"] != "running":
                continue
            a["status"] = "lost"
            # Whoever dispatched it learns what is undone; the lost agent's result never arrives. The event
            # wakes the foreman's waiter; the unit's inbox reaches its lead on the next start.
            uid = a.get("unit") if a.get("unit") in s["units"] else "_foreman"
            text = (f"Subagent {a.get('name') or a.get('type')} ({aid}) was lost before it reported. "
                    f"It was asked: {a.get('asked') or '(the spawn recorded no description)'}. "
                    "Re-dispatch it if the work is still needed.")
            s["events"].append({"eid": f"lost-{aid}", "unit": uid, "wid": None, "kind": "agent.lost", "ref": aid,
                                "summary": text, "ts": ts, "consumed": None})
            if uid != "_foreman":
                u = s["units"][uid]
                u["inbox"].append({"iid": f"lost-{aid}", "ts": ts, "text": text, "from": "foundry", "delivered": None})
                u["updated"] = ts
    elif op == "todo":
        uid = r.get("unit") or "_foreman"
        u = _unit(s, uid)
        if uid == "_foreman":
            u["kind"] = "foreman"
            u["status"] = u["status"] if u["status"] != "queue" else "in_progress"
        key = f'{r.get("agent") or "main"}:{r["task_id"]}'
        t = u["todos"].setdefault(key, {"subject": None, "status": "pending"})
        for k in ("subject", "status"):
            if r.get(k):
                t[k] = r[k]
        if t["status"] == "deleted":
            u["todos"].pop(key, None)
    elif op == "todo.replace":
        uid = r.get("unit") or "_foreman"
        u = _unit(s, uid)
        agent = r.get("agent") or "main"
        u["todos"] = {k: v for k, v in u["todos"].items() if not k.startswith(agent + ":")}
        for i, t in enumerate(r["todos"]):
            u["todos"][f"{agent}:w{i}"] = {"subject": t.get("content"), "status": t.get("status")}
    elif op == "event":
        s["events"].append({k: r.get(k) for k in ("eid", "unit", "wid", "kind", "ref", "summary")} | {"ts": ts, "consumed": None})
    elif op == "event.consumed":
        for e in s["events"]:
            if e["eid"] in r["eids"]:
                e["consumed"] = ts
    elif op == "session.foundry":
        prev = s["foundry_sessions"].get(r["session"], {})
        s["foundry_sessions"][r["session"]] = {"started": prev.get("started", ts), "last_start": ts, "source": r.get("source"),
                                               "role": r.get("role") or prev.get("role"),
                                               "unit": r.get("unit") or prev.get("unit"),
                                               "procs": ([r["proc"]] if r.get("proc") else []) + [
                                                   p for p in prev.get("procs") or [] if p != r.get("proc")][:3],
                                            "compactions": prev.get("compactions", 0)}
    elif op == "session.compact":
        sess = s["foundry_sessions"].setdefault(r["session"], {"started": ts, "source": None, "compactions": 0})
        sess["compactions"] = sess.get("compactions", 0) + 1
    elif op == "session.cleared":
        if r["session"] in s["foundry_sessions"]:
            s["foundry_sessions"][r["session"]]["compactions"] = 0
    elif op == "handoff.write":
        u = _unit(s, r["id"])
        if r["id"] == "_foreman":
            u["kind"] = "foreman"
        h = u["handoff"]
        for k in ("goal", "next"):
            if r.get(k):
                h[k] = r[k]
        for f_ in r.get("files") or []:
            if f_ not in h["files"]:
                h["files"].append(f_)
        h["ts"] = ts
        if r.get("summary"):
            u["notes"].append({"ts": ts, "text": r["summary"]})
        u["updated"] = ts
    elif op == "handoff.snapshot":
        u = _unit(s, r["id"])
        if r["id"] == "_foreman":
            u["kind"] = "foreman"
        u["handoff"]["snapshots"] += 1
        u["handoff"]["last_trigger"] = r.get("trigger")
    elif op == "hk.plan":
        hk = s["housekeeping"]
        hk["seq"] = hk.get("seq", 0) + 1
        hk["plans"].append({k: r.get(k) for k in ("hid", "cls", "target", "reason", "verdict", "in_use", "nowhere")}
                           | {"ts": ts, "seq": hk["seq"]})
    elif op == "hk.done":
        hk = s["housekeeping"]
        hk["seq"] = hk.get("seq", 0) + 1
        hk["done"].append({k: r.get(k) for k in ("hid", "cls", "target", "result")} | {"ts": ts, "seq": hk["seq"]})
    elif op in EXT_OPS:
        EXT_OPS[op](s, r, ts)
    else:
        for fn in _ext_ops(op):
            fn(s, r, ts)
    _mark_closed(s, r, ts)
    return s


def _mark_closed(s, r, ts):
    """`closed`: when the unit a record touched last became done/cancelled (the board's archive age); reopening it
    clears that and its archive mark."""
    for uid in {r.get("id"), r.get("unit")}:
        u = s["units"].get(uid) if isinstance(uid, str) else None
        if not u:
            continue
        if u.get("status") in ("done", "cancelled"):
            u["closed"] = u.get("closed") or ts
        elif u.get("closed") or u.get("archived"):
            u["closed"] = u["archived"] = None


# ---------- sessions, worktrees, approvals, pressure ----------

def _session(s, name):
    return s["sessions"].setdefault(name, {
        "name": name, "unit": None, "role": None, "bg_id": None, "session_id": None, "cwd": None,
        "model": None, "state": "launching", "launched": None, "updated": None, "stopped": None,
        "launches": 0, "note": None})


def _op_session_launch(s, r, ts):
    x = _session(s, r["name"])
    x.update({k: r.get(k) for k in ("unit", "role", "bg_id", "cwd", "model", "agents")})
    x.update({"state": "launching", "launched": ts, "updated": ts, "stopped": None, "note": r.get("note"),
              "session_id": r.get("session_id") or (x["session_id"] if r.get("resume") else None)})
    x["launches"] += 1
    if r.get("unit") in s["units"]:
        u = s["units"][r["unit"]]
        u["session"] = r["name"]
        u["held"] = None
        if u["status"] == "queue":
            u["status"] = "in_progress"


def _op_session_reserve(s, r, ts):
    x = _session(s, r["name"])
    x.update({"unit": r.get("unit"), "role": r.get("role"), "bg_id": None, "state": "launching",
              "updated": ts, "stopped": None, "note": "reserved"})


def _op_session_release(s, r, ts):
    """A reservation whose launch failed: forget it, or put a relaunched session back to stopped."""
    x = s["sessions"].get(r["name"])
    if not x or x.get("bg_id"):
        return
    if not x.get("launches"):
        del s["sessions"][r["name"]]
    else:
        x.update({"state": "stopped", "updated": ts, "note": r.get("note")})


def _op_session_bind(s, r, ts):
    x = _session(s, r["name"])
    for k in ("session_id", "unit", "role", "bg_id", "cwd"):
        if r.get(k):
            x[k] = r[k]
    if x["state"] == "launching":
        x["state"] = "running"
    x["updated"] = ts


def _op_session_state(s, r, ts):
    x = s["sessions"].get(r["name"])
    if x:
        x["state"] = r["state"]
        x["note"] = r.get("note") or x.get("note")
        x["updated"] = ts
        if r["state"] in ("stopped", "lost"):
            x["stopped"] = ts


def _op_unit_worktree(s, r, ts):
    u = _unit(s, r["id"])
    u["worktree"] = {"repo": r.get("repo"), "path": r.get("path"), "branch": r.get("branch")}
    if r.get("repo"):
        u["repo"] = r["repo"]
    u["updated"] = ts


def _op_unit_tracker(s, r, ts):
    u = _unit(s, r["id"])
    u["tracker"] = r.get("tracker")
    u["updated"] = ts


def _op_unit_report(s, r, ts):
    u = _unit(s, r["id"])
    u["notes"].append({"ts": ts, "text": "[report] " + r["text"]})
    u["updated"] = ts
    # The whole report rides on the event (`text`); `summary` is the short form lists print, with a pointer back.
    text = r["text"]
    short = text if len(text) <= 300 else text[:300].rstrip() + f"… (full report: `foundry unit show {r['id']}`, event {r['eid']})"
    s["events"].append({"eid": r["eid"], "unit": r["id"], "wid": None, "kind": "report", "ref": None,
                        "summary": short, "text": text, "ts": ts, "consumed": None})


def _op_launch_held(s, r, ts):
    u = _unit(s, r["id"])
    u["held"] = {"reason": r.get("reason"), "ts": ts}


def _op_approval_request(s, r, ts):
    s["approvals"][r["rid"]] = {k: r.get(k) for k in ("rid", "key", "session", "unit", "role", "tool", "summary", "tier",
                                                       "why", "wait_s")} | {
        "asked": ts, "answer": None, "by": None, "answered": None, "reason": None}


def _op_approval_answer(s, r, ts):
    a = s["approvals"].get(r["rid"])
    if a and a["answer"] is None:
        a.update({"answer": r["behavior"], "by": r.get("by"), "answered": ts, "reason": r.get("reason")})
        if r.get("quote"):
            a["quote"] = r["quote"]  # the owner's words the foreman acted on
        if r.get("remember") and a.get("key"):
            s["approval_ledger"][ledger_key(a.get("unit"), a["key"])] = {"behavior": r["behavior"], "by": r.get("by"), "ts": ts, "rid": r["rid"]}


def _op_approval_note(s, r, ts):
    a = s["approvals"].get(r["rid"])
    if a:
        for k in ("evidence", "advisor"):
            if r.get(k):
                a[k] = r[k]


def _op_approval_mark(s, r, ts):
    """`gave_up`: the hook timed out and denied (the request stays queued); `used`: a late answer was applied once;
    `stale`: a queued request that can never be valid again (with its reason): off the board, never applied late."""
    a = s["approvals"].get(r["rid"])
    if a and r.get("field") in ("gave_up", "used", "stale"):
        a[r["field"]] = ts
        if r["field"] == "stale":
            a["stale_reason"] = r.get("reason")


def _op_pressure(s, r, ts):
    s["pressure"] = {"level": r.get("level") or "ok", "reasons": r.get("reasons") or [], "ts": ts}


def _op_trust_register(s, r, ts):
    """Keys foundry added to .claude.json; kept in state so compaction never forgets one."""
    s.setdefault("trust", {})[r["path"]] = {"unit": r.get("unit"), "file": r.get("file"), "ts": ts}


def _op_trust_unregister(s, r, ts):
    s.setdefault("trust", {}).pop(r["path"], None)


EXT_OPS = {
    "session.launch": _op_session_launch,
    "session.bind": _op_session_bind,
    "session.reserve": _op_session_reserve,
    "session.release": _op_session_release,
    "session.state": _op_session_state,
    "unit.worktree": _op_unit_worktree,
    "unit.tracker": _op_unit_tracker,
    "unit.report": _op_unit_report,
    "launch.held": _op_launch_held,
    "approval.request": _op_approval_request,
    "approval.note": _op_approval_note,
    "approval.answer": _op_approval_answer,
    "approval.mark": _op_approval_mark,
    "pressure": _op_pressure,
    "trust.register": _op_trust_register,
    "trust.unregister": _op_trust_unregister,
}


def ledger_key(unit, key):
    """A remembered answer applies to the same request from the same unit only."""
    return f"{unit or '-'}:{key}"


LIVE_STATES = ("launching", "running", "idle")
RESERVATION_TTL = dt.timedelta(minutes=5)


def reservation_stale(sess, at=None):
    """A `launching` reservation with no background id older than RESERVATION_TTL: its launcher died."""
    if not sess or sess.get("state") != "launching" or sess.get("bg_id"):
        return False
    t = parse_ts(sess.get("updated"))
    return t is not None and (at or now()) - t > RESERVATION_TTL


def session_live(sess, at=None):
    return bool(sess) and sess.get("state") in LIVE_STATES and not reservation_stale(sess, at)


WAITING = ("waiting_others", "waiting_owner")


def holds_slot(snap, sess, at=None):
    """Whether a session counts against the lead cap: live, and not an idle lead whose unit is only waiting
    (review, CI, a decision). Those are parked by the sessions stream and must never starve the queue."""
    if not session_live(sess, at):
        return False
    u = (snap.get("units") or {}).get(sess.get("unit") or "")
    return not (sess.get("state") == "idle" and u and u.get("status") in WAITING)


def slot_holders(snap, at=None):
    return [n for n, x in (snap.get("sessions") or {}).items() if holds_slot(snap, x, at)]


def lead_live(snap, u):
    """Whether a unit's lead session is up (launching, working, or idle and resumable by message)."""
    return session_live(snap["sessions"].get(u.get("session") or ""))


# ---------- PC pressure: one vocabulary ----------

PRESSURE_LEVELS = ("ok", "warn", "danger")
WARN_LAUNCHES = 1


def pressure_level(snap):
    """ok | warn | danger; a legacy `high` record reads as danger."""
    level = (snap.get("pressure") or {}).get("level") or "ok"
    return level if level in PRESSURE_LEVELS else "danger"


def pressure_room(snap, at=None):
    """New launches PC pressure still allows: None (no limit) at ok, 0 at danger, and at warn
    WARN_LAUNCHES minus the sessions launched (or reserved) since the level turned warn."""
    level = pressure_level(snap)
    if level == "ok":
        return None
    if level == "danger":
        return 0
    since = parse_ts((snap.get("pressure") or {}).get("ts"))
    n = 0
    for x in (snap.get("sessions") or {}).values():
        t = parse_ts(x.get("launched") if x.get("bg_id") else x.get("updated"))
        if session_live(x, at) and t is not None and (since is None or t > since):
            n += 1
    return max(0, WARN_LAUNCHES - n)


# ---------- caller identity ----------

def _ps(pid, fields):
    try:
        r = subprocess.run(["ps", "-o", fields, "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip()


def claude_ancestors(limit=24):
    """[(pid, start time)] of every `claude` process above this one. A clean environment (`env -i`, a script
    that drops it) cannot shed these, so they identify the session that started a command."""
    fake = os.environ.get("FOUNDRY_TEST_ANCESTORS")  # tests run under a real claude; FOUNDRY_* cannot be set by a lead
    if fake is not None:
        return json.loads(fake)
    out, pid = [], os.getpid()
    for _ in range(limit):
        line = _ps(pid, "ppid=,lstart=,comm=")
        if not line:
            break
        parts = line.split()
        ppid, start, comm = parts[0], " ".join(parts[1:6]), " ".join(parts[6:])
        if os.path.basename(comm) == "claude" and pid != os.getpid():
            out.append([pid, start])
        if not ppid.isdigit() or int(ppid) <= 1:
            break
        pid = int(ppid)
    return out


def in_foundry_session(snap=None):
    """Whether this process runs inside a foundry session (any role): by environment, by session id, or by a
    recorded foundry session's `claude` process among its ancestors."""
    if "FOUNDRY_ROLE" in os.environ or os.environ.get("FOUNDRY_SESSION") == "1":
        return True
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    try:
        snap = snap or load()
    except Exception:
        return True  # cannot tell: fail closed
    if sid and (sid in (snap.get("foundry_sessions") or {})
                or any(x.get("session_id") == sid for x in (snap.get("sessions") or {}).values())):
        return True
    procs = {tuple(p) for f in (snap.get("foundry_sessions") or {}).values() for p in (f.get("procs") or [])}
    if not procs:
        return False
    try:
        ancestors = claude_ancestors()
    except Exception:
        return True  # identity unknown: fail closed, so an owner-only act is refused rather than crashing
    return any(tuple(a) in procs for a in ancestors)


HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
# A shell command handed to the owner: "Please run ...", or a command line of a CLI the owner never needs to type.
# `/foundry approve allow <rid>` (typed into the session, run by the mod) is the one owner act a card may name.
OWNER_SHELL_RE = re.compile(r"(?i)\bplease\s+run\b|(?:^|[\s`:(!])(?<!/)(?:gh|git|kubectl|argocd|helm|terraform|foundry)\s+"
                            r"(?:pr|api|push|merge|apply|sync|app|approve|unit|repo|release|auth)\b")


def card_text_refusal(field, text, snap=None):
    """Why an owner-facing card text is refused, or None. Only inside a foundry session (leads, the foreman, the
    dispatcher write cards): a card is Korean when prefs language is ko (code ids, PR refs and shas stay as they
    are), and it never hands the owner a shell command."""
    text = text or ""
    if not text.strip() or not in_foundry_session(snap):
        return None
    if OWNER_SHELL_RE.search(text):
        return (f"refused: the {field} hands the owner a shell command. Merges and other plumbing are the lead's own "
                "act (the merge gate and approval rules decide them); when an owner act is truly needed the card says "
                "the board's [allow] button or `/foundry approve allow <rid>`, nothing else.")
    try:
        import prefs
        lang = prefs.load().get("language") or "ko"
    except Exception:
        lang = "ko"
    if lang == "ko" and not HANGUL_RE.search(text):
        return (f"refused: the {field} is read by the owner and prefs language is ko: write it in Korean "
                f"(code ids, PR refs and shas stay as they are): {text[:80]!r}")
    return None


OWNER_NONCE_ENV = "FOUNDRY_OWNER_NONCE"
OWNER_NONCE_DIR = "owner-nonce"
OWNER_NONCE_TTL = 30
_OWNER = None


def owner_attested():
    """Whether the foundry mod started this process for the owner's own input (a board button, `/foundry`).

    The mod writes a fresh nonce file and passes its name in the env; the first check consumes it, so a nonce
    works once, for one process, within OWNER_NONCE_TTL seconds. The hook refuses any tool call that names the
    variable or the directory, so a session cannot mint one with the usual tools."""
    global _OWNER
    if _OWNER is None:
        _OWNER = False
        n = os.environ.pop(OWNER_NONCE_ENV, "")
        d = path(OWNER_NONCE_DIR)
        if re.fullmatch(r"[0-9a-f]{32}", n):
            try:
                age = time.time() - os.path.getmtime(os.path.join(d, n))
                os.remove(os.path.join(d, n))
                _OWNER = 0 <= age <= OWNER_NONCE_TTL
            except OSError:
                pass
        try:
            for f in os.listdir(d):
                if time.time() - os.path.getmtime(os.path.join(d, f)) > OWNER_NONCE_TTL:
                    os.remove(os.path.join(d, f))
        except OSError:
            pass
    return _OWNER


RELAY_NOTE = "relayed by the foreman from the owner's chat answer"


def plugin_version():
    """The plugin's version from its manifest (`foundry --version`), or "unknown" when the manifest is unreadable."""
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".claude-plugin",
                               "plugin.json")) as f:
            return json.load(f).get("version") or "unknown"
    except (OSError, ValueError):
        return "unknown"


# ---------- single-instance streams ----------
# One running instance per stream kind (and `events wait`) per machine. A starting instance takes the kind over:
# streams/<kind>.json names the owner; every running one checks it each loop and exits once another pid owns it,
# so an upgrade or a restart never leaves an older copy running beside the new one.

def _streams_dir():
    d = path("streams")
    os.makedirs(d, exist_ok=True)
    return d


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def claim_instance(kind):
    """Take `kind` over for this process: record it as the owner and as one of the running instances."""
    d = _streams_dir()
    rec = {"kind": kind, "pid": os.getpid(), "version": plugin_version(), "started": iso()}
    for name in os.listdir(d):  # instances whose process is gone
        if name.startswith(f"{kind}.") and name.count(".") == 2 and not _alive(name.split(".")[1]):
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass
    write_atomic(os.path.join(d, f"{kind}.{os.getpid()}.json"), json.dumps(rec))
    write_atomic(os.path.join(d, f"{kind}.json"), json.dumps(rec))
    return rec


def owns_instance(kind):
    """Whether this process still owns `kind` (a newer start took it over otherwise)."""
    try:
        with open(os.path.join(_streams_dir(), f"{kind}.json")) as f:
            return json.load(f).get("pid") == os.getpid()
    except (OSError, ValueError):
        return True  # no record (cleaned up by hand): keep running rather than leave the kind empty


def release_instance(kind):
    try:
        os.remove(os.path.join(_streams_dir(), f"{kind}.{os.getpid()}.json"))
    except OSError:
        pass


def instances():
    """{kind: {owner, running: [instance records with alive / age_min], duplicates}} from the streams dir."""
    d = _streams_dir()
    out = {}
    now = time.time()
    for name in sorted(os.listdir(d)):
        parts = name[:-5].split(".") if name.endswith(".json") else []
        try:
            with open(os.path.join(d, name)) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        k = out.setdefault(rec.get("kind") or parts[0], {"owner": None, "running": []})
        if len(parts) == 1:
            k["owner"] = rec
        elif _alive(rec.get("pid")):
            t = parse_ts(rec.get("started"))
            k["running"].append({**rec, "age_min": round((now - t.timestamp()) / 60) if t else None})
    for k in out.values():
        k["duplicates"] = [r["pid"] for r in k["running"] if not k["owner"] or r["pid"] != k["owner"].get("pid")]
    return out


def unknown_unit(uid, snap=None, n=5):
    """`unknown unit X` plus the nearest open unit ids, so a guessed id is corrected from the message itself."""
    import difflib
    units = (snap if snap is not None else load()).get("units") or {}
    open_ids = sorted(k for k, u in units.items() if u.get("status") not in ("done", "cancelled"))
    key = str(uid or "").lower()
    near = [k for k in open_ids if key and (key in k.lower() or k.lower() in key)]
    near += [k for k in difflib.get_close_matches(str(uid or ""), open_ids, n=n, cutoff=0.4) if k not in near]
    if not near:
        return f"unknown unit {uid}"
    return f"unknown unit {uid}; nearest open unit(s): {', '.join(near[:n])}"


def relay_quote(by, quote):
    """The owner's exact chat words when the foreman relays an owner act, else None.

    The foreman acts for the owner only on the owner's own words: the record keeps by=foreman and the quote, so
    the journal shows who decided. A relay never mints an owner nonce and never passes require_owner; acts that
    stay the owner's even with a quote (a tier-3 allow, prod merges) refuse it at their own call site."""
    q = (quote or "").strip()
    return q if by == "foreman" and q else None


def owner_how(args, button=None):
    """Where the owner does an owner-only act, as paths that work: the board pane's button, `/foundry <args>`
    typed into a Claude session (the mod runs it as the owner), or a separate terminal tab. Never a shell escape
    inside a session: that inherits the session's env and the guard refuses it. `args` without `--by owner`."""
    import shim
    ways = [f"the board pane's {button} button"] if button else []
    ways += [f"`/foundry {args}` typed into the foreman session",
             f"a separate terminal tab: `{shim.cli()} {args} --by owner`"]
    return ", ".join(ways[:-1]) + ", or " + ways[-1]


def _argv_text():
    import shlex
    if not sys.argv or not sys.argv[0].endswith("foundry.py"):
        return None
    words = [w for w in sys.argv[1:]]
    if "--by" in words:
        i = words.index("--by")
        del words[i:i + 2]
    return " ".join(shlex.quote(w) for w in words) or None


def require_owner(action, snap=None):
    """None when the caller may act as the owner (a terminal or the board, outside every foundry
    session, or the mod on the owner's own input), else the refusal text. Every owner-only CLI act goes
    through here."""
    if owner_attested() or not in_foundry_session(snap):
        return None
    args = _argv_text()
    how = owner_how(args) if args else "the board pane, `/foundry <verb> ...` typed into the foreman session, or a separate terminal tab"
    return (f"refused: {action} is the owner's act, and this process runs inside a foundry session (a shell "
            f"escape typed into a session inherits it). The owner does it from {how}; "
            "a foundry session relays the request to the owner instead.")


UNIT_ID_RE = re.compile(r"[A-Za-z0-9][\w.-]{0,63}", re.ASCII)


def unit_id_error(uid):
    """None for a usable unit id, else why not (ids become paths: worktrees, handoffs, trust keys)."""
    if not isinstance(uid, str) or not UNIT_ID_RE.fullmatch(uid) or ".." in uid:
        return f"invalid unit id {uid!r}: use [A-Za-z0-9][A-Za-z0-9_.-]{{0,63}}, no '..'"
    return None


def new_id(prefix, *parts):
    h = hashlib.sha1(("|".join(map(str, parts)) + iso()).encode()).hexdigest()[:8]
    return f"{prefix}-{h}"


PR_RE = re.compile(r"(?:github\.com/([\w.-]+/[\w.-]+)/pull/(\d+))|(?:\b([\w.-]+/[\w.-]+)#(\d+)\b)")
TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def norm_pr(text):
    """PR refs in text, as owner/repo#123 (bare #123 is too ambiguous to attach on)."""
    out = []
    for m in PR_RE.finditer(text or ""):
        repo, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        ref = f"{repo}#{num}"
        if ref not in out:
            out.append(ref)
    return out


ENV_ALIASES = {"development": "dev", "develop": "dev", "production": "prod", "prd": "prod", "staging": "stg"}


def norm_env(env):
    """One name per deploy environment: dev/development -> dev, prod/production -> prod (journal values as written)."""
    e = str(env or "").strip().lower()
    return ENV_ALIASES.get(e, e)


def tickets(text):
    return list(dict.fromkeys(TICKET_RE.findall(text or "")))


def load_config():
    p = path("config.json")
    cfg = {
        "owner_slack_user_id": None,
        "owner_dm_channels": [],
        "send_ttl_hours": {"default": 24},
        "send_unknown_ttl_minutes": 10,
        "review_ask_ttl_hours": 24,
        "retention_days": {"events": 14, "housekeeping": 30, "agents": 30},
        # Advisor for the foreman's and leads' judgment calls (settings key advisorModel; no frontmatter field).
        "advisor_model": "claude-fable-5-2",
        # Owner-only categories, as `ask` rules so they reach the PermissionRequest tiers under auto mode.
        "lead_ask_rules": [
            "Bash(gh pr merge:*)", "Bash(kubectl:*)", "Bash(argocd:*)", "Bash(helm:*)", "Bash(terraform apply:*)",
            "Edit(~/.claude/**)", "Write(~/.claude/**)", "Edit(~/.claude.json)", "Write(~/.claude.json)",
            "Edit(**/.claude/settings*.json)", "Write(**/.claude/settings*.json)",
            "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.netrc)", "Read(~/.config/gh/**)",
            "Edit(~/.local/state/foundry/**)", "Write(~/.local/state/foundry/**)",
            "mcp__slack__slack_send_message", "mcp__slack__slack_schedule_message",  # mapped per install
        ],
        "foreman_extra_agents": [],
        "repo_roots": [],
        "docker_project_prefix": "foundry-",
        # Lead sessions (`foundry unit launch`).
        "max_concurrent": 8,
        # The foreman's start line (install.sh prints it); the owner keeps Opus 5.5 at medium.
        "foreman_effort": "medium",
        "lead_model": "opus", "lead_effort": "medium",
        "boss_model": "sonnet", "boss_effort": "medium",
        "goal_max_turns": 200,
        # Machine paths, each a config key with this default (`foundry init` sets them): where unit worktrees go,
        # the foreman's home (its background session's cwd), and where main checkouts live (install's repo scan).
        "worktree_root": "~/work/wt",
        "foreman_dir": "~/work/foundry",
        "repos_dir": "~/src",
        "permission_mode": "auto",
        "jira_base": None,
        # PermissionRequest approver (approve.py); the wait stays below the hook's 600 s timeout.
        "approval_wait_seconds": 540, "notify": True,
        "approval_rules": [], "approval_mcp_allow": [], "approval_own_orgs": [],
    }
    if os.path.exists(p):
        try:
            with open(p) as f:
                user = json.load(f)
            if not isinstance(user, dict):
                raise ValueError("top level is not an object")
        except (OSError, ValueError) as e:
            # A broken config must not brick every hook and CLI call: run on the defaults and say so.
            cfg["config_error"] = f"{p}: {e}"[:300]
            return cfg
        for k in ("send_ttl_hours", "retention_days"):
            if isinstance(user.get(k), dict):
                user[k] = cfg[k] | user[k]
        cfg.update(user)
    return cfg
