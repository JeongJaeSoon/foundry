"""Git policy guard and PR journal: no wrappers, raw `git -C <abs> ...` one command per call.

pre_tool_use denies only real violations, each with a one-line reason and the corrected command;
post_tool_use journals pushes, opened PRs and merges so state never depends on the model remembering.
Repo policy lives next to the launcher's registry in <state>/repos.json, one entry per `foundry repo add` name,
with these extra keys (missing ones default to POLICY_DEFAULTS): slug (owner/name, matched against `gh -R`),
merge ("self": foundry merges when approved+CLEAN; "auto": approved, then `--auto` or a CLEAN merge; "owner": the
owner merges; unset: prefs merge.default, "self", except names matching prefs merge.owner_repos, the prod IaC repos),
owner_only (no foundry merge at all), argo_app (prod merges into one Argo app go one at a time), release
("batch": one release per group of merged PRs). defaults/repos.example.json seeds them; `<org>` is a placeholder
to replace with the real owner, since only names already in this plugin are spelled out.

Every foundry merge is gated here, not in a prompt: `gh pr merge <n> -R <slug> --match-head-commit <sha>` passes
only when GitHub says the PR is approved, its head is that sha and (without `--auto`) its merge state is CLEAN, and
when none of its files match prefs merge.owner_paths (an infra repo's production paths). A PR the policy keeps for the
owner merges only after `foundry unit order <unit> --pr <slug>#<n> --quote "<the owner's words>"` records the
owner's explicit order. On a release unit the order may come before its prod PR exists: `foundry unit order <unit>
--quote ...` without --pr covers every PR that unit opens.
"""

import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import roles  # noqa: E402

POLICY_DEFAULTS = {"default_branch": "main", "merge": None, "owner_only": False, "argo_app": None, "release": None,
                   "slug": None}
OPERATORS = {";", "&&", "||", "|", "&", "|&", ";;", "(", ")"}
PR_URL_RE = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
PUSH_LINE_RE = re.compile(r"^\s*[+*\- !=]?\s*(?:([0-9a-f]{7,40})(?:\.\.\.?|\.\.)([0-9a-f]{7,40})|\[new branch\])\s+(\S+)\s+->\s+(\S+)",
                          re.M)
TAG_LINE_RE = re.compile(r"^\s*\*?\s*\[new tag\]\s+(\S+)\s+->\s+(\S+)", re.M)
PLAN_MSG = "plan mode: investigate and plan only; ask for proceed (`foundry intake promote <unit>` is the owner's)."

OPS = {}
UNIT_FIELDS = {"prs": [], "branches": [], "owner_orders": [], "reviewed": {}}


# ---------- repos ----------

def repos():
    """The launcher's registry ({name: {path, default_branch, remote, ...}}) with policy defaults filled in."""
    try:
        import launcher
        raw = launcher.load_repos()
    except ImportError:
        import json
        try:
            with open(cl.path("repos.json")) as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
    return {name: POLICY_DEFAULTS | {"name": name} | (r if isinstance(r, dict) else {}) for name, r in raw.items()}


def _merge_prefs():
    try:
        import prefs
        return prefs.load().get("merge") or {}
    except Exception:
        return {}


def _names(repo):
    return {str(repo.get(k) or "").lower() for k in ("name", "slug")} - {""}


def merge_mode(repo, mp=None):
    """self | auto | owner: the repo's own `merge`, else owner for a prefs merge.owner_repos name, else merge.default."""
    if not repo:
        return "owner"
    if repo.get("owner_only"):
        return "owner"
    if repo.get("merge"):
        return repo["merge"]
    mp = _merge_prefs() if mp is None else mp
    if any(fnmatch.fnmatch(n, g) for n in _names(repo) for g in mp.get("owner_repos") or []):
        return "owner"
    return mp.get("default") or "owner"


def owner_paths(repo, mp=None):
    """The path globs that make a PR in this repo the owner's (prefs merge.owner_paths entries whose repo matches)."""
    mp = _merge_prefs() if mp is None else mp
    out = []
    for e in mp.get("owner_paths") or []:
        if isinstance(e, dict) and any(fnmatch.fnmatch(n, str(e.get("repo") or "")) for n in _names(repo or {})):
            out += list(e.get("paths") or [])
    return out


def argo_rules(repo, mp=None):
    """[(app, path globs or None)] the Argo apps a merge in this repo deploys: repos.json `argo_app` (every PR),
    then prefs merge.argo_apps entries whose repo glob matches (only PRs touching their `paths`, when given)."""
    mp = _merge_prefs() if mp is None else mp
    out = [(repo["argo_app"], None)] if (repo or {}).get("argo_app") else []
    for e in mp.get("argo_apps") or []:
        if isinstance(e, dict) and e.get("app") and any(fnmatch.fnmatch(n, str(e.get("repo") or "")) for n in _names(repo or {})):
            out.append((e["app"], list(e["paths"]) if e.get("paths") else None))
    return out


def argo_apps_hit(rules, files):
    """The apps of `rules` a PR with these file paths deploys."""
    paths = [str((f or {}).get("path") or "") for f in files or [] if isinstance(f, dict)]
    return sorted({app for app, globs in rules
                   if globs is None or any(fnmatch.fnmatch(x, g) for x in paths for g in globs)})


def argo_lock(app):
    return f"argo:{app}"


def repo_by_ref(ref, rs=None):
    """A repo by registry name, slug (owner/name) or the name part of a slug."""
    if not ref:
        return None
    rs = rs if rs is not None else repos()
    if ref in rs:
        return rs[ref]
    for r in rs.values():
        if r.get("slug") == ref:
            return r
    tail = ref.rsplit("/", 1)[-1]
    return rs.get(tail)


def _real(p):
    return os.path.realpath(os.path.expanduser(p)) if p else None


def _inside(path, root):
    path, root = _real(path), _real(root)
    return bool(path and root) and (path == root or path.startswith(root.rstrip(os.sep) + os.sep))


def repo_by_path(path, snap=None, rs=None):
    rs = rs if rs is not None else repos()
    for r in rs.values():
        if r.get("path") and _inside(path, r["path"]):
            return r
    for u in (snap or {}).get("units", {}).values():
        w = u.get("worktree") or {}
        if w.get("path") and _inside(path, w["path"]):
            return repo_by_ref(w.get("repo") or u.get("repo"), rs)
    return None


def current_branch(path):
    if not path or not os.path.isdir(path):
        return None
    try:
        r = subprocess.run([os.environ.get("FOUNDRY_GIT") or "git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    b = r.stdout.strip()
    return b if r.returncode == 0 and b and b != "HEAD" else None


def rev_parse(path, ref):
    try:
        r = subprocess.run([os.environ.get("FOUNDRY_GIT") or "git", "-C", path or ".", "rev-parse", "--verify", "--quiet",
                            ref + "^{commit}"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def unit_branch(u):
    if not u:
        return None
    if u.get("branch"):
        return u["branch"]
    w = u.get("worktree") or {}
    return current_branch(w.get("path")) or w.get("branch")


def unit_branches(u):
    """Branches the unit owns: its own branch plus the head of every open PR recorded on it, so a stacked
    PR's second branch is the unit's too."""
    heads = [p.get("branch") for p in (u or {}).get("prs") or []
             if p.get("branch") and (p.get("state") or "OPEN").upper() == "OPEN"]
    return list(dict.fromkeys([b for b in [unit_branch(u), *heads] if b]))


def _flag_value(args, name):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


# ---------- parsing ----------

def tokens(cmd):
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""  # the shell reads `note#1` as one word; shlex would drop everything after the `#`
    return list(lex)


def segments(cmd):
    """[(argv, operator_before)] split on shell operators; raises ValueError on unbalanced quotes."""
    out, cur = [], []
    for t in tokens(cmd):
        if t in OPERATORS:
            out.append(cur)
            cur = []
        else:
            cur.append(t)
    out.append(cur)
    return [s for s in out if s]


def strip_env(argv):
    i = 0
    while i < len(argv) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[i]):
        i += 1
    while i < len(argv) and argv[i] in ("command", "exec", "env", "nohup", "time"):
        i += 1
    return argv[i:]


def program(argv):
    argv = strip_env(argv)
    return os.path.basename(argv[0]) if argv else ""


def git_parts(argv, cwd):
    """(effective dir, subcommand, args) for a git argv."""
    argv = strip_env(argv)[1:]
    d = cwd
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        a = argv[i]
        if a == "-C" and i + 1 < len(argv):
            nxt = os.path.expanduser(argv[i + 1])
            d = nxt if os.path.isabs(nxt) else os.path.join(d or "", nxt)
            i += 2
        elif a in ("-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(argv):
            i += 2
        else:
            i += 1
    return d, (argv[i] if i < len(argv) else ""), argv[i + 1:]


def gh_parts(argv):
    """(repo flag value, [subcommand words], args) for a gh argv."""
    argv = strip_env(argv)[1:]
    repo, words, rest = None, [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-R", "--repo") and i + 1 < len(argv):
            repo = argv[i + 1]
            i += 2
            continue
        if a.startswith("--repo="):
            repo = a.split("=", 1)[1]
        elif not a.startswith("-") and len(words) < 2 and not rest:
            words.append(a)
        else:
            rest.append(a)
        i += 1
    return repo, words, rest


PUSH_VALUE_FLAGS = ("--repo", "--receive-pack", "--exec", "-o", "--push-option")


def push_parts(args):
    """(flags, remote, refspecs) of `git push` args."""
    flags, pos = [], []
    i = 0
    while i < len(args):
        a = args[i]
        if a in PUSH_VALUE_FLAGS and i + 1 < len(args):
            flags.append(a)
            i += 2
            continue
        (flags if a.startswith("-") else pos).append(a)
        i += 1
    return flags, (pos[0] if pos else None), pos[1:]


def push_targets(refspecs, cur):
    """[(dst branch, forced by '+', local src)] of refspecs; none means the current branch."""
    out = []
    for r in refspecs or [None]:
        if r is None:
            out.append((cur, False, "HEAD"))
            continue
        plus = r.startswith("+")
        r = r.lstrip("+")
        src, dst = r.split(":", 1) if ":" in r else (r, r)
        if dst in ("HEAD", "@"):
            dst = cur
        if dst:
            dst = re.sub(r"^refs/heads/", "", dst)
        out.append((dst, plus, src or None))
    return out


def is_force(flags):
    return any(f in ("--force", "-f") or (re.match(r"^-[a-zA-Z]*f[a-zA-Z]*$", f) and not f.startswith("--")) for f in flags)


def has_lease(flags):
    return any(f.startswith("--force-with-lease") for f in flags)


# ---------- the guard ----------

def _unit(snap, ctx):
    return (snap.get("units") or {}).get(ctx.get("unit") or "")


def _q(argv):
    """A command line for a deny reason: quoted only where the shell needs it, so `<sha>` reads as a slot."""
    return " ".join(shlex.quote(a) if re.search(r"[\s'\"$`;&|\\]", a) or not a else a for a in argv)


# Output filters a split drops: the caller reads the whole output instead of piping it.
FILTERS = {"head", "tail", "grep", "egrep", "wc", "sort", "uniq", "jq", "cut", "awk", "sed", "tr", "less", "more",
           "cat", "tee", "xargs", "column", "fold", "nl"}
REDIRECT_RE = re.compile(r"^[0-9]*[<>&]*[<>][<>&]*$")
# Deny reasons about a command's shape only (chaining, pipes, redirects, substitution, the program not being the
# plain first word). A retry that fixes the shape and does the same or less is the correction, not an evasion.
ONE_PER_CALL = "one command per Bash call (no `;`, `&&`, `||`, `|`, redirects)"
SHAPE_MARKS = (ONE_PER_CALL, "no command substitution in git/gh calls", "run programs by their literal name",
               "no variables in git/gh calls", "as the plain first word of one command",
               "one git/gh command per call")


def is_shape_reason(reason):
    return any(m in (reason or "") for m in SHAPE_MARKS)


def _drop_redirects(argv):
    """argv without its redirects (`> f`, `2>&1`, `< in`): each operator and its target go."""
    out, skip, dropped = [], False, False
    for a in argv:
        if skip:
            skip = False
            continue
        if REDIRECT_RE.match(a):
            dropped = skip = True
            if out and out[-1] in ("1", "2"):
                out.pop()  # the fd number of `2>`; shlex splits it off
            continue
        out.append(a)
    return out, dropped


def split_parts(segs):
    """The separate calls a chain becomes, plus notes: `cd X && git ...` becomes `git -C X ...`, pipe filters
    and redirects are dropped (read the whole output; no temp files)."""
    parts, notes, cwd = [], [], None
    for n, argv in enumerate(segs):
        argv, dropped = _drop_redirects(strip_env(argv))
        if dropped and "no redirects or temp files: read the output directly" not in notes:
            notes.append("no redirects or temp files: read the output directly")
        if not argv:
            continue
        if argv[0] == "cd" and len(argv) == 2:
            cwd = argv[1]
            continue
        if n and program(argv) in FILTERS:
            if "no pipe filters: read the whole output" not in notes:
                notes.append("no pipe filters: read the whole output")
            continue
        if cwd and program(argv) == "git" and "-C" not in argv:
            argv = [argv[0], "-C", cwd] + argv[1:]
        parts.append(_q(argv))
    return parts, notes


def split_hint(segs):
    parts, notes = split_parts(segs)
    if len(parts) == 1:
        hint = f"run it alone: `{parts[0]}`"
    else:
        hint = f"run {len(parts)} separate Bash calls: " + ", then ".join(f"`{p}`" for p in parts)
    return hint + "".join(f" ({x})" for x in notes)


def chain_reason(cmd):
    try:
        segs = segments(cmd)
    except ValueError:
        return None
    progs = [program(s) for s in segs]
    if not any(p in ("git", "gh") for p in progs):
        return None
    redirect = any(REDIRECT_RE.match(t) for s in segs for t in s)
    if len(segs) == 1 and not redirect and any("\n" in t for t in segs[0]):
        # One command whose argument spans lines: re-running it "alone" would trip the same rule.
        return (f"{ONE_PER_CALL}: multi-line text never goes inline: write it with the Write tool to a file in "
                "your worktree, then pass the file (`git -C <worktree> commit -F <file>`, "
                "`gh pr create|edit|comment --body-file <file>`)")
    if "\n" in cmd.strip() or len(segs) > 1 or redirect:
        return f"{ONE_PER_CALL}: {split_hint(segs)}"
    if "$(" in cmd or "`" in cmd:
        return ("no command substitution in git/gh calls: run the inner command first and pass its literal output "
                "(for messages: write a file in the worktree and use `git commit -F <file>`)")
    return None


def push_reason(snap, ctx, u, argv, cwd):
    d, _, args = git_parts(argv, cwd)
    flags, remote, refspecs = push_parts(args)
    cur = current_branch(d)
    repo = repo_by_path(d, snap) if d else None
    repo = repo or repo_by_ref((u or {}).get("repo"))
    default = (repo or {}).get("default_branch")
    defaults = (default,) if default else ("main", "master")
    ub = unit_branch(u)
    good = ["git", "-C", d or "<worktree>", "push", remote or "origin", ub or "<unit-branch>"]
    if any(f in ("--mirror", "--all", "--branches") for f in flags):
        return f"no --all/--mirror pushes (they include the default branch): push the unit branch: {_q(good)}"
    if any(f in ("--delete", "-d") for f in flags):
        if any(re.sub(r"^:?(refs/heads/)?", "", r.lstrip("+")) in defaults for r in refspecs):
            return f"no deleting the default branch: push the unit branch: {_q(good)}"
        return None  # deleting other branches is the approver's business, not a branch-policy question
    for dst, plus, _ in push_targets(refspecs, cur):
        if dst in defaults:
            return f"no push to the default branch {dst}: push the unit branch and open a PR: {_q(good)}"
        plain = plus or is_force(flags)
        if plain:
            fixed = [a for a in argv if a not in ("--force", "-f")]
            fixed = [a.lstrip("+") if a in refspecs else a for a in fixed]
            if not has_lease(flags):
                i = fixed.index("push") + 1 if "push" in fixed else len(fixed)
                fixed.insert(i, "--force-with-lease")
            return f"force push needs --force-with-lease (no --force, no +refspec): {_q(fixed)}"
        own = unit_branches(u) if u else []
        if has_lease(flags) and u and dst not in own:
            return (f"force push only to this unit's branches ({', '.join(own) or 'unknown'}: its branch and its "
                    f"open PRs' heads), not {dst}: {_q(good[:4] + ['--force-with-lease'] + good[4:])}")
    if u and u.get("mode") == "plan":
        return PLAN_MSG
    import prefs
    pf = prefs.load()
    if prefs.is_night(p=pf):
        names = {(repo or {}).get("name"), (repo or {}).get("slug")} - {None}
        if not names & set(pf.get("push_exceptions_at_night") or []):
            end = prefs.window_end(cl.now(), pf["night_window"], prefs.tz(pf)).strftime("%H:%M")
            return f"night window {pf['night_window']}: pushes wait until {end}; commit locally and push then: {_q(argv)}"
    return None


def commit_reason(u, argv, cwd):
    w = (u or {}).get("worktree") or {}
    if not w.get("path"):
        return None
    d, _, args = git_parts(argv, cwd)
    if d and _inside(d, w["path"]):
        return None
    return f"commit only inside this unit's worktree {w['path']}: {_q(['git', '-C', w['path'], 'commit'] + args)}"


def held_lock(snap, app, uid):
    """The unit holding the Argo app lock, when it is another unit. Locks are graph's; read defensively."""
    locks = snap.get("locks") or {}
    if not app or not isinstance(locks, dict):
        return None
    for key in (app, f"argo:{app}", f"lock:{app}", f"lock:argo:{app}"):
        v = locks.get(key)
        if not v:
            continue
        holder = v if isinstance(v, str) else (v.get("unit") or v.get("holder") or v.get("by")) if isinstance(v, dict) else None
        closed = ((snap.get("units") or {}).get(holder) or {}).get("status") in ("done", "cancelled")
        if holder and holder != uid and not closed:  # a closed holder frees the lock, as graph.lock_holder says
            return holder
    return None


API_REVIEW_RE = re.compile(r"(?:^|/)repos/([\w.-]+/[\w.-]+)/pulls/(\d+)/reviews\b")
API_MERGE_RE = re.compile(r"(?:^|/)repos/([\w.-]+/[\w.-]+)/pulls/(\d+)/merge\b|\b(?:mergePullRequest|enablePullRequestAutoMerge)\b")


def api_merge_reason(argv):
    """`gh api .../pulls/N/merge` (or the GraphQL merge mutation) is a merge without the merge guard."""
    for a in argv[1:]:
        m = API_MERGE_RE.search(a)
        if m:
            fixed = ["gh", "pr", "merge", m.group(2) or "<n>", "-R", m.group(1) or "<owner/repo>",
                     "--match-head-commit", "<sha>"]
            return f"merge through `gh pr merge`, not the API, so the merge policy applies: {_q(fixed)}"
    m = next((API_REVIEW_RE.search(a) for a in argv[1:] if API_REVIEW_RE.search(a)), None)
    if m and any("APPROVE" in a for a in argv[1:]):
        return (f"approve through `gh pr review {m.group(2)} -R {m.group(1)} --approve --body ...`, not the API, "
                "so the approve gate applies")
    return None


def gh_reason(snap, ctx, u, argv, cwd):
    flag_repo, words, rest = gh_parts(argv)
    if words[:1] == ["api"]:
        return api_merge_reason(strip_env(argv))
    if words[:1] != ["pr"] or len(words) < 2:
        return None
    sub = words[1]
    if sub in ("create", "merge") and u and u.get("mode") == "plan":
        return PLAN_MSG
    if sub == "review" and any(a in ("--approve", "-a") for a in rest):
        repo = repo_by_ref(flag_repo) or (repo_by_path(cwd, snap) if not flag_repo else None) or repo_by_ref((u or {}).get("repo"))
        url_repo, num = _pr_number(rest)
        slug = url_repo or (repo or {}).get("slug") or flag_repo
        if not num or not slug or "/" not in slug:
            return "name the PR and its repo so the approve gate can read it: gh pr review <n> -R <owner/repo> --approve"
        return approve_reason(snap, u, repo_by_ref(slug) or repo, slug, num, rest, _merge_prefs())
    if sub != "merge":
        return None
    mp = _merge_prefs()
    repo = repo_by_ref(flag_repo) or (repo_by_path(cwd, snap) if not flag_repo else None) or repo_by_ref((u or {}).get("repo"))
    name = flag_repo or (repo or {}).get("slug") or (repo or {}).get("name") or "<repo>"
    if any(a == "--admin" or a.startswith("--admin=") for a in rest):
        fixed = [a for a in argv if a != "--admin" and not a.startswith("--admin=")]
        return f"no --admin merges: wait for the required checks and reviews, then: {_q(fixed)}"
    # An unregistered repo is the owner's; so is a prod IaC repo (prefs merge.owner_repos) unless repos.json says otherwise.
    if not repo or repo.get("owner_only"):
        return (f"{name} merges are the owner's: ask on a decision card, in Korean, that the owner merge the PR on "
                "GitHub (its link, never a shell command for them to run)")
    sha = _flag_value(rest, "--match-head-commit")
    if not sha:
        return (f"merge with the reviewed head pinned: {_q(argv + ['--match-head-commit', '<sha>'])} "
                f"(sha: gh pr view <n> -R {name} --json headRefOid)")
    url_repo, num = _pr_number(rest)
    slug = url_repo or repo.get("slug") or flag_repo
    if not num or not slug or "/" not in slug:
        return (f"name the PR and its repo so the merge gate can read it: "
                f"gh pr merge <n> -R <owner/repo> --match-head-commit {sha}")
    mode = merge_mode(repo, mp)
    ordered = _owner_ordered(u, slug, num) or _prod_go(snap, u, slug, num) or bool(
        _unit_go(snap, u) and not deploy_pr_refusal(u, slug, num, mp))
    if mode == "owner" and not ordered:
        return (f"{name} merges are the owner's: ask on a decision card, in Korean, that the owner merge the PR on "
                "GitHub (its link, never a shell command for them to run) "
                f"(or merge it after the foreman records the owner's explicit order: foundry unit order <unit> "
                f"--pr {slug}#{num} --by foreman --quote \"<the owner's words>\")")
    rules = argo_rules(repo, mp)

    def argo_reason(files):
        for app in argo_apps_hit(rules, files):
            holder = held_lock(snap, app, ctx.get("unit"))
            if holder:
                return (f"Argo app {app} is locked by unit {holder}: merge after its sync and PreSync hook finish "
                        f"and it runs `foundry lock release {argo_lock(app)} --unit {holder}` "
                        "(one prod merge per Argo app at a time)")
        return None

    return merge_gate(slug, num, sha, "--auto" in rest, owner_paths(repo, mp), ordered, argv,
                      argo_reason if rules else None)


def _flag_value(rest, flag):
    for i, a in enumerate(rest):
        if a == flag and i + 1 < len(rest):
            return rest[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def _owner_ordered(u, slug, num):
    """An order naming this PR, or a unit-wide order on a release unit for a PR the unit itself opened."""
    ref = f"{slug}#{num}"
    own = ref in (((u or {}).get("keys") or {}).get("pr") or [])
    return any(o.get("quote") and (o.get("pr") == ref or (o.get("pr") is None and own))
               for o in (u or {}).get("owner_orders") or [])


PROD_GO = "go"


def _unit_go(snap, u):
    """The owner's words behind a release unit's unit-wide prod go, else None: a `foundry unit order` without --pr,
    or a `go` answer on its `<unit>:prod-deploy` card (the owner's own, or the foreman's relay with a quote)."""
    uid = (u or {}).get("id")
    if not uid or u.get("release") is None:
        return None
    for o in reversed(u.get("owner_orders") or []):
        if o.get("pr") is None and o.get("quote"):
            return o["quote"]
    for c in (snap.get("cards") or {}).values():
        if c.get("key") == f"{uid}:prod-deploy" and c.get("unit") == uid and c.get("answer") == PROD_GO:
            if c.get("answered_by") == "owner":
                return c.get("answer_quote") or c.get("answer_note") or PROD_GO
            if c.get("answered_by") == "foreman" and c.get("answer_quote"):
                return c["answer_quote"]
    return None


def _prod_go(snap, u, slug, num):
    """The unit-wide go covers the prod PRs the unit opened itself."""
    own = f"{slug}#{num}" in (((u or {}).get("keys") or {}).get("pr") or [])
    return bool(own and _unit_go(snap, u))


TAG_LINE = re.compile(r"""^\s*["']?([\w.-]*tag)["']?\s*:\s*["']?([^"'\s#]+)["']?\s*(?:#.*)?$""", re.I)


def release_tags(u):
    """The tags a release unit shipped: its dev deploy tag and the tags its PRs were released in."""
    rel = (u or {}).get("release") or {}
    tags = [rel.get("dev_deployed")] + list((rel.get("released") or {}).values())
    return {t for t in tags if isinstance(t, str) and t and cl.parse_ts(t) is None}


def _gh_api(path):
    gh = shlex.split(os.environ.get("FOUNDRY_GH", "gh"))
    try:
        r = subprocess.run(gh + ["api", path], capture_output=True, text=True, timeout=20)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


SHA_TOKEN = re.compile(r"\b[0-9a-f]{7,40}\b")


def _same_sha(a, b):
    a, b = str(a or ""), str(b or "")
    return len(a) >= 7 and len(b) >= 7 and (a.startswith(b) or b.startswith(a))


def _deploy_pr_check(u, slug, num, mp, pr=None):
    """(None, new tag values, head) when slug#num is a bot deploy PR whose every changed line swaps one image-tag value:
    author in prefs merge.deploy_pr_authors (or merge.bump_pr_authors); every file a `modified` prefs
    merge.deploy_tag_files file; per change block as many `*tag: <value>` lines removed as added, pairwise the same key;
    on a release unit every new value one of its released tags or `<tag>-<image>` (v1.1.37-app). Judged on the PR's
    facts, not on how the unit was made. Else (why, None, None), naming the failed condition; fails closed when GitHub
    cannot be read."""
    ref = f"{slug}#{num}"
    rel = (u or {}).get("release") is not None
    if not (isinstance(pr, dict) and "changedFiles" in pr and "headRefOid" in pr):
        pr = _gh_pr_view(slug, num, "author,changedFiles,headRefOid")
    if not isinstance(pr, dict):
        return f"[author] could not read {ref} from GitHub to check its author", None, None
    login = str(((pr.get("author") or {}).get("login")) or "")
    bots = list(mp.get("deploy_pr_authors") or []) + list(_bump_prefs(mp)["bump_pr_authors"] or [])
    if login not in bots:
        return (f"[author] {ref} is authored by {login or '?'}, not a deploy bot (prefs merge.deploy_pr_authors): "
                "the owner reviews it on GitHub"), None, None
    tags = release_tags(u)
    if rel and not tags:
        return f"{u.get('id')} has no released tag recorded yet: record the release and the dev deploy first", None, None
    files = _gh_api(f"repos/{slug}/pulls/{num}/files?per_page=100")
    if not isinstance(files, list) or not files:
        return f"[tag-only diff] could not read {ref}'s diff from GitHub to check it is tag-only", None, None
    if len(files) != pr.get("changedFiles"):
        return (f"[tag-only diff] {ref} changes {pr.get('changedFiles')} files but GitHub listed {len(files)}: open a "
                "card for the owner, do not approve"), None, None
    globs = mp.get("deploy_tag_files") or []
    values = set()
    card = "open a card for the owner, do not approve"
    for f in files:
        path = str((f or {}).get("filename") or "")
        if not any(fnmatch.fnmatch(path, x) for x in globs):
            return (f"[tag-only diff] {ref} changes {path}, not an image-tag file (prefs merge.deploy_tag_files): "
                    f"{card}"), None, None
        if (f or {}).get("status") != "modified":
            return (f"[tag-only diff] {ref} {(f or {}).get('status') or 'changes'} {path} instead of modifying it: "
                    f"{card}"), None, None
        patch = (f or {}).get("patch")
        if not patch:
            return f"[tag-only diff] {ref}: no readable patch for {path}: {card}", None, None
        out, inn = [], []
        for line in patch.splitlines() + [" "]:
            if line[:1] not in "+-" or line.startswith(("+++", "---")):
                if len(out) != len(inn):
                    return (f"[tag-only diff] {ref} removes {len(out)} and adds {len(inn)} tag lines in one block of "
                            f"{path}: not a one-line value swap: {card}"), None, None
                for a, b in zip(out, inn):
                    if a.group(1) != b.group(1):
                        return (f"[tag-only diff] {ref} turns key {a.group(1)} into {b.group(1)} in {path}: "
                                f"{card}"), None, None
                    if rel and not any(b.group(2) == t or b.group(2).startswith(t + "-") for t in tags):
                        return (f"{ref} sets {b.group(1)} {b.group(2)} in {path}, not {u.get('id')}'s released tag "
                                f"({', '.join(sorted(tags))}): {card}"), None, None
                    values.add(b.group(2))
                out, inn = [], []
                continue
            m = TAG_LINE.match(line[1:])
            if not m:
                return f"[tag-only diff] {ref} changes a non-tag line in {path} ({line[:80]!r}): {card}", None, None
            (out if line[0] == "-" else inn).append(m)
    head = str((_gh_pr_view(slug, num, "headRefOid") or {}).get("headRefOid") or "")
    if not _same_sha(head, pr.get("headRefOid")):
        return (f"[head pin] {ref} head moved while it was checked ({str(pr.get('headRefOid'))[:12]} -> "
                f"{head[:12] or '?'}): re-read the diff and re-record the reviewed commit"), None, None
    return None, values, head


def deploy_pr_refusal(u, slug, num, mp, pr=None):
    """None when slug#num is a bot deploy PR with a tag-only diff (`_deploy_pr_check`), else why not."""
    return _deploy_pr_check(u, slug, num, mp, pr)[0]


VERSION_LINE = re.compile(r"""^\s*version\s*=\s*["']([^"']+)["']\s*$""")
WORKSPACE_SOURCE = re.compile(r"""^\s*source\s*=\s*\{\s*(editable|virtual)\s*=""")
BUMP_DEFAULTS = {"bump_pr_authors": [], "bump_branch_glob": "release/bump-v*",
                 "bump_files": ["pyproject.toml", "*/pyproject.toml", "uv.lock", "*/uv.lock"]}


def _bump_prefs(mp):
    return {k: mp.get(k, v) for k, v in BUMP_DEFAULTS.items()}


def bump_version(branch, glob):
    """The version a bump branch targets: what the glob's last `*` stands for, without a leading v (release/bump-v1.1.38
    -> 1.1.38), else None."""
    if not branch or "*" not in glob or not fnmatch.fnmatchcase(branch, glob):
        return None
    head, _, tail = glob.rpartition("*")
    rx = re.escape(head).replace(r"\*", ".*") + "(.+)" + re.escape(tail) + "$"
    m = re.match(rx, branch)
    v = m.group(1).lstrip("vV") if m else ""
    return v or None


def _bump_patch_refusal(ref, path, patch, version):
    """None when every changed line of the patch is a version line moving to `version`; in uv.lock also only inside a
    workspace-own package block (its `source = { editable|virtual = ... }` line visible in the hunk)."""
    lock = path.rsplit("/", 1)[-1] == "uv.lock"
    for hunk in re.split(r"^@@[^\n]*@@[^\n]*$", patch, flags=re.M):
        blocks, cur = [], []
        for line in hunk.splitlines():
            if line[1:].strip() == "[[package]]":
                blocks.append(cur)
                cur = []
            cur.append(line)
        blocks.append(cur)
        for block in blocks:
            changed = [x for x in block if x[:1] in ("+", "-") and not x.startswith(("+++", "---"))]
            if not changed:
                continue
            if lock and not any(WORKSPACE_SOURCE.match(x[1:]) for x in block if x[:1] in (" ", "+", "-")):
                return f"{ref} changes {path} outside a workspace-own package's version line"
            for x in changed:
                m = VERSION_LINE.match(x[1:])
                if not m:
                    return f"{ref} changes a non-version line in {path} ({x[:80]!r})"
                if x[0] == "+" and m.group(1) != version:
                    return f"{ref} sets version {m.group(1)} in {path}, not {version} (the branch's target)"
    return None


def bump_pr_refusal(u, slug, num, mp, pr=None):
    """(None, version) when slug#num is a release unit's release bump PR, foundry's to approve and merge under the
    owner's standing rule: authored by prefs merge.bump_pr_authors, head branch matching merge.bump_branch_glob,
    every file matching merge.bump_files, every changed line a version line moving to the branch's version. Else
    (why not, version or None); fails closed when GitHub cannot be read."""
    ref, bp = f"{slug}#{num}", _bump_prefs(mp)
    if (u or {}).get("release") is None:
        return f"{ref}: only a release unit's bump PR skips review", None
    pr = pr if isinstance(pr, dict) and "headRefName" in pr else _gh_pr_view(slug, num, "author,headRefName")
    if not isinstance(pr, dict):
        return f"could not read {ref} from GitHub to check its author and branch", None
    login = str(((pr.get("author") or {}).get("login")) or "")
    if login not in (bp["bump_pr_authors"] or []):
        return f"{ref} is authored by {login or '?'}, not a bump bot (prefs merge.bump_pr_authors)", None
    version = bump_version(str(pr.get("headRefName") or ""), str(bp["bump_branch_glob"] or ""))
    if not version:
        return (f"{ref}'s branch {pr.get('headRefName')!r} does not match prefs merge.bump_branch_glob "
                f"{bp['bump_branch_glob']!r}"), None
    files = _gh_api(f"repos/{slug}/pulls/{num}/files?per_page=100")
    if not isinstance(files, list) or not files:
        return f"could not read {ref}'s diff from GitHub to check it is version-only", version
    for f in files:
        path = str((f or {}).get("filename") or "")
        if not any(fnmatch.fnmatch(path, x) for x in bp["bump_files"] or []):
            return f"{ref} changes {path}, not a version file (prefs merge.bump_files)", version
        patch = (f or {}).get("patch")
        if not patch:
            return f"{ref}: no readable patch for {path}", version
        why = _bump_patch_refusal(ref, path, patch, version)
        if why:
            return why, version
    return None, version


def bump_review_refusal(snap, unit, ref):
    """The refusal text for a review ask or reviewer pick on a PR the release-bump class covers, else None."""
    u = ((snap or {}).get("units") or {}).get(unit or "")
    if not u or u.get("release") is None:
        return None
    m = cl.norm_pr(ref)
    if not m:
        return None
    slug, num = m[0].split("#")
    why, version = bump_pr_refusal(u, slug, num, _merge_prefs())
    if why:
        return None
    return (f"refused: {slug}#{num} is a release bump PR (version-only, from the bump bot): the owner's standing rule "
            f"is no review ask. Approve it with the owner account: gh pr review {num} -R {slug} --approve --body "
            f"\"{version} release bump (owner standing rule)\", then once checks are green and it is CLEAN: "
            f"gh pr merge {num} -R {slug} --squash --match-head-commit <sha>")


def bump_approve_reason(u, slug, num, rest, mp):
    """`gh pr review --approve` on a non-owner repo PR: a bump-bot PR on a release-bump branch is approved only when the
    whole class holds (else it takes the normal review path) and the body names the version. Other PRs: None."""
    if (u or {}).get("release") is None:
        return None
    bp = _bump_prefs(mp)
    pr = _gh_pr_view(slug, num, "author,headRefName")
    if not isinstance(pr, dict):
        return None
    login = str(((pr.get("author") or {}).get("login")) or "")
    if login not in (bp["bump_pr_authors"] or []) or not fnmatch.fnmatchcase(str(pr.get("headRefName") or ""),
                                                                             str(bp["bump_branch_glob"] or "")):
        return None
    why, version = bump_pr_refusal(u, slug, num, mp, pr)
    if why:
        return (f"{why}: not a release bump the owner's standing rule covers, so it takes the normal review path "
                f"(foundry review assign {slug}#{num} --unit {u.get('id')}); do not approve it yourself")
    body = _flag_value(rest, "--body") or _flag_value(rest, "-b") or ""
    if version not in body:
        return (f"say what is approved: gh pr review {num} -R {slug} --approve --body "
                f"\"{version} release bump (owner standing rule)\"")
    return None


def approve_reason(snap, u, repo, slug, num, rest, mp):
    """`gh pr review --approve` on a PR the merge policy keeps for the owner: foundry approves only a release unit's
    bot deploy PR (tag-only diff to the unit's released tag) after the owner's prod go is recorded."""
    ref = f"{slug}#{num}"
    repo = repo or {"slug": slug, "name": slug.split("/")[-1]}  # unregistered: judged by name and paths only
    if merge_mode(repo, mp) != "owner":
        globs = owner_paths(repo, mp)
        if not globs:
            return bump_approve_reason(u, slug, num, rest, mp)
        pr = _gh_pr_view(slug, num, "author,files")
        if not isinstance(pr, dict):
            return f"could not read {ref} from GitHub to check which paths it touches"
        if not any(fnmatch.fnmatch(str((f or {}).get("path") or ""), x)
                   for f in pr.get("files") or [] for x in globs):
            return bump_approve_reason(u, slug, num, rest, mp)
    else:
        pr = None
    uid = (u or {}).get("id")
    quote = _unit_go(snap, u) or next((o.get("quote") for o in (u or {}).get("owner_orders") or []
                                      if o.get("pr") == ref and o.get("quote")), None)
    if not quote and (u or {}).get("release") is not None:
        return (f"[owner order] {ref}: the owner's prod go for {uid} is not recorded yet: a `go` answer on its "
                f"`{uid}:prod-deploy` card or the foreman's `foundry unit order {uid} --by foreman "
                "--quote ...`. Until then it is a wait; never ask the owner to approve, merge or choose a Slack ask")
    if not quote:
        return (f"[owner order] {ref} is a prod PR and no owner order names it: the foreman records the owner's "
                f"words with `foundry unit order {uid or '<unit>'} --pr {ref} --by foreman --quote ...`, else the "
                "owner reviews it on GitHub. Until then it is a wait; never ask the owner to approve, merge or choose "
                "a Slack ask")
    why, values, head = _deploy_pr_check(u, slug, num, mp, pr)
    if why:
        return why
    pin = ((u or {}).get("reviewed") or {}).get(ref)
    if not pin:
        return (f"[head pin] {uid or 'this unit'} recorded no reviewed commit for {ref}: after reading its diff, "
                f"`foundry unit reviewed {uid or '<unit>'} --pr {ref} --sha {head}`, then approve naming that sha")
    if not _same_sha(head, pin):
        return (f"[head pin] {ref} head is {head[:12]}, not the reviewed {pin[:12]}: re-read the diff, re-record "
                f"`foundry unit reviewed {uid} --pr {ref} --sha {head}`, then approve")
    body = _flag_value(rest, "--body") or _flag_value(rest, "-b") or ""
    tags = release_tags(u) or values
    if not any(_same_sha(head, t) for t in SHA_TOKEN.findall(body)) or not any(t in body for t in tags):
        return (f"say what is approved, pinned to the reviewed head: gh pr review {num} -R {slug} --approve --body "
                f"\"{sorted(tags)[0]} prod deploy at {head[:12]} (owner order: {quote})\"")
    return None


def _gh_pr_view(slug, num, fields):
    gh = shlex.split(os.environ.get("FOUNDRY_GH", "gh"))
    try:
        r = subprocess.run(gh + ["pr", "view", str(num), "-R", slug, "--json", fields],
                           capture_output=True, text=True, timeout=20)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def merge_gate(slug, num, sha, auto, prod_globs, ordered, argv, argo_reason=None):
    """None when GitHub says the PR may merge now; else the reason. Fails closed when GitHub cannot be read."""
    fields = "headRefOid,reviewDecision,mergeStateStatus,latestReviews" + (",files" if prod_globs or argo_reason else "")
    pr = _gh_pr_view(slug, num, fields)
    if not isinstance(pr, dict):
        return f"could not read {slug}#{num} from GitHub to check approval and merge state: retry {_q(argv)}"
    head = str(pr.get("headRefOid") or "")
    if not head or not (head.startswith(sha) or sha.startswith(head)):
        return f"{slug}#{num} head is {head[:12] or '?'}, not {sha}: re-read it, re-check review and CI, then pin the new sha"
    state = str(pr.get("mergeStateStatus") or "")
    if state in ("DIRTY", "BEHIND"):
        how = "has merge conflicts with its base" if state == "DIRTY" else "is behind its base"
        return (f"{slug}#{num} {how} ({state}); waiting will not fix it: update the branch against the base "
                "(merge or rebase it), resolve, run the tests, push, re-request review from the previous approver(s) "
                "(the push may dismiss the approval), `foundry wait add --kind pr-review`, then merge with the new "
                "head pinned once approved, CLEAN and green")
    approved = pr.get("reviewDecision") == "APPROVED" or (
        not pr.get("reviewDecision") and any((x or {}).get("state") == "APPROVED" for x in pr.get("latestReviews") or []))
    if not approved:
        return f"{slug}#{num} is not approved ({pr.get('reviewDecision') or 'no approving review'}): wait for the review"
    if not auto and state != "CLEAN":
        return f"{slug}#{num} merge state is {state or 'unknown'}, not CLEAN: wait for CI and required reviews"
    if prod_globs and not ordered:
        hits = sorted({f.get("path") for f in pr.get("files") or [] if isinstance(f, dict)
                       and any(fnmatch.fnmatch(str(f.get("path") or ""), g) for g in prod_globs)})
        if hits:
            return (f"{slug}#{num} touches production paths ({', '.join(hits[:3])}): it merges here once the owner's "
                    "go is recorded: a `go` answer on the release unit's `<unit>:prod-deploy` card, or the foreman's "
                    f"`foundry unit order <unit> [--pr {slug}#{num}] --by foreman --quote \"<words>\"`. Until then it "
                    "is a wait; never hand the owner a merge line or a /foundry command")
    return argo_reason(pr.get("files")) if argo_reason else None


GUARDED = ("git", "gh", "claude", "foundry", "foundry.py")
WRAPPERS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "env", "command", "builtin", "timeout", "gtimeout", "nice",
            "nohup", "xargs", "eval", "exec", "sudo", "doas", "time", "stdbuf", "caffeinate", "script", "watch",
            "parallel", "unbuffer", "setsid", "chronic"}
CONTROL = {"{", "}", "if", "then", "else", "elif", "fi", "!", "for", "while", "until", "do", "done", "case", "esac",
           "select", "function", "coproc", "[[", "]]"}
GUARDED_IN_TEXT = re.compile(r"(?:^|[\s;&|(`{!])(?:[^\s;&|'\"]*/)?(git|gh|claude|foundry(?:\.py)?)(?=$|[\s;&|)}'\"])")
ENV_GUARD_RE = re.compile(r"\b(?:FOUNDRY_\w*|CLAUDE_CONFIG_DIR)[:+]?=|(?:\bunset|\s-u|--unset)[\s=]+['\"]?"
                          r"(?:FOUNDRY_\w*|CLAUDE_CONFIG_DIR)\b|\bunset\s+(?:-[vf]\s+)?(?:\S+\s+)*(?:FOUNDRY_|CLAUDE_CONFIG_DIR)")
ENV_CLEAR_RE = re.compile(r"(?:^|[\s;&|(`{!])(?:\S*/)?env(?:\s+-[^\s]*)*\s+(?:-[A-Za-z]*i[A-Za-z]*|-|--ignore-environment)(?=\s|$)")
INTERPRETERS = re.compile(r"^(?:python[\d.]*|node|ruby|perl|php|osascript|deno|bun)$")
VALUE_RE = re.compile(r"^(?:\d+(?:\.\d+)?[smhd]?|[A-Za-z_]\w*=.*)$")
# Wrapper options that take the next word as their value (env -u, sudo -u, timeout -s/-k, nice -n, xargs -I/-n ...).
VALUE_OPTS = {"-u", "-s", "-k", "-n", "-I", "-L", "-P", "-d", "-g", "-C", "-a", "-c", "-D", "-U", "-S", "-o",
              "--signal", "--kill-after", "--adjustment", "--unset", "--chdir", "--user", "--group", "--max-args",
              "--max-procs", "--delimiter", "--replace", "--arg-file"}


def _guarded(word):
    return os.path.basename(word) in GUARDED


def _plain_tail(seg):
    """The argv from the first guarded word on: the corrected command a wrapper or control word was hiding."""
    for i, w in enumerate(seg):
        if _guarded(w):
            return seg[i:]
        m = GUARDED_IN_TEXT.search(w) if " " in w else None
        if m:
            inner = re.split(r"[;&|\n]", w[m.start(1):])[0]
            try:
                return shlex.split(inner)
            except ValueError:
                return inner.split()
    return seg


def _wrapped_guarded(words):
    """A guarded program run through a wrapper: scan past options, option values and durations to the wrapped
    program; also catch a `-c '<script>'` string that runs one."""
    prev_opt = False
    for w in words:
        if _guarded(w) or (" " in w or "\n" in w) and GUARDED_IN_TEXT.search(w):
            return True
        if w.startswith("-"):
            prev_opt = w in VALUE_OPTS
            continue
        if os.path.basename(w) in WRAPPERS or VALUE_RE.match(w) or prev_opt:
            prev_opt = False
            continue
        if INTERPRETERS.match(os.path.basename(w)):
            continue  # `env -u X python3 .../foundry.py ...`: the script is what runs
        return False  # a plain, unguarded program is what the wrapper runs
    return False


def _expands(cmd):
    """Whether cmd has a `$` the shell would expand: outside single quotes and not backslash-escaped."""
    single = double = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == "\\" and not single:
            i += 2
            continue
        if c == "'" and not double:
            single = not single
        elif c == '"' and not single:
            double = not double
        elif c == "$" and not single and i + 1 < len(cmd) and (cmd[i + 1].isalnum() or cmd[i + 1] in "_{(@*#?!-$"):
            return True
        i += 1
    return False


def wrapper_reason(cmd):
    """git/gh/claude/foundry must be the plain first word of one simple command: no wrapper, shell, grouping or
    variable can stand between the policy and the program it guards. Also no FOUNDRY_* / CLAUDE_CONFIG_DIR
    assignment or unset, which would change who the hooks think is calling."""
    if ENV_CLEAR_RE.search(cmd):
        return ("no `env -i` / cleared environment: it hides who is calling. Run the command with the environment "
                "the session was given, e.g. `foundry unit report <unit> --text ...`")
    if ENV_GUARD_RE.search(cmd):
        return ("no FOUNDRY_*/CLAUDE_CONFIG_DIR assignment or unset: run the command with the environment the "
                "session was given, e.g. `foundry unit report <unit> --text ...`")
    try:
        segs = segments(cmd)
    except ValueError:
        return None
    for seg in segs:
        i = 0
        while i < len(seg) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[i]):
            i += 1
        argv = seg[i:]
        controlled = False
        while argv and argv[0] in CONTROL:
            argv, controlled = argv[1:], True
        if not argv:
            continue
        head = argv[0]
        if "$" in head or "`" in head:
            return "run programs by their literal name, not through a variable: e.g. `foundry unit show <unit>`"
        base = os.path.basename(head)
        hidden = (controlled and _guarded(head)) or (base in WRAPPERS and _wrapped_guarded(argv[1:])) or \
                 (base == "find" and any(_guarded(w) for w in argv[1:]) and
                  any(w in ("-exec", "-execdir", "-ok", "-okdir") for w in argv))
        if hidden:
            fixed = _plain_tail(argv)
            return (f"run {os.path.basename(fixed[0]) if fixed else 'git/gh/claude/foundry'} as the plain first word "
                    f"of one command (no wrapper, shell -c, grouping or control word): {_q(fixed)}")
        if base in ("git", "gh") and "$(" not in cmd and _expands(cmd):
            return f"no variables in git/gh calls: pass literal values: {_q(argv)}"
    return None


GH_API_WRITE_RE = re.compile(r"/git/refs\b|/merges\b|/git/updateRef|updateRef|mergeBranch|query=@|--input\b")


def gh_api_reason(argv):
    """`gh api` writes that move a branch or merge outside `gh pr merge`; GraphQL from a file is not readable."""
    if len(argv) < 2 or os.path.basename(argv[0]) != "gh" or argv[1] != "api":
        return None
    method = next((argv[i + 1] for i, w in enumerate(argv[:-1]) if w in ("-X", "--method")), None)
    method = method or next((w.split("=", 1)[1] for w in argv if w.startswith(("--method=", "-X="))), None)
    writes = (method or "GET").upper() != "GET" or any(w.startswith(("-f", "-F", "--field", "--raw-field")) for w in argv)
    if writes and any(GH_API_WRITE_RE.search(w) for w in argv[2:]):
        return ("no branch moves or merges through `gh api`: push the unit branch and merge with "
                "`gh pr merge <n> -R <owner/repo> --match-head-commit <sha>`")
    return None


def bash_reason(inp, snap, ctx):
    cmd = (ctx.get("input") or {}).get("command") or ""
    why = wrapper_reason(cmd)
    if why:
        return why
    try:
        for seg in segments(cmd):
            why = gh_api_reason(seg)
            if why:
                return why
    except ValueError:
        pass
    if not re.search(r"\b(git|gh)\b", cmd):
        return None
    why = chain_reason(cmd)
    if why:
        return why
    try:
        segs = segments(cmd)
    except ValueError:
        return None
    if len(segs) != 1:
        return None
    argv, cwd, u = segs[0], inp.get("cwd"), _unit(snap, ctx)
    prog = program(argv)
    if prog == "git":
        _, sub, args = git_parts(argv, cwd)
        if sub == "worktree" and args[:1] == ["remove"]:
            return WORKTREE_REMOVE_MSG
        if sub == "push":
            return push_reason(snap, ctx, u, argv, cwd)
        if sub == "commit":
            return commit_reason(u, argv, cwd)
    elif prog == "gh":
        return gh_reason(snap, ctx, u, argv, cwd)
    return None


WORKTREE_REMOVE_MSG = ("remove worktrees only with `foundry sweep remove-worktree <path> --by owner|foreman "
                       "--reason ...`: it refuses a worktree a live session uses as its directory and never forces")


def rm_worktree_reason(cmd, cwd):
    """`rm -r` of a whole git worktree (a directory holding a `.git` file) from a foundry session."""
    try:
        segs = segments(cmd)
    except ValueError:
        return None
    for argv in segs:
        if program(argv) != "rm":
            continue
        args = strip_env(argv)[1:]
        if not any(a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a) or a == "--recursive"
                   for a in args):
            continue
        for a in args:
            if a.startswith("-"):
                continue
            p = os.path.expanduser(a)
            p = p if os.path.isabs(p) else os.path.join(cwd or "", p)
            if os.path.isfile(os.path.join(p.rstrip("/"), ".git")):
                return WORKTREE_REMOVE_MSG
    return None


def pre_tool_use(inp, snap, ctx):
    if ctx.get("caller") == roles.OUTSIDE:
        return None
    tool = ctx.get("tool") or ""
    why = None
    if tool == "Bash":
        why = rm_worktree_reason((ctx.get("input") or {}).get("command") or "", inp.get("cwd")) or \
            bash_reason(inp, snap, ctx)
    else:
        import slackguard as sg
        u = _unit(snap, ctx)
        if sg.is_slack_send(tool) and u and u.get("mode") == "plan":
            why = PLAN_MSG
    if why:
        _record_denial(ctx, why)
    return why


def _record_denial(ctx, why):
    try:
        import triage
    except ImportError:
        return
    triage.record(ctx.get("unit"), ctx.get("tool"), (ctx.get("input") or {}).get("command") or "", why, "hook")


# ---------- journal: pushes, PRs, merges ----------

def _text(resp):
    if isinstance(resp, dict):
        return "\n".join(str(resp.get(k) or "") for k in ("stdout", "stderr", "output"))
    return str(resp or "")


def _pr_number(rest):
    for a in rest:
        m = PR_URL_RE.search(a)
        if m:
            return m.group(1), int(m.group(2))
        if re.fullmatch(r"#?\d+", a):
            return None, int(a.lstrip("#"))
    return None, None


def _repo_name(snap, u, flag_repo=None, path=None):
    r = repo_by_ref(flag_repo) if flag_repo else None
    r = r or (repo_by_path(path, snap) if path else None) or repo_by_ref((u or {}).get("repo"))
    return flag_repo or (r or {}).get("slug") or (r or {}).get("name") or (u or {}).get("repo")


def argo_lock_records(snap, uid, repo_ref, num):
    """A merge that deploys into an Argo app takes that app's lock for the merging unit; its lead releases it
    (`foundry lock release argo:<app> --unit <id>`) once the sync and the PreSync hook finished with the new image."""
    rules = argo_rules(repo_by_ref(repo_ref) or ({"name": repo_ref} if repo_ref else None))
    if not uid or not rules or not num:
        return []
    files = None
    if any(g is not None for _, g in rules):
        files = (_gh_pr_view(repo_ref, num, "files") or {}).get("files") if "/" in str(repo_ref) else None
    apps = argo_apps_hit([(a, g) for a, g in rules if g is None or files is not None], files)
    return [("lock.take", {"name": argo_lock(app), "unit": uid}) for app in apps
            if not held_lock(snap, app, None)]


def journal_records(inp, snap, ctx):
    """The records a successful Bash call implies (pure apart from `git rev-parse` for the branch)."""
    cmd = (ctx.get("input") or {}).get("command") or ""
    if not re.search(r"\b(git|gh)\b", cmd):
        return []
    try:
        segs = segments(cmd)
    except ValueError:
        return []
    if len(segs) != 1:
        return []
    argv, cwd, u, uid = segs[0], inp.get("cwd"), _unit(snap, ctx), ctx.get("unit")
    out = _text(inp.get("tool_response"))
    prog = program(argv)
    if prog == "git":
        d, sub, args = git_parts(argv, cwd)
        if sub != "push":
            return []
        flags, _, refspecs = push_parts(args)
        if any(f in ("--delete", "-d", "--dry-run", "-n") for f in flags):
            return []
        repo = _repo_name(snap, u, path=d)
        shas = {m.group(4): m.group(2) for m in PUSH_LINE_RE.finditer(out)}
        # A tag is known from git's own `[new tag]` line, a refs/tags/ refspec, or `push <remote> tag <name>`.
        tags = [m.group(2) for m in TAG_LINE_RE.finditer(out)]
        for i, r in enumerate(refspecs):
            name = r.lstrip("+").split(":")[-1]
            if name.startswith("refs/tags/"):
                tags.append(name[len("refs/tags/"):])
            elif i and refspecs[i - 1] == "tag":
                tags.append(name)
        tags = list(dict.fromkeys(tags))
        recs = [("tag.pushed", {"unit": uid, "repo": repo, "tag": t, "sha": rev_parse(d, t)}) for t in tags]
        if "--tags" in flags and not refspecs:
            return recs
        for dst, _, src in push_targets([r for r in refspecs if r != "tag"] if refspecs else refspecs, current_branch(d)):
            if dst and dst not in tags and not dst.startswith("refs/tags/"):
                sha = shas.get(dst) or (rev_parse(d, src) if src else None)
                recs.append(("branch.pushed", {"unit": uid, "repo": repo, "branch": dst, "sha": sha}))
        return recs
    if prog == "gh":
        flag_repo, words, rest = gh_parts(argv)
        if words == ["pr", "create"]:
            m = PR_URL_RE.search(out)
            if m:
                head = _flag_value(rest, "--head") or _flag_value(rest, "-H") or current_branch(cwd)
                return [("pr.opened", {"unit": uid, "repo": m.group(1), "number": int(m.group(2)), "url": m.group(0),
                                       "branch": head})]
        if words == ["pr", "merge"] and "--auto" not in rest:
            url_repo, num = _pr_number(rest)
            repo = url_repo or _repo_name(snap, u, flag_repo, cwd)
            return ([("pr.merged", {"unit": uid, "repo": repo, "number": num})]
                    + argo_lock_records(snap, uid, repo, num))
    return []


def post_tool_use(inp, snap, ctx):
    if ctx.get("tool") != "Bash" or ctx.get("caller") == roles.OUTSIDE:
        return
    recs = journal_records(inp, snap, ctx)
    if recs:
        cl.append_many(recs)


def _pr(u, repo, number):
    for p in u["prs"]:
        if p.get("repo") == repo and p.get("number") == number:
            return p
    p = {"repo": repo, "number": number, "url": None, "state": "OPEN", "head": None, "branch": None,
         "opened": None, "merged": None}
    u["prs"].append(p)
    return p


def _units(s, r):
    uid = r.get("unit")
    return [cl._unit(s, uid)] if uid else []


def _op_branch_pushed(s, r, ts):
    for u in _units(s, r):
        b = next((x for x in u["branches"] if x.get("repo") == r.get("repo") and x.get("branch") == r.get("branch")), None)
        if not b:
            b = {"repo": r.get("repo"), "branch": r.get("branch"), "sha": None, "pushed": None, "pushes": 0}
            u["branches"].append(b)
        b.update({"sha": r.get("sha") or b["sha"], "pushed": ts, "pushes": b.get("pushes", 0) + 1})
        u["updated"] = ts


def _op_pr_opened(s, r, ts):
    for u in _units(s, r):
        p = _pr(u, r.get("repo"), r.get("number"))
        p.update({"url": r.get("url") or p["url"], "opened": p["opened"] or ts, "state": "OPEN",
                  "branch": r.get("branch") or p.get("branch")})
        ref = f"{r.get('repo')}#{r.get('number')}"
        if r.get("repo") and ref not in u["keys"]["pr"]:
            u["keys"]["pr"].append(ref)
        u["updated"] = ts


def _op_pr_merged(s, r, ts):
    for u in _units(s, r):
        p = _pr(u, r.get("repo"), r.get("number"))
        p.update({"state": "MERGED", "merged": ts})
        u["updated"] = ts


def _op_pr_state(s, r, ts):
    for u in _units(s, r):
        p = _pr(u, r.get("repo"), r.get("number"))
        if str(r.get("review") or "").upper() == "APPROVED" and str(p.get("review") or "").upper() != "APPROVED":
            p["approved"] = ts  # when it turned approved: the handover's 「이후 변경」 reads it
        for k in ("state", "head", "url", "branch", "title", "review", "ci", "merge_state"):
            if r.get(k):
                p[k] = r[k]
        for k in ("reviewers", "approvers", "auto_reviewers"):
            if r.get(k) is not None:
                p[k] = list(r[k])
        if r.get("created") and not p.get("opened"):
            p["opened"] = r["created"]
        if r.get("state") == "MERGED" and not p["merged"]:
            p["merged"] = ts


def _op_pr_released(s, r, ts):
    for u in _units(s, r):
        held = any(p.get("repo") == r.get("repo") and p.get("number") == r.get("number") for p in u["prs"])
        if not held and u.get("release") is not None:  # a release unit covers it without holding it as its own PR
            u["release"].setdefault("released", {})[f"{r.get('repo')}#{r.get('number')}"] = r.get("tag") or ts
            continue
        p = _pr(u, r.get("repo"), r.get("number"))
        p["released"] = r.get("tag") or ts


def _op_owner_order(s, r, ts):
    u = (s.get("units") or {}).get(r.get("unit"))
    if u is not None:
        u.setdefault("owner_orders", []).append({"pr": r.get("pr"), "quote": r.get("quote"), "by": r.get("by"),
                                                 "ts": ts})


def _op_pr_reviewed(s, r, ts):
    u = (s.get("units") or {}).get(r.get("unit"))
    if u is not None:
        u.setdefault("reviewed", {})[r.get("pr")] = r.get("sha")


OPS.update({"branch.pushed": _op_branch_pushed, "pr.opened": _op_pr_opened, "pr.merged": _op_pr_merged,
            "pr.state": _op_pr_state, "pr.released": _op_pr_released, "owner.order": _op_owner_order,
            "pr.reviewed": _op_pr_reviewed})


ORDER_RE = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")


def cmd_order(a):
    """The owner's explicit order to merge a PR the merge policy keeps for the owner (prod deploy, prod IaC)."""
    import graph
    snap = cl.load()
    if a.id not in snap["units"]:
        print(cl.unknown_unit(a.id, snap), file=sys.stderr)
        return 2
    if a.pr is not None and not ORDER_RE.match(a.pr):
        print(f"refused: --pr is owner/repo#n (got {a.pr!r})", file=sys.stderr)
        return 2
    if a.pr is None and snap["units"][a.id].get("release") is None:
        print(f"refused: {a.id} is not a release unit: name the PR with --pr owner/repo#n", file=sys.stderr)
        return 2
    by, err = graph.check_by(a.by, "recording an owner order")
    if err or by not in ("owner", "foreman"):
        print(err or "refused: only the owner, or the foreman relaying the owner's words, records an owner order",
              file=sys.stderr)
        return 2
    quote = (a.quote or "").strip()
    if not quote:
        print("refused: --quote \"<the owner's exact words>\" is required: the order is the owner's, not yours",
              file=sys.stderr)
        return 2
    cl.append("owner.order", unit=a.id, pr=a.pr, quote=quote, by=by)
    print(json.dumps({"ordered": a.pr or "every PR this unit opens", "unit": a.id, "by": by}, ensure_ascii=False))
    return 0


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def cmd_reviewed(a):
    """The commit a lead read a PR's diff at: the approve gate pins a prod deploy approval to it."""
    import graph
    snap = cl.load()
    if a.id not in snap["units"]:
        print(cl.unknown_unit(a.id, snap), file=sys.stderr)
        return 2
    if not ORDER_RE.match(a.pr or "") or not FULL_SHA.match(a.sha or ""):
        print("refused: --pr owner/repo#n --sha <the full 40-hex headRefOid you read the diff at>", file=sys.stderr)
        return 2
    by, err = graph.check_by(None, "recording a reviewed commit")
    if err:
        print(err, file=sys.stderr)
        return 2
    cl.append("pr.reviewed", unit=a.id, pr=a.pr, sha=a.sha, by=by)
    print(json.dumps({"unit": a.id, "pr": a.pr, "reviewed": a.sha}))
    return 0


def add_unit_cli(u):
    rv = u.add_parser("reviewed", help="record the head commit you read a PR's diff at (pins a deploy approval)")
    rv.add_argument("id")
    rv.add_argument("--pr", required=True, help="owner/repo#n")
    rv.add_argument("--sha", required=True, help="the full headRefOid")
    rv.set_defaults(func=cmd_reviewed)
    o = u.add_parser("order", help="record the owner's explicit order to merge an owner-policy PR")
    o.add_argument("id")
    o.add_argument("--pr", help="owner/repo#n; omit on a release unit to cover every PR it opens (its prod PR)")
    o.add_argument("--quote", required=True, help="the owner's exact words")
    o.add_argument("--by", choices=["foreman", "owner"], help="foreman relays; owner only from the owner's own input")
    o.set_defaults(func=cmd_order)


def board(snap, model):
    for sec in model.get("sections") or []:
        for row in sec.get("rows") or []:
            u = (snap.get("units") or {}).get(row.get("id")) or {}
            row["pr_items"] = u.get("prs") or []
            row["branches"] = u.get("branches") or []
