"""Lead sessions: the repo registry, worktrees, and `claude --bg` launch / resume / stop / status."""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import prefs  # noqa: E402
import roles  # noqa: E402
import trust as trust_mod  # noqa: E402

EXIT_HELD = 6
EXIT_UNTRUSTED = 7
EXIT_NO_MCP = 8
LIVE = ("launching", "running", "idle")
ROLE_NAME = {"foundry:line-lead": "line-lead", "foundry:shop-boss": "shop-boss"}
BG_RE = re.compile(r"backgrounded\s*[·:-]\s*(\S+)")
STATE_OF = {"busy": "running", "running": "running", "idle": "idle", "waiting": "idle",
            "done": "done", "completed": "done", "exited": "done", "stopped": "stopped"}


def _op_adopted(s, r, ts):
    u = (s.get("units") or {}).get(r.get("id"))
    if u is not None:
        u["adopted"] = {"session_id": r.get("session_id"), "name": r.get("name"), "bg_id": r.get("bg_id"),
                        "cwd": r.get("cwd"), "ts": ts}


def _op_foreman_current(s, r, ts):
    s["foreman_current"] = {"session": r.get("session"), "source": r.get("source"), "ts": ts}
    if r.get("session"):
        s["foreman_sessions"][r["session"]] = ts


def _op_foreman_prune(s, r, ts):
    s["foreman_prune"] = {"stale": r.get("stale") or [], "stopped": r.get("stopped") or [], "by": r.get("by"), "ts": ts,
                          "stop_failed": r.get("stop_failed") or {}}
    for x in r.get("stopped") or []:  # claude said it no longer exists: a listing that still shows it is not retried
        if x.get("note") == GONE_NOTE:
            s.setdefault("foreman_gone", {})[x["session"]] = ts


OPS = {"unit.adopted": _op_adopted, "foreman.current": _op_foreman_current, "foreman.prune": _op_foreman_prune}
STATE = {"foreman_current": None, "foreman_sessions": {}, "foreman_prune": None, "foreman_gone": {}}
UNIT_FIELDS = {"adopted": None}
FOREMAN_NAME = "foreman"  # the `claude --bg -n` name of foundry's foreman start line (firstrun uses this one)


def board(snap, m):
    """stale foremen: N until a prune finds them gone (the last foreman.prune record; rendering never lists), and
    how many of them a `claude stop` failed for (backing off)."""
    pr = snap.get("foreman_prune") or {}
    m["stale_foremen"] = len(pr.get("stale") or [])
    m["stale_foremen_failed"] = sum(1 for x in pr.get("stale") or [] if x.get("session") in (pr.get("stop_failed") or {}))


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def fail(msg, code=2):
    print(msg, file=sys.stderr)
    return code


def claude_bin():
    return shlex.split(os.environ.get("FOUNDRY_CLAUDE") or "claude")


def git_bin():
    return shlex.split(os.environ.get("FOUNDRY_GIT") or "git")


def run(argv, cwd=None, timeout=60):
    """(returncode, stdout, stderr); a missing binary or a timeout is a failure, never an exception."""
    try:
        r = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


def git(*args, timeout=120):
    return run([*git_bin(), *args], timeout=timeout)


# ---------- repo registry ----------

# Keys a repo's policy lives in: folding a duplicate never changes one (an owner decision, not a dedupe's).
POLICY_KEYS = ("merge", "owner_only", "argo_app", "release", "release_tag", "tracker", "default_branch")


def _same_clone(a, b):
    pa, pb = (a or {}).get("path"), (b or {}).get("path")
    return bool(pa and pb) and os.path.realpath(os.path.expanduser(pa)) == os.path.realpath(os.path.expanduser(pb))


def dedupe_repos(raw):
    """One entry per clone: an `owner/name` key registered beside `name` for the same checkout folds into `name`
    as its slug. When the two disagree on a policy key, both stay: which one wins is the owner's call."""
    out = dict(raw)
    for k, v in raw.items():
        short = k.rsplit("/", 1)[-1]
        s = out.get(short)
        if "/" not in k or not isinstance(v, dict) or not isinstance(s, dict) or not _same_clone(s, v):
            continue
        if s.get("slug") not in (None, k) or any(x in v and v.get(x) != s.get(x) for x in POLICY_KEYS):
            continue
        out[short] = s | {"slug": k}
        del out[k]
    return out


def load_repos():
    try:
        with open(cl.path("repos.json")) as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return dedupe_repos(raw) if isinstance(raw, dict) else {}


def repo_name(ref, repos=None):
    """The registry key for a unit's repo: the key itself, or the entry whose slug it is."""
    repos = load_repos() if repos is None else repos
    if not ref or ref in repos:
        return ref
    return next((k for k, v in repos.items() if isinstance(v, dict) and v.get("slug") == ref), ref)


def cmd_repo(a):
    if a.sub == "list":
        return emit(load_repos()) or 0
    import prefs
    why = prefs.tracker_refusal(a.tracker)
    if why:
        return fail(why)
    main = os.path.abspath(os.path.expanduser(a.path))
    if git("-C", main, "rev-parse", "--git-dir")[0] != 0:
        return fail(f"{main} is not a git checkout")
    remote = a.remote
    if not remote:
        names = git("-C", main, "remote")[1].split()
        remote = "origin" if "origin" in names or not names else names[0]
    branch = a.default_branch
    if not branch:
        rc, out, _ = git("-C", main, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD")
        branch = out.strip().removeprefix(remote + "/") if rc == 0 and out.strip() else "main"
    entry = {"path": main, "default_branch": branch, "remote": remote}
    with cl.locked():
        repos = load_repos()
        name = repo_name(a.name, repos)
        if name not in repos and "/" in name and _same_clone(repos.get(name.rsplit("/", 1)[-1]), entry):
            name = name.rsplit("/", 1)[-1]  # `owner/name` for a clone already registered as `name`: its slug
            entry["slug"] = a.name
        entry["tracker"] = a.tracker or (repos.get(name) or {}).get("tracker") or "none"
        repos[name] = (repos.get(name) or {}) | entry  # re-adding keeps the entry's policy keys
        repos = dedupe_repos(repos)
        cl.write_atomic(cl.path("repos.json"), json.dumps(repos, ensure_ascii=False, indent=1))
    return emit({name: repos[name]}) or 0


# ---------- gates, trust, worktrees ----------

def held_reason(snap, cfg):
    live = cl.slot_holders(snap)  # idle leads that only wait do not hold a slot
    if len(live) >= cfg["max_concurrent"]:
        return f"cap: {len(live)} working sessions >= max_concurrent {cfg['max_concurrent']}"
    if cl.pressure_room(snap) == 0:  # the same rule as `foundry next`: warn allows one launch, danger none
        p = snap.get("pressure") or {}
        return f"pressure {cl.pressure_level(snap)}: " + "; ".join(p.get("reasons") or [])
    return None


def module_block(snap, uid):
    """The first feature module's refusal to launch uid (graph: halt, blockers, status), or None."""
    return next((r for m in cl.modules() if hasattr(m, "launch_block") for r in [m.launch_block(snap, uid)] if r), None)


def hold(uid, cfg, name=None, role=None):
    """Record and return a hold when a feature module, the cap or PC pressure forbids another session, else None.

    With `name`, a free slot is reserved in the same transaction (session state `launching`), so two
    concurrent launches cannot both pass the cap check. `release()` undoes a reservation that never started."""
    def decide(snap):
        block = module_block(snap, uid)
        if block:
            return block, [("launch.held", {"id": uid, "reason": block})]
        if name and cl.session_live(snap["sessions"].get(name)):
            return f"session {name} is already launching or live", []
        reason = held_reason(snap, cfg)
        if reason:
            return reason, [("launch.held", {"id": uid, "reason": reason})]
        return None, ([("session.reserve", {"name": name, "unit": uid, "role": role})] if name else [])
    return cl.transact(decide)[0]


def release(name, why):
    cl.append("session.release", name=name, note=f"launch failed: {why}"[:300])


UNTRUSTED_RE = re.compile(r"Workspace not trusted", re.I)


def required_mcp(u, p, repos=None):
    """[(stage, [server globs])] the unit's stages need (prefs required_mcp): an entry applies when the unit's
    tracker equals its `tracker`, or the unit's repo (name or slug) matches one of its `repos` globs."""
    import fnmatch
    repos = repos if repos is not None else load_repos()
    name = repo_name(u.get("repo"), repos) or u.get("repo") or ""
    r = repos.get(name) or {}
    names = {n.lower() for n in (name, r.get("slug") or "") if n}
    tracker = u.get("tracker") or r.get("tracker")
    out = []
    for e in p.get("required_mcp") or []:
        if not isinstance(e, dict) or not e.get("servers"):
            continue
        if (e.get("tracker") and e["tracker"] == tracker) or any(
                fnmatch.fnmatch(n, g) for n in names for g in e.get("repos") or []):
            out.append((e.get("stage") or "?", list(e["servers"])))
    return out


def _mcp_blocks(p):
    """({name: def} usable everywhere, {name: def} scoped to some other project) from the configured config files."""
    top, scoped = {}, {}
    for path in (p.get("preflight") or {}).get("mcp_config_paths") or []:
        try:
            with open(os.path.expanduser(path)) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        top.update(data.get("mcpServers") or {} if isinstance(data.get("mcpServers"), dict) else {})
        for v in (data.get("projects") or {}).values():
            if isinstance(v, dict) and isinstance(v.get("mcpServers"), dict):
                for k, d in v["mcpServers"].items():
                    scoped.setdefault(k, d)
    return top, scoped


MCP_LIST_TTL_S = 600  # `claude mcp list` health-checks every server (up to 90 s): one read serves a burst of launches


def _connected_cached(p):
    path = cl.path("mcp-connected.json")
    now = cl.now()
    try:
        with open(path) as f:
            c = json.load(f)
        t = cl.parse_ts(c.get("ts"))
        if t and (now - t).total_seconds() < MCP_LIST_TTL_S and c.get("names"):
            return set(c["names"])
    except (OSError, ValueError, AttributeError):
        pass
    import factory
    names = factory._mcp_connected(p)
    if names:  # an empty answer (list failed) is never cached
        cl.write_atomic(path, json.dumps({"ts": cl.iso(now), "names": sorted(names)}))
    return names


def mcp_plan(u, p):
    """(servers to add with --mcp-config {name: def}, [(stage, globs) nothing provides], [(stage, server) found]).

    User-scope servers reach every session. A server defined only under another project's scope does not reach a
    new worktree, so it is passed with --mcp-config. Plugin servers and claude.ai connectors are only visible to
    `claude mcp list` (connected ones), read once and only when the config files do not answer."""
    import fnmatch
    need = required_mcp(u, p)
    if not need:
        return {}, [], []
    fake = os.environ.get("FOUNDRY_MCP_FAKE")  # tests: {"top": {...}, "scoped": {...}, "connected": [...]}
    if fake:
        fake = json.loads(fake)
        top, scoped, connected = fake.get("top") or {}, fake.get("scoped") or {}, set(fake.get("connected") or [])
    else:
        (top, scoped), connected = _mcp_blocks(p), None
    add, missing, found = {}, [], []
    for stage, globs in need:
        hit = [n for n in top if any(fnmatch.fnmatch(n, g) for g in globs)]
        if hit:
            found.append((stage, hit[0]))
            continue
        if connected is None:
            connected = _connected_cached(p)
        hit = [n for n in connected if any(fnmatch.fnmatch(n, g) for g in globs)]
        if hit:
            found.append((stage, hit[0]))
            continue
        hit = [n for n in scoped if any(fnmatch.fnmatch(n, g) for g in globs)]
        if hit:
            add[hit[0]] = scoped[hit[0]]
            found.append((stage, hit[0] + " (added with --mcp-config)"))
            continue
        missing.append((stage, globs))
    return add, missing, found


def escalate_no_mcp(uid, missing):
    what = "; ".join(f"{stage} needs {' or '.join(g)}" for stage, g in missing)
    cl.append_many([("launch.held", {"id": uid, "reason": f"missing MCP: {what}"}),
                    ("owner.todo", {"kind": "mcp", "target": uid, "tid": f"mcp:{uid}",
                                    "text": f"{uid} cannot launch: {what}, and no configured or connected MCP "
                                            "server matches. Connect it (`claude mcp add`, the plugin, or the "
                                            f"claude.ai connector), then `foundry unit launch {uid}`."})])
    return what


def escalate_untrusted(uid, path, text):
    """The session refused to start: one owner to-do (deduped while open), never a retry loop."""
    cl.append_many([("trust.failed", {"unit": uid, "path": path, "detail": text[-300:]}),
                    ("owner.todo", {"kind": "trust", "target": path, "tid": f"trust:{path}",
                                    "text": f"Claude Code did not accept workspace trust for {path}; "
                                            f"open it once with `claude` there, or check auto_trust_worktrees"})])


def trust_status(path, git_root=None):
    """'path' | 'parent' | 'none': whether Claude Code's workspace trust covers path. Read-only.

    Claude Code (2.1.326) walks up from the path but stops at the nearest directory holding `.git`: a
    trusted parent above a repository or a worktree does not count. `git_root` forces that bound for a
    worktree that does not exist yet (its root is the path itself)."""
    import trust
    try:
        with open(trust.config_file()) as fh:
            projects = json.load(fh).get("projects") or {}
    except (OSError, json.JSONDecodeError, AttributeError):
        return "none"
    p = os.path.abspath(path)
    if (projects.get(p) or {}).get("hasTrustDialogAccepted"):
        return "path"
    if git_root or os.path.exists(os.path.join(p, ".git")):
        return "none"
    while True:
        parent = os.path.dirname(p)
        if parent == p:
            return "none"
        p = parent
        if (projects.get(p) or {}).get("hasTrustDialogAccepted"):
            return "parent"
        if os.path.exists(os.path.join(p, ".git")):
            return "none"


def plan_worktree(snap, u, cfg, branch=None, cwd=None):
    """(worktree dict, error): where the unit's session runs, holding one unit = one worktree = one branch."""
    uid, cur = u["id"], u.get("worktree") or {}
    if cwd:
        wt = {"repo": u.get("repo"), "path": os.path.abspath(os.path.expanduser(cwd)), "branch": None}
        if not os.path.isdir(wt["path"]):
            return None, f"--cwd {wt['path']} is not a directory"
    else:
        repos = load_repos()
        repo = repo_name(u.get("repo"), repos)
        if not repo:
            return None, (f"refused: unit {uid} has no repo, so it has no worktree to run in. Record one with "
                          f"`foundry unit set {uid} --repo <registry name>` (see `foundry repo list`), or launch it "
                          "with --cwd <dir> when it truly works outside a repository")
        if repo not in repos:
            return None, (f"unit {uid} has repo {repo!r}, which is not in the registry: run "
                          f"`foundry repo add {repo or '<name>'} --path <main checkout>`, or pass --cwd <dir>")
        want = branch or f"foundry/{uid}"
        held = checked_out_at(repos[repo].get("path"), want)
        if cur.get("path") and repo_name(cur.get("repo"), repos) == repo and (not branch or branch == cur.get("branch")):
            wt = dict(cur, repo=repo)
        elif held:
            # made under an older layout (worktree_root/<owner>/<repo>/<uid>): reuse it, never a second checkout
            root = os.path.realpath(os.path.expanduser(cfg["worktree_root"]))
            if not os.path.realpath(held).startswith(root + os.sep):
                return None, f"branch {want} is checked out at {held}, outside worktree_root {root}: refused"
            wt, cur = {"repo": repo, "path": held, "branch": want}, {**cur, "path": held}
        else:
            wt = {"repo": repo, "path": os.path.join(os.path.expanduser(cfg["worktree_root"]), repo, uid),
                  "branch": want}
    for v in snap["units"].values():
        w = v.get("worktree") or {}
        if v["id"] == uid or not w:
            continue
        if w.get("path") == wt["path"]:
            return None, f"worktree {wt['path']} already belongs to unit {v['id']}"
        if wt["branch"] and w.get("branch") == wt["branch"] and w.get("repo") == wt["repo"]:
            return None, f"branch {wt['branch']} already belongs to unit {v['id']}"
    if os.path.exists(wt["path"]) and not cwd and cur.get("path") != wt["path"]:
        return None, f"{wt['path']} exists but is not this unit's worktree; remove it or pass --cwd"
    return wt, None


def checked_out_at(main, branch):
    """The worktree path where `branch` is checked out in the repo at `main`, or None."""
    if not main or not branch:
        return None
    rc, out, _ = git("-C", main, "worktree", "list", "--porcelain")
    path = None
    for line in out.splitlines() if rc == 0 else []:
        if line.startswith("worktree "):
            path = line[9:]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def make_worktree(wt):
    """Create the worktree when missing. Returns an error text or None."""
    if os.path.exists(wt["path"]) or not wt["branch"]:
        return None
    r = load_repos()[wt["repo"]]
    main, remote, base = r["path"], r["remote"], r["default_branch"]
    start = base  # a repo with no such remote (local only) branches from its local default branch
    if git("-C", main, "remote", "get-url", remote)[0] == 0:
        rc, _, err = git("-C", main, "fetch", remote, base)
        if rc:
            return f"git fetch {remote} {base} failed: {err.strip()}"
        start = f"{remote}/{base}"
    os.makedirs(os.path.dirname(wt["path"]), exist_ok=True)
    if git("-C", main, "rev-parse", "--verify", "--quiet", f"refs/heads/{wt['branch']}")[0] == 0:
        rc, _, err = git("-C", main, "worktree", "add", wt["path"], wt["branch"])
    else:
        rc, _, err = git("-C", main, "worktree", "add", "-b", wt["branch"], wt["path"], start)
    return f"git worktree add failed: {err.strip()}" if rc else None


# ---------- claude ----------

def goal_prompt(uid, turns):
    return (f"/goal Unit {uid} is recorded in foundry state as done, waiting_others or waiting_owner "
            f"(check with: foundry unit show {uid}), and its report is recorded with foundry unit report {uid}; "
            f"or stop after {turns} turns.")


# The model gateway / auth variables a --bg session needs and never inherits from the shell. `foundry up`
# records their NAMES only (gateway-env.json); every spawn re-reads the values from this process's environment
# or the user's settings `env`, so no value is ever written by foundry.
GATEWAY_ENV_RE = re.compile(r"^(ANTHROPIC_[A-Z0-9_]+|CLAUDE_CODE_USE_[A-Z0-9_]+|CLAUDE_CODE_SKIP_[A-Z0-9_]+_AUTH|"
                            r"AWS_(PROFILE|REGION|DEFAULT_REGION)|CLOUD_ML_REGION|HTTPS?_PROXY|NO_PROXY|"
                            r"NODE_EXTRA_CA_CERTS)$")
GATEWAY_FILE = "gateway-env.json"


def gateway_names():
    try:
        with open(cl.path(GATEWAY_FILE)) as f:
            names = json.load(f).get("names") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [n for n in names if isinstance(n, str) and GATEWAY_ENV_RE.match(n)]


def record_gateway_env(env=None):
    """Record the gateway variable names set in `env` (the foreman's environment), keeping earlier ones."""
    env = os.environ if env is None else env
    names = sorted(set(gateway_names()) | {n for n in env if GATEWAY_ENV_RE.match(n)})
    if names:
        cl.write_atomic(cl.path(GATEWAY_FILE), json.dumps({"names": names, "ts": cl.iso()}))
    return names


def _settings_env():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    try:
        with open(os.path.join(base, "settings.json")) as f:
            env = json.load(f).get("env")
    except (OSError, ValueError, AttributeError):
        return {}
    return env if isinstance(env, dict) else {}


def gateway_env():
    """{name: value} for every recorded name with a value now, and the names with none."""
    names = gateway_names()
    if not names:
        return {}, []
    user = _settings_env()
    vals = {n: os.environ.get(n) or user.get(n) for n in names}
    return {n: str(v) for n, v in vals.items() if v}, [n for n, v in vals.items() if not v]


def settings_json(cfg, role, uid):
    extra = cfg.get("extra_settings") if isinstance(cfg.get("extra_settings"), dict) else {}
    env = {**gateway_env()[0], **(extra.get("env") or {}), "FOUNDRY_ROLE": role, "FOUNDRY_UNIT": uid}
    if os.environ.get("FOUNDRY_STATE_DIR"):
        # The shell environment never reaches a --bg session; its hooks must still find this state.
        env["FOUNDRY_STATE_DIR"] = os.environ["FOUNDRY_STATE_DIR"]
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        # The lead's hooks (trust, transcripts) must read the config the session itself runs under.
        env["CLAUDE_CONFIG_DIR"] = os.environ["CLAUDE_CONFIG_DIR"]
    perms = dict(extra.get("permissions") or {})
    # Auto mode alone is no gate: owner-only acts must reach the PermissionRequest tiers, and only an
    # `ask` rule sends them there.
    import mcpmap
    rules = mcpmap.map_tools(cfg["lead_ask_rules"], mcpmap.mapping())
    perms["ask"] = [*(perms.get("ask") or []), *[r for r in rules if r not in (perms.get("ask") or [])]]
    out = {**extra, "env": env, "permissions": perms}
    if cfg.get("advisor_model"):
        out["advisorModel"] = cfg["advisor_model"]
    return json.dumps(out)


def mapped_agent(lead_type):
    """`--agents {lead_type: def}` with this install's MCP server names, or [] when the mapping changes nothing
    (mcpmap.agent_flag: a --bg `--agent` session would otherwise keep the file's generic names)."""
    import mcpmap
    flag = mcpmap.agent_flag(lead_type)
    return ["--agents", json.dumps(flag)] if flag else []


def launch_argv(cfg, name, lead_type, model, effort, uid, turns):
    argv = [*claude_bin(), "--bg", "-n", name, *mapped_agent(lead_type), "--agent", lead_type, "--model", model,
            "--effort", effort, "--permission-mode", cfg["permission_mode"],
            "--settings", settings_json(cfg, ROLE_NAME[lead_type], uid)]
    for d in os.environ.get("FOUNDRY_PLUGIN_DIRS", "").split(os.pathsep):
        if d:
            argv += ["--plugin-dir", d]
    return argv + [goal_prompt(uid, turns)]


def list_agents(all_=False):
    """`claude agents --json` entries, or None when the listing failed (never read as 'all gone')."""
    rc, out, _ = run([*claude_bin(), "agents", "--json", *(["--all"] if all_ else [])])
    if rc:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        data = data.get("agents") or data.get("sessions") or []
    return [e for e in data if isinstance(e, dict)]


def entry_ids(e):
    return {str(e[k]) for k in ("name", "sessionId", "id", "bgId", "shortId") if e.get(k)}


def find_entry(entries, sess):
    keys = {str(sess[k]) for k in ("name", "bg_id", "session_id") if sess.get(k)}
    named = [e for e in entries if e.get("name") == sess.get("name")]
    return (named or [e for e in entries if entry_ids(e) & keys] or [None])[-1]


def bind(name, bg_id):
    entries = list_agents() or []
    e = find_entry(entries, {"name": name, "bg_id": bg_id})
    if e and e.get("sessionId"):
        cl.append("session.bind", name=name, session_id=e["sessionId"])
        return e["sessionId"]
    return None


def start_bg(argv, cwd):
    rc, out, err = run(argv, cwd=cwd)
    m = BG_RE.search(out) or BG_RE.search(err)
    if rc or not m:
        sys.stderr.write(f"claude --bg failed (exit {rc}):\n{err}{out}")
        return None, out + err
    return m.group(1), out + err


# ---------- commands ----------

def cmd_launch(a):
    snap, cfg = cl.load(), cl.load_config()
    u = snap["units"].get(a.id)
    if not u or u["kind"] not in roles.LEAD_TYPE:
        return fail(cl.unknown_unit(a.id, snap))
    if u["status"] in ("done", "cancelled", "parked"):
        import graph
        return fail(f"refused: unit {a.id} is {u['status']}; launch takes {', '.join(graph.LAUNCHABLE)}: "
                    f"{graph.launch_path(a.id, u['status'])}")
    block = module_block(snap, a.id)
    if block and not a.dry_run:
        cl.append("launch.held", id=a.id, reason=block)
        emit({"held": {"id": a.id, "reason": block}})
        return EXIT_HELD
    lead_type = roles.LEAD_TYPE[u["kind"]]
    name = roles.session_name(lead_type, a.id)
    if cl.lead_live(snap, u) or cl.session_live(snap["sessions"].get(name)):
        return fail(f"unit {a.id} already has a live session {name}: resume or message it", 1)
    wt, err = plan_worktree(snap, u, cfg, a.branch, a.cwd)
    if err:
        return fail(err)
    prefix = "lead" if lead_type == "foundry:line-lead" else "boss"
    model = a.model or cfg[f"{prefix}_model"]
    effort = a.effort or cfg[f"{prefix}_effort"]
    argv = launch_argv(cfg, name, lead_type, model, effort, a.id, a.max_turns or cfg["goal_max_turns"])
    trust = trust_status(wt["path"], git_root=bool(wt["branch"]))
    mcp_add, mcp_missing, mcp_found = mcp_plan(u, prefs.load())
    if mcp_add:
        argv[-1:-1] = ["--mcp-config", json.dumps({"mcpServers": mcp_add})]
    mcp = {"required": [f"{s}: {n}" for s, n in mcp_found], "added": sorted(mcp_add),
           "missing": [f"{s}: {' or '.join(g)}" for s, g in mcp_missing]}
    if a.dry_run:
        return emit({"dry_run": True, "argv": argv, "cwd": wt["path"], "worktree": wt, "trust": trust, "mcp": mcp,
                     "would_hold": held_reason(snap, cfg)}) or 0
    if mcp_missing:
        what = escalate_no_mcp(a.id, mcp_missing)
        return fail(f"refused: unit {a.id} needs MCP servers this machine does not provide ({what}); recorded as "
                    "an owner to-do. Launch it after the server is connected.", EXIT_NO_MCP)
    reason = hold(a.id, cfg, name, ROLE_NAME[lead_type])
    if reason:
        emit({"held": {"id": a.id, "reason": reason}})
        return EXIT_HELD
    err = make_worktree(wt)
    if err:
        release(name, err)
        return fail(err, 1)
    if wt != u.get("worktree"):
        cl.append("unit.worktree", id=a.id, **wt)
    if trust == "none" and wt["branch"] and prefs.load().get("auto_trust_worktrees", True):
        # Only worktrees foundry made for a registered repo; the key is removed with the worktree.
        if trust_mod.register(wt["path"], a.id) in ("registered", "present"):
            trust = "registered"
    if trust == "none":
        # A --bg session in an untrusted directory exits at the trust check; nobody answers a dialog there.
        release(name, "untrusted")
        where = "this git worktree (trust is per repository root)" if wt["branch"] else "this directory or a parent"
        escalate_untrusted(a.id, wt["path"], "not trusted before launch")
        return fail(f"workspace trust does not cover {where}: {wt['path']} (auto trust applies only to worktrees "
                    f"foundry creates for a registered repo). Recorded as an owner to-do.", EXIT_UNTRUSTED)
    bg_id, text = start_bg(argv, wt["path"])
    if not bg_id:
        release(name, "claude --bg did not start")
        if UNTRUSTED_RE.search(text or ""):
            escalate_untrusted(a.id, wt["path"], text)
            return EXIT_UNTRUSTED
        return 1
    cl.append("session.launch", name=name, unit=a.id, role=ROLE_NAME[lead_type], bg_id=bg_id, cwd=wt["path"], model=model,
              agents="--agents" in argv)
    sid = bind(name, bg_id)
    out = {"launched": name, "bg_id": bg_id, "session_id": sid, "cwd": wt["path"], "branch": wt["branch"],
           "attach": f"claude attach {name}", "trust": trust, "mcp": mcp}
    return emit(out) or 0


def resume(uid):
    """Resume a unit's stopped lead session. Returns (exit code, payload); payload has `error` on failure."""
    snap, cfg = cl.load(), cl.load_config()
    u = snap["units"].get(uid)
    if not u:
        return 2, {"error": cl.unknown_unit(uid, snap)}
    sess = snap["sessions"].get(u.get("session") or "")
    if not sess or not sess.get("session_id"):
        return 2, {"error": f"unit {uid} has no session with a recorded session id; launch it instead"}
    if cl.session_live(sess):
        return 1, {"error": f"unit {uid} already has a live session {sess['name']}: message it"}
    cwd = (u.get("worktree") or {}).get("path") or sess.get("cwd")
    if not cwd or not os.path.isdir(cwd):
        return 2, {"error": f"worktree {cwd!r} is missing; launch the unit again instead"}
    lead_type = roles.LEAD_TYPE.get(u["kind"]) or "foundry:line-lead"
    prefix = "lead" if lead_type == "foundry:line-lead" else "boss"
    mcp_add, mcp_missing, _ = mcp_plan(u, prefs.load())
    if mcp_missing:
        what = escalate_no_mcp(uid, mcp_missing)
        return EXIT_NO_MCP, {"error": f"refused: unit {uid} needs MCP servers this machine does not provide ({what}); "
                                      "recorded as an owner to-do"}
    reason = hold(uid, cfg, sess["name"], ROLE_NAME[lead_type])
    if reason:
        return EXIT_HELD, {"held": {"id": uid, "reason": reason}}
    # An in-place resume re-applies the original launch flags (measured 2.1.329: passing flags starts a copy
    # instead). A session launched without the MCP-mapped --agents (before 0.1.81, or not recorded) would come back
    # with the file's generic MCP names and no MCP tool, so it takes the copy path below, which passes them.
    stale = bool(mapped_agent(lead_type)) and not sess.get("agents")
    bg_id, text = (None, "") if stale else resume_in_place(sess, uid, cwd, cfg)
    if bg_id:
        cl.append("session.launch", name=sess["name"], unit=uid, role=sess.get("role"), bg_id=bg_id, cwd=cwd,
                  model=sess.get("model"), resume=True, session_id=sess["session_id"], note="resumed in place",
                  agents=sess.get("agents"))
        sid = bind(sess["name"], bg_id) or sess["session_id"]
        return 0, {"resumed": sess["name"], "bg_id": bg_id, "session_id": sid, "cwd": cwd, "in_place": True,
                   "attach": f"claude attach {sess['name']}"}
    # Fallback: with the launch flags claude starts a copy that carries the whole conversation under a new id
    # (measured 2.1.326); the copy is what we record.
    argv = launch_argv(cfg, sess["name"], lead_type, sess.get("model") or cfg[f"{prefix}_model"], cfg[f"{prefix}_effort"],
                       uid, cfg["goal_max_turns"])
    argv[argv.index("--bg") + 1:argv.index("--bg") + 1] = ["--resume", sess["session_id"]]
    if mcp_add:
        argv[-1:-1] = ["--mcp-config", json.dumps({"mcpServers": mcp_add})]
    bg_id, text = start_bg(argv, cwd)
    if not bg_id:
        release(sess["name"], "claude --bg --resume did not start")
        return 1, {"error": f"claude --bg --resume did not start: {(text or '').strip()[-200:]}"}
    cl.append("session.launch", name=sess["name"], unit=uid, role=sess.get("role"), bg_id=bg_id, cwd=cwd,
              model=sess.get("model"), resume=True, session_id=sess["session_id"], agents="--agents" in argv,
              **({"note": "resumed as a copy with the MCP-mapped agent (launched without it)"} if stale else {}))
    sid = bind(sess["name"], bg_id) or sess["session_id"]
    out = {"resumed": sess["name"], "bg_id": bg_id, "session_id": sid, "cwd": cwd, "attach": f"claude attach {sess['name']}"}
    if sid != sess["session_id"]:
        out["note"] = "resumed as a copy carrying the conversation; the new session id is recorded"
    pruned = prune_copies(sess["name"], cwd, {bg_id, sid})
    if pruned:
        out["pruned"] = pruned
    return 0, out


def resume_in_place(sess, uid, cwd, cfg):
    """Continue the recorded --bg session under its own id, so finished copies do not pile up in `claude agents`.

    Measured on 2.1.328: once the old process is stopped, a bare `claude --bg --resume <session id> <prompt>`
    continues under the same id and re-applies the original launch's flags and env (FOUNDRY_ROLE/UNIT,
    permission mode). While the old process is still alive it starts a copy without that env instead. Returns
    (bg id, output) when the same id came back; on a copy, stops and removes it and returns (None, output)."""
    old = sess.get("bg_id")
    if not old:
        return None, ""
    entry = next((e for e in list_agents(all_=True) or [] if old in entry_ids(e)), None)
    if entry is None:
        return None, ""
    if classify(entry)[0] != "stopped" and run([*claude_bin(), "stop", old])[0]:
        return None, ""
    bg_id, text = start_bg([*claude_bin(), "--bg", "--resume", sess["session_id"], goal_prompt(uid, cfg["goal_max_turns"])],
                           cwd)
    if bg_id == old:
        return bg_id, text
    if bg_id:
        run([*claude_bin(), "stop", bg_id])
        run([*claude_bin(), "rm", bg_id])
    return None, text


def prune_copies(name, cwd, keep):
    """Remove the unit's earlier, finished --bg entries once its resumed copy is confirmed running. Only entries
    with the lead's name AND the unit's worktree as cwd (foundry's), state done/stopped (never working or waiting
    for input), no worktree of claude's own, and not the copy just started. Returns the removed ids."""
    entries = list_agents(all_=True) or []
    keep = {str(k) for k in keep if k}
    cur = [e for e in entries if entry_ids(e) & keep]
    if not cur or classify(cur[-1])[0] not in ("running", "idle"):
        return []
    here = os.path.realpath(cwd)
    removed = []
    for e in entries:
        if e.get("name") != name or entry_ids(e) & keep or os.path.realpath(e.get("cwd") or "/nonexistent") != here:
            continue
        if e.get("worktree") or e.get("worktreePath") or classify(e)[0] not in ("done", "stopped"):
            continue
        rid = e.get("id") or e.get("bgId") or e.get("shortId")
        if rid and run([*claude_bin(), "rm", str(rid)])[0] == 0:
            removed.append(str(rid))
    if removed:
        cl.append("session.pruned", name=name, ids=removed)
    return removed


def cmd_resume(a):
    rc, out = resume(a.id)
    if "error" in out:
        return fail(out["error"], rc)
    emit(out)
    return rc


def relaunch(uid, note):
    """Stop a stalled lead, queue `note` for it, and resume it. Returns the resume payload; raises on failure."""
    u = cl.load()["units"].get(uid)
    if not u or not u.get("session"):
        raise RuntimeError(f"unit {uid} has no session")
    stopped = stop_session(u["session"], f"relaunch: {note}"[:300])
    if "error" in stopped:
        raise RuntimeError(stopped["error"])
    cl.append("inbox.add", id=uid, iid=cl.new_id("in", uid, "relaunch", note), **{"from": "foundry"},
              text=f"[foundry] relaunched after a stall: {note}.")
    rc, out = resume(uid)
    if rc or "error" in out or "held" in out:
        raise RuntimeError(out.get("error") or f"held: {(out.get('held') or {}).get('reason')}")
    return out


PARK_NOTE = "parked: "
IDLE_WAKE = "owner approval answered (idle lead)"


def wake_source(snap, u):
    """What will clear a waiting unit's wait without its lead: open waits the github stream polls, or open
    decision cards. None when nothing watched would ever wake it (then it is not parked)."""
    if u.get("waits"):
        return "waits " + ", ".join(f"{w['kind']} {w.get('ref') or ''}".strip() for w in u["waits"])
    cards = [k for k, d in (snap.get("decisions") or {}).items() if d.get("unit") == u["id"] and d.get("answer") is None]
    return ("decision " + ", ".join(cards)) if cards else None


def _pending_for(snap, uid):
    u = snap["units"][uid]
    inbox = [i for i in u.get("inbox") or [] if not i.get("delivered")]
    events = [e for e in snap.get("events") or [] if e.get("unit") == uid and not e.get("consumed")]
    return len(inbox) + len(events)


def parkable(snap):
    """[(unit, session name, wake source)]: idle leads whose unit only waits on something watched, with nothing
    queued for them to read."""
    out = []
    for name, sess in snap["sessions"].items():
        u = snap["units"].get(sess.get("unit") or "")
        if (not u or u.get("session") != name or sess.get("state") != "idle" or u["status"] not in cl.WAITING
                or not cl.session_live(sess) or not sess.get("session_id") or _pending_for(snap, u["id"])):
            continue
        src = wake_source(snap, u)
        if src:
            out.append((u["id"], name, src))
    return out


def park(uid, name, src):
    """Hand over and stop a waiting lead so its slot is free; the sessions stream resumes it (unpark)."""
    cl.append("handoff.write", id=uid, next=f"Parked by foundry while waiting ({src}). On resume: read the events "
                                           "and inbox that woke you, then continue the unit.")
    return stop_session(name, (PARK_NOTE + src)[:300])


# Inbox lines that wake an ended or idle lead: an answered owner approval (approve.wake_records), a release unit
# whose done gate passed (release.gate_records).
WAKE_IIDS = {"apr-": "owner approval answered", "rel-": "release done gate passed",
             "cfl-": "PR conflicts with its base"}


def approval_wake(u):
    """Why an undelivered inbox line should wake this unit's lead now, else None."""
    for i in u.get("inbox") or []:
        if not i.get("delivered"):
            why = next((w for pre, w in WAKE_IIDS.items() if str(i.get("iid") or "").startswith(pre)), None)
            if why:
                return why
    return None


def unparkable(snap):
    """[(unit, why)]: parked leads whose wait cleared — the unit left waiting_*, something arrived for it, or
    nothing watched holds it any more — and any ended lead whose owner approval was just answered."""
    out = []
    for name, sess in snap["sessions"].items():
        u = snap["units"].get(sess.get("unit") or "")
        if not u or u.get("session") != name or u["status"] in ("done", "cancelled", "parked"):
            continue
        if sess.get("state") in ("stopped", "done", "lost") and approval_wake(u):
            out.append((u["id"], approval_wake(u)))
            continue
        if sess.get("state") == "idle" and approval_wake(u):
            # An idle lead reads its inbox only at a session start: stop it, then resume (park_lines).
            out.append((u["id"], IDLE_WAKE))
            continue
        if sess.get("state") != "stopped" or not (sess.get("note") or "").startswith(PARK_NOTE):
            continue
        n = _pending_for(snap, u["id"])
        if u["status"] not in cl.WAITING:
            out.append((u["id"], f"status {u['status']}"))
        elif n:
            out.append((u["id"], f"{n} event(s)/message(s) arrived"))
        elif not wake_source(snap, u):
            out.append((u["id"], "its waits cleared"))
    return out


def stop_session(name, reason=None):
    """Stop a lead session by name; returns {stopped|error: ...}. Importable for the sweeper."""
    sess = cl.load()["sessions"].get(name)
    if not sess or not sess.get("bg_id"):
        return {"error": f"no session {name} with a background id"}
    rc, out, err = run([*claude_bin(), "stop", sess["bg_id"]])
    if rc and not already_gone(out, err):
        return {"error": f"claude stop {sess['bg_id']} failed (exit {rc}): {(err or out).strip()}"}
    cl.append("session.state", name=name, state="stopped", note=GONE_NOTE if rc else reason)
    return {"stopped": name, "bg_id": sess["bg_id"], **({"note": GONE_NOTE} if rc else {})}


GONE_NOTE = "claude: no session matching: already gone"


def already_gone(out, err):
    """True when a failed `claude stop` said the session does not exist: it is stopped, not a stop to retry."""
    return "no session matching" in f"{out or ''} {err or ''}".lower()


def cmd_stop(a):
    u = cl.load()["units"].get(a.id)
    if not u or not u.get("session"):
        return fail(f"unit {a.id} has no session")
    res = stop_session(u["session"], a.reason)
    emit(res)
    return 1 if "error" in res else 0


def pid_alive(pid):
    """False only when no process has this pid; True when one does or it cannot be told."""
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (OSError, ValueError, TypeError, OverflowError):
        return True
    return True


def stale_foremen(snap, entries, alive=pid_alive):
    """[(entry, "stop" | "wait")] for the foreman sessions foundry started that are not the current one: named
    `foreman` (foundry's start line), recorded as the foreman (foreman.current, or a foreman SessionStart with no
    unit), not stopped or done. "stop" when `claude agents` says idle, or when the state is unknown and the entry
    has no pid or a dead one (a leftover the listing still shows as working); busy, or unknown with a live pid,
    waits for the next check. None, so nothing is stopped, when that cannot be told: no listing, no recorded
    current foreman, or the current one is not in the listing."""
    cur = (snap.get("foreman_current") or {}).get("session")
    if entries is None or not cur or not any(e.get("sessionId") == cur for e in entries):
        return None
    ours = set(snap.get("foreman_sessions") or {}) | {
        sid for sid, x in (snap.get("foundry_sessions") or {}).items() if x.get("role") == "foreman" and not x.get("unit")}
    out = []
    for e in entries:
        sid = e.get("sessionId")
        if (e.get("name") != FOREMAN_NAME or not sid or sid == cur or sid not in ours
                or sid in (snap.get("foreman_gone") or {})):
            continue
        state = classify(e)[0]
        if state in ("stopped", "done"):
            continue
        gone = state is None and (e.get("pid") in (None, "") or not alive(e["pid"]))
        out.append((e, "stop" if state == "idle" or gone else "wait"))
    return out


STOP_RETRY = 24 * 3600  # seconds between `claude stop` tries for a stale foreman whose stop failed


def prune_foremen(dry_run=False, by="foundry"):
    """Stop the idle stale foremen (stale_foremen) and journal each stop and what is left; with dry_run, list them
    and write nothing. A failed `claude stop` is journaled once (stop_failed: session -> when) and not tried again
    for STOP_RETRY; a check that changes nothing journals nothing."""
    entries = list_agents(all_=True)
    snap = cl.load()
    cands = stale_foremen(snap, entries)
    if cands is None:
        why = ("`claude agents --json` could not be read" if entries is None
               else "the current foreman is not recorded or not listed")
        return {"skipped": f"nothing stopped: {why}"}
    rows = [{"session": e["sessionId"], "id": e.get("id") or e["sessionId"], "state": classify(e)[1], "action": act}
            for e, act in cands]
    if dry_run:
        return {"dry_run": True, "stale": rows}
    prev = snap.get("foreman_prune") or {}
    failed_before = prev.get("stop_failed") or {}
    failed, stopped, left, now = {}, [], [], cl.now()
    for x in rows:
        sid, at = x["session"], failed_before.get(x["session"])
        t = cl.parse_ts(at) if isinstance(at, str) else None
        if x["action"] == "stop" and t and (now - t).total_seconds() < STOP_RETRY:
            failed[sid] = at  # backing off: not tried again yet
            left.append(dict(x, action="stop-failed"))
        elif x["action"] == "stop":
            rc, out, err = run([*claude_bin(), "stop", x["id"]])
            if rc == 0 or already_gone(out, err):
                stopped.append(dict(x, note=GONE_NOTE) if rc else x)
            else:
                failed[sid] = cl.iso(now)
                left.append(dict(x, action="stop-failed"))
        else:
            left.append(x)
    same = (not stopped and failed == failed_before
            and [(x["session"], x["action"]) for x in left] == [(x.get("session"), x.get("action")) for x in prev.get("stale") or []])
    if (rows or prev.get("stale")) and not same:
        cl.append("foreman.prune", stopped=stopped, stale=left, by=by, stop_failed=failed,
                  current=(snap.get("foreman_current") or {}).get("session"))
    return {"stopped": stopped, "stale": left}


def classify(entry):
    """(foundry session state or None, raw value) for one `claude agents --json` entry."""
    raw = entry.get("status") or entry.get("state")
    return STATE_OF.get(str(raw or "").lower()), raw


def changes_for(snap, entries):
    recs, changes = [], []
    for name, sess in snap["sessions"].items():
        if sess.get("state") in ("stopped", "lost"):
            continue
        if sess.get("state") == "launching" and not sess.get("bg_id"):
            if cl.reservation_stale(sess):  # its launcher died before `release` or `session.launch`
                note = "reservation expired: the launch never reported back"
                recs.append(("session.state", {"name": name, "state": "lost", "note": note}))
                changes.append({"name": name, "unit": sess.get("unit"), "from": "launching", "to": "lost", "note": note})
            continue  # otherwise reserved by a launch still starting; `release` or `session.launch` settles it
        e = find_entry(entries, sess)
        if e is None:
            if sess.get("state") == "done":
                continue
            state, note = "lost", "absent from claude agents --all"
        else:
            state, raw = classify(e)
            note = f"status={raw}"
            if e.get("sessionId") and not sess.get("session_id"):
                recs.append(("session.bind", {"name": name, "session_id": e["sessionId"]}))
            if state is None:
                continue
        if state != sess.get("state"):
            recs.append(("session.state", {"name": name, "state": state, "note": note}))
            changes.append({"name": name, "unit": sess.get("unit"), "from": sess.get("state"), "to": state, "note": note})
    return changes, recs


def refresh(snap=None):
    """Map `claude agents --json --all` onto recorded sessions; record and return the state changes."""
    entries = list_agents(all_=True)
    if entries is None:
        return []
    if snap is not None:
        changes, recs = changes_for(snap, entries)
        if recs:
            cl.append_many(recs)
        return changes
    return cl.transact(lambda fresh: changes_for(fresh, entries))[0]


def cmd_status(a):
    changes = refresh() if a.refresh else []
    snap = cl.load()
    rows = [{k: x.get(k) for k in ("name", "unit", "role", "state", "bg_id", "session_id", "cwd", "note", "updated")}
            for x in snap["sessions"].values() if not a.id or x.get("unit") == a.id]
    out = {"sessions": rows}
    if a.refresh:
        out["changes"] = changes
    return emit(out) or 0


def adoptable(entries, snap):
    """The `claude agents` entries foundry does not run: not a recorded foundry session (lead, boss, foreman),
    not a session SessionStart marked foundry's, not one a unit already adopted."""
    ours = {str(v) for s in snap["sessions"].values() for v in (s.get("name"), s.get("bg_id"), s.get("session_id")) if v}
    ours |= set(snap.get("foundry_sessions") or {})
    ours |= {str(v) for u in snap["units"].values() for v in (u.get("adopted") or {}).values() if v}
    return [e for e in entries if not entry_ids(e) & ours
            and not str(e.get("name") or "").startswith(("lead-", "boss-", "foreman"))]


def cmd_adopt(a):
    """`foundry adopt <session>`: an existing non-foundry `claude agents` session becomes an ad-hoc unit (unit.new +
    unit.adopted with its session id). The session list is read live and never copied into the journal."""
    err = cl.require_owner("foundry adopt")
    if err:
        return fail(err)
    entries = list_agents(all_=True)
    if entries is None:
        return fail("refused: `claude agents --json` could not be read; nothing adopted")
    snap = cl.load()
    free = adoptable(entries, snap)
    if not a.session:
        return emit({"adoptable": [{"name": e.get("name"), "session_id": e.get("sessionId"), "id": e.get("id") or e.get("bgId"),
                                    "cwd": e.get("cwd"), "status": e.get("status")} for e in free]}) or 0
    hit = [e for e in entries if a.session in entry_ids(e)]
    if not hit:
        return fail(f"refused: no session {a.session!r} in `claude agents` (`foundry adopt` lists the adoptable ones)")
    e = hit[-1]
    if e not in free:
        return fail(f"refused: {a.session} is already foundry's (a foundry session, or adopted by a unit)")
    sid = e.get("sessionId") or e.get("id") or a.session
    uid = a.id or "adopt-" + re.sub(r"[^A-Za-z0-9._-]+", "-", str(e.get("name") or sid)).strip("-.")[:50]
    bad = cl.unit_id_error(uid)
    if bad:
        return fail(f"refused: {bad} (pass --id)")
    if uid in snap["units"]:
        return fail(f"refused: unit {uid} exists (pass --id)")
    title = a.title or str(e.get("name") or e.get("summary") or f"adopted session {sid}")[:80]
    name = e.get("name")
    cl.append_many([
        ("unit.new", {"id": uid, "kind": "project", "title": title, "repo": a.repo, "lead": "foundry:line-lead",
                      "ticket": [], "pr": [], "topic": []}),
        ("unit.status", {"id": uid, "status": "in_progress", "note": f"adopted from session {name or sid}"}),
        ("unit.adopted", {"id": uid, "session_id": sid, "name": name, "bg_id": e.get("bgId") or e.get("id"),
                          "cwd": e.get("cwd")}),
    ])
    return emit({"unit": uid, "adopted": sid, "name": name}) or 0


def add_cli(sp):
    ad = sp.add_parser("adopt", help="make an existing non-foundry `claude agents` session an ad-hoc unit "
                                     "(no session: list the adoptable ones)")
    ad.add_argument("session", nargs="?", help="its name, session id, or background id")
    ad.add_argument("--id", help="the unit id (default adopt-<name>)")
    ad.add_argument("--title")
    ad.add_argument("--repo", help="a registered repo name")
    ad.add_argument("--by", choices=("owner",))
    ad.set_defaults(func=cmd_adopt)
    r = sp.add_parser("repo").add_subparsers(dest="sub", required=True)
    ra = r.add_parser("add")
    ra.add_argument("name"); ra.add_argument("--path", required=True)
    ra.add_argument("--default-branch"); ra.add_argument("--remote")
    ra.add_argument("--tracker", choices=["jira", "linear", "github", "none"], help="default: kept on re-add, else none")
    r.add_parser("list")
    for p in r.choices.values():
        p.set_defaults(func=cmd_repo)

    unit = next(x for x in sp.choices["unit"]._actions if isinstance(x, argparse._SubParsersAction))
    la = unit.add_parser("launch"); la.add_argument("id")
    la.add_argument("--model"); la.add_argument("--effort"); la.add_argument("--max-turns", type=int)
    la.add_argument("--branch"); la.add_argument("--cwd"); la.add_argument("--dry-run", action="store_true")
    la.set_defaults(func=cmd_launch)
    rs = unit.add_parser("resume"); rs.add_argument("id"); rs.set_defaults(func=cmd_resume)
    st = unit.add_parser("stop"); st.add_argument("id"); st.add_argument("--reason"); st.set_defaults(func=cmd_stop)
