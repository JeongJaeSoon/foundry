"""Tiered approver for the PermissionRequest hook of unattended foundry sessions.

tier 0: rules (config `approval_rules`, then RULES) -> allow / deny / owner-only
tier 1: a remembered answer in `approval_ledger`
tier 2: the foreman answers (with foundry:advisor) while the hook waits
tier 3: owner-only; the owner answers (board button, `/foundry approve ...`, or a separate terminal)
A wait that runs out denies and leaves the request queued. Auto-mode classifier denials never reach this hook.
A lead or shop-boss session (`claude --bg`) never blocks on tier 3: the request is queued at once, the unit gets an
`approval` wait, and answering it queues an inbox line and clears the wait, so the sessions stream resumes the
lead (launcher.unparkable) and the same command then runs once on the recorded answer (`late`).
"""

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import roles  # noqa: E402

QUEUED_TEXT = ("[foundry] Owner approval needed; queued as {rid} (the owner answers it on the board). Do not retry "
               "it now and do not ask anyone to run it by hand: your unit waits on {rid}. Do other work or end your "
               "turn; you are resumed when it is answered, and the same command then runs once on that answer.")

HOME = re.escape(os.path.expanduser("~"))
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Read")
WRITE_TOOLS = r"Write|Edit|MultiEdit|NotebookEdit"
DEPLOY = r"(?i)\b(kubectl|argocd|helm|terraform|deploy\S*)\b.*\b(prod|production|prd)\b"
# `where`: "inside" = every path the call names is in the unit worktree; "outside" = at least one is not.
RULES = [
    {"tool": "Bash", "pattern": DEPLOY, "action": "owner", "reason": "production"},
    {"tool": ".*", "pattern": r"(?i)cluster-?ops\S*[/_-](prod|production|prd)\b", "action": "owner", "reason": "production"},
    {"tool": "Bash", "pattern": r"\bgh\s+auth\b|\baws\s+configure\b|\bsecurity\s+find-\w+-password\b|(?i:keychain)"
     r"|(^|[;&|(]\s*)op\s|\b(docker|npm|gh|aws|gcloud|az|vault|op|claude|argocd|kubectl)\b[^;&|\n]*\blogin\b", "action": "owner", "reason": "secrets/logins"},
    {"tool": ".*", "pattern": r"(^|[\s/'\"=])\.env(\.[\w.-]+)?($|[\s'\"])|\.(pem|key|p12|p8)\b", "action": "owner",
     "reason": "secrets"},
    # Credential stores and files named for credentials, for reads too.
    {"tool": ".*", "pattern": r"(^|[\s'\"=:])(~|\$HOME|\$\{HOME\}|" + HOME + r")?/?\.(ssh|aws|gnupg|kube|netrc|docker/config\.json|config/gh)(/|\b)"
     r"|(?i:[\w./~-]*(credential|token|secret|passw(or)?d)s?\b[\w./-]*)", "action": "owner", "reason": "secrets"},
    {"tool": ".*", "pattern": rf"(~|\$HOME|\$\{{HOME\}}|{HOME})/\.claude(/|\b)|\.claude/settings[^/\s]*\.json|\.claude\.json",
     "where": "not-scratch", "action": "owner", "reason": "settings/permissions"},
    {"tool": "Bash", "pattern": r"(^|[;&|(]\s*)sudo\b", "action": "owner", "reason": "settings/permissions"},
    {"tool": "Bash", "pattern": r"\b(chmod|chown)\b", "where": "outside", "action": "owner",
     "reason": "settings/permissions"},
    {"tool": ".*", "pattern": r"(?i)\b(stripe|billing|purchase)\b", "action": "owner", "reason": "payments"},
    {"tool": "Bash", "pattern": r"\bgh\s+release\s+create\b|\bnpm\s+publish\b"
     r"|\bgit\b.*\bpush\b(?=.*(--force|\s-f\b|\s\+))(?=.*\b(main|master)\b)", "action": "owner",
     "reason": "external publishing"},
    {"tool": "Bash", "pattern": r"\bgh\s+pr\s+merge\b|\bgh\s+api\b.*(/pulls/\d+/merge\b|mergePullRequest)",
     "where": "foreign-repo", "action": "owner", "reason": "merge in another org's repo"},
    # A pinned merge in a registered repo already passed gitpolicy's PreToolUse gate (approved, CLEAN, head sha,
    # merge mode, owner_paths, Argo lock) before this hook runs: a second, owner-tier approval adds nothing.
    {"tool": "Bash", "full": True, "pattern": r"(?!.*--admin)\s*gh\s+pr\s+merge\s[^;&|<>`$\n\\]*--match-head-commit[\s=][0-9a-f]{7,40}\b[^;&|<>`$\n\\]*",
     "where": "registered-repo", "action": "allow", "reason": "merge gated by gitpolicy (approved, CLEAN, pinned head)"},
    {"tool": "Bash", "pattern": r"(?i)slack\.com/api/(chat\.|files\.)|hooks\.slack\.com/", "action": "owner",
     "reason": "Slack post outside the guarded MCP send"},
    # Written by the foundry CLI only: config, prefs, repos, journal, ledger. Reads through Read stay tier 0.
    {"tool": WRITE_TOOLS + "|Bash", "pattern": "", "where": "state-dir", "action": "owner", "reason": "foundry state"},
    {"tool": r"mcp__.*(?i:send|post|publish|create|update|delete|merge).*", "pattern": "", "where": "mcp-not-allowlisted",
     "action": "owner", "reason": "MCP write"},
    {"tool": r"Read|Glob|Grep|LS|NotebookRead", "pattern": "", "action": "allow", "reason": "read-only"},
    {"tool": "Bash", "full": True, "pattern": r"^\s*(?!.*\s(--output|--pre|--pre-glob|--exec|--ext-diff|--textconv|-o)(\s|=|$))"
     r"((ls|cat|head|tail|grep|rg|wc|pwd|which|file|stat|tree|du|df|git\s+(status|log|diff|show|rev-parse))\b[^;&|<>`$\n]*"
     r"|git\s+branch(\s+(-a|-r|-v|-vv|--all|--remotes|--list|--show-current))*\s*"
     r"|git\s+remote(\s+-v|\s+get-url\s+[\w.-]+)?\s*)$", "action": "allow", "reason": "read-only"},
    {"tool": WRITE_TOOLS, "pattern": "", "where": "inside", "action": "allow", "reason": "edit inside the unit worktree"},
    {"tool": WRITE_TOOLS, "pattern": "", "where": "scratch", "action": "allow", "reason": "session scratch"},
]


def out(behavior, message=None):
    d = {"behavior": behavior}
    if message:
        d["message"] = message
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": d}},
                                ensure_ascii=False))


def normalized(tool_input):
    return json.dumps({k: v for k, v in (tool_input or {}).items() if k != "description"},
                      sort_keys=True, ensure_ascii=False)


def request_key(tool, tool_input):
    return hashlib.sha1((tool + "\n" + normalized(tool_input)).encode()).hexdigest()


def subject(tool, ti):
    if tool == "Bash":
        return ti.get("command") or ""
    p = ti.get("file_path") or ti.get("notebook_path") or ti.get("path")
    return p if tool in FILE_TOOLS and p else normalized(ti)


def paths(tool, ti, cwd):
    if tool in FILE_TOOLS or tool == "NotebookEdit":
        p = ti.get("file_path") or ti.get("notebook_path")
        return [os.path.join(cwd or "", os.path.expanduser(p))] if p else []
    if tool == "Bash":
        try:
            words = shlex.split(ti.get("command") or "")
        except ValueError:
            words = (ti.get("command") or "").split()
        return [os.path.expanduser(w) for w in words if w.startswith(("/", "~"))]
    return []


def inside(p, root):
    if not root:
        return False
    p, root = os.path.realpath(p).lower(), os.path.realpath(root).lower()  # macOS: case-insensitive names
    return p == root or p.startswith(root.rstrip("/") + "/")


def remote_org(cwd):
    """The GitHub owner of the checkout's origin remote, or None."""
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        r = subprocess.run([os.environ.get("FOUNDRY_GIT") or "git", "-C", cwd, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"github\.com[:/]([\w.-]+)/", r.stdout or "")
    return m.group(1) if r.returncode == 0 and m else None


def _registry():
    try:
        import gitpolicy
        return gitpolicy.repos()
    except Exception:
        return {}


def own_orgs(cfg, rs=None):
    """Lower-cased GitHub orgs whose merges are not "another org's": config approval_own_orgs, plus the org of
    every repo registered with `foundry repo add` (its slug, else its checkout's origin)."""
    out = {str(o).lower() for o in cfg.get("approval_own_orgs") or []}
    for r in (_registry() if rs is None else rs).values():
        slug = r.get("slug") or ""
        org = slug.split("/", 1)[0] if "/" in slug else remote_org(os.path.expanduser(r.get("path") or ""))
        if org:
            out.add(org.lower())
    return out


def merge_repo_registered(cmd, cwd, rs=None):
    """The repo a `gh pr merge` names (-R, a PR URL, else the checkout) is in the registry and not owner_only."""
    import gitpolicy
    rs = _registry() if rs is None else rs
    try:
        flag_repo, _, rest = gitpolicy.gh_parts(gitpolicy.strip_env(gitpolicy.tokens(cmd)))
    except (ValueError, IndexError):
        return False
    url = next((m.group(1) for a in rest for m in [gitpolicy.PR_URL_RE.match(a)] if m), None)
    ref = flag_repo or url
    repo = gitpolicy.repo_by_ref(ref, rs) if ref else gitpolicy.repo_by_path(cwd, None, rs) if cwd else None
    return bool(repo) and not repo.get("owner_only")


def _state_roots():
    d = cl.state_dir()
    home = os.path.expanduser("~")
    roots = {os.path.abspath(d), os.path.realpath(d)}
    return [r.rstrip("/") for r in roots], home


def _word_paths(word, home):
    """Absolute forms a shell word may name: ~ and $HOME expanded."""
    w = word.strip("'\"")
    for pre in ("$HOME", "${HOME}"):
        if w.startswith(pre):
            w = home + w[len(pre):]
    w = os.path.expanduser(w)
    return w


def exempt_roots(snap):
    """Unit worktrees and registered repos: work trees, not state, even when they live under the state dir."""
    out = [((u.get("worktree") or {}).get("path")) for u in (snap.get("units") or {}).values()]
    try:
        import gitpolicy
        out += [r.get("path") for r in gitpolicy.repos().values()]
    except Exception:
        pass
    return [os.path.expanduser(p) for p in out if p]


def _names_state_dir(cmd, roots, home, exempt):
    """The state dir spelled anywhere in the text (inside a `python -c` string too), any letter case: macOS
    file names are case-insensitive. Exempt roots (worktrees, repos) are cut out first."""
    low = cmd.lower()
    for e in exempt:
        for form in {e, os.path.realpath(e)}:
            low = low.replace(form.lower().rstrip("/"), "")
    forms = set()
    for r in roots:
        forms.add(r.lower())
        if r.startswith(home.rstrip("/") + "/"):
            forms.add("/" + r[len(home.rstrip("/")) + 1:].lower())  # ~/..., $HOME/..., expanduser("~")+"/..."
    return any(re.search(re.escape(f) + r"(?![\w.-])", low) for f in forms)  # not foundry-tests


def _relative_hits(words, cwd, roots, exempt):
    """Relative paths resolved against the command's cwd (`../../prefs.json` from inside the state dir)."""
    if not cwd:
        return False
    low_roots = [os.path.realpath(r).lower() for r in roots]
    for w in words:
        if not w or w.startswith(("/", "~", "$", "-")) or w in ("|", "||", "&&", ";", "&", ">", ">>", "<"):
            continue
        if "/" not in w and not os.path.lexists(os.path.join(cwd, w)):
            continue  # a plain word (`gh`, `5`) names no file
        p = os.path.realpath(os.path.join(cwd, w)).lower()
        if any(p == r or p.startswith(r + "/") for r in low_roots) and \
                not any(inside(os.path.join(cwd, w), e) for e in exempt):
            return True
    return False


def touches_state_dir(tool, ti, cwd, exempt=()):
    """A write (Write/Edit) under the state dir, or a Bash command naming it (globs included) that is not the
    foundry CLI. Matched on the directory, not on file names."""
    roots, home = _state_roots()
    if tool != "Bash":
        p = ti.get("file_path") or ti.get("notebook_path")
        if not p:
            return False
        p = os.path.join(cwd or "", os.path.expanduser(p))
        return any(inside(p, r) for r in roots) and not any(inside(p, e) for e in exempt)
    cmd = ti.get("command") or ""
    try:
        import gitpolicy
        words = gitpolicy.tokens(cmd)
    except (ImportError, ValueError):
        words = cmd.split()
    if _names_state_dir(cmd, roots, home, exempt) or _relative_hits(words, cwd, roots, exempt):
        return not is_foundry_cli(words)
    for w in words:
        for part in re.split(r"[=:]", w) if "/" in w else [w]:
            p = _word_paths(part, home)
            if not p.startswith("/"):
                continue
            glob_at = re.search(r"[*?\[{]", p)
            if glob_at:
                prefix = p[:glob_at.start()]
                in_exempt = any(prefix.startswith(os.path.realpath(e).rstrip("/") + "/") or
                                prefix.startswith(e.rstrip("/") + "/") for e in exempt)
                if not in_exempt and any(r.startswith(prefix) or prefix.startswith(r + "/") or prefix == r
                                         for r in roots):
                    return not is_foundry_cli(words)
            elif any(inside(p, r) for r in roots) and not any(inside(p, e) for e in exempt):
                return not is_foundry_cli(words)
    return False


def is_foundry_cli(words):
    """A single plain `foundry ...` / `python3 <...>/foundry.py ...` call."""
    if not words or any(w in ("|", "||", "&&", ";", "&", ">", ">>", "<", "(", ")") for w in words):
        return False
    prog = os.path.basename(words[0])
    if prog == "foundry":
        return True
    return prog.startswith("python") and len(words) > 1 and os.path.basename(words[1]) == "foundry.py"


JOBS_TMP = re.compile(r"/\.claude/jobs/[\w-]+/tmp(/|$)")


def scratch_path(p):
    """A path in a session's own temp dir (`~/.claude/jobs/<id>/tmp/...`, where Claude Code points TMPDIR): scratch,
    not settings. Resolved first, so `..` or a symlink out of it is judged by where it lands."""
    real = os.path.realpath(os.path.expanduser(p))
    home = os.path.realpath(os.path.expanduser("~"))
    return real.startswith(home + "/") and bool(JOBS_TMP.match(real[len(home):]))


def names_only_scratch(tool, ti):
    """Every `.claude` path the call names is session scratch (Write/Edit: the file; Bash: each word)."""
    if tool != "Bash":
        p = ti.get("file_path") or ti.get("notebook_path")
        return bool(p) and p.startswith(("/", "~")) and scratch_path(p)
    home = os.path.expanduser("~")
    try:
        import gitpolicy
        words = gitpolicy.tokens(ti.get("command") or "")
    except (ImportError, ValueError):
        return False
    hits = [w for w in words if ".claude" in w]
    return bool(hits) and all(_word_paths(w, home).startswith("/") and scratch_path(_word_paths(w, home))
                              for w in hits)


def where_ok(rule, tool, ti, ctx):
    where = rule.get("where")
    if not where:
        return True
    if where in ("inside", "outside"):
        ps = paths(tool, ti, ctx.get("worktree"))
        ins = [inside(p, ctx.get("worktree")) for p in ps]
        # "outside" also holds when no absolute path is named: a relative path's base is not known here.
        return bool(ps) and all(ins) if where == "inside" else not ps or not all(ins)
    if where == "foreign-repo":
        own = own_orgs(ctx["cfg"])
        cmd = ti.get("command") or ""
        orgs = re.findall(r"(?:github\.com/|(?:-R|--repo)[\s=]+|repos/)([\w.-]+)/", cmd)
        # No -R and no URL: the repo is the checkout's; an unknown remote counts as foreign.
        orgs = orgs or [remote_org(ctx.get("cwd")) or "<unknown>"]
        return any(o.lower() not in own for o in orgs)
    if where == "registered-repo":
        return merge_repo_registered(ti.get("command") or "", ctx.get("cwd"))
    if where == "not-scratch":
        return not names_only_scratch(tool, ti)
    if where == "scratch":
        return names_only_scratch(tool, ti)
    if where == "state-dir":
        return touches_state_dir(tool, ti, ctx.get("cwd"), ctx.get("exempt") or ())
    if where == "mcp-not-allowlisted":
        return not any(re.search(p, tool) for p in ctx["cfg"].get("approval_mcp_allow") or [])
    return False


def effective_rules(cfg):
    """No config "allow" (a catch-all included) precedes a built-in owner rule; config deny/owner rules still
    come first, since they only tighten."""
    if cfg.get("config_error"):
        # load_config fell back to defaults; the owner's approval_rules are unknown, so the approver fails closed.
        raise ValueError(cfg["config_error"])
    extra = [{"tool": r.get("tool") or ".*", "pattern": r.get("pattern") or "", "action": r["action"],
              "reason": r.get("reason") or "config approval_rules", "source": "config", "json": True}
             for r in cfg.get("approval_rules") or []]
    builtin = [r | {"source": "builtin"} for r in RULES]
    return ([r for r in extra if r["action"] != "allow"] + [r for r in builtin if r["action"] == "owner"]
            + [r for r in extra if r["action"] == "allow"] + [r for r in builtin if r["action"] != "owner"])


def classify(tool_name, tool_input, ctx):
    """("allow"|"deny"|"owner"|None, reason). Config rules match the normalized input JSON, built-ins the subject."""
    ti = tool_input or {}
    for r in effective_rules(ctx["cfg"]):
        if not re.fullmatch(r["tool"], tool_name):
            continue
        text = normalized(ti) if r.get("json") else subject(tool_name, ti)
        if r.get("full"):
            # An allow rule must cover the whole command: a second line is a second command.
            if "\n" in text or "\r" in text or not re.fullmatch(r["pattern"], text):
                continue
        elif r["pattern"] and not re.search(r["pattern"], text, re.M):
            continue
        if where_ok(r, tool_name, ti, ctx):
            return r["action"], f"{r['source']}: {r['reason']}"
    return None, "no rule"


def summary_of(tool, ti):
    return f"{tool}: {subject(tool, ti)}"[:300]


# ---------- stale queued requests ----------
# A queued (gave_up) request can outlive what it asked for: the lead asked again, the PR moved on, or it simply aged.
# Such a request leaves the board and is never applied late; aging records why (`approval.mark field=stale`).
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
MATCH_HEAD_RE = re.compile(r"\bgh pr merge\b[^;&|\n]*?--match-head-commit[= ]([0-9a-f]{7,40})")


def family(a):
    """The request with commit shas blanked: the same command asked again for another head is one family."""
    return " ".join(SHA_RE.sub("<sha>", a.get("summary") or "").split())


def _pr_states(snap):
    out = {}
    for u in (snap.get("units") or {}).values():
        for pr in u.get("prs") or []:
            if isinstance(pr, dict) and pr.get("repo") and pr.get("number") is not None:
                out[f"{pr['repo']}#{pr['number']}"] = pr
    return out


def _merge_target(summary):
    """(repo#n, pinned sha) of a `gh pr merge <n> -R <repo> --match-head-commit <sha>` request, else None."""
    m = MATCH_HEAD_RE.search(summary or "")
    if not m:
        return None
    toks = re.search(r"\bgh pr merge\b([^;&|\n]*)", summary)[1].split()
    num = next((x for x in toks if x.isdigit()), None)
    repo = next((toks[i + 1] for i, x in enumerate(toks[:-1]) if x in ("-R", "--repo")), None)
    return (f"{repo}#{num}", m[1]) if num and repo else None


def stale_reason(a, snap, ttl_hours, now, prs=None):
    """Why a queued request can never be valid again, else None. Only queued (gave_up), never-used requests."""
    if not a.get("gave_up") or a.get("used"):
        return None
    if a.get("stale"):
        return a.get("stale_reason") or "stale"
    fam = family(a)
    for b in (snap.get("approvals") or {}).values() if fam else ():
        if (b.get("rid") != a.get("rid") and b.get("unit") == a.get("unit") and b.get("session") == a.get("session")
                and b.get("tool") == a.get("tool") and (b.get("asked") or "") > (a.get("asked") or "")
                and family(b) == fam):
            return f"superseded by {b['rid']}"
    tgt = _merge_target(a.get("summary"))
    if tgt:
        pr = (prs if prs is not None else _pr_states(snap)).get(tgt[0])
        if pr:
            st = "MERGED" if pr.get("merged") else str(pr.get("state") or "").upper()
            if st in ("MERGED", "CLOSED"):
                return f"{tgt[0]} is {st}"
            head = str(pr.get("head") or "")
            if head and not (head.startswith(tgt[1]) or tgt[1].startswith(head)):
                return f"{tgt[0]} head is now {head[:12]}, not {tgt[1][:12]}"
    asked = cl.parse_ts(a.get("asked"))
    if ttl_hours and asked and (now - asked).total_seconds() > float(ttl_hours) * 3600:
        return f"queued over {ttl_hours}h (approvals.queued_ttl_hours)"
    return None


def queued_ttl_hours(p=None):
    if p is None:
        import prefs
        p = prefs.load()
    return (p.get("approvals") or {}).get("queued_ttl_hours", 6)


def stale_records(snap, p=None, now=None):
    """`approval.mark field=stale` records for queued requests that just went stale (each once)."""
    ttl, now, prs = queued_ttl_hours(p), now or cl.now(), _pr_states(snap)
    out = []
    for rid, a in sorted((snap.get("approvals") or {}).items()):
        if a.get("stale"):
            continue
        why = stale_reason(a, snap, ttl, now, prs)
        if why:
            out.append(("approval.mark", {"rid": rid, "field": "stale", "reason": why}))
            u = (snap.get("units") or {}).get(a.get("unit") or "")
            if u and any(w.get("wid") == f"approval:{rid}" for w in u.get("waits") or []):
                out.append(("wait.clear", {"id": a["unit"], "wid": f"approval:{rid}"}))  # nothing will answer it
    return out


def notify(cfg, text):
    if not cfg.get("notify", True):
        return
    cl.desktop_notify("foundry: owner approval", text)


def log(**rec):
    cl.append_log("enforce.log", {"event": "PermissionRequest", **rec})


def handle(inp):
    rid = key = None
    try:
        import hook  # lazy: hook imports this module lazily too
        snap = cl.load()
        if not hook.is_foundry_session(inp, snap):
            return
        tool, ti = inp.get("tool_name") or "", inp.get("tool_input") or {}
        caller = hook.caller_of(inp, snap)
        unit = hook.unit_of_agent(inp, snap)
        cfg = cl.load_config()
        # No unit, no worktree: "inside the unit worktree" never falls back to the session's cwd.
        wt = ((snap["units"].get(unit) or {}).get("worktree") or {}).get("path")
        key = request_key(tool, ti)
        base = {"session": inp.get("session_id"), "caller": caller, "unit": unit, "tool": tool, "key": key}

        action, reason = classify(tool, ti, {"worktree": wt, "cfg": cfg, "unit": unit, "cwd": inp.get("cwd"),
                                       "exempt": exempt_roots(snap)})
        if action in ("allow", "deny"):
            log(**base, tier=0, decision=action, reason=reason)
            return out(action, None if action == "allow" else f"[foundry] Denied by rule ({reason}).")
        # The foreman cannot answer its own session's requests while it is blocked in this hook, nor those of its
        # own subagents (dispatcher, advisor), which it is waiting on: route by the session's role, not the caller's.
        session_role = roles.ROLE_ENV.get(hook.env_role() or hook.session_rec(inp, snap).get("role") or "", roles.FOREMAN)
        if not inp.get("agent_id") and str(inp.get("agent_type") or "").startswith("foundry:"):
            session_role = caller
        tier = 3 if action == "owner" or roles.FOREMAN in (caller, session_role) else 2

        led = snap["approval_ledger"].get(cl.ledger_key(unit, key))
        if led and (tier == 2 or led.get("by") == "owner"):
            log(**base, tier=1, decision=led["behavior"], rid=led.get("rid"), reason="ledger")
            return out(led["behavior"], None if led["behavior"] == "allow" else
                       f"[foundry] Denied earlier ({led.get('by')}, {led.get('rid')}); remembered.")

        def same(a):
            return a.get("key") == key and (a.get("unit") or a.get("session")) == (unit or inp.get("session_id"))

        ttl, now_ = queued_ttl_hours(), cl.now()
        late = next((a for a in snap["approvals"].values()
                     if same(a) and a["answer"] and a.get("gave_up") and not a.get("used")
                     and not stale_reason(a, snap, ttl, now_)), None)
        if late:
            cl.append("approval.mark", rid=late["rid"], field="used")
            log(**base, tier=late.get("tier"), decision=late["answer"], rid=late["rid"], reason="late answer")
            return out(late["answer"], None if late["answer"] == "allow" else
                       f"[foundry] Denied by {late.get('by')} ({late['rid']}): {late.get('reason') or ''}".rstrip(": "))

        summary = summary_of(tool, ti)
        queue_now = tier == 3 and unit and session_role in (roles.LEAD, roles.BOSS)
        wait = max(1, min(int(cfg.get("approval_wait_seconds", 540)), 595) - 5)
        # Why it is the owner's (the owner-only rule's class, or the foreman's own session), and how long the hook
        # waits before it denies and queues: the board's approval card shows both.
        why = (reason.split(": ", 1)[-1] if action == "owner" else "foreman session") if tier == 3 else None
        rid = next((a["rid"] for a in snap["approvals"].values() if same(a) and a["answer"] is None), None)
        if not rid:
            rid = cl.new_id("ap", key, inp.get("session_id"), time.time_ns(), os.getpid())
            cl.append_many([
                ("approval.request", {"rid": rid, "key": key, "session": inp.get("session_id"), "unit": unit,
                                      "role": caller, "tool": tool, "summary": summary, "tier": tier,
                                      "why": why, "wait_s": None if queue_now else wait}),
                ("event", {"eid": cl.new_id("ev", rid), "unit": unit, "wid": None, "kind": "approval", "ref": rid,
                           "summary": f"[approval tier {tier}] {rid}: {summary}"})])
            if tier == 3:
                notify(cfg, f"{rid} ({unit or caller}): {summary}"[:200])

        if queue_now:
            # A background session must not sit in a 9-minute hook (it can end there, and the late answer then
            # reaches nobody): queue it now and let the answer wake the unit.
            recs = [("approval.mark", {"rid": rid, "field": "gave_up"}),
                    ("wait.add", {"id": unit, "wid": f"approval:{rid}", "kind": "approval", "ref": rid,
                                  "until": None, "note": summary[:120], "fingerprint": None})]
            if (snap["units"].get(unit) or {}).get("status") == "in_progress":
                # The unit now waits on the owner: an idle lead is parked, and resumed when the answer lands.
                recs.append(("unit.status", {"id": unit, "status": "waiting_owner", "note": f"owner approval {rid}"}))
            cl.append_many(recs)
            log(**base, tier=tier, decision="queued", rid=rid)
            return out("deny", QUEUED_TEXT.format(rid=rid))

        deadline = time.time() + wait
        while time.time() < deadline:
            a = cl.load()["approvals"].get(rid) or {}
            if a.get("answer"):
                log(**base, tier=tier, decision=a["answer"], rid=rid, by=a.get("by"))
                return out(a["answer"], None if a["answer"] == "allow" else
                           f"[foundry] Denied by {a.get('by')} ({rid}): {a.get('reason') or 'no reason given'}")
            time.sleep(1)
        cl.append("approval.mark", rid=rid, field="gave_up")
        log(**base, tier=tier, decision="timeout", rid=rid)
        return out("deny", f"[foundry] No answer within {wait} s; denied and queued as {rid}. "
                           "Continue with other work; the foreman will answer and tell you.")
    except Exception as e:
        try:
            log(decision="error", rid=rid, key=key, reason=traceback.format_exc()[-1500:])
        except Exception:
            pass
        out("deny", f"[foundry] Approver error, denied: {type(e).__name__}: {e}"[:300])


STATE_FILES = re.compile(r"journal\.jsonl|snapshot\.json|approval_ledger")


READERS = ("cat", "head", "tail", "ls", "grep", "rg", "wc", "jq", "less", "stat", "file")


def plain_read(cmd):
    """One read-only program, no operators or redirects: reading a log in the state dir is fine."""
    if re.search(r"[\n\r;&|<>`$()]", cmd):
        return False
    try:
        words = shlex.split(cmd)
    except ValueError:
        return False
    return bool(words) and words[0] in READERS


def bash_decision(caller, cmd, snap, cwd=None):
    """Models answering permission requests: only the foreman, never tier 3, never as the owner.

    Word-based (quotes and variables cannot hide it), and any command naming the state files is refused:
    state is written by the foundry CLI only. The owner's own `!` commands and the board buttons skip this hook.
    """
    if caller == roles.OUTSIDE or not cmd:
        return None
    if STATE_FILES.search(cmd) or (touches_state_dir("Bash", {"command": cmd}, cwd, exempt_roots(snap))
                                    and not plain_read(cmd)):
        return ("foundry state files are written by the foundry CLI only; read them with `foundry unit show`, "
                "`foundry approve list` or `foundry board`.")
    try:
        words = shlex.split(cmd)
    except ValueError:
        words = cmd.split()
    words = [w.strip("'\"") for w in words]
    if "approve" not in words and not any(w.endswith("approve") for w in words):
        return None
    answering = any(w in ("allow", "deny") for w in words)
    if any(w == "owner" or w.endswith("=owner") for w in words) or ("$" in cmd and answering):
        return ("Answering as the owner is the owner's act: the owner answers tier 3 from "
                + cl.owner_how("approve allow|deny <rid>", "[allow]/[deny]") + ". Relay the request to the owner.")
    if not answering:
        return None
    if caller != roles.FOREMAN:
        return (f"A {caller} never answers permission requests: the foreman answers tier 2 and the owner tier 3. "
                "Report what you need instead: `foundry unit report <unit> --text ...`.")
    approvals = snap.get("approvals") or {}
    if any((approvals.get(w) or {}).get("tier") == 3 for w in words):
        at = next(i for i, w in enumerate(words) if w == "approve" or w.endswith("approve"))
        verb = words[at + 1] if at + 1 < len(words) else ""
        quoted = any((w == "--quote" and i + 1 < len(words) and words[i + 1].strip())
                     or (w.startswith("--quote=") and w[8:].strip()) for i, w in enumerate(words))
        if verb == "deny" and quoted:
            return None  # a tier-3 deny on the owner's word: journaled with the quote
        return ("Tier 3 allow is the owner's alone: the owner answers from "
                + cl.owner_how("approve allow <rid>", "[allow]") + ". A deny you record when the owner said so in chat: "
                "`foundry approve deny <rid> --quote \"<the owner's exact words>\"`. Otherwise relay it to the owner.")
    return None


def wake_records(snap, x, behavior, by):
    """An answer to a request its unit waits on: clear that wait and queue one inbox line, which resumes a parked
    or ended lead (launcher.unparkable) and reaches it at session start."""
    uid = x.get("unit")
    u = (snap.get("units") or {}).get(uid or "")
    wid = f"approval:{x['rid']}"
    if not u or not any(w.get("wid") == wid for w in u.get("waits") or []):
        return []
    text = (f"[foundry] {x['rid']} was answered {behavior} by {by}: run the same command again now; "
            f"the recorded answer applies to it once ({(x.get('summary') or '')[:160]}).")
    return [("wait.clear", {"id": uid, "wid": wid}),
            ("inbox.add", {"id": uid, "iid": f"apr-{x['rid']}", "from": "foundry", "text": text})]


# ---------- CLI ----------

def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def cmd_note(a):
    """Evidence and an advisor line on an open request: words only, they never answer it."""
    snap = cl.load()
    x = (snap.get("approvals") or {}).get(a.rid)
    if not x or x.get("answer"):
        return emit({"error": f"{a.rid}: no open request"}) or 2
    if not (a.evidence or a.advisor):
        return emit({"error": "give --evidence and/or --advisor"}) or 2
    fields = {k: " ".join(v.split())[:300] for k, v in (("evidence", a.evidence), ("advisor", a.advisor)) if v}
    cl.append("approval.note", rid=a.rid, **fields)
    return emit({"noted": a.rid, **fields}) or 0


def cmd_approve(a):
    if a.sub == "note":
        return cmd_note(a)
    if a.sub == "list":
        snap, ttl, now = cl.load(), queued_ttl_hours(), cl.now()
        rows = sorted((x for x in snap["approvals"].values()
                       if a.all or (x["answer"] is None and not stale_reason(x, snap, ttl, now))),
                      key=lambda x: x.get("asked") or "")
        return emit(rows) or 0
    if a.sub == "show":
        x = cl.load()["approvals"].get(a.rid)
        if not x:
            print(f"unknown request {a.rid}", file=sys.stderr)
            return 1
        return emit(x) or 0
    if a.sub == "rules":
        return emit([{k: r.get(k) for k in ("source", "tool", "pattern", "where", "action", "reason")}
                     for r in effective_rules(cl.load_config())]) or 0

    behavior = a.sub
    relay = cl.relay_quote(a.by, a.quote)
    if relay:
        import graph
        _, err = graph.check_by("foreman", f"answering {a.rid}")
        if err:
            print(err, file=sys.stderr)
            return 2
    if a.by == "owner":
        err = cl.require_owner(f"answering {a.rid}")
        if err:
            print(err, file=sys.stderr)
            return 2

    def decide(snap):
        x = snap["approvals"].get(a.rid)
        if not x:
            return (1, f"unknown request {a.rid}"), []
        if x["answer"]:
            return (0, {"noop": f"{a.rid} was already answered: {x['answer']} by {x['by']}"}), []
        if x.get("tier") == 3 and a.by != "owner" and not (behavior == "deny" and relay):
            return (2, f"{a.rid} is tier 3 (owner-only): an allow is the owner's alone (from "
                       f"{cl.owner_how(f'approve allow {a.rid}', f'[allow] ({a.rid})')}); a deny the foreman records on the owner's word with --quote "
                       "\"<the owner's exact words>\""), []
        rec = {"rid": a.rid, "behavior": behavior, "by": a.by, "remember": a.remember,
               "reason": a.reason or (cl.RELAY_NOTE if relay else None)} | ({"quote": relay} if relay else {})
        wake = wake_records(snap, x, behavior, a.by)
        res = {"rid": a.rid, "behavior": behavior, "by": a.by, "remembered": a.remember,
               "queued": bool(x.get("gave_up"))}
        if x.get("gave_up"):
            sess = (snap["units"].get(x.get("unit") or "") or {}).get("session")
            live = bool(x.get("unit")) and cl.lead_live(snap, snap["units"][x["unit"]])
            res["next"] = (f"the hook already denied; tell the lead: SendMessage(to={sess!r})" if live else
                           f"the hook already denied; queue it: `foundry inbox route --unit {x.get('unit')} --text ...`")
        if wake:
            res["next"] = f"queued for unit {x['unit']}: its lead is resumed with this answer (no new request id)"
        return (0, res), [("approval.answer", rec), *wake]

    (rc, res), _ = cl.transact(decide)
    if isinstance(res, str):
        print(res, file=sys.stderr)
        return rc
    return emit(res) or rc


def add_cli(sp):
    ap = sp.add_parser("approve").add_subparsers(dest="sub", required=True)
    ap.add_parser("list").add_argument("--all", action="store_true")
    ap.add_parser("show").add_argument("rid")
    ap.add_parser("rules")
    for verdict in ("allow", "deny"):
        p = ap.add_parser(verdict)
        p.add_argument("rid")
        p.add_argument("--by", choices=["foreman", "owner"], default="foreman")
        p.add_argument("--remember", action="store_true")
        p.add_argument("--reason")
        p.add_argument("--quote", help="a tier-3 deny on the owner's word: the owner's exact chat words")
    nt = ap.add_parser("note", help="attach evidence or the advisor's one-line note to an open request (shown on "
                                    "the board's approval card)")
    nt.add_argument("rid")
    nt.add_argument("--evidence", help="facts the request rests on, one short line (PR merged, dev deploy time, ...)")
    nt.add_argument("--advisor", help="the foreman's or advisor's one-line view")
    for p in ap.choices.values():
        p.set_defaults(func=cmd_approve)
