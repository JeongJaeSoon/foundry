"""Whether a worktree's leftovers already exist elsewhere, so housekeeping may remove it without the owner.

A leftover is an uncommitted change or a commit no remote holds. It is accounted for when it is
- a lead's working note: an untracked top-level file named like MEMO_GLOBS (.pr-body.md, .foundry-*.md, ...);
- a commit `git cherry` finds patch-equivalent in the default branch (origin/HEAD; with no remote, the local
  main/master it was merged into), or whose patch-id equals a commit of one of the repo's recent PRs (`gh pr diff
  --patch`), e.g. a commit rebased into another PR.
A tracked change or any other untracked file is never removed (git would need --force): it stays a proposal, shown
with where its content is found (its blob in the default branch or the PR head) or as a change found nowhere.
Every check fails closed: a git or gh call that does not answer leaves the leftover unproven.
"""

import datetime as dt
import fnmatch
import json
import os
import shlex
import subprocess
import tarfile

import foundrylib as cl

GIT = shlex.split(os.environ.get("FOUNDRY_GIT", "git"))
GH = shlex.split(os.environ.get("FOUNDRY_GH", "gh"))
MEMO_GLOBS = (".foundry-*", ".pr-body*", ".report*", ".card-*", ".prod-card*", ".reply-*", ".git-commit-msg*",
              ".commit-msg*", "review-*-draft.md", "close-*-comment.md")
PR_LIMIT = 30
ARCHIVE_DAYS = 30
_PR_IDS = {}


def _run(argv, cwd=None, data=None):
    try:
        r = subprocess.run(argv, cwd=cwd, input=data, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def git(path, *args, data=None):
    return _run([*GIT, "-C", path, *args], data=data)


def is_memo(name):
    return "/" not in name and any(fnmatch.fnmatchcase(name, g) for g in MEMO_GLOBS)


def entries(path):
    """[(xy, name)] of uncommitted changes, untracked files listed one by one (ignored ones excluded); None on error."""
    rc, out = git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if rc:
        return None
    parts, res, i = out.split("\0"), [], 0
    while i < len(parts):
        p = parts[i]
        i += 1
        if len(p) < 4:
            continue
        res.append((p[:2], p[3:]))
        if p[0] in "RC":
            i += 1  # the rename's source path
    return res


def base_ref(path):
    """(default branch ref, has a remote): origin/HEAD (or origin/main|master); with no remote, local main|master."""
    rc, remotes = git(path, "remote")
    if rc:
        return None, None
    if remotes.strip():
        rc, ref = git(path, "rev-parse", "--abbrev-ref", "origin/HEAD")
        cands = [ref.strip()] if rc == 0 and ref.strip() else []
        cands += ["origin/main", "origin/master"]
    else:
        cands = ["main", "master"]
    for c in cands:
        if git(path, "rev-parse", "--verify", "-q", c)[0] == 0:
            return c, bool(remotes.strip())
    return None, bool(remotes.strip())


def unique_commits(path, ref, has_remote):
    """Commits on HEAD that no remote holds (or, with no remote, that the local default branch lacks)."""
    rc, out = git(path, "rev-list", "HEAD", "--not", *(["--remotes"] if has_remote else [ref]))
    return out.split() if rc == 0 else None


def _patch_ids(text, path):
    rc, out = git(path, "patch-id", "--stable", data=text)
    return [ln.split()[0] for ln in out.splitlines() if ln.split()] if rc == 0 else []


def pr_patch_ids(path, slug):
    """{patch-id: "owner/repo#n"} over the repo's PR_LIMIT most recent PRs, cached per scan process."""
    if slug in _PR_IDS:
        return _PR_IDS[slug]
    ids = {}
    rc, out = _run([*GH, "pr", "list", "-R", slug, "--state", "all", "--limit", str(PR_LIMIT), "--json", "number"],
                   cwd=path)
    try:
        nums = [x["number"] for x in json.loads(out)] if rc == 0 else []
    except (ValueError, KeyError, TypeError):
        nums = []
    for n in nums:
        rc, diff = _run([*GH, "pr", "diff", str(n), "-R", slug, "--patch"], cwd=path)
        for pid in _patch_ids(diff, path) if rc == 0 else []:
            ids.setdefault(pid, f"{slug}#{n}")
    _PR_IDS[slug] = ids
    return ids


def repo_slug(path, pr_url):
    if pr_url and "github.com/" in pr_url:
        return "/".join(pr_url.split("github.com/", 1)[1].split("/")[:2])
    rc, out = _run([*GH, "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], cwd=path)
    return out.strip() if rc == 0 and out.strip() else None


def _file_where(path, name, refs):
    rc, blob = git(path, "hash-object", "--", name)
    if rc:
        return None
    for r in refs:
        rc, have = git(path, "rev-parse", "-q", "--verify", f"{r}:{name}")
        if rc == 0 and have.strip() == blob.strip():
            return r
    return None


def _file_summary(path, xy, name):
    if xy == "??":
        try:
            with open(os.path.join(path, name), "rb") as f:
                n = f.read().count(b"\n")
            return f"new, {n} lines"
        except OSError:
            return "new"
    rc, out = git(path, "diff", "HEAD", "--numstat", "--", name)
    cols = out.split("\t") if rc == 0 and out.strip() else []
    return f"+{cols[0]} -{cols[1]}" if len(cols) >= 2 else xy.strip()


def proof(path, pr_url=None, pr_head=None):
    """{memo, files, commits, proven, has_remote, base}; proven: only memo files are uncommitted and every unique
    commit is in the default branch or a recent PR of the repo."""
    ref, has_remote = base_ref(path)
    ents = entries(path)
    shas = unique_commits(path, ref, has_remote) if ref else None
    out = {"base": ref, "has_remote": has_remote, "memo": [], "files": [], "commits": [], "proven": False}
    if ents is None or shas is None:
        out["error"] = "git status or rev-list failed"
        return out
    if not has_remote:  # a branch just cut from the default branch has merged nothing: it is not finished work
        out["fresh"] = git(path, "rev-parse", "HEAD")[1] == git(path, "rev-parse", ref)[1]
    refs = [r for r in (ref, pr_head) if r]
    for xy, name in ents:
        if xy == "??" and is_memo(name):
            out["memo"].append(name)
        else:
            out["files"].append({"path": name, "status": xy.strip(), "where": _file_where(path, name, refs),
                                 "summary": _file_summary(path, xy, name)})
    if shas:
        equal = set()
        if has_remote:
            rc, ch = git(path, "cherry", ref, "HEAD")
            equal = {ln[2:].strip() for ln in ch.splitlines() if ln.startswith("- ")} if rc == 0 else set()
        prs = None
        for sha in shas:
            _, subj = git(path, "log", "-1", "--format=%s", sha)
            where = ref if sha in equal else None
            if where is None and has_remote:
                if prs is None:
                    slug = repo_slug(path, pr_url)
                    prs = pr_patch_ids(path, slug) if slug else {}
                _, show = git(path, "show", sha)
                pid = (_patch_ids(show, path) or [None])[0]
                where = prs.get(pid) if pid else None
            out["commits"].append({"sha": sha[:12], "subject": subj.strip()[:80], "where": where})
    out["proven"] = not out["files"] and all(c["where"] for c in out["commits"])
    return out


def nowhere(p):
    """One line for the owner's proposal: the changes found nowhere else, else None."""
    files = [f"{f['path']} ({f['summary']})" for f in p.get("files") or [] if not f.get("where")]
    commits = [f"{c['sha'][:8]} {c['subject']}" for c in p.get("commits") or [] if not c.get("where")]
    if p.get("error"):
        return p["error"]
    if not files and not commits:
        return None
    return "; ".join(x for x in (("files: " + ", ".join(files[:8])) if files else "",
                                 ("commits: " + ", ".join(commits[:5])) if commits else "") if x)


def archive(path, p):
    """Back up what removal would drop: the memo files (tar), the unique commits (format-patch) and the proof."""
    repo = os.path.basename(os.path.dirname(os.path.realpath(path)))
    d = cl.path(os.path.join("archive", f"{repo}-{os.path.basename(path)}-{cl.now().strftime('%Y%m%d%H%M%S')}"))
    os.makedirs(d, exist_ok=True)
    with tarfile.open(os.path.join(d, "files.tar"), "w") as t:
        for name in p.get("memo") or []:
            t.add(os.path.join(path, name), arcname=name)
    for i, c in enumerate(p.get("commits") or [], 1):
        git(path, "format-patch", "-1", c["sha"], "-o", os.path.join(d, "patches"), "--start-number", str(i))
    with open(os.path.join(d, "proof.json"), "w") as f:
        json.dump({"path": path, **p}, f, ensure_ascii=False, indent=1)
    return d


def prune_archive(days=ARCHIVE_DAYS):
    import shutil
    top = cl.path("archive")
    cut = (cl.now() - dt.timedelta(days=days)).timestamp()
    gone = []
    for n in sorted(os.listdir(top)) if os.path.isdir(top) else []:
        p = os.path.join(top, n)
        if os.path.isdir(p) and os.path.getmtime(p) < cut:
            shutil.rmtree(p, ignore_errors=True)
            gone.append(n)
    return gone
