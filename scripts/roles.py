"""Who may spawn, launch or message whom. Pure functions so the tests can table-drive them.

Chain: owner -> foreman -> (line-lead | shop-boss) -> hand.
The foreman is the main thread of its own session; every line-lead and shop-boss is the main thread
of its own `claude --bg` session (started only by `foundry unit launch`); hands are subagents inside a
lead session. The dispatcher, sweeper-judge and advisor are leaf subagents.
"""

FOREMAN = "foreman"
LEAD = "lead"            # line-lead
BOSS = "boss"            # shop-boss
STAFF = "staff"          # dispatcher, sweeper-judge, advisor: leaves
HAND = "hand"
EXTERNAL = "external"    # any non-foundry agent type (Explore, general-purpose, other plugins)
OUTSIDE = "outside"      # a session that is not a foundry session

TYPE_ROLE = {
    "foundry:foreman": FOREMAN,
    "foundry:line-lead": LEAD,
    "foundry:shop-boss": BOSS,
    "foundry:dispatcher": STAFF,
    "foundry:sweeper-judge": STAFF,
    "foundry:advisor": STAFF,
    "foundry:hand": HAND,
    "foundry:hand-light": HAND,
    "foundry:hand-heavy": HAND,
}
ROLE_ENV = {"foreman": FOREMAN, "line-lead": LEAD, "shop-boss": BOSS}
SESSION_ROLES = (FOREMAN, LEAD, BOSS)       # roles that are whole sessions, never subagents
LEAD_TYPE = {"project": "foundry:line-lead", "repo": "foundry:shop-boss"}
SESSION_PREFIX = {"foundry:line-lead": "lead", "foundry:shop-boss": "boss"}
ADVISORS = ("foundry:advisor",)             # judgment-point consultant any session role may call


def role_of(agent_type):
    if not agent_type:
        return EXTERNAL
    if agent_type in TYPE_ROLE:
        return TYPE_ROLE[agent_type]
    if agent_type.startswith("foundry:"):
        return HAND  # an unknown foundry agent gets the least power
    return EXTERNAL


def session_name(lead_type, unit):
    return f"{SESSION_PREFIX.get(lead_type, 'lead')}-{unit}"


def caller_role(hook_input, foundry_session, env_role=None):
    """Role of the agent making the tool call.

    agent_id is present only inside a subagent; agent_type alone (no agent_id) means the main thread of a
    session started with --agent. FOUNDRY_ROLE (from `--settings env`) names a session's role when
    --agent is missing.
    """
    if not foundry_session:
        return OUTSIDE  # foundry:* agents spawned from an ordinary session follow that session, not foundry rules
    agent_type = hook_input.get("agent_type")
    if hook_input.get("agent_id"):
        role = role_of(agent_type)  # a built-in worker inside a foundry session is a leaf (EXTERNAL)
        return HAND if role in SESSION_ROLES else role  # a session role run as a subagent gets no session powers
    if agent_type and agent_type.startswith("foundry:"):
        role = role_of(agent_type)
        return role if role in SESSION_ROLES else HAND
    return ROLE_ENV.get(env_role or "", FOREMAN)


def spawn_decision(caller, target_type, extra_foreman_agents=()):
    """Agent-tool spawns. Return (allow: bool, reason: str)."""
    target = role_of(target_type)
    if caller == OUTSIDE:
        return True, "not a foundry session"
    if caller == FOREMAN:
        if target == STAFF:
            return True, "foreman -> staff"
        if target_type in extra_foreman_agents:
            return True, "foreman -> configured extra agent"
        if target in (LEAD, BOSS):
            return False, ("Line-leads and shop-bosses are their own background sessions, never subagents: "
                           "run `foundry unit launch <id>` (foundry-launch skill).")
        return False, (f"The foreman never spawns {target_type!r} ({target}) directly. "
                       "Route the work to a unit and launch its line-lead or shop-boss "
                       "(`foundry unit launch <id>`); the lead picks a hand.")
    if caller in (LEAD, BOSS):
        if target in (HAND, EXTERNAL) or target_type in ADVISORS:
            return True, f"{caller} -> hand"
        return False, (f"A {caller} only spawns hands (foundry:hand*, foundry:advisor, or built-in workers like "
                       f"Explore); {target_type!r} is {target}. Hand lead-level work back to the foreman: "
                       "`foundry unit report <unit> --text ...`.")
    if caller == STAFF:
        return False, f"{caller} agents are leaves: do the work yourself or report back to the foreman."
    return False, (f"A {caller} is a leaf and never spawns agents. Do the work yourself, "
                   "or end with a report that says what else is needed.")


def message_decision(caller, target_type):
    """SendMessage by topology: the foreman talks to lead sessions, never to hands."""
    if caller == FOREMAN and role_of(target_type) in (HAND, EXTERNAL) and target_type:
        return False, ("The foreman never messages hands directly; "
                       "message the line-lead or shop-boss session that owns them.")
    return True, "ok"


def launch_decision(caller):
    """Who may start, resume or stop lead sessions (`foundry unit launch|resume|stop`, raw `claude --bg`)."""
    if caller in (OUTSIDE, FOREMAN):
        return True, "ok"
    return False, (f"A {caller} never starts or stops sessions. Only the foreman launches line-leads and "
                   "shop-bosses, through `foundry unit launch`. Report what you need instead: "
                   "`foundry unit report <unit> --text ...`.")
