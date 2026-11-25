"""Entry point for every foundry hook: `python3 hook.py <Event>` with the hook input on stdin."""

import json
import os
import re
import shlex
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import roles  # noqa: E402
import slackguard as sg  # noqa: E402

JUDGE_FORBIDDEN = set("\n\r;&|<>$`()")
JUDGE_VERDICTS = ("safe", "propose", "skip")
UNIT_RE = re.compile(r"^\s*UNIT:\s*([\w.-]+)\s*$", re.M)
TODO_TOOLS = ("TaskCreate", "TaskUpdate", "TodoWrite")
CLEAR_AFTER_COMPACTIONS = 2


def log_decision(**rec):
    cl.append_log("enforce.log", rec)


PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
HOUSEKEEP = os.path.join(PLUGIN_ROOT, "scripts", "housekeep.py")
HOUSEKEEP_BIN = os.path.join(PLUGIN_ROOT, "bin", "foundry-housekeep")


def _is_this_plugin(path, target):
    return os.path.realpath(os.path.expanduser(path)) == os.path.realpath(target)


def _plugin_script(word, cwd):
    """Whether word names a foundry script: in a `scripts/` dir beside foundrylib.py, this copy or any other."""
    if "CLAUDE_PLUGIN_ROOT" in word and "/scripts/" in word:
        return True
    p = os.path.expanduser(word)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    d = os.path.dirname(os.path.realpath(p))
    return os.path.basename(d) == "scripts" and os.path.isfile(os.path.join(d, "foundrylib.py"))


def raw_plugin_script(cmd, cwd=None):
    """The foundry script a command runs by path (`python3 .../scripts/foundry.py ...`), or None.

    Reading one (cat, grep, an editor) is not running it; `python3 -m ...` / `-c ...` run something else."""
    import gitpolicy
    try:
        segs = gitpolicy.segments(cmd)
    except ValueError:
        segs = [cmd.split()]
    for seg in segs:
        argv = gitpolicy.strip_env(seg)
        if not argv:
            continue
        if argv[0].endswith(".py") and _plugin_script(argv[0], cwd):
            return argv[0]
        for i, w in enumerate(argv):
            if not gitpolicy.INTERPRETERS.match(os.path.basename(w)):
                continue
            for x in argv[i + 1:]:
                if x in ("-m", "-c", "-e"):
                    break
                if x.startswith("-"):
                    continue
                if x.endswith(".py") and _plugin_script(x, cwd):
                    return x
                break
    return None


def judge_command_ok(cmd):
    """The judge's whole Bash surface: `python3 <...>/scripts/housekeep.py scan` or `... plan --hid --verdict --reason`.

    Checked as an argv whitelist, not a regex: any newline, redirect, substitution or chaining is refused
    before parsing, so nothing after the whitelisted argv can run.
    """
    if any(c in JUDGE_FORBIDDEN for c in cmd):
        return False
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return False
    if argv and os.path.basename(argv[0]) == "foundry-housekeep":
        # A bare name runs whatever PATH finds first: it must be this plugin's wrapper (or absent, then nothing runs).
        import shutil
        found = shutil.which(argv[0]) if "/" not in argv[0] else argv[0]
        if found and not _is_this_plugin(found, HOUSEKEEP_BIN):
            return False
        argv = ["python3", HOUSEKEEP, *argv[1:]]
    if len(argv) < 3 or os.path.basename(argv[0]) != "python3":
        return False
    if not _is_this_plugin(argv[1], HOUSEKEEP):
        return False
    sub, rest = argv[2], argv[3:]
    if sub == "scan":
        return rest == []
    if sub == "plan" and len(rest) == 6:
        opts = dict(zip(rest[0::2], rest[1::2]))
        return sorted(opts) == ["--hid", "--reason", "--verdict"] and opts["--verdict"] in JUDGE_VERDICTS
    return False


_EMITTED = []


def out(obj):
    """Queue a hook output; `flush` writes exactly one JSON object (Claude Code reads one)."""
    _EMITTED.append(obj)


def _is_deny(obj):
    return (obj.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny" or obj.get("decision") == "block"


def flush():
    """A deny wins alone; otherwise one object, with every additionalContext joined."""
    if not _EMITTED:
        return
    deny_obj = next((o for o in _EMITTED if _is_deny(o)), None)
    obj = deny_obj or _EMITTED[0]
    if not deny_obj and len(_EMITTED) > 1:
        obj = json.loads(json.dumps(_EMITTED[0]))
        ctxs = [(o.get("hookSpecificOutput") or {}).get("additionalContext") for o in _EMITTED]
        hso = obj.setdefault("hookSpecificOutput", {})
        hso["additionalContext"] = "\n\n".join(c for c in ctxs if c)
        upd = next(((o.get("hookSpecificOutput") or {}).get("updatedInput") for o in _EMITTED
                    if (o.get("hookSpecificOutput") or {}).get("updatedInput")), None)
        if upd:
            hso["updatedInput"] = upd
    _EMITTED.clear()
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))


def deny(reason, event="PreToolUse"):
    out({"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                                "permissionDecisionReason": "[foundry] " + reason}})


def env_role():
    return os.environ.get("FOUNDRY_ROLE") or None


def is_foundry_session(inp, snap):
    """Foundry rules follow the session, never the agent type alone: a foundry:* agent spawned from an ordinary
    session is that session's business. A session is foundry's when SessionStart recorded it, when it runs
    with FOUNDRY_ROLE (lead sessions get it through `--settings env`), or when its main thread is foundry:*."""
    return (os.environ.get("FOUNDRY_SESSION") == "1" or bool(env_role())
            or inp.get("session_id") in snap["foundry_sessions"]
            or (not inp.get("agent_id") and (inp.get("agent_type") or "").startswith("foundry:")))


def session_rec(inp, snap):
    return snap["foundry_sessions"].get(inp.get("session_id")) or {}


def agent_record(inp, snap):
    """A subagent caller's foundry record. A named spawn reports agent_type = its name, not its foundry type,
    so its type and unit come from the bound agent or from its hand.spawn record (name + session)."""
    aid = inp.get("agent_id")
    if not aid:
        return {}
    a = snap["agents"].get(aid) or {}
    if (a.get("type") or "").startswith("foundry:"):
        return a
    name = inp.get("agent_type") or a.get("name")
    hands = sorted((snap.get("hands") or {}).values(), key=lambda h: h.get("ts") or "", reverse=True)
    for h in hands:
        if h.get("agent_id") == aid or (name and h.get("name") == name and h.get("session") == inp.get("session_id")):
            return {**a, "type": h.get("type"), "unit": a.get("unit") or h.get("unit")}
    return a


def caller_of(inp, snap):
    rec = agent_record(inp, snap)
    if (rec.get("type") or "").startswith("foundry:"):
        inp = {**inp, "agent_type": rec["type"]}
    return roles.caller_role(inp, is_foundry_session(inp, snap), env_role() or session_rec(inp, snap).get("role"))


def unit_of_agent(inp, snap):
    """The unit the caller works for: a subagent's bound unit, else the lead session's unit."""
    if inp.get("agent_id"):
        return agent_record(inp, snap).get("unit")
    return os.environ.get("FOUNDRY_UNIT") or session_rec(inp, snap).get("unit")


def proc_gone(proc):
    """True when the recorded [pid, start] claude process no longer runs (a reused pid has another start)."""
    if not proc:
        return False
    pid, start = (list(proc) + [None, None])[:2]
    line = " ".join(cl._ps(pid, "lstart=").split())
    return not line or bool(start and line != start)


def lost_agents(snap, sid, source, caller):
    """Running subagents with positive evidence they ended. Compaction (and /clear) keeps a session's
    subagents running, so those sources mark nothing. A resumed or restarted session's own earlier
    subagents died with its old process; another foreman session's count only once its recorded process
    is gone. Without a process record nothing is marked: a false loss re-dispatches live work."""
    if source in ("compact", "clear"):
        return []
    out = []
    for a in snap["agents"].values():
        if a["status"] != "running":
            continue
        if a.get("session") == sid:
            out.append(a["agent_id"])
            continue
        srec = snap["foundry_sessions"].get(a.get("session")) or {}
        if caller == roles.FOREMAN and srec.get("role") in (None, "foreman") and proc_gone((srec.get("procs") or [None])[0]):
            out.append(a["agent_id"])
    return out


# `claude` / `foundry unit launch|resume|stop` where a command starts: the session lifecycle belongs to the foreman.
CMD_POS = r"(?:^|[;&|(`\n]|\$\()\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:(?:sudo|command|exec|env|nohup|time)\s+)*"
CLAUDE_SESSION_RE = re.compile(CMD_POS + r"(?:\S*/)?claude\s+(?:[^\n|;&]*\s)?(?:--bg|--background|-p|--print|--resume|-r|"
                               r"--continue|-c|respawn|stop|rm|attach)(?:\s|=|$)")
CLAUDE_LEAD_RE = re.compile(r"--agent[\s=]+['\"]?foundry:(?:line-lead|shop-boss)\b")
SLACK_API_RE = re.compile(r"(?i)slack\.com/api/(?:chat\.|files\.)|hooks\.slack\.com/")
LIFECYCLE_RE = re.compile(r"foundry(?:\.py)?['\"]?\s+unit\s+(?:launch|resume|stop)\b")


def bash_decision(caller, inp, cmd, snap):
    """None when allowed, else the deny reason."""
    if inp.get("agent_type") == "foundry:sweeper-judge" and not judge_command_ok(cmd):
        return "The sweeper judge only runs `housekeep.py scan` and `housekeep.py plan`; the executor acts."
    raw = inp.get("agent_type") != "foundry:sweeper-judge" and raw_plugin_script(cmd, inp.get("cwd"))
    if raw:
        return (f"Run the installed `foundry <verb>` (on PATH while the plugin is enabled), not {raw} by path: a "
                "source checkout can be another version than the hooks and the state this session runs with.")
    if SLACK_API_RE.search(cmd):
        return ("Slack posts go through the Slack MCP send tool, where quiet hours and dedup apply; "
                "never curl/wget to the Slack API: use slack_send_message (or slack_schedule_message).")
    ok, why = roles.launch_decision(caller)
    if not ok and (CLAUDE_SESSION_RE.search(cmd) or LIFECYCLE_RE.search(cmd)):
        return why
    if caller == roles.FOREMAN and CLAUDE_SESSION_RE.search(cmd) and CLAUDE_LEAD_RE.search(cmd):
        return ("Start lead sessions only with `foundry unit launch <id>` (or `resume`): it creates the worktree, "
                "applies the concurrency cap and records the session in the journal. A raw `claude --bg` does none of that.")
    try:
        import approve
    except ImportError:
        return None
    return approve.bash_decision(caller, cmd, snap, inp.get("cwd"))


def finished_target(to, snap):
    """The unit a SendMessage target names, when that unit is done or its session is gone."""
    sess = snap["sessions"].get(to)
    if not sess:
        m = re.match(r"^(?:lead|boss)-(.+)$", to or "")
        uid = m.group(1) if m else None
        if uid not in snap["units"]:
            return None
        sess = {"unit": uid, "state": None}
    u = snap["units"].get(sess.get("unit")) or {}
    if u.get("status") == "done" or sess.get("state") in ("done", "stopped", "lost"):
        return u.get("id") or sess.get("unit")
    return None


def module_ctx(inp, snap):
    caller = caller_of(inp, snap)
    return {"caller": caller, "unit": unit_of_agent(inp, snap) if caller != roles.OUTSIDE else None,
            "tool": inp.get("tool_name", ""), "input": inp.get("tool_input") or {}}


def pre_tool_use(inp):
    snap = cl.load()
    tool = inp.get("tool_name", "")
    ti = inp.get("tool_input") or {}
    text = json.dumps(ti, ensure_ascii=False)
    if cl.OWNER_NONCE_ENV in text or "/" + cl.OWNER_NONCE_DIR in text:
        # Only the mod mints owner nonces (the owner's own button or /foundry); no tool call may name them.
        log_decision(session=inp.get("session_id"), tool=tool, decision="deny", reason="owner nonce")
        return deny("owner-only acts run from the board pane or the owner's /foundry command; "
                    "a tool call cannot act as the owner")
    caller = caller_of(inp, snap)
    base = {"session": inp.get("session_id"), "caller": caller, "caller_type": inp.get("agent_type"),
            "agent_id": inp.get("agent_id"), "tool": tool}
    ctx = module_ctx(inp, snap)
    warns = []
    for mod in cl.modules():
        fn = getattr(mod, "pre_tool_use", None)
        res = fn(inp, snap, ctx) if fn else None
        if isinstance(res, tuple) and res and res[0] == "warn":
            log_decision(**base, decision="warn", reason=res[1], module=mod.__name__)
            warns.append(res[1])
        elif res:
            log_decision(**base, decision="deny", reason=res, module=mod.__name__)
            return deny(res)
    if warns:
        out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": " ".join(warns)}})

    if tool in ("Agent", "Task") and caller != roles.OUTSIDE:
        target = ti.get("subagent_type") or "general-purpose"
        cfg = cl.load_config()
        ok, reason = roles.spawn_decision(caller, target, cfg["foreman_extra_agents"])
        m = UNIT_RE.search(ti.get("prompt") or "")
        unit = m.group(1) if m else unit_of_agent(inp, snap)
        log_decision(**base, target=target, decision="allow" if ok else "deny", reason=reason, unit=unit)
        if not ok:
            return deny(reason)
        cl.append("agent.spawn", session=inp.get("session_id"), caller=inp.get("agent_id") or "main",
                  caller_type=inp.get("agent_type") or caller, target_type=target, unit=unit,
                  tool_use_id=inp.get("tool_use_id"), name=ti.get("name"),
                  asked=" ".join(str(ti.get("description") or ti.get("prompt") or "").split())[:300])
        return None

    if tool == "SendMessage":
        to = str(ti.get("to") or "").split(" [")[0].strip()
        done = finished_target(to, snap)
        if done:
            reason = (f"Unit {done} is finished (or its session is gone); a message would wake an old session. "
                      f"Queue it instead: `foundry inbox route --unit {done} --text ...` "
                      "-- it is applied on the unit's next launch.")
            log_decision(**base, target=to, decision="deny", reason=reason)
            return deny(reason)
        if caller != roles.OUTSIDE:
            target_type = (snap["agents"].get(to) or {}).get("type")
            ok, reason = roles.message_decision(caller, target_type)
            if not ok:
                log_decision(**base, target=to, decision="deny", reason=reason)
                return deny(reason)
        return None

    if tool == "Bash" and caller != roles.OUTSIDE:
        cmd = ti.get("command") or ""
        reason = bash_decision(caller, inp, cmd, snap)
        if reason:
            log_decision(**base, target=cmd[:200], decision="deny", reason=reason)
            return deny(reason)
        sign_body_file(cmd)
        return None

    if JIRA_COMMENT_RE.match(tool):
        foot = tracker_footer()
        body = ti.get("commentBody")
        if not ti.get("commentId") and isinstance(body, str) and ti.get("contentFormat") != "html" \
                and body.rstrip().split("\n")[-1].strip() != foot:
            out({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                        "updatedInput": {**ti, "commentBody": f"{body.rstrip()}\n\n{foot}"}}})
        return None

    if sg.is_slack_draft(tool):
        why = sg.draft_decision(ti)
        log_decision(**base, target=str(ti.get("channel_id") or "")[:40], decision="deny" if why else "allow",
                     reason=why or "draft")
        return deny(why) if why else signed(ti)

    if sg.is_slack_send(tool):
        t = cl.now()
        cfg = cl.load_config()
        keys, kind = sg.idempotency_keys(ti)
        warnings = []

        def decide(fresh):
            # Check and pending-record in one lock: two concurrent sends cannot both pass.
            why = sg.broadcast_decision(ti) or sg.quiet_decision(tool, ti, t, cfg, fresh["send_overrides"],
                                                                 warnings.append)
            why = why or sg.dedup_decision(keys, fresh["sends"], t, cfg, sg.post_time(tool, ti, t))
            return why, [] if why else send_records(keys, kind, "pending", ti, tool)

        why, _ = cl.transact(decide)
        for w in warnings:
            log_decision(**base, target=keys[0], decision="warn", reason=w)
        log_decision(**base, target=keys[0], decision="deny" if why else "allow", reason=why or kind)
        if why:
            return deny(why)
        return signed(ti)
    return None


JIRA_COMMENT_RE = re.compile(r"^mcp__.+__addOrEditJiraIssueComment$")
GH_COMMENT_RE = re.compile(r"^gh (issue|pr) comment\b")
BODY_FILE_RE = re.compile(r"^/tmp/foundry-[\w.-]+\.md$")


def tracker_footer():
    import sync
    return sync.footer(sync._prefs())


def sign_body_file(cmd):
    """A new tracker comment from a body file foundry names (/tmp/foundry-<batch>.md): the file gets a blank line
    and prefs sync.comment_footer appended when it does not end with it, right before gh reads it."""
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return
    if not GH_COMMENT_RE.match(" ".join(argv[:3])) or "--edit-last" in argv:
        return
    path = next((argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ("--body-file", "-F")), None)
    if not path or not BODY_FILE_RE.match(path) or not os.path.isfile(path):
        return
    foot = tracker_footer()
    with open(path, encoding="utf-8") as f:
        body = f.read().rstrip()
    if body.split("\n")[-1].strip() != foot:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{body}\n\n{foot}\n")


def signed(ti):
    """Append prefs slack.footer to a top-level message that lacks it, through updatedInput (no permission decision,
    so the normal permission flow still applies): no prompt or agent has to remember it."""
    updated = sg.with_footer(ti)
    if updated:
        out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}})


def send_records(keys, kind, state, ti, tool):
    text = ti.get("message") or ti.get("text") or ti.get("initial_comment") or ""
    post = sg.post_time(tool, ti, cl.now()) if str(tool).endswith("schedule_message") else None
    return [("send.record", {"key": k, "state": state, "channel": ti.get("channel_id"), "thread": ti.get("thread_ts"),
                             "kind": sg.key_kind(k, kind), "tool": tool, "prs": cl.norm_pr(text),
                             "tickets": cl.tickets(text), **({"post_at": post.isoformat()} if post else {})})
            for k in keys]


def post_tool_use(inp):
    tool = inp.get("tool_name", "")
    ti = inp.get("tool_input") or {}
    resp = inp.get("tool_response")
    mods = [m for m in cl.modules() if hasattr(m, "post_tool_use")]
    if mods:
        snap = cl.load()
        ctx = module_ctx(inp, snap)
        for mod in mods:
            mod.post_tool_use(inp, snap, ctx)
    if sg.is_slack_send(tool):
        keys, kind = sg.idempotency_keys(ti)
        cl.append_many(send_records(keys, kind, sg.send_outcome(resp), ti, tool))
        return
    if sg.is_slack_draft(tool):
        state, link = sg.draft_outcome(resp)
        cl.append("outbox.drafted", channel=ti.get("channel_id"), thread_ts=ti.get("thread_ts"), link=link,
                  state=state, prs=cl.norm_pr(ti.get("message") or ""))
        return
    if tool in TODO_TOOLS:
        snap = cl.load()
        if not is_foundry_session(inp, snap):
            return
        unit = unit_of_agent(inp, snap)
        agent = inp.get("agent_id") or "main"
        if tool == "TodoWrite":
            cl.append("todo.replace", unit=unit, agent=agent, todos=ti.get("todos") or [])
        elif tool == "TaskCreate":
            task = (resp or {}).get("task") or {} if isinstance(resp, dict) else {}
            cl.append("todo", unit=unit, agent=agent, task_id=str(task.get("id") or ti.get("subject")),
                      subject=ti.get("subject"), status="pending")
        else:
            cl.append("todo", unit=unit, agent=agent, task_id=str(ti.get("taskId")),
                      subject=ti.get("subject"), status=ti.get("status"))


def run_module_hook(name, inp):
    mods = [m for m in cl.modules() if hasattr(m, name)]
    if mods:
        snap = cl.load()
        ctx = module_ctx(inp, snap)
        for mod in mods:
            getattr(mod, name)(inp, snap, ctx)


def post_tool_use_failure(inp):
    run_module_hook("post_tool_use_failure", inp)
    tool = inp.get("tool_name", "")
    if sg.is_slack_send(tool):
        ti = inp.get("tool_input") or {}
        keys, kind = sg.idempotency_keys(ti)
        cl.append_many(send_records(keys, kind, "failed", ti, tool))


def session_start(inp):
    snap = cl.load()
    if not is_foundry_session(inp, snap):
        import watchers
        notice = watchers.owner_notice(snap)
        if notice:
            out({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "[foundry] " + notice}})
        return
    import board
    import handoff
    sid = inp.get("session_id")
    source = inp.get("source") or "startup"
    caller = caller_of(inp, snap)
    unit = unit_of_agent(inp, snap) if caller in (roles.LEAD, roles.BOSS) else None
    role = {roles.LEAD: "line-lead", roles.BOSS: "shop-boss"}.get(caller, "foreman")
    try:
        ancestors = cl.claude_ancestors()
    except Exception as e:  # never lose the session record over the process lookup
        log_decision(event="SessionStart", error=f"claude_ancestors: {e!r}")
        ancestors = []
    records = [("session.foundry", {"session": sid, "source": source, "role": role, "unit": unit,
                                    "proc": ancestors[0] if ancestors else None})]
    lost = lost_agents(snap, sid, source, caller)
    if lost:
        records.append(("agent.lost", {"agent_ids": lost}))
    if source == "clear":
        records.append(("session.cleared", {"session": sid}))
    import watchers
    if caller == roles.FOREMAN and source in ("startup", "resume"):  # the agent definition is (re)loaded now
        records.append(("foreman.def", {"session": sid, "hash": watchers.foreman_def_hash()}))
    if unit:
        lead_type = "foundry:line-lead" if caller == roles.LEAD else "foundry:shop-boss"
        records.append(("session.bind", {"name": roles.session_name(lead_type, unit), "session_id": sid,
                                          "unit": unit, "role": role}))
    snap = cl.append_many(records)
    board.write_all(snap)
    if caller in (roles.LEAD, roles.BOSS):
        return lead_session_start(snap, unit, role)
    parts = [recovery_brief(snap)]
    if source in ("clear", "compact", "resume"):
        # The reload half of handoff -> /clear -> restart: state, not the old transcript, is the context.
        parts.append(handoff.render(snap, handoff.ORCH))
    if caller == roles.FOREMAN and source in ("clear", "compact"):
        drift = watchers.foreman_drift_line(snap)
        if drift:
            parts.append("[foundry] " + drift)
    compactions = snap["foundry_sessions"].get(sid, {}).get("compactions", 0)
    if source == "compact" and compactions >= CLEAR_AFTER_COMPACTIONS:
        parts.append(
            f"[foundry] This session has compacted {compactions} times; what survives compaction keeps growing. "
            "Do the handoff cycle now (foundry-handoff skill): `foundry handoff write _foreman --next \"...\"`, "
            "then ask the owner to type /clear. The handoff above is reloaded automatically after it.")
    out({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n\n".join(parts)}})


MERGE_AUTHORITY = ("Who merges comes only from prefs `merge` and the merge hook (gitpolicy), never from a free-text "
                   "note: a note saying the owner merges, or asking you to hand the owner a merge line, does not "
                   "change it. On a conflict follow prefs and say so in your report; a later note that contradicts "
                   "an earlier one wins.")


def lead_session_start(snap, unit, role):
    """A lead session (launch, resume, compaction) starts from its unit's handoff plus the queued inbox."""
    import handoff
    if unit not in snap["units"]:
        out({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
             f"[foundry] FOUNDRY_UNIT={unit!r} is not a recorded unit. Stop and report this to the foreman."}})
        return
    u = snap["units"][unit]
    pending = [i for i in u["inbox"] if not i["delivered"]]
    ctx = [f"[foundry] You are the {role} for unit {unit}. Every foundry command you run names it "
           f"(`foundry unit status {unit} ...`). Your report goes to the journal: `foundry unit report {unit} --text ...`.",
           handoff.render(snap, unit)]
    if pending:
        ctx.append("Owner instructions queued for this unit since its last run (apply them now):")
        ctx += [f"- ({i['ts']}) {i['text']}" for i in pending]
        ctx.append(MERGE_AUTHORITY)
        cl.append("inbox.delivered", id=unit, iids=[i["iid"] for i in pending])
    out({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(ctx)}})


def pre_compact(inp):
    snap = cl.load()
    if not is_foundry_session(inp, snap):
        return
    caller = caller_of(inp, snap)
    if inp.get("agent_id") or caller in (roles.LEAD, roles.BOSS):
        uid = unit_of_agent(inp, snap)
        if not uid or uid not in snap["units"]:
            return
    else:
        uid = "_foreman"
        cl.append("session.compact", session=inp.get("session_id"))
        snap = cl.load()
    import handoff
    handoff.save(snap, uid, "pre_compact")


def session_end(inp):
    snap = cl.load()
    if not is_foundry_session(inp, snap) or inp.get("agent_id"):
        return
    import handoff
    reason = inp.get("reason") or "other"
    caller = caller_of(inp, snap)
    if caller in (roles.LEAD, roles.BOSS):
        uid = unit_of_agent(inp, snap)
        name = next((n for n, x in snap["sessions"].items() if x.get("session_id") == inp.get("session_id")), None)
        if name:
            snap = cl.append("session.state", name=name, state="stopped", note=f"session_end:{reason}")
        if uid in snap["units"]:
            handoff.save(snap, uid, f"session_end:{reason}")
        return
    handoff.save(snap, "_foreman", f"session_end:{reason}")


def recovery_brief(snap):
    lines = ["[foundry] State rebuilt from ~/.local/state/foundry/journal.jsonl."]
    need = [u for u in snap["units"].values()
            if u["status"] == "in_progress" and u["kind"] != "foreman" and not cl.lead_live(snap, u)]
    if need:
        lines.append("Units in progress with no live lead session (re-launch each with `foundry unit resume <id>`, "
                     "or `foundry unit launch <id>` when it has no session yet): "
                     + ", ".join(f"{u['id']} ({u['title']})" for u in need))
    held = [u["id"] for u in snap["units"].values() if u.get("held")]
    if held:
        lines.append("Launches held (cap or PC pressure; `foundry unit launch` retries): " + ", ".join(held))
    ev = [e for e in snap["events"] if not e["consumed"]]
    if ev:
        lines.append("Unconsumed events (handle via the foundry-resume skill): "
                     + "; ".join(f"{e['unit']}: {e['summary']}" for e in ev[:20]))
    pend = [d for d in snap["decisions"].values() if d["answer"] is None]
    if pend:
        lines.append("Owner decisions still open: " + "; ".join(f"{d['key']} ({d.get('unit')})" for d in pend[:20]))
    asks = [a for a in snap["approvals"].values() if a["answer"] is None]
    if asks:
        lines.append("Permission requests waiting (foundry-approve skill): "
                     + "; ".join(f"{a['rid']} tier{a.get('tier')} {a.get('unit')}: {a.get('summary')}" for a in asks[:20]))
    import watch
    apps = watch.known_apps(snap)
    stuck = {u["id"]: why for u in snap["units"].values() for why in [watch.unwatched(snap, u, apps)] if why}
    waiting = [u["id"] for u in snap["units"].values() if u["status"] == "waiting_others" and u["id"] not in stuck]
    if waiting:
        lines.append("Waiting on others (watcher covers these): " + ", ".join(waiting))
    if stuck:
        lines.append("Waiting on nothing a watcher resolves (ask the lead for a `foundry wait add` it accepts, or "
                     "set the real status): " + "; ".join(f"{k} ({v})" for k, v in stuck.items()))
    running = [a for a in snap["agents"].values() if a["status"] == "running"]
    if running:
        lines.append("Subagents still running (a compaction does not stop them: wait for their result, do not "
                     "re-dispatch): " + "; ".join(f"{a.get('name') or a.get('type')} {a['agent_id']}" for a in running[:20]))
    lines.append("Re-arm the event waiter: `foundry events wait` in the background (foundry-resume skill).")
    return "\n".join(lines)


def subagent_start(inp):
    snap = cl.load()
    if not is_foundry_session(inp, snap):
        return
    aid = inp.get("agent_id")
    if aid in snap["agents"]:
        # A finished agent woken by SendMessage starts under its old id: same hand, same unit, no new spawn.
        cl.append("agent.resume", agent_id=aid, session=inp.get("session_id"))
        return
    cl.append("agent.start", agent_id=aid, type=inp.get("agent_type") or "", session=inp.get("session_id"))


def subagent_stop(inp):
    snap = cl.load()
    if inp.get("agent_id") in snap["agents"]:
        cl.append("agent.stop", agent_id=inp.get("agent_id"))


def enqueue_sync(uid):
    """Queue the unit's tracker/Obsidian sync; never blocks or fails the stop."""
    try:
        import sync
        sync.enqueue_for(uid)
    except Exception:
        log_decision(event="Stop", decision="sync-enqueue-error", reason=traceback.format_exc()[-500:])


def last_assistant_text(inp):
    """The text of the turn's last assistant message: the Stop input's own field, else the transcript's tail."""
    if inp.get("last_assistant_message"):
        return str(inp["last_assistant_message"])
    path = inp.get("transcript_path")
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 262144))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError):
        return ""
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        msg = rec.get("message") if isinstance(rec, dict) and rec.get("type") == "assistant" else None
        content = (msg or {}).get("content") if isinstance(msg, dict) else None
        texts = [c.get("text") or "" for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
        if any(t.strip() for t in texts):
            return "\n".join(texts)
    return ""


def card_check(inp, snap, caller):
    """Why the foreman's or a lead's closing message needs a decision card first, else None."""
    import decide
    if caller == roles.FOREMAN:
        rec = session_rec(inp, snap)
        return decide.uncarded_question(last_assistant_text(inp), snap, None, rec.get("last_start") or rec.get("started"))
    return decide.uncarded_question(last_assistant_text(inp), snap, unit_of_agent(inp, snap))


def stop(inp):
    """A lead session may not go idle before it has recorded where its unit stands, and no foundry session ends a
    turn on an owner question that has no decision card (once per stop: stop_hook_active lets the next one pass)."""
    snap = cl.load()
    if inp.get("agent_id") or inp.get("stop_hook_active") or not is_foundry_session(inp, snap):
        return
    caller = caller_of(inp, snap)
    try:
        ask = card_check(inp, snap, caller) if caller in (roles.FOREMAN, roles.LEAD, roles.BOSS) else None
    except Exception:
        log_decision(event="Stop", decision="card-check-error", reason=traceback.format_exc()[-500:])
        ask = None
    if ask:
        log_decision(event="Stop", decision="uncarded-question", reason=ask[:300])
    if caller not in (roles.LEAD, roles.BOSS):
        if ask:
            out({"decision": "block", "reason": f"[foundry] {ask}"})
        return
    uid = unit_of_agent(inp, snap)
    enqueue_sync(uid)
    u = snap["units"].get(uid)
    rec = session_rec(inp, snap)
    since = rec.get("last_start") or rec.get("started")  # this run's start: notes from before a resume do not count
    if not u or not since:
        return
    recorded = any(n["ts"] >= since for n in u["notes"]) or u["status"] in ("done", "waiting_owner")
    recorded = recorded or any(w["since"] >= since for w in u["waits"])
    recorded = recorded or ((u.get("handoff") or {}).get("ts") or "") >= since
    if not recorded:
        out({"decision": "block", "reason": (
            f"[foundry] Before you stop, record where unit {uid} stands so it can be resumed: "
            f"`foundry unit status {uid} done --note ...`, `foundry wait add {uid} --kind ... --ref ...` "
            f"for an external wait, or `foundry unit checkpoint {uid} --note ...`. Then stop."
            + (f" Also: {ask}" if ask else ""))})
    elif ask:
        out({"decision": "block", "reason": f"[foundry] {ask}"})


def permission_denied(inp):
    run_module_hook("permission_denied", inp)


def permission_request(inp):
    import approve
    approve.handle(inp)


HANDLERS = {
    "PreToolUse": pre_tool_use,
    "PostToolUse": post_tool_use,
    "PostToolUseFailure": post_tool_use_failure,
    "SessionStart": session_start,
    "SubagentStart": subagent_start,
    "SubagentStop": subagent_stop,
    "PreCompact": pre_compact,
    "SessionEnd": session_end,
    "Stop": stop,
    "PermissionRequest": permission_request,
    "PermissionDenied": permission_denied,
}


def main():
    event = sys.argv[1]
    inp = {}
    try:
        inp = json.load(sys.stdin)
        HANDLERS[event](inp)
    except Exception:
        try:
            log_decision(event=event, decision="error", reason=traceback.format_exc()[-1500:])
        except Exception:
            pass
        if event == "PreToolUse" and fail_closed(inp if isinstance(inp, dict) else {}):
            _EMITTED.clear()
            out({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "[foundry] Hook error; denied. Details in enforce.log."}})
    flush()
    return 0


def fail_closed(inp):
    """Without state: a foundry session, or a Slack send, is denied when its guard crashed; others fail open.
    A session known only from the journal is looked up in snapshot.json read raw (load() may be what crashed)."""
    if inp.get("tool_name") == "Write":
        return False  # the Write hook only warns about size: it never denies, not even when it crashes
    if (os.environ.get("FOUNDRY_ROLE") or os.environ.get("FOUNDRY_SESSION") == "1"
            or str(inp.get("agent_type") or "").startswith("foundry:")
            or sg.is_slack_send(str(inp.get("tool_name") or "")) or sg.is_slack_draft(str(inp.get("tool_name") or ""))):
        return True
    try:
        with open(cl.path("snapshot.json")) as f:
            sessions = json.load(f).get("foundry_sessions") or {}
        return inp.get("session_id") in sessions
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
