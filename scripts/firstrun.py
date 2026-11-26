"""`foundry init`: first-run setup. Discovers what it can (GitHub login, the machine's timezone), takes the rest as
flags, creates the worktree and foreman directories, registers repos, and prints the foreman start command.

Re-runnable: a value already set in config.json or the user's prefs.json is kept unless its flag is given, so a
second run only fills gaps. Only the owner runs it (it writes config and prefs).
"""

import argparse
import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402



def local_zone():
    """The machine's IANA zone name (TZ, else the /etc/localtime link), or None."""
    import zoneinfo
    names = [os.environ.get("TZ", "").lstrip(":")]
    try:
        names.append(os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1])
    except (OSError, IndexError):
        pass
    for n in names:
        try:
            if n and zoneinfo.ZoneInfo(n):
                return n
        except Exception:
            continue
    return None


def _read(p):
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


FOREMAN_NAME = "foreman"
LIVE_STATES = ("running", "idle")


def _foreman_settings(cfg):
    """--settings for the foreman: advisorModel from config advisor_model (a user-level one may not exist), and the
    role (plus CLAUDE_CONFIG_DIR when set) in its env."""
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR") or ""
    env = '"FOUNDRY_ROLE":"foreman"' + (f',"CLAUDE_CONFIG_DIR":"{cfg_dir}"' if cfg_dir else "")
    return '{"advisorModel":"%s","env":{%s}}' % (cfg.get("advisor_model") or "claude-fable-5-2", env)


def foreman_command(cfg):
    """The foreman's start line, the same one install.sh prints (`foundry foreman line`)."""
    d = cfg.get("foreman_dir") or cl.load_config()["foreman_dir"]
    eff = cfg.get("foreman_effort") or "medium"
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR") or ""
    prefix = f'CLAUDE_CONFIG_DIR="{cfg_dir}" ' if cfg_dir else ""
    agents = "foundry mcp agent foundry:foreman 2>/dev/null || echo {}"  # the ~/.local/bin shim: no versioned path
    return (f"cd {d} && {prefix}claude --bg -n {FOREMAN_NAME} --agents \"$({prefix}{agents})\" --agent foundry:foreman "
            f"--model opus --effort {eff} --permission-mode auto --settings '{_foreman_settings(cfg)}'")


def foreman_argv(cfg, prompt=None):
    """(cwd, argv) of the same start line, for running without a shell: the --agents JSON from the same
    `foundry mcp agent` mapping (none when it maps nothing), the prompt last when given."""
    import launcher
    import mcpmap
    try:
        flag = mcpmap.agent_flag("foundry:foreman")
    except Exception:  # the file agent stays, as `|| echo {}` does in the line
        flag = {}
    argv = [*launcher.claude_bin(), "--bg", "-n", FOREMAN_NAME, *(["--agents", json.dumps(flag)] if flag else []),
            "--agent", "foundry:foreman", "--model", "opus", "--effort", cfg.get("foreman_effort") or "medium",
            "--permission-mode", "auto", "--settings", _foreman_settings(cfg)]
    cwd = os.path.expanduser(cfg.get("foreman_dir") or cl.load_config()["foreman_dir"])
    return cwd, argv + ([prompt] if prompt else [])


def live_foremen(entries):
    """`claude agents` entries named foreman that are running or idle."""
    import launcher
    return [e for e in entries or [] if e.get("name") == FOREMAN_NAME and launcher.classify(e)[0] in LIVE_STATES]


def cmd_foreman(a):
    cfg = cl.load_config()
    if a.sub == "line":
        print(foreman_command(cfg))
        return 0
    import launcher
    err = cl.require_owner("foundry foreman start")
    if err or os.environ.get("CLAUDECODE"):
        print(err or "refused: run `foundry foreman start` from a plain terminal, not from inside a Claude Code "
              "session", file=sys.stderr)
        return 2
    entries = launcher.list_agents()
    if entries is None:
        print("refused: `claude agents --json` could not be read, so a running foreman cannot be ruled out; "
              "nothing started", file=sys.stderr)
        return 2
    live = live_foremen(entries)
    if live:
        e = live[-1]
        sid = e.get("shortId") or e.get("id") or e.get("bgId") or e.get("sessionId")
        print(json.dumps({"refused": "a foreman session is already running", "id": sid,
                          "status": launcher.classify(e)[1], "attach": f"claude attach {sid}"}, ensure_ascii=False))
        return 1
    cwd, argv = foreman_argv(cfg, a.prompt)
    os.makedirs(cwd, exist_ok=True)
    bg_id, out = launcher.start_bg(argv, cwd)
    if not bg_id:
        return 1
    res = {"started": bg_id, "attach": f"claude attach {bg_id}"}
    if not a.prompt:
        res["next"] = "attach, then send `foundry up` as the first prompt: a --bg session waits for one"
    print(json.dumps(res, ensure_ascii=False))
    return 0


def cmd_init(a):
    err = cl.require_owner("foundry init")
    if err:
        print(err, file=sys.stderr)
        return 2
    import prefs
    cfg_path, prefs_path = cl.path("config.json"), prefs.user_path()
    cfg, user = _read(cfg_path), _read(prefs_path)
    effective = cl.load_config()
    eff_prefs = prefs.merge(prefs.defaults(), user)
    wrote, kept, old, nxt = {}, {}, {}, []

    def put(store, key, value, given, current=None):
        """Write value unless the key is already set (or already in effect) and its flag was not given."""
        current = store.get(key, current)
        if value in (None, ""):
            return
        if not given and (key in store or value == current):
            kept[key] = current
            return
        if store.get(key) != value:
            old[key] = store.get(key)
            store[key] = value
            wrote[key] = value

    if bool(a.owner_slack_user) != bool(a.owner_dm):
        print("--owner-slack-user and --owner-dm go together", file=sys.stderr)
        return 2
    import review
    put(cfg, "owner_github_login", a.github_login or (None if cfg.get("owner_github_login") else review.owner_login()),
        bool(a.github_login))
    put(cfg, "worktree_root", a.worktree_root, True, effective["worktree_root"])
    put(cfg, "foreman_dir", a.foreman_dir, True, effective["foreman_dir"])
    put(cfg, "repos_dir", a.repos_dir, True, effective["repos_dir"])
    if a.owner_slack_user:
        if not a.owner_slack_user.startswith(("U", "W")) or not a.owner_dm.startswith("D"):
            print("--owner-slack-user is a Slack user id (U...), --owner-dm a DM channel id (D...)", file=sys.stderr)
            return 2
        put(cfg, "owner_slack_user_id", a.owner_slack_user, True)
        dms = list(cfg.get("owner_dm_channels") or [])
        put(cfg, "owner_dm_channels", dms + [a.owner_dm] if a.owner_dm not in dms else dms, True)
    put(user, "timezone", a.timezone or local_zone(), bool(a.timezone), eff_prefs["timezone"])
    if a.tracker:
        put(user, "trackers", [] if a.tracker == "none" else [a.tracker], True)
    errs = prefs.validate(user)
    if errs:
        print("refused: " + "; ".join(errs), file=sys.stderr)
        return 2
    if not cfg.get("owner_github_login"):
        nxt.append("GitHub login unknown: run `gh auth login`, or pass --github-login")
    if not cfg.get("owner_slack_user_id"):
        nxt.append("Slack owner ids not set (optional): --owner-slack-user U... --owner-dm D...")

    import launcher
    known = sorted(launcher.load_repos())
    if known:
        kept["repos"] = known
    repos = []
    for spec in a.repo or []:
        name, _, path = spec.partition("=")
        if not name or not path:
            print(f"--repo {spec!r}: use NAME=PATH", file=sys.stderr)
            return 2
        repos.append((name, path))
    if a.dry_run:
        print(json.dumps({"dry_run": True, "would_write": wrote, "kept": kept,
                          "repos": [n for n, _ in repos], "next": nxt}, ensure_ascii=False, indent=1))
        return 0

    for d in (cfg.get("worktree_root") or effective["worktree_root"], cfg.get("foreman_dir") or effective["foreman_dir"]):
        os.makedirs(os.path.expanduser(d), exist_ok=True)
    if any(k in wrote for k in cfg):
        tmp = f"{cfg_path}.{os.getpid()}.tmp"  # owner ids: never on disk at a wider mode, even briefly
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, cfg_path)
    for k in ("timezone", "trackers"):
        if k in wrote:
            cl.write_atomic(prefs_path, json.dumps(user, ensure_ascii=False, indent=1))
            break
    for k in ("timezone", "trackers"):
        if k in wrote:
            cl.append("prefs.changed", key=k, old=old.get(k), new=user[k], by="owner", note="foundry init")

    registered, failed = [], {}
    for name, path in repos:
        buf, ebuf = io.StringIO(), io.StringIO()
        ns = argparse.Namespace(sub="add", name=name, path=path, default_branch=None, remote=None,
                                tracker=a.tracker if a.tracker else None)
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(ebuf):
            rc = launcher.cmd_repo(ns)
        if rc:
            failed[name] = ebuf.getvalue().strip()
        else:
            registered.append(name)
    if not (launcher.load_repos() or registered):
        nxt.append("no repo registered: foundry init --repo NAME=PATH (or foundry repo add)")
    nxt.append(f"trust the foreman directory once: python3 {os.path.dirname(os.path.abspath(__file__))}/trust.py "
               f"register {cfg.get('foreman_dir') or cl.load_config()['foreman_dir']}")
    out = {"wrote": wrote, "kept": kept, "repos": registered, "repo_errors": failed,
           "foreman": foreman_command(cl.load_config()), "next": nxt}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 2 if failed else 0


def add_cli(sp):
    fm = sp.add_parser("foreman", help="the foreman session: print its start line, or start it (owner, plain terminal)")
    fs = fm.add_subparsers(dest="sub", required=True)
    fs.add_parser("line", help="print the start line install.sh prints (read-only)").set_defaults(func=cmd_foreman)
    st = fs.add_parser("start", help="start the foreman --bg; refused while one is running or idle")
    st.add_argument("--prompt", help="the first prompt, sent at launch (e.g. \"foundry up\")")
    st.add_argument("--by", choices=("owner",))
    st.set_defaults(func=cmd_foreman)
    p = sp.add_parser("init", help="first-run setup: config, prefs, repos, the foreman start command")
    p.add_argument("--github-login", help="default: `gh api user`")
    p.add_argument("--timezone", help="IANA zone; default: the machine's")
    p.add_argument("--worktree-root", help="where unit worktrees go (config worktree_root)")
    p.add_argument("--foreman-dir", help="the foreman's working directory (config foreman_dir)")
    p.add_argument("--repos-dir", help="where main checkouts live, for install's repo scan (config repos_dir)")
    p.add_argument("--tracker", choices=["jira", "linear", "github", "none"], help="issue tracker for prefs and new repos")
    p.add_argument("--repo", action="append", help="NAME=PATH of a local clone to register; repeatable")
    p.add_argument("--owner-slack-user", help="the owner's Slack user id (U...), optional")
    p.add_argument("--owner-dm", help="the owner's self-DM channel id (D...), with --owner-slack-user")
    p.add_argument("--dry-run", action="store_true", help="show what would be written; change nothing")
    p.add_argument("--by", choices=("owner",))
    p.set_defaults(func=cmd_init)
