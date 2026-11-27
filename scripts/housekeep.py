"""Housekeeping: scan (watcher), plan (records the judge's verdicts), execute (re-checks, then acts).

Only these classes are ever acted on, and only after the executor re-checks them itself:
  worktree      a git worktree whose PR is MERGED or CLOSED and whose PR head is the worktree HEAD, with no
                uncommitted changes, no unpushed commits, no gitignored files, and no process using it as its cwd;
                or finished work (PR MERGED/CLOSED, unit done, merged locally with no remote) whose leftovers wtproof
                accounts for (lead memo files, commits in the default branch or another PR): backed up under
                archive/ (kept 30 days), memo files deleted, then removed;
                every removal (this executor, reclaim, `foundry sweep remove-worktree`) goes through remove_worktree,
                which re-checks the cwd users right before it and refuses (hk.refused) when there are any
  docker        a docker compose project whose name starts with the configured foundry prefix
  session       a lead session (running/idle/done) whose unit is done: stopped through the launcher
  subagent      a finished foundry subagent: closed by the script (a finished task needs no TaskStop; TaskStop
                fails on it with "is not running"); only one the journal still shows running goes to the foreman
  docker cache  dangling images and build cache older than 7 days: pruned by every execute, no judge needed
Everything else (stuck, orphaned or lost sessions, other worktrees, other containers) is a proposal for the owner.
Every check fails closed: a git, gh, lsof, docker or claude command that does not succeed makes the item unsafe.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import graph  # noqa: E402
import wtproof  # noqa: E402

GIT = shlex.split(os.environ.get("FOUNDRY_GIT", "git"))
LSOF = shlex.split(os.environ.get("FOUNDRY_LSOF", "lsof"))
MIN_DOCKER_PREFIX = 3
GH = shlex.split(os.environ.get("FOUNDRY_GH", "gh"))
DOCKER = shlex.split(os.environ.get("FOUNDRY_DOCKER", "docker"))
CLAUDE = shlex.split(os.environ.get("FOUNDRY_CLAUDE", "claude"))
# Exactly these, never `-a`, volumes, networks or containers.
DOCKER_PRUNE = (["image", "prune", "-f", "--filter", "dangling=true", "--filter", "until=168h"],
                ["builder", "prune", "-f", "--filter", "until=168h"])
SESSION_LIVE = ("running", "idle", "done")


def run(argv, cwd=None):
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as ex:
        return 127, "", str(ex)


def worktrees(root):
    rc, outp, _ = run([*GIT, "-C", root, "worktree", "list", "--porcelain"])
    if rc:
        return []
    items, cur = [], {}
    for line in outp.splitlines() + [""]:
        if not line:
            if cur:
                items.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = line[9:]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].removeprefix("refs/heads/")
    return items[1:]  # the first entry is the main checkout: never a candidate


def worktree_dirs(cfg):
    """Directories holding linked worktrees: `worktree_root` (FOUNDRY_WORKTREE_DIRS overrides)."""
    override = os.environ.get("FOUNDRY_WORKTREE_DIRS")
    if override is not None:
        return [d for d in override.split(":") if d]
    return [cfg.get("worktree_root") or cl.load_config()["worktree_root"]]


def linked_worktrees(top):
    """Linked worktrees (a `.git` file, never a main checkout's `.git` dir) at depth 1 or 2 under top."""
    top = os.path.expanduser(top)
    if not os.path.isdir(top):
        return []
    out = []
    for d1 in sorted(os.listdir(top)):
        p1 = os.path.join(top, d1)
        if not os.path.isdir(p1):
            continue
        if os.path.isfile(os.path.join(p1, ".git")):
            out.append(p1)
        elif not os.path.exists(os.path.join(p1, ".git")):
            out += [os.path.join(p1, d2) for d2 in sorted(os.listdir(p1))
                    if os.path.isfile(os.path.join(p1, d2, ".git"))]
    return out


def users(path):
    """The owner's processes whose cwd is path or inside it, as [{pid, cmd}]; None when lsof cannot tell."""
    rc, outp, _ = run([*LSOF, "-a", "-u", str(os.getuid()), "-d", "cwd", "-Fpcn"])
    if rc != 0 and not (rc == 1 and not outp.strip()):
        return None
    root = os.path.realpath(path)
    out, proc = [], {}
    for line in outp.splitlines():
        if line.startswith("p"):
            proc = {"pid": line[1:], "cmd": ""}
        elif line.startswith("c"):
            proc["cmd"] = line[1:]
        elif line.startswith("n"):
            cwd = os.path.realpath(line[1:])
            if (cwd == root or cwd.startswith(root + os.sep)) and proc and proc not in out:
                out.append(dict(proc))
    return out


def in_use(path):
    """True when one of the owner's processes has its cwd inside path; None when lsof cannot tell."""
    u = users(path)
    return None if u is None else bool(u)


def users_text(procs):
    return ", ".join(f"{p['cmd'] or '?'} pid {p['pid']}" for p in procs[:3]) + (" …" if len(procs) > 3 else "")


def remove_worktree(path, by, reason):
    """The one way foundry removes a git worktree (the executor, a finished unit's reclaim, an owner-approved
    proposal): never while a process uses it or anything under it as its cwd (that session would lose its
    directory), never with --force. A refusal is journaled as hk.refused with the processes. Returns the result."""
    procs = users(path)
    if procs is None or procs:
        why = "lsof could not tell whether a process uses it" if procs is None else f"in use by {users_text(procs)}"
        cl.append("hk.refused", target=path, by=by, reason=reason, why=why, procs=procs or [])
        return f"refused: {why}"
    _, common, _ = run([*GIT, "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"])
    rc, _, err = run([*GIT, "--git-dir", common.strip(), "worktree", "remove", path])
    if rc != 0:
        return f"failed: {err.strip()[:200]}"
    import trust
    trust.unregister(path)
    return "removed"


def worktree_facts(path, branch):
    """Facts for the safe-worktree rule; a value is None when its command failed (never read as clean)."""
    rc_s, status, _ = run([*GIT, "-C", path, "status", "--porcelain", "--ignored"])
    lines = status.splitlines() if rc_s == 0 else None
    rc_a, ahead, _ = run([*GIT, "-C", path, "rev-list", "--count", "HEAD", "--not", "--remotes"])
    rc_h, head, _ = run([*GIT, "-C", path, "rev-parse", "HEAD"])
    rc_p, pr, _ = run([*GH, "pr", "view", branch, "--json", "state,number,url,headRefOid"], cwd=path)
    try:
        pr = json.loads(pr) if rc_p == 0 and pr.strip() else None
    except ValueError:
        pr = None
    try:
        unpushed = int(ahead.strip()) if rc_a == 0 else None
    except ValueError:
        unpushed = None
    return {"dirty": None if lines is None else any(not ln.startswith("!! ") for ln in lines),
            "ignored": None if lines is None else [ln[3:] for ln in lines if ln.startswith("!! ")][:20],
            "unpushed": unpushed,
            "head": head.strip() if rc_h == 0 else None,
            "pr_head": (pr or {}).get("headRefOid"),
            **_use_facts(path),
            "pr_state": (pr or {}).get("state"), "pr": (pr or {}).get("url")}


def _use_facts(path):
    procs = users(path)
    return {"in_use": None if procs is None else bool(procs), "in_use_by": users_text(procs) if procs else None}


FINISHED_UNIT = graph.CLOSED


def finished_work(f):
    """The work behind the worktree is over: its PR is MERGED or CLOSED, its unit is done or cancelled, or (no
    remote at all) its commits are merged into the local default branch."""
    p = f.get("proof") or {}
    return (f["pr_state"] in ("MERGED", "CLOSED") or f.get("unit_status") in FINISHED_UNIT
            or (p.get("has_remote") is False and not p.get("commits") and bool(p.get("base")) and not p.get("fresh")))


def worktree_safe(f):
    if f["ignored"] != [] or f["in_use"] is not False:
        return False
    if (f["pr_state"] in ("MERGED", "CLOSED") and f["dirty"] is False and f["unpushed"] == 0
            and bool(f["head"]) and f["head"] == f["pr_head"]):
        return True
    # a unit still open may come back to its worktree (prod verify after the merge): only finished or unowned work
    open_unit = f.get("unit_status") is not None and f.get("unit_status") not in FINISHED_UNIT
    return bool((f.get("proof") or {}).get("proven")) and finished_work(f) and not open_unit


def worktree_suggest(f):
    """The verdict the facts point to. Finished work held back by gitignored files (local secrets, build output)
    or by a change found nowhere else is the owner's call: propose it, with that change named."""
    if worktree_safe(f):
        return "safe"
    if f.get("in_use"):
        return "skip"  # a live session's directory is nobody's to remove, the owner's approval included
    if finished_work(f) or (f.get("nowhere") and f["pr_state"] is None and f.get("unit_status") is None):
        return "propose"  # an unowned worktree with changes found nowhere else is shown to the owner too
    return "skip"


def with_proof(f, path, unit_status=None):
    """Add the unit's status and, when the worktree holds leftovers, where they are found (wtproof)."""
    f["unit_status"] = unit_status
    if f["dirty"] is not False or f["unpushed"] != 0 or f["head"] != f["pr_head"] or f["pr_state"] is None:
        f["proof"] = wtproof.proof(path, f.get("pr"), f.get("pr_head"))
        f["nowhere"] = wtproof.nowhere(f["proof"])
    return f


def unit_status_of(snap, path):
    rp = os.path.realpath(path)
    return next((u.get("status") for u in snap["units"].values()
                 if (u.get("worktree") or {}).get("path") and os.path.realpath(u["worktree"]["path"]) == rp), None)


def docker_prefix(cfg):
    """The configured prefix, or None when it is too short to tell foundry's projects from anyone else's."""
    p = cfg.get("docker_project_prefix") or ""
    return p if len(p) >= MIN_DOCKER_PREFIX else None


def docker_unit(name, prefix, snap):
    """The unit a `<prefix><unit>[-<suffix>]` compose project belongs to (longest id match), or None."""
    rest = name[len(prefix):] if prefix and name.startswith(prefix) else None
    if not rest:
        return None
    ids = [u for u in snap["units"] if rest == u or rest.startswith(u + "-")]
    return max(ids, key=len) if ids else None


def docker_projects():
    rc, outp, _ = run([*DOCKER, "compose", "ls", "-a", "--format", "json"])
    if rc or not outp.strip():
        return []
    return [p["Name"] for p in json.loads(outp)]


def claude_agents():
    """`claude agents --json` entries, or None when the command fails (then nothing is called orphaned)."""
    rc, outp, _ = run([*CLAUDE, "agents", "--json"])
    if rc:
        return None
    try:
        data = json.loads(outp or "[]")
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def session_candidates(snap, cfg):
    """Done-unit sessions are safe; stuck, orphaned and lost ones are proposals."""
    out = []
    stuck = cfg.get("stuck_hours", 6)
    for name, x in snap["sessions"].items():
        u = snap["units"].get(x.get("unit") or "")
        st = x.get("state")
        facts = {"state": st, "unit": x.get("unit"), "unit_status": (u or {}).get("status"), "updated": x.get("updated")}

        def add(safe, reason):
            out.append({"cls": "session", "target": name, "safe": safe, "suggest": "safe" if safe else "propose",
                        "facts": facts, "reason": reason})
        if st in SESSION_LIVE and u and u["status"] == "done":
            add(True, "unit is done")
        elif st in SESSION_LIVE + ("launching",) and not u:
            add(False, f"orphaned: unit {x.get('unit')} is missing, stop with claude stop {x.get('bg_id')}")
        elif st in ("running", "idle"):
            when = cl.parse_ts(x.get("updated"))
            if when and (cl.now() - when).total_seconds() > stuck * 3600:
                add(False, f"stuck: no journal update for over {stuck}h")
        elif st == "lost" and u and u["status"] not in ("done", "queue"):
            add(False, f"lost while unit is {u['status']}: foundry unit resume {u['id']}")
    for a in claude_agents() or []:
        n = a.get("name") or ""
        if n.startswith(("lead-", "boss-")) and n not in snap["sessions"]:
            out.append({"cls": "session", "target": n, "safe": False, "suggest": "propose",
                        "facts": {"orphan": True, "id": a.get("sessionId"), "status": a.get("status")},
                        "reason": f"orphaned: foundry does not know it, stop with claude stop {a.get('sessionId')}"})
    return out


JUDGE = "foundry:sweeper-judge"
SCRIPTED = "scripted: finished subagent (nothing of its own to remove; its unit's worktree is judged as the unit's)"


FINISHED = "finished: already stopped, no TaskStop needed"


def finished_subagents(snap):
    stopped = {d["hid"] for d in snap["housekeeping"]["done"] if d.get("cls") == "subagent"}
    return [a for a in snap["agents"].values()
            if a["status"] == "stopped" and (a.get("type") or "").startswith("foundry:")
            and f"subagent:{a['agent_id']}" not in stopped]


def scripted_verdicts(snap=None):
    """Record `safe` for every finished foundry subagent without a judge, a unit's hands included: closing one is
    a journal line (it is already stopped, so no TaskStop) and its worktree, branch or PR are its unit's, judged as
    such. There is nothing to weigh, and a judge woken for each finished hand (and then for itself) was a loop.
    Returns the hids newly planned."""
    snap = snap or cl.load()
    planned = {p["hid"] for p in snap["housekeeping"]["plans"]}
    out = []
    for a in finished_subagents(snap):
        hid = f"subagent:{a['agent_id']}"
        if hid not in planned:
            plan(hid, "safe", SCRIPTED)
            out.append(hid)
    return out


def scan():
    cfg = cl.load_config()
    roots = cfg["repo_roots"] + [r for r in os.environ.get("FOUNDRY_REPO_ROOTS", "").split(":") if r]
    found = {}
    for root in roots:
        for wt in worktrees(os.path.expanduser(root)):
            if wt.get("branch"):
                found.setdefault(os.path.realpath(wt["path"]), (wt["path"], wt["branch"]))
    for top in worktree_dirs(cfg):
        for p in linked_worktrees(top):
            rp = os.path.realpath(p)
            if rp in found:
                continue
            rc, b, _ = run([*GIT, "-C", p, "rev-parse", "--abbrev-ref", "HEAD"])
            if rc == 0 and b.strip() not in ("", "HEAD"):
                found[rp] = (p, b.strip())
    cands = []
    snap = cl.load()
    for wt_path, branch in found.values():
        f = with_proof(worktree_facts(wt_path, branch), wt_path, unit_status_of(snap, wt_path))
        suggest = worktree_suggest(f)
        cands.append({"cls": "worktree", "target": wt_path, "branch": branch, "facts": f,
                      "safe": worktree_safe(f), "suggest": suggest,
                      **({"note": f"in use by {f['in_use_by']}"} if f.get("in_use") else
                         {"note": f"found nowhere else: {f['nowhere']}"} if f.get("nowhere") else {})})
    prefix = docker_prefix(cfg)
    for name in docker_projects():
        ours = bool(prefix) and name.startswith(prefix)
        uid = docker_unit(name, prefix, snap) if ours else None
        status = (snap["units"].get(uid) or {}).get("status")
        safe = status == "done"  # only a stack whose unit is finished; a live unit's stack is never taken down
        facts = ({"ours": ours, "unit": uid, "unit_status": status} if prefix else
                 {"ours": False, "error": "docker_project_prefix must be 3+ characters"})
        # a project foundry did not make (another tool's stack) is not foundry's to propose: counted, never judged
        cands.append({"cls": "docker", "target": name, "safe": safe,
                      "suggest": "safe" if safe else "propose" if ours else "skip", "facts": facts})
    for a in finished_subagents(snap):
        if a["type"] != JUDGE:  # the judge never judges itself: each finished judge would wake the next one
            cands.append({"cls": "subagent", "target": a["agent_id"], "safe": True,
                          "facts": {"type": a["type"], "unit": a.get("unit")}})
    cands += session_candidates(snap, cfg)
    import hook  # the judge copies these into `plan --reason`: none may hold a character its hook refuses
    for c in cands:
        c["hid"] = f"{c['cls']}:{c['target']}"
        for k in ("reason", "note"):
            if c.get(k):
                c[k] = hook.judge_safe(c[k])
    return cands


def plan(hid, verdict, reason):
    cls, target = hid.split(":", 1)
    use = _use_facts(target) if cls == "worktree" and os.path.isdir(target) else {}
    extra = {"in_use": use["in_use_by"]} if use.get("in_use") else {}
    if cls == "worktree" and verdict == "propose" and os.path.isdir(target) and not use.get("in_use"):
        _, b, _ = run([*GIT, "-C", target, "rev-parse", "--abbrev-ref", "HEAD"])
        f = with_proof(worktree_facts(target, b.strip()), target, unit_status_of(cl.load(), target))
        if f.get("nowhere"):
            extra["nowhere"] = f["nowhere"]  # what the owner weighs: the changes found nowhere else
    cl.append("hk.plan", hid=hid, cls=cls, target=target, verdict=verdict, reason=reason, **extra)


def recheck(cls, target):
    """The executor's own check: a judge's 'safe' is never enough by itself."""
    if cls == "worktree":
        if not os.path.isdir(target):
            return False, "gone"
        _, b, _ = run([*GIT, "-C", target, "rev-parse", "--abbrev-ref", "HEAD"])
        f = with_proof(worktree_facts(target, b.strip()), target, unit_status_of(cl.load(), target))
        return worktree_safe(f), json.dumps(f)
    if cls == "docker":
        prefix = docker_prefix(cl.load_config())
        if not prefix:
            return False, "docker_project_prefix is empty or shorter than 3 characters"
        uid = docker_unit(target, prefix, cl.load())
        status = (cl.load()["units"].get(uid) or {}).get("status")
        if status != "done":
            return False, f"compose project's unit {uid} is {status}"
        return target in docker_projects(), "unit done + exists"
    if cls == "session":
        snap = cl.load()
        x = snap["sessions"].get(target)
        u = snap["units"].get((x or {}).get("unit") or "")
        if not x or x.get("state") not in SESSION_LIVE:
            return False, f"session state is {(x or {}).get('state')}"
        if not u or u["status"] != "done":
            return False, f"unit status is {(u or {}).get('status')}"
        return True, "unit done"
    return False, f"{cls} is not an executable class"


def stop_session(name, reason):
    """Through the launcher when it is installed; otherwise `claude stop <bg_id>` and the state record here."""
    try:
        import launcher
    except ImportError:
        launcher = None
    if launcher is not None and hasattr(launcher, "stop_session"):
        try:
            res = launcher.stop_session(name, reason)
        except Exception as ex:  # its failure is a failed result, never a crashed sweep
            return 1, str(ex)
        return (1, res["error"]) if "error" in res else (0, "")
    bg = (cl.load()["sessions"].get(name) or {}).get("bg_id")
    if not bg:
        return 1, "no bg_id recorded"
    rc, out, err = run([*CLAUDE, "stop", bg])
    gone = rc and "no session matching" in f"{out} {err}".lower()  # launcher.already_gone, without launcher
    if rc == 0 or gone:
        cl.append("session.state", name=name, state="stopped",
                  note="claude: no session matching: already gone" if gone else reason)
        return 0, ""
    return rc, err


def docker_prune(dry_run=False):
    """Dangling images and build cache older than 7 days. Nothing runs unless `docker info` succeeds."""
    if run([*DOCKER, "info"])[0] != 0:
        return [{"hid": "docker-cache", "result": "skipped: docker info failed"}]
    out = []
    for argv in DOCKER_PRUNE:
        hid = f"docker-cache:{argv[0]}"
        if dry_run:
            out.append({"hid": hid, "result": "would act"})
            continue
        rc, outp, err = run([*DOCKER, *argv])
        last = outp.strip().splitlines()[-1][:200] if outp.strip() else "nothing"
        out.append({"hid": hid, "result": f"pruned: {last}" if rc == 0 else f"failed: {err.strip()[:200]}"})
    return out


def remove_proven(path, by, reason):
    """remove_worktree for a worktree whose leftovers are accounted for (wtproof): back them up under
    archive/<repo>-<wt>-<ts>/ first, delete only the memo files, then remove (still no --force)."""
    _, b, _ = run([*GIT, "-C", path, "rev-parse", "--abbrev-ref", "HEAD"])
    f = with_proof(worktree_facts(path, b.strip()), path, unit_status_of(cl.load(), path))
    p = f.get("proof")
    if not p:
        return remove_worktree(path, by, reason)
    if not worktree_safe(f):
        return "refused: re-check failed"
    d = wtproof.archive(path, p)
    for name in p["memo"]:
        os.remove(os.path.join(path, name))
    for top in sorted({n.split("/", 1)[0] for n in p["memo"] if "/" in n}):
        for sub, _, _ in sorted(os.walk(os.path.join(path, top)), reverse=True):  # memo dirs left empty
            try:
                os.rmdir(sub)
            except OSError:
                pass
    res = remove_worktree(path, by, reason)
    return f"{res} (backup {d})"


def act(cls, target):
    if cls == "worktree":
        return remove_proven(target, "foundry", "housekeeping")
    if cls == "session":
        rc, err = stop_session(target, "housekeeping: unit is done")
        return "stopped" if rc == 0 else f"failed: {err.strip()[:200]}"
    rc, _, err = run([*DOCKER, "compose", "-p", target, "down", "--remove-orphans"])
    return "removed" if rc == 0 else f"failed: {err.strip()[:200]}"


def safe_cleanup(cands, reason="pressure"):
    """The scan's safe worktrees and the docker cache, without waiting for a judge: the executor's
    re-check is the authority either way, so this is exactly what `execute` does on a `safe` verdict."""
    results = []
    for c in cands:
        if c["cls"] != "worktree" or not c["safe"]:
            continue
        ok, why = recheck("worktree", c["target"])
        if not ok:
            results.append({"hid": c["hid"], "result": f"skipped: re-check failed ({why})"})
            continue
        res = act("worktree", c["target"])
        cl.append("hk.done", hid=c["hid"], cls="worktree", target=c["target"], result=f"{res} ({reason})")
        results.append({"hid": c["hid"], "result": res})
    return results + docker_prune()


RECLAIM_STATUS = graph.CLOSED
HAND_STOPPED = ("stopped", "done", "finished", "completed", "cancelled", "failed", "lost")


def reclaim_reason(snap, uid, f):
    """Why a done/cancelled unit's worktree stays, in one line of ui.language (reclaim.json is rewritten every
    sweep, so a language switch shows from the next one); None when it may go. Fails closed."""
    import hands
    import i18n
    tr = lambda k, **kw: i18n.t("cli." + k, **kw)  # noqa: E731
    u = snap["units"][uid]
    if cl.lead_live(snap, u):
        return tr("kept_lead_live")
    if any(str(h.get("state") or "") not in HAND_STOPPED for h in hands.unit_hands(snap, uid)):
        return tr("kept_hand_running")
    if f.get("dirty") is None or f.get("unpushed") is None:
        return tr("kept_git_unknown")
    if f["dirty"] and not (f.get("proof") or {}).get("proven"):
        return tr("kept_dirty")
    if f.get("in_use") is not False:
        return tr("kept_in_use") if f.get("in_use") else tr("kept_in_use_unknown")
    if f["unpushed"] and f.get("pr_state") not in ("MERGED", "CLOSED"):
        return (tr("kept_unpushed", n=f["unpushed"], state=f["pr_state"]) if f.get("pr_state")
                else tr("kept_unpushed_no_pr", n=f["unpushed"]))
    return None


def reclaim(cands=None, snap=None):
    """Remove each done/cancelled unit's worktree (no --force) and its local branch once the unit's sessions are
    stopped, git status is clean (untracked included), and HEAD is on a remote or the PR is MERGED/CLOSED.
    Writes {unit: {path, result | kept}} to reclaim.json for the board and returns it."""
    snap = snap or cl.load()
    facts = {os.path.realpath(c["target"]): c["facts"] for c in cands or [] if c["cls"] == "worktree"}
    out = {}
    for uid, u in sorted(snap["units"].items()):
        p = (u.get("worktree") or {}).get("path")
        if u.get("status") not in RECLAIM_STATUS or not p or not os.path.isdir(p):
            continue
        rc, b, _ = run([*GIT, "-C", p, "rev-parse", "--abbrev-ref", "HEAD"])
        branch = b.strip() if rc == 0 and b.strip() != "HEAD" else None
        f = facts.get(os.path.realpath(p)) or with_proof(worktree_facts(p, branch or "HEAD"), p, u.get("status"))
        why = reclaim_reason(snap, uid, f)
        if why:
            out[uid] = {"path": p, "kept": why}
            continue
        _, common, _ = run([*GIT, "-C", p, "rev-parse", "--path-format=absolute", "--git-common-dir"])
        res = (remove_proven(p, "foundry", "reclaim") if f["dirty"]
               else remove_worktree(p, "foundry", "housekeeping"))
        if res.startswith("removed") and branch:
            rc, _, err = run([*GIT, "--git-dir", common.strip(), "branch", "-d", branch])
            if rc != 0 and f.get("pr_state") == "MERGED" and f.get("head") and f["head"] == f.get("pr_head"):
                # squash-merged: git cannot see it merged, but the branch is exactly the PR head that merged
                rc, _, err = run([*GIT, "--git-dir", common.strip(), "branch", "-D", branch])
            res += "; branch deleted" if rc == 0 else f"; branch kept: {err.strip()[:120]}"
        cl.append("hk.done", hid=f"worktree:{p}", cls="worktree", target=p, result=f"{res} (unit {uid} {u['status']})")
        out[uid] = {"path": p, "result": res} if res.startswith("removed") else {"path": p, "kept": res}
        if res.startswith("refused"):
            continue
    cl.write_atomic(cl.path("reclaim.json"), json.dumps(out, ensure_ascii=False))
    return out


def reclaim_notes():
    try:
        with open(cl.path("reclaim.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def execute(dry_run=False):
    snap = cl.load()
    done = {}
    for d in snap["housekeeping"]["done"]:
        done[d["hid"]] = max(done.get(d["hid"], 0), d.get("seq") or 0)
    latest = {}
    for p in snap["housekeeping"]["plans"]:
        latest[p["hid"]] = p
    results, stop_list = [], []
    for hid, p in latest.items():
        # A target acted on after its latest verdict is finished; a later verdict (a relaunched session) is new.
        if p["verdict"] != "safe" or done.get(hid, 0) >= (p.get("seq") or 0):
            continue
        if p["cls"] == "subagent":
            # SubagentStop already ended it: TaskStop would only fail with "is not running". Only a subagent the
            # journal still shows running (resumed since the verdict) is one for the foreman to stop.
            agent = snap["agents"].get(p["target"]) or {}
            if agent.get("status") == "running":
                stop_list.append(p["target"])
            elif not dry_run:
                cl.append("hk.done", hid=hid, cls="subagent", target=p["target"], result=FINISHED)
            results.append({"hid": hid, "result": "TaskStop (running)" if agent.get("status") == "running"
                            else ("would close" if dry_run else FINISHED)})
            continue
        ok, why = recheck(p["cls"], p["target"])
        if not ok:
            results.append({"hid": hid, "result": f"skipped: re-check failed ({why})"})
            continue
        if dry_run:
            results.append({"hid": hid, "result": "would act"})
            continue
        res = act(p["cls"], p["target"])
        if not res.startswith(("failed", "refused")):  # stays pending, so the next run retries it
            cl.append("hk.done", hid=hid, cls=p["cls"], target=p["target"], result=res)
        results.append({"hid": hid, "result": res})
    pruned = [] if dry_run else wtproof.prune_archive()
    return {"results": results, "docker_cache": docker_prune(dry_run), "foreman_taskstop": stop_list,
            **({"archive_pruned": pruned} if pruned else {})}


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("scan")
    pl = sp.add_parser("plan")
    pl.add_argument("--hid", required=True)
    pl.add_argument("--verdict", required=True, choices=["safe", "propose", "skip"])
    pl.add_argument("--reason", required=True)
    ex = sp.add_parser("execute")
    ex.add_argument("--dry-run", action="store_true")
    sp.add_parser("subagent-done").add_argument("agent_id")
    a = ap.parse_args()
    if a.cmd == "scan":
        print(json.dumps(scan(), ensure_ascii=False, indent=1))
    elif a.cmd == "plan":
        plan(a.hid, a.verdict, a.reason)
    elif a.cmd == "execute":
        print(json.dumps(execute(a.dry_run), ensure_ascii=False, indent=1))
    elif a.cmd == "subagent-done":
        cl.append("hk.done", hid=f"subagent:{a.agent_id}", cls="subagent", target=a.agent_id, result="TaskStop by foreman")
    return 0


if __name__ == "__main__":
    sys.exit(main())
