"""Hands: the mod's agent.spawn check (`foundry spawn-check`) and the hands each unit runs, for the board.

The mod hook asks before a subagent starts; the PreToolUse hook stays the enforcing fallback when the mod is
absent or this command fails. A spawn allowed here is journaled once per tool_use_id as `hand.spawn`.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import roles  # noqa: E402

AGENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents")
KEEP_HANDS = 300
# A hand still "spawned" with no agent after this long never started (or its start was never seen): lost.
SPAWN_STALE_MIN = 30
MODEL_RE = re.compile(r"^model:\s*(\S+)\s*$", re.M)

STATE = {"hands": {}}


def _op_spawn(s, r, ts):
    key = r.get("tool_use_id") or f"{r.get('agent_id') or r.get('name')}@{ts}"
    if key in s["hands"]:
        return
    s["hands"][key] = {k: r.get(k) for k in ("unit", "name", "type", "role", "parent", "agent_id", "session", "model")} | {
        "key": key, "state": "spawned", "ts": ts}
    if len(s["hands"]) > KEEP_HANDS:
        for k in sorted(s["hands"], key=lambda k: s["hands"][k].get("ts") or "")[:len(s["hands"]) - KEEP_HANDS]:
            del s["hands"][k]


def _op_state(s, r, ts):
    for h in s["hands"].values():
        if h["key"] == r.get("key") or (r.get("agent_id") and h.get("agent_id") == r.get("agent_id")):
            h["state"] = r.get("state") or h["state"]
            h["updated"] = ts


OPS = {"hand.spawn": _op_spawn, "hand.state": _op_state}


def agent_model(subagent_type):
    """The `model:` a foundry agent pins in its frontmatter; None for other agents or none set."""
    if not (subagent_type or "").startswith("foundry:"):
        return None
    name = subagent_type.split(":", 1)[1]
    if not re.fullmatch(r"[\w-]+", name):
        return None
    try:
        with open(os.path.join(AGENTS_DIR, name + ".md")) as f:
            text = f.read()
    except OSError:
        return None
    head = text.split("---", 2)[1] if text.startswith("---") else ""
    m = MODEL_RE.search(head)
    return m.group(1) if m else None


def _inp(req, snap):
    parent = req.get("parentAgentId") or None
    inp = {"session_id": req.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")}
    if parent:
        inp["agent_id"] = parent
        inp["agent_type"] = (snap["agents"].get(parent) or {}).get("type")
    return inp


def check(req, snap=None):
    """{deny: reason} | {model: alias} | {} for one spawn request, and the record to journal (or None)."""
    import hook
    snap = snap or cl.load()
    inp = _inp(req, snap)
    caller = hook.caller_of(inp, snap)
    target = req.get("subagentType") or "general-purpose"
    if caller == roles.OUTSIDE:
        return {"caller": caller}, None
    ok, reason = roles.spawn_decision(caller, target, cl.load_config()["foreman_extra_agents"])
    if not ok:
        return {"deny": "[foundry] " + reason, "caller": caller}, None
    out = {"caller": caller}
    if req.get("resume"):
        # SendMessage waking a finished agent: the same hand runs again. SubagentStart journals agent.resume.
        return out, None
    pinned = agent_model(target)
    if pinned and req.get("model") and req["model"] != pinned:
        out["model"] = pinned
    tid = req.get("tool_use_id") or None
    pre = next((p for p in snap.get("pending_spawns") or [] if tid and p.get("tool_use_id") == tid), None)
    m = hook.UNIT_RE.search(req.get("prompt") or "")
    unit = (pre or {}).get("unit") or (m.group(1) if m else None) or hook.unit_of_agent(inp, snap)
    rec = {"tool_use_id": tid, "unit": unit, "name": req.get("name") or target, "type": target, "role": roles.role_of(target),
           "parent": inp.get("agent_id") or "main", "session": inp.get("session_id"),
           "model": out.get("model") or req.get("model")}
    return out, rec


def cmd_spawn_check(a):
    try:
        req = json.loads(a.json or "{}")
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"bad --json: {e}"}))
        return 2

    def decide(snap):
        out, rec = check(req, snap)
        if rec and rec["tool_use_id"] and rec["tool_use_id"] in (snap.get("hands") or {}):
            rec = None
        return out, [("hand.spawn", rec)] if rec else []

    out, _ = cl.transact(decide)
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_hand_state(a):
    cl.append("hand.state", key=a.key, agent_id=a.agent, state=a.state)
    print(json.dumps({"key": a.key, "agent_id": a.agent, "state": a.state}))
    return 0


EDIT_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")
DELEGATE = ("Leads delegate execution: hand this change to a hand (`foundry:hand-light` for a small or mechanical "
            "edit, `foundry:hand` for code and tests, `foundry:hand-heavy` for hard or wide work; model-selection "
            "skill) and judge its result.")


def pre_tool_use(inp, snap, ctx):
    """A line-lead or shop-boss editing files itself is doing a hand's work: say so on every edit. A warning, never a
    deny, because a Write must never fail on a hook problem (Streams.test_large_write_never_denied...)."""
    if ctx.get("tool") in EDIT_TOOLS and ctx.get("caller") in (roles.LEAD, roles.BOSS):
        return ("warn", DELEGATE)
    return None


def add_cli(sp):
    s = sp.add_parser("spawn-check", help="mod agent.spawn check: deny, pin model, journal the hand")
    s.add_argument("--json", required=True)
    s.set_defaults(func=cmd_spawn_check)
    h = sp.add_parser("hand-state", help="record a hand's state")
    h.add_argument("--key")
    h.add_argument("--agent")
    h.add_argument("--state", required=True)
    h.set_defaults(func=cmd_hand_state)


def unit_hands(snap, uid, now=None):
    """[{name, role, state}] for one unit: journaled hand.spawn records, then agents state for the rest."""
    now = now or cl.now()
    agents = snap.get("agents") or {}
    bound = {p.get("tool_use_id"): p.get("bound") for p in snap.get("pending_spawns") or [] if p.get("bound")}
    out, seen = [], set()
    for h in (snap.get("hands") or {}).values():
        if h.get("unit") != uid:
            continue
        aid = h.get("agent_id") or bound.get(h["key"]) or next(
            (k for k, a in agents.items() if k not in seen and a.get("unit") == uid and a.get("type") == h.get("type")
             and a.get("session") == h.get("session") and (a.get("started") or "") >= (h.get("ts") or "")), None)
        state = (agents.get(aid) or {}).get("status") if aid else None
        seen.add(aid)
        state = state or h.get("state")
        t = cl.parse_ts(h.get("ts"))
        if not aid and state == "spawned" and t and (now - t).total_seconds() >= SPAWN_STALE_MIN * 60:
            state = "lost"
        out.append({"name": h.get("name"), "role": h.get("role"), "state": state, "ts": h.get("ts")})
    for aid, a in agents.items():
        if a.get("unit") == uid and aid not in seen:
            out.append({"name": a.get("type"), "role": roles.role_of(a.get("type")), "state": a.get("status"),
                        "ts": a.get("started")})
    out.sort(key=lambda h: h.get("ts") or "")
    return out[-12:]


def board(snap, model):
    for sec in model.get("sections") or []:
        for row in sec.get("rows") or []:
            row["hands"] = unit_hands(snap, row["id"])
