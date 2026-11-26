"""The factory floor view: inventory of open PRs/branches/worktrees, preflight checks, the morning handover,
the weekly kaizen report (near-misses, rework), cost display and unit traceability.

Everything here reads state (plus read-only `gh`, injectable through FOUNDRY_GH); the only writes are its
own report ops. Cost is display only: no budgets, nothing is parked on it.
"""

import datetime as dt
import fnmatch
import functools
import glob
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import i18n  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEARMISS_MAX = 500
STAGES = ("draft", "ci-running", "ci-failing", "review", "approved", "merged", "released", "deployed-dev",
          "prod-waiting", "deployed")
OPEN_STAGES = ("review", "approved", "ci-running", "ci-failing")  # an open PR, before its merge
WAITS_ON = {"draft": "lead", "ci-running": "ci", "ci-failing": "lead", "review": "reviewer", "approved": "merge",
            "merged": "release", "released": "deploy", "deployed-dev": "prod", "prod-waiting": "owner",
            "deployed": None}
STATE = {"nearmiss": [], "costs": {}, "preflight": None, "handover": None}


# ---------- reducers ----------

def _op_nearmiss(s, r, ts):
    """Tolerant: triage and watchers both emit it, with whatever fields they have."""
    s.setdefault("nearmiss", [])
    s["nearmiss"].append({"ts": ts, "kind": r.get("kind") or "unknown", "unit": r.get("unit"),
                          "detail": r.get("detail") if r.get("detail") is not None else r.get("text")})
    s["nearmiss"] = s["nearmiss"][-NEARMISS_MAX:]


def _op_cost(s, r, ts):
    s.setdefault("costs", {})[r.get("unit") or "_other"] = {"usd": r.get("usd") or 0, "by_model": r.get("by_model") or {},
                                                           "ts": ts}


def _op_preflight(s, r, ts):
    s["preflight"] = {"ok": bool(r.get("ok")), "failures": r.get("failures") or [], "ts": ts}


def _op_handover(s, r, ts):
    s["handover"] = {"text": r.get("text") or "", "ts": ts}


def _op_kaizen(s, r, ts):
    s["kaizen"] = {"text": r.get("text") or "", "ts": ts}


OPS = {"nearmiss": _op_nearmiss, "cost": _op_cost, "preflight": _op_preflight, "report.handover": _op_handover,
       "report.kaizen": _op_kaizen}


# ---------- helpers ----------

def _prefs():
    import prefs
    return prefs.load()


def _zone(p):
    import prefs
    return prefs.tz(p)


def business_hours(a, b, zone):
    """Hours between a and b that fall on Monday-Friday in the given zone."""
    if not a or not b or b <= a:
        return 0.0
    a, b = a.astimezone(zone), b.astimezone(zone)
    total = 0.0
    day = a.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < b:
        nxt = (day + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if day.weekday() < 5:
            lo, hi = max(a, day), min(b, nxt)
            if hi > lo:
                total += (hi - lo).total_seconds() / 3600
        day = nxt
    return total


def gh(*args):
    """Read-only gh call; parsed JSON, or None on any failure."""
    cmd = shlex.split(os.environ.get("FOUNDRY_GH", "gh")) + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def _ref_of(pr):
    if isinstance(pr, str):
        return pr
    if pr.get("repo") and pr.get("number") is not None:
        return f'{pr["repo"]}#{pr["number"]}'
    return pr.get("url") or pr.get("ref") or "?"


# ---------- inventory ----------

def stage_of(pr, ci=True):
    """A PR's stage. An open one says its CI first (failing, then running: the github stream's latest-run bucket
    in the snapshot), then its review; a merged one how far its release got (`deploy_stage`, from the trace chain)."""
    st = str(pr.get("state") or "").lower()
    if pr.get("stage") in STAGES:
        return pr["stage"]
    if pr.get("deploy_stage") in STAGES:
        return pr["deploy_stage"]
    if pr.get("deployed"):
        return "deployed"
    if pr.get("released"):
        return "released"
    if pr.get("merged") or st == "merged":
        return "merged"
    if st == "closed":
        return None
    if pr.get("isDraft") or st == "draft":
        return "draft"
    bucket = str(pr.get("ci") or "").lower() if ci else ""
    if bucket == "failure":
        return "ci-failing"
    if bucket == "pending":
        return "ci-running"
    if st == "approved" or str(pr.get("reviewDecision") or pr.get("review") or "").upper() == "APPROVED":
        return "approved"
    return "review"


def deploy_stage(snap, ref, pr):
    """deployed / prod-waiting / deployed-dev for a released PR, through the trace chain: its release tag (pr.released
    from tag.pushed, or the release unit that holds it) against the newest deploy.done per environment of every unit
    (a later tag of the same family covers it). prod-waiting: on dev, and the prod card of its unit or release unit
    is open. None before any deploy."""
    import report
    units = snap.get("units") or {}
    rel = [u for u in units.values() if ref in ((u.get("release") or {}).get("prs") or [])]
    tag = pr.get("released") if isinstance(pr.get("released"), str) and not cl.parse_ts(pr["released"]) else None
    tag = tag or next((((u.get("release") or {}).get("released") or {}).get(ref) for u in rel
                       if ((u.get("release") or {}).get("released") or {}).get(ref)), None)
    if not tag:
        return None
    deployed = [(cl.norm_env(env), d.get("tag")) for u in units.values()
                for env, d in (u.get("deployed") or {}).items() if isinstance(d, dict)]
    if report._tag_covered(tag, [t for e, t in deployed if e == "prod"]):
        return "deployed"
    if not report._tag_covered(tag, [t for e, t in deployed if e == "dev"]):
        return None
    keys = {f"{u['id']}:prod-deploy" for u in rel} | {f"{pr.get('unit')}:prod-deploy"}
    open_card = any(c.get("key") in keys and c.get("answer") is None for c in (snap.get("cards") or {}).values())
    return "prod-waiting" if open_card else "deployed-dev"


def gh_open_prs(repos):
    """Open PRs per repo from gh (read-only); missing gh or a failure yields nothing."""
    out = {}
    for repo in sorted(repos):
        rows = gh("pr", "list", "-R", repo, "--author", "@me", "--state", "open", "--json",
                  "number,title,isDraft,reviewDecision,createdAt,url,reviewRequests,headRefName")
        if isinstance(rows, list):
            out[repo] = rows
    return out


def collect_prs(snap, live=None):
    """{repo#n: pr dict with unit} from units[*].prs, units[*].keys.pr, then the gh listing."""
    prs = {}
    done = lambda uid: (snap["units"].get(uid) or {}).get("status") == "done"  # noqa: E731
    for uid, u in sorted(snap["units"].items(), key=lambda kv: kv[1].get("status") != "done"):
        for pr in u.get("prs") or []:
            if isinstance(pr, dict):
                ref = _ref_of(pr)
                prs[ref] = dict(pr, unit=uid) if ref not in prs or done(prs[ref]["unit"]) else prs[ref]
        for ref in (u.get("keys") or {}).get("pr") or []:
            if "#" in ref and (ref not in prs or (done(prs[ref]["unit"]) and not done(uid))):
                repo, num = ref.rsplit("#", 1)
                prs[ref] = {**prs.get(ref, {}), "repo": repo, "number": num, "unit": uid}
    for repo, rows in (live or {}).items():
        for row in rows:
            ref = f'{repo}#{row.get("number")}'
            base = prs.get(ref, {})
            prs[ref] = {**row, **{k: v for k, v in base.items() if v is not None}, "repo": repo,
                        "reviewers": base.get("reviewers") or row.get("reviewRequests") or [],
                        "opened": base.get("opened") or row.get("createdAt")}
    return prs


def release_tag(repo, p=None, rs=None):
    """The tag glob whose tags mark a merged PR of `repo` released, or None when the repo has no release step:
    repos.json `release_tag` ("none" or a glob such as `v*`), else prefs release.none (name globs) and release.tag."""
    import gitpolicy
    tag = (gitpolicy.repo_by_ref(repo, rs) or {}).get("release_tag")
    if tag:
        return None if tag == "none" else tag
    rp = (p or _prefs()).get("release") or {}
    if any(fnmatch.fnmatch(str(repo or "").lower(), g) for g in rp.get("none") or []):
        return None
    return rp.get("tag") or "*"


def pr_release_state(pr, p=None, rs=None):
    """Where a recorded PR stands on the way to a release: open | closed | released | no-release (merged in a repo
    with no release step, e.g. cluster ops or IaC) | release-wait (merged, no release tag holds it yet)."""
    st = str(pr.get("state") or "").upper()
    if pr.get("released") or pr.get("deployed"):
        return "released"
    if st == "MERGED" or pr.get("merged"):
        return "no-release" if release_tag(pr.get("repo"), p, rs) is None else "release-wait"
    return "closed" if st == "CLOSED" else "open"


@functools.lru_cache(maxsize=None)
def _remote_slug(path, remote="origin"):
    try:
        r = subprocess.run(["git", "-C", os.path.expanduser(path), "remote", "get-url", remote],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", r.stdout.strip()) if r.returncode == 0 else None
    return m.group(1) if m else None


def canonical_repo(name, known=(), rs=None, path=None):
    """owner/name for a short repo name, so one repo is one group: a full name already seen with that tail, the
    registry's slug, a registry key with the same clone, or the clone's (or worktree's) git remote."""
    if not name or "/" in name:
        return name
    full = {k for k in known if k and k.rsplit("/", 1)[-1] == name}
    if len(full) == 1:
        return full.pop()
    rs = rs if rs is not None else {}
    r = rs.get(name) or {}
    if r.get("slug"):
        return r["slug"]
    same = [k for k, v in rs.items() if "/" in k and r.get("path") and (v or {}).get("path") == r["path"]]
    if len(same) == 1:
        return same[0]
    for where in (path, r.get("path")):
        slug = where and _remote_slug(where, r.get("remote") or "origin")
        if slug:
            return slug
    return name


@functools.lru_cache(maxsize=256)
def ahead_of(path, branch, sha=None):
    """Commits on `branch` that the remote default branch lacks, read from the worktree; None when git can't say.
    Cached per head sha, so a refresh re-reads git only after a push moved it."""
    path = os.path.expanduser(path or "")
    if not branch or not os.path.isdir(path):
        return None
    try:
        r = subprocess.run(["git", "-C", path, "rev-list", "--count", f"origin/HEAD..{branch}"],
                           capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None


def inventory(snap, t=None, p=None, live=None, rs=None):
    p = p or _prefs()
    t = t or cl.now()
    zone = _zone(p)
    limit_h = 24 * (p.get("stale_business_days") or 1)
    repos = {}
    if rs is None:
        import gitpolicy
        rs = gitpolicy.repos()
    prs = collect_prs(snap, live)
    all_prs = dict(prs)
    import views
    known = {str(pr.get("repo")) for pr in prs.values() if "/" in str(pr.get("repo") or "")} | {k for k in rs if "/" in k}

    def add(repo, row, path=None):
        repos.setdefault(canonical_repo(repo, known, rs, path) or "?", []).append(row)

    def age(ts):
        when = cl.parse_ts(ts)
        return round((t - when).total_seconds() / 3600, 1) if when else None

    import review
    pool = review.pool_logins(p)
    dropped = snap.get("pr_dropped") or {}
    for ref, pr in prs.items():
        if ref in dropped:
            continue
        if str(pr.get("state") or "").upper() == "MERGED" or pr.get("merged") or pr.get("released"):
            pr = dict(pr, deploy_stage=deploy_stage(snap, ref, pr))
        stage = stage_of(pr)
        # Out of stock: closed, released or deployed, or merged in a repo with no release step (IaC, cluster ops).
        # A release on dev (deployed-dev, prod-waiting) stays until prod has it.
        if stage in (None, "released", "deployed") or (stage == "merged" and release_tag(pr.get("repo"), p, rs) is None):
            continue
        revs, auto = review.split_reviewers(pr, pool, review._picked(snap, ref, pr.get("unit")))
        since = cl.parse_ts(pr.get("review_requested") or pr.get("opened") or pr.get("createdAt"))
        stale = stage in OPEN_STAGES and business_hours(since, t, zone) > limit_h
        waits = (", ".join(revs) or "reviewer") if stage == "review" else WAITS_ON[stage]
        add(pr.get("repo"), {"kind": "pr", "ref": ref, "unit": pr.get("unit"), "title": pr.get("title"),
                             "age_h": age(pr.get("opened") or pr.get("createdAt")), "waits_on": waits,
                             "stage": stage, "review_stage": stage_of(pr, ci=False), "stale": stale, "reviewers": revs, "auto_reviewers": auto,
                             "url": pr.get("url") or (f"https://github.com/{pr.get('repo')}/pull/{pr.get('number')}"
                                                      if "/" in str(pr.get("repo") or "") else None),
                             "opened_at": pr.get("opened") or pr.get("createdAt"), "merged_at": pr.get("merged"),
                             "approved_at": pr.get("approved"),
                             "approvers": list(pr.get("approvers") or []),
                             "since": cl.iso(since) if since else None, "ci": pr.get("ci"),
                             "gh": views.gh_state(stage, pr.get("state"), pr.get("isDraft")),
                             "gh_review": views.gh_review(pr.get("review"), stage),
                             "known_open": str(pr.get("state") or "").upper() == "OPEN" and bool(pr.get("title"))})
    import housekeep
    reclaim = housekeep.reclaim_notes()
    for uid, u in snap["units"].items():
        if u.get("kind") == "foreman":
            continue
        finished = u.get("status") in housekeep.RECLAIM_STATUS
        wt = u.get("worktree") or {}
        if finished:  # a finished unit's worktree waits for the sweep's reclaim (or says why it stays), then drops
            note = reclaim.get(uid) or {}
            if wt.get("path") and os.path.isdir(os.path.expanduser(wt["path"])) and not note.get("result"):
                add(wt.get("repo") or u.get("repo"), {
                    "kind": "worktree", "ref": wt["path"], "unit": uid, "age_h": age(u.get("updated") or u.get("created")),
                    "waits_on": None, "stage": None, "stale": False, "status": u.get("status"),
                    "reclaim": i18n.t("views.kept", why=note["kept"]) if note.get("kept") else i18n.t("cli.reclaim_waiting"),
                    "reclaim_kept": bool(note.get("kept"))}, wt["path"])
            continue
        for b in u.get("branches") or []:
            name = b if isinstance(b, str) else b.get("name") or b.get("branch")
            pushed = None if isinstance(b, str) else b.get("pushed") or b.get("ts")
            own = next((pr for pr in all_prs.values() if pr.get("branch") == name
                        and pr.get("unit") == uid), None)
            row = {"kind": "branch", "ref": name, "unit": uid, "age_h": age(pushed or u.get("created")),
                   "waits_on": "lead", "stage": None, "stale": False, "gh": None, "gh_review": None, "pr": None}
            if own:  # a branch says its PR's state, in GitHub's words and colours; none means 「PR 없음」
                row.update(pr=f"{own.get('repo')}#{own.get('number')}", url=views.pr_url(own), stage=stage_of(own),
                           gh=views.gh_state(stage_of(own), own.get("state"), own.get("isDraft")),
                           gh_review=views.gh_review(own.get("review"), stage_of(own)))
            if wt.get("path") and (wt.get("branch") in (None, name)):
                row["ahead"] = ahead_of(wt["path"], name, None if isinstance(b, str) else b.get("sha"))
            add((b.get("repo") if isinstance(b, dict) else None) or u.get("repo"), row)
        if wt.get("path"):
            # A worktree whose branch has a PR says so, and the pane opens that PR from the row.
            wpr = next((pr for pr in all_prs.values() if pr.get("unit") == uid and wt.get("branch")
                        and pr.get("branch") == wt.get("branch")), None)
            add(wt.get("repo") or u.get("repo"), {"kind": "worktree", "ref": wt["path"], "unit": uid,
                                                  "age_h": age(u.get("created") or u.get("updated")),
                                                  "waits_on": "lead", "stage": None, "stale": False,
                                                  "status": u.get("status"), "reclaim": None,
                                                  "pr": f"{wpr.get('repo')}#{wpr.get('number')}" if wpr else None,
                                                  "url": views.pr_url(wpr) if wpr else None}, wt["path"])
    warn = p.get("reviewer_load_warn") or 0
    reviewers = [{**x, "pending": x["mine"], "text": review.load_text(x), "warn": bool(warn) and x["total"] >= warn}
                 for x in review.load_rows(snap, prs={k: v for k, v in prs.items() if k not in dropped}, p=p)
                 if x["mine"] or x["others"]]
    return {"repos": repos, "reviewers": reviewers, "warn_at": warn or None}


def live_inventory(snap):
    repos = {pr.get("repo") for pr in collect_prs(snap).values() if "/" in str(pr.get("repo") or "")}
    repos |= {u.get("repo") for u in snap["units"].values() if "/" in str(u.get("repo") or "")}
    return inventory(snap, live=gh_open_prs(repos))


def cmd_inventory(a):
    inv = live_inventory(cl.load())
    if a.json:
        return _emit(inv)
    for repo, rows in sorted(inv["repos"].items()):
        print(repo)
        for r in rows:
            mark = " STALE" if r["stale"] else ""
            print(f'  {r["kind"]:8} {r["ref"]}  {r["stage"] or "-"}  waits:{r["waits_on"] or "-"}  {r["age_h"]}h{mark}')
    for r in inv["reviewers"]:
        print(i18n.t("cli.reviewer_line_heavy" if r["warn"] else "cli.reviewer_line", login=r["login"], load=r["text"]))
    return 0


# ---------- preflight ----------

def _run_ok(cmd_env, default, *args):
    cmd = shlex.split(os.environ.get(cmd_env, default)) + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _check_vpn(p):
    pf = p.get("preflight") or {}
    host = pf.get("vpn_host")
    if not host:
        return True, "not configured"
    try:
        socket.create_connection((host, int(pf.get("vpn_port") or 443)), timeout=3).close()
        return True, host
    except OSError as e:
        return False, f"{host}: {e}"


def _check_mcp(p):
    paths = [os.path.expanduser(x) for x in (p.get("preflight") or {}).get("mcp_config_paths") or []]
    found = [x for x in paths if os.path.exists(x)]
    return bool(found), ", ".join(found) or "none of " + ", ".join(paths)


# The dispatcher's tools line grants these Linear servers (mcp__<name>__*): a self-added `linear` server and the
# claude.ai Linear connector. Turning Linear on is settings-only; the server just has to be one of these names.
LINEAR_SERVERS = ("linear", "claude_ai_Linear")


def _check_trackers(p):
    """Linear only when enabled: then a Linear MCP server the dispatcher may use must be configured or connected."""
    enabled = p.get("trackers") or []
    try:
        with open(cl.path("repos.json")) as f:
            repos = json.load(f)
    except (OSError, ValueError):
        repos = {}
    off = sorted(f"{n}={r.get('tracker')}" for n, r in repos.items()
                 if isinstance(r, dict) and r.get("tracker") not in (None, "none", *enabled))
    if off:
        return False, f"repos.json uses trackers not enabled in prefs trackers {enabled}: {', '.join(off)}"
    ok, detail = _check_linear(p, enabled)
    uses_jira = "jira" in enabled or any(isinstance(r, dict) and r.get("tracker") == "jira" for r in repos.values())
    if uses_jira:
        import views
        if not views.jira_base():
            heal_jira_base()
        if not views.jira_base():
            return False, (("" if ok else detail + "; ") + "jira is in use but `jira_base` is unset (config.json or "
                           "prefs.json, e.g. \"https://<site>.atlassian.net\"): unit and ticket links to Jira are not drawn")
        if not ((p.get("sync") or {}).get("jira") or {}).get("project"):
            return False, (("" if ok else detail + "; ") + "jira is in use but prefs sync.jira.project is empty: the "
                           "dispatcher creates no ticket (foundry prefs set sync.jira.project <KEY>)")
    return ok, detail


JIRA_BROWSE_RE = re.compile(r"(https://[\w.-]+)/browse/[A-Z][A-Z0-9]+-\d+")


def heal_jira_base():
    """jira_base missing: take it from a ticket URL foundry already recorded (journal notes, inbox, reports). Returns
    the value set, or None (then the foreman reads it from the Atlassian MCP; never an owner chore)."""
    import collections
    import prefs
    seen = collections.Counter()
    for r in cl.read_journal():
        for m in JIRA_BROWSE_RE.finditer(json.dumps(r, ensure_ascii=False)):
            seen[m.group(1)] += 1
    if not seen:
        return None
    base = seen.most_common(1)[0][0]
    return base if not prefs.set_discovered("jira_base", base, f"ticket URLs in the journal ({seen[base]})") else None


def _check_linear(p, enabled):
    if "linear" not in enabled:
        return True, ", ".join(enabled)  # Linear off: nothing about a Linear MCP to report
    hit = sorted(s for s in _mcp_servers(p) if s in LINEAR_SERVERS)
    how = "configured"
    if not hit:
        connected = _mcp_connected(p)
        hit, how = sorted(s for s in connected if s in LINEAR_SERVERS), "connected"
        if not hit:
            near = sorted(s for s in connected | _mcp_servers(p) if "linear" in s.lower())
            return False, ("linear enabled, but no Linear MCP server is configured or connected under a name the "
                           f"dispatcher may use ({', '.join(LINEAR_SERVERS)})"
                           + (f"; found {', '.join(near)}, rename it to `linear`" if near else
                              ": connect one (`claude mcp add` as `linear`, or the claude.ai Linear connector)"))
    return True, f"{', '.join(enabled)}; linear via {', '.join(hit)} ({how})"


def _mcp_servers(p):
    """MCP server names in the configured Claude config files (top level and per project)."""
    names = set()
    for path in (p.get("preflight") or {}).get("mcp_config_paths") or []:
        try:
            with open(os.path.expanduser(path)) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        blocks = [data.get("mcpServers")] + [(v or {}).get("mcpServers") for v in (data.get("projects") or {}).values()
                                              if isinstance(v, dict)]
        names |= {k for b in blocks if isinstance(b, dict) for k in b}
    return names


def _mcp_connected(p):
    """Servers `claude mcp list` reports as connected, named as their tools are (`claude.ai Linear` -> claude_ai_Linear).

    The only view that includes claude.ai connectors; it health-checks every server, so it runs only as a fallback."""
    cmd = (p.get("preflight") or {}).get("mcp_list_cmd", ["claude", "mcp", "list"])
    if not cmd:
        return set()
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=90).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    names = set()
    for line in out.splitlines():
        m = re.match(r"(.+?): .* - \S*\s*Connected\s*$", line)
        if m:
            names.add(re.sub(r"[^A-Za-z0-9_-]", "_", m.group(1).strip()))
    return names


def _check_disk(p):
    free = shutil.disk_usage("/").free / 1e9
    warn = (p.get("pressure") or {}).get("disk_free_gb_warn", 50)
    return free >= warn, f"{free:.0f} GB free (warn {warn})"


def _check_foreman(snap):
    try:
        import watchers
        if hasattr(watchers, "foreman_alive"):
            alive = watchers.foreman_alive(snap)
            return bool(alive), "alive" if alive else "not alive"
    except ImportError:
        pass
    fm = snap.get("foreman") or {}
    if "alive" not in fm:
        return True, "unknown (no foreman record)"
    return bool(fm["alive"]), f'alive={fm["alive"]} at {fm.get("ts")}'


def _check_version():
    try:
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as f:
            return True, json.load(f).get("version") or "?"
    except (OSError, ValueError) as e:
        return False, str(e)


def preflight_checks(snap, p):
    """{name: {ok, detail}}. FOUNDRY_PREFLIGHT_FAKE (JSON {name: bool | {ok, detail}}) replaces every check."""
    fake = os.environ.get("FOUNDRY_PREFLIGHT_FAKE")
    names = ("gh_auth", "vpn", "mcp_config", "mcp_names", "trackers", "docker", "disk", "foreman", "plugin_version")
    if fake:
        data = json.loads(fake)
        out = {}
        for n in names:
            v = data.get(n, True)
            out[n] = v if isinstance(v, dict) else {"ok": bool(v), "detail": "fake"}
        return out
    checks = {
        "gh_auth": lambda: (_run_ok("FOUNDRY_GH", "gh", "auth", "status"), "gh auth status"),
        "vpn": lambda: _check_vpn(p),
        "mcp_config": lambda: _check_mcp(p),
        "mcp_names": lambda: __import__("mcpmap").check(p),
        "trackers": lambda: _check_trackers(p),
        "docker": lambda: (_run_ok("FOUNDRY_DOCKER", "docker", "info"), "docker info"),
        "disk": lambda: _check_disk(p),
        "foreman": lambda: _check_foreman(snap),
        "plugin_version": _check_version,
    }
    out = {}
    for n, fn in checks.items():
        try:
            ok, detail = fn()
        except Exception as e:  # one broken check is a failed check, not a crashed preflight
            ok, detail = False, f"error: {e}"
        out[n] = {"ok": bool(ok), "detail": detail}
    return out


def cmd_preflight(a):
    import prefs
    prefs.ensure_materialized()
    p = _prefs()
    res = preflight_checks(cl.load(), p)
    failures = [n for n, v in res.items() if not v["ok"]]
    cl.append("preflight", ok=not failures, failures=failures)
    if a.json:
        _emit({"ok": not failures, "failures": failures, "checks": res})
    else:
        for n, v in res.items():
            print(f'{"OK  " if v["ok"] else "FAIL"} {n}: {v["detail"]}')
    return 1 if failures else 0


# ---------- rework ----------

FAIL_CI = ("failure", "failed", "error", "red")
GREEN_CI = ("success", "passed", "green", "ok")
REVERT_RE = re.compile(r"^\s*(revert|hotfix)\b", re.I)


def _unit(r):
    return r.get("unit") or r.get("id")


def rework(journal, t=None):
    """Per unit: review rounds, CI failures before green, reverts/hotfixes within 7 days of a merge, rate."""
    t = t or cl.now()
    out = {}

    def slot(uid):
        return out.setdefault(uid, {"review_rounds": 0, "ci_failures": 0, "reverts": 0, "merged": 0})

    ci = {}
    merges = []
    later = []
    for r in journal:
        op, uid = r.get("op"), _unit(r)
        if op == "pr.state" and uid:
            st = str(r.get("state") or r.get("review") or r.get("reviewDecision") or "").lower()
            if st in ("changes_requested", "changes-requested"):
                slot(uid)["review_rounds"] += 1
        elif op == "ci.state" and uid:
            k = (uid, _ref_of(r) if (r.get("repo") or r.get("url")) else "")
            st = str(r.get("state") or r.get("conclusion") or "").lower()
            if st in FAIL_CI:
                ci.setdefault(k, {"fails": 0, "counted": 0})["fails"] += 1
            elif st in GREEN_CI and k in ci:
                slot(uid)["ci_failures"] += ci[k]["fails"]
                ci[k]["fails"] = 0
        elif op == "pr.merged":
            if uid:
                slot(uid)["merged"] += 1
                merges.append(r)
            later.append(r)
        elif op == "pr.opened":
            later.append(r)
    for m in merges:
        mt = cl.parse_ts(m.get("ts"))
        refs = {str(m.get("number") or "#"), m.get("url") or "\0", m.get("title") or "\0"}
        for r in later:
            rt = cl.parse_ts(r.get("ts"))
            title = r.get("title") or ""
            if r is m or not (mt and rt) or not (mt < rt <= mt + dt.timedelta(days=7)) or not REVERT_RE.match(title):
                continue
            text = f'{title} {r.get("body") or ""} {r.get("ref") or ""}'
            if any(x in text for x in refs if x not in ("#", "\0")):
                slot(_unit(m))["reverts"] += 1
                break
    for v in out.values():
        v["rate"] = round((v["review_rounds"] + v["ci_failures"] + v["reverts"]) / max(1, v["merged"]), 2)
    return out


# ---------- cost ----------

def load_prices():
    with open(os.path.join(ROOT, "defaults", "prices.json")) as f:
        return json.load(f)


def _price(model, prices):
    m = str(model or "").split("[")[0]
    table = prices["models"]
    if m in table:
        return m, table[m]
    m = prices.get("aliases", {}).get(m, m)
    for k in sorted(table, key=len, reverse=True):
        if m.startswith(k):
            return k, table[k]
    return m or "unknown", None


def _session_units(snap):
    out = {}
    for sid, rec in (snap.get("foundry_sessions") or {}).items():
        if rec.get("unit"):
            out[sid] = rec["unit"]
    for x in (snap.get("sessions") or {}).values():
        if x.get("session_id") and x.get("unit"):
            out[x["session_id"]] = x["unit"]
    return out


def costs(snap, root=None):
    """{unit: {usd, by_model: {model: {tokens..., usd}}}} summed from Claude transcripts, deduped by message id."""
    root = root or os.environ.get("FOUNDRY_TRANSCRIPTS") or os.path.expanduser("~/.claude/projects")
    prices = load_prices()
    owners = _session_units(snap)
    seen = set()
    out = {}
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        parts = os.path.relpath(path, root).replace(".jsonl", "").split(os.sep)
        try:
            f = open(path)
        except OSError:
            continue
        with f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message") if isinstance(rec, dict) else None
                usage = (msg or {}).get("usage") if isinstance(msg, dict) else None
                if not usage:
                    continue
                mid = msg.get("id") or rec.get("uuid")
                if mid in seen:
                    continue
                seen.add(mid)
                sid = rec.get("sessionId")
                unit = owners.get(sid) or next((owners[p] for p in parts if p in owners), "_other")
                name, price = _price(msg.get("model"), prices)
                toks = {"input": usage.get("input_tokens") or 0, "output": usage.get("output_tokens") or 0,
                        "cache_write": usage.get("cache_creation_input_tokens") or 0,
                        "cache_read": usage.get("cache_read_input_tokens") or 0}
                usd = sum(toks[k] * price[k] for k in toks) / 1e6 if price else 0.0
                u = out.setdefault(unit, {"usd": 0.0, "by_model": {}})
                bm = u["by_model"].setdefault(name, {**{k: 0 for k in toks}, "usd": 0.0, "priced": bool(price)})
                for k in toks:
                    bm[k] += toks[k]
                bm["usd"] += usd
                u["usd"] += usd
    for u in out.values():
        u["usd"] = round(u["usd"], 4)
        for bm in u["by_model"].values():
            bm["usd"] = round(bm["usd"], 4)
    return out


def cmd_cost(a):
    snap = cl.load()
    res = costs(snap)
    if a.unit:
        res = {a.unit: res.get(a.unit, {"usd": 0.0, "by_model": {}})}
    old = snap.get("costs") or {}
    recs = [("cost", {"unit": k, "by_model": v["by_model"], "usd": v["usd"]}) for k, v in res.items()
            if (old.get(k) or {}).get("usd") != v["usd"]]
    if recs:
        cl.append_many(recs)
    _emit({"units": res, "total_usd": round(sum(v["usd"] for v in res.values()), 4),
           "note": "display only; cache_write prices are estimates (defaults/prices.json)"})
    return 0


# ---------- handover and kaizen ----------

def _open_cards(snap):
    import decide
    out = []
    for cid, c in (snap.get("cards") or {}).items():
        if (c.get("answer") is None and c.get("state") not in ("answered", "closed", "expired")
                and not decide.moot_reason(snap, c)):
            out.append({"id": cid, "unit": c.get("unit"), "question": c.get("question")})
    for k, d in (snap.get("decisions") or {}).items():
        if d.get("answer") is None and not decide.moot_reason(snap, d):
            out.append({"id": k, "unit": d.get("unit"), "question": d.get("question")})
    return out


def _section(title, lines):
    return [f"## {title} ({len(lines)})"] + (lines or ["- " + i18n.t("cli.none")])


def handover(snap, journal, inv, t=None):
    t = t or cl.now()
    prev = cl.parse_ts((snap.get("handover") or {}).get("ts"))
    since = prev or t - dt.timedelta(hours=24)
    recent = [r for r in journal if (cl.parse_ts(r.get("ts")) or since) > since]
    title = lambda uid: (snap["units"].get(uid) or {}).get("title") or uid  # noqa: E731
    import views
    done = list(dict.fromkeys(f"- {r.get('id') or r.get('unit')}: {title(r.get('id') or r.get('unit'))}" for r in recent
                              if r.get("op") in ("unit.status", "unit.set") and r.get("status") == "done"))
    cards = [f"- {c.get('unit') or '-'} · {views.headline(c, snap)} (`foundry decide show {c['id']}`)" for c in _open_cards(snap)]
    allc = snap.get("cards") or {}
    alld = snap.get("decisions") or {}
    auto = [f"- {(allc.get(r.get('id')) or {}).get('unit') or '-'} · {r.get('id')} "
            f"{views.gist((allc.get(r.get('id')) or {}).get('question') or '', 50)} — {r.get('moot')}"
            for r in recent if r.get("op") == "decide.answer" and r.get("by") == "foundry" and r.get("moot")]
    auto += [f"- {r.get('unit') or '-'} · {r.get('key')} {views.gist((alld.get(r.get('key')) or {}).get('question') or '', 50)}"
             f" — {r.get('moot')}" for r in recent if r.get("op") == "decision.answer" and r.get("moot")]
    cleared = {_unit(r) for r in recent if r.get("op") == "stall.cleared"}
    stalls = [f"- {_unit(r)}: {r.get('kind') or ''} {r.get('text') or r.get('detail') or ''}".rstrip()
              for r in recent if r.get("op") == "unit.stalled" and _unit(r) not in cleared]
    import aging
    op, done_nm = aging.split(snap)
    near = [f"- {g['pattern']} x{g['count']} ({', '.join(g['units'][:4]) or '-'})" for g in aging.groups(op)]
    if done_nm:
        near.append("- " + i18n.t("cli.near_misses_folded", n=len(done_nm)))
    changed = pr_changes(snap, inv, since, t)
    usd = sum((v or {}).get("usd") or 0 for v in (snap.get("costs") or {}).values())
    tr = i18n.t
    lines = ["# " + tr("cli.handover_title", at=t.astimezone(cl.zone()).strftime('%Y-%m-%d %H:%M'),
                       since=since.astimezone(cl.zone()).strftime('%m-%d %H:%M'))]
    for key, xs in (("h_done", done), ("h_decisions", cards), ("h_auto_closed", auto), ("h_stalls", stalls),
                    ("h_near_misses", near)):
        lines += _section(tr("cli." + key), xs)
    lines += _section(tr("cli.h_pr_changes"),
                      [f"- {x['unit'] + ': ' if x['unit'] else ''}{x['line']}" for x in changed[:PR_CHANGES_CAP]])
    lines.append("- " + tr("cli.pr_tab_all", cmd="`foundry board --tab pr`"))
    if usd > 0:
        lines += ["## " + tr("cli.h_cost"), "- " + tr("cli.cost_total", usd=f"{usd:.2f}")]
    return "\n".join(lines)


PR_CHANGES_CAP = 5
PR_SECTION = re.compile(r"\bPR\b")


def _short_ref(ref):
    return str(ref or "").rsplit("/", 1)[-1]


def pr_changes(snap, inv, since, t=None, p=None):
    """What happened to the owner's PRs since `since`, newest first, as one-liners in ui.language: merged, approved, newly
    over the stale line, newly the owner's call. The lists themselves live on the PR tab; this is only the delta."""
    import views
    t = t or cl.now()
    p = p or _prefs()
    if not since:
        return []
    zone = _zone(p)
    limit_h = 24 * (p.get("stale_business_days") or 1)
    esc_h = 24 * ((p.get("review") or {}).get("escalate_business_days") or 5)
    out, seen = [], set()

    def add(ref, unit, when, what, title):
        if ref in seen:
            return
        seen.add(ref)
        line = f"{_short_ref(ref)} {what}" + (f" — {views.gist(title, 50)}" if title else "")
        out.append({"unit": unit, "line": line, "id": None, "ref": ref, "ts": cl.iso(when) if when else None,
                    "url": f"https://github.com/{ref.replace('#', '/pull/')}" if "/" in ref else None})

    for uid, u in (snap.get("units") or {}).items():
        for pr in u.get("prs") or []:
            when = cl.parse_ts(pr.get("merged"))
            if when and when > since:
                add(f"{pr.get('repo')}#{pr.get('number')}", uid, when, i18n.t("cli.pr_merged"), pr.get("title"))
    rows = [r for rs in (inv.get("repos") or {}).values() for r in rs if r.get("kind") == "pr"]
    for r in rows:
        when = cl.parse_ts(r.get("approved_at"))
        if when and when > since and r.get("stage") in ("approved", "merged"):
            add(r["ref"], r.get("unit"), when, i18n.t("cli.pr_approved"), r.get("title"))
    for r in rows:
        if r.get("stage") != "review":
            continue
        start = cl.parse_ts(r.get("since"))
        then, now = business_hours(start, since, zone), business_hours(start, t, zone)
        if then < esc_h <= now:
            add(r["ref"], r.get("unit"), t, i18n.t("cli.pr_owner_call"), r.get("title"))
        elif then <= limit_h < now:
            add(r["ref"], r.get("unit"), t, i18n.t("cli.pr_review_stale"), r.get("title"))
    return sorted(out, key=lambda x: x["ts"] or "", reverse=True)


HANDOVER_HEAD = re.compile(r"^##\s+(.+?)(?:\s+\((\d+)\))?\s*$")
HANDOVER_ITEM = re.compile(r"^-\s+(?:\[[^\]]+\]\s+)?(?:([A-Za-z0-9][\w.-]*):\s+)?(.*)$")
CODE_SPAN = re.compile(r"`[^`]*`?")


def _handover_line(text):
    """One line: commands and query specs folded to 「(명령)」, the first sentence, at most 70 columns."""
    import views
    return views.gist(CODE_SPAN.sub(i18n.t("cli.command_folded"), text or ""), 70)


def _in_any_language(key):
    """A stored handover keeps the language it was written in, so the parser knows each word in all of them."""
    return {i18n.t(key, lang) for lang in i18n.LANGS}


def handover_view(snap, inv=None):
    """The morning handover as the board draws it: sections of `unit — one line` items, with 결정 대기 read from the
    live decision state (the snapshot's list goes stale as soon as a card is answered) and a count of what changed
    since. The full text stays in `handover.text` and each unit's detail; the board never prints it raw.
    Stored section titles stay as written; the live sections the board adds take the current ui.language."""
    h = snap.get("handover") or {}
    text = h.get("text") or ""
    if not text:
        return None
    ts = cl.parse_ts(h.get("ts"))
    none, live_titles, pr_titles = (_in_any_language(k) for k in ("cli.none", "cli.h_decisions", "cli.h_pr_changes"))
    live_title = i18n.t("cli.h_decisions")
    sections, cur = [], None
    for line in text.splitlines():
        m = HANDOVER_HEAD.match(line)
        if m:
            cur = {"title": m.group(1).strip(), "items": [], "live": False}
            sections.append(cur)
            continue
        m = HANDOVER_ITEM.match(line)
        if cur is None or not m or m.group(2).strip() in none:
            continue
        unit = m.group(1) if m.group(1) in (snap.get("units") or {}) else None
        body = m.group(2) if unit else line[2:]
        cur["items"].append({"unit": unit, "line": _handover_line(body), "id": None})
    live = [{"unit": c.get("unit"), "id": c["id"], "line": _handover_line(c.get("question") or c["id"])}
            for c in _open_cards(snap)]
    dec = next((x for x in sections if x["title"] in live_titles), None)
    if dec is None:
        dec = {"title": live_title, "items": [], "live": True}
        sections.insert(1 if sections else 0, dec)
    dec.update(title=live_title, items=live, live=True)
    # PR lists are the PR tab's: the snapshot's PR sections give way to what changed since, read now.
    sections = [x for x in sections if not PR_SECTION.search(x["title"]) and x["title"] not in pr_titles]
    if (snap.get("kaizen") or {}).get("text"):  # the kaizen section lists near misses: one list, not two
        near = _in_any_language("cli.h_near_misses")
        sections = [x for x in sections if x["title"] not in near]
    for x in sections:
        x["count"] = len(x["items"])
    if ts:
        ch = pr_changes(snap, inventory(snap) if inv is None else inv, ts)
        sections.append({"title": i18n.t("cli.h_pr_changes"), "items": ch[:PR_CHANGES_CAP], "live": True, "count": len(ch),
                         "more": max(0, len(ch) - PR_CHANGES_CAP), "link": "pr"})

    def after(v):
        w = cl.parse_ts(v)
        return bool(ts and w and w > ts)
    changes = sum(after(u.get("updated")) for u in (snap.get("units") or {}).values() if u.get("kind") != "foreman")
    changes += sum(after(c.get("answered")) or after(c.get("opened")) for c in (snap.get("cards") or {}).values())
    changes += sum(after(d.get("answered")) or after(d.get("asked")) for d in (snap.get("decisions") or {}).values())
    return {"text": text, "ts": h.get("ts"), "at": ts.astimezone(cl.zone()).strftime("%m-%d %H:%M") if ts else None,
            "changes": changes, "sections": sections}


def kaizen(snap, journal, t=None):
    t = t or cl.now()
    week = t - dt.timedelta(days=7)
    near = [n for n in snap.get("nearmiss") or [] if (cl.parse_ts(n.get("ts")) or t) >= week]
    by_kind = {}
    for n in near:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
    rw = rework([r for r in journal if (cl.parse_ts(r.get("ts")) or t) >= week - dt.timedelta(days=7)], t)
    tr = i18n.t
    none = "- " + tr("cli.none")
    lines = ["# " + tr("cli.kaizen_title", date=t.astimezone(cl.zone()).strftime('%Y-%m-%d')),
             f"## {tr('cli.h_near_misses')} ({len(near)})"]
    lines += [f"- {k}: {v}" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])] or [none]
    lines.append("## " + tr("cli.h_rework"))
    lines += ["- " + tr("cli.rework_line", unit=u, rate=v["rate"], reviews=v["review_rounds"], ci=v["ci_failures"],
                        reverts=v["reverts"], merged=v["merged"]) for u, v in sorted(rw.items())] or [none]
    return {"nearmiss": by_kind, "rework": rw, "text": "\n".join(lines)}


def cmd_report(a):
    snap = cl.load()
    journal = cl.read_journal()
    if a.kind == "handover":
        text = handover(snap, journal, live_inventory(snap))
        cl.append("report.handover", text=text)
        return _emit({"text": text}) if a.json else print(text) or 0
    res = kaizen(snap, journal)
    cl.append("report.kaizen", text=res["text"])  # the board and the pane show the latest one
    return _emit(res) if a.json else print(res["text"]) or 0


def kaizen_view(snap):
    """The latest weekly kaizen for the board: when, and its lines as (depth, text), the title dropped."""
    k = snap.get("kaizen") or {}
    if not k.get("text"):
        return None
    t = cl.parse_ts(k.get("ts"))
    lines = [{"head": ln.startswith("## "), "text": ln.lstrip("#- ").strip()}
             for ln in k["text"].splitlines()[1:] if ln.strip()]
    return {"at": t.astimezone(cl.zone()).strftime("%m-%d %H:%M") if t else None, "lines": lines}


NEARMISS_PARSER = None


def cmd_nearmiss(a):
    import aging
    snap = cl.load()
    xs = aging.split(snap)[0] if a.open else snap.get("nearmiss") or []
    if a.unit:
        xs = [n for n in xs if n.get("unit") == a.unit]
    return _emit(xs[-a.limit:])


# ---------- traceability ----------

def trace(snap, journal, uid):
    u = snap["units"].get(uid)
    if not u:
        return None
    prs = []
    for ref, pr in collect_prs(snap).items():
        if pr.get("unit") != uid:
            continue
        view = gh("pr", "view", str(pr.get("number")), "-R", pr.get("repo") or "", "--json",
                  "commits,mergeCommit,title,state,url") if pr.get("repo") and pr.get("number") else None
        view = view if isinstance(view, dict) else {}
        prs.append({"ref": ref, "title": view.get("title") or pr.get("title"), "state": view.get("state") or pr.get("state"),
                    "url": view.get("url") or pr.get("url"),
                    "commits": [c.get("oid") for c in view.get("commits") or [] if isinstance(c, dict)],
                    "merge_commit": (view.get("mergeCommit") or {}).get("oid")})
    refs = {p["ref"] for p in prs}
    mine = [r for r in journal if _unit(r) == uid or (isinstance(r.get("pr"), str) and r["pr"] in refs)]
    tags = [r.get("tag") for r in mine if r.get("op") in ("release.tag", "tag.pushed", "release.done") and r.get("tag")]
    deploys = [{k: r.get(k) for k in ("ts", "env", "tag", "app")} for r in mine if r.get("op") == "deploy.done"]
    tags += [d["tag"] for d in deploys if d.get("tag") and d["tag"] not in tags]
    return {"unit": uid, "title": u.get("title"), "tickets": u["keys"]["ticket"], "prs": prs, "tags": tags,
            "deploys": deploys}


def cmd_trace(a):
    res = trace(cl.load(), cl.read_journal(), a.unit)
    if res is None:
        return _emit({"error": f"no unit {a.unit}"}) or 1
    return _emit(res)


# ---------- board and CLI ----------

def board(snap, model):
    model["inventory"] = inventory(snap)
    model["handover"] = handover_view(snap, model["inventory"])
    model["kaizen"] = kaizen_view(snap)
    model["nearmiss_count"] = len(snap.get("nearmiss") or [])
    costs_ = snap.get("costs") or {}
    for sec in model.get("sections") or []:
        for row in sec.get("rows") or []:
            row["cost_usd"] = (costs_.get(row.get("id")) or {}).get("usd")


def add_cli(sp):
    i = sp.add_parser("inventory")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_inventory)
    pf = sp.add_parser("preflight")
    pf.add_argument("--json", action="store_true")
    pf.set_defaults(func=cmd_preflight)
    # `report` (handover, kaizen and the rest) is report.py's parser; it calls cmd_report here.
    global NEARMISS_PARSER
    n = NEARMISS_PARSER = sp.add_parser("nearmiss").add_subparsers(dest="sub", required=True)
    nl = n.add_parser("list")
    nl.add_argument("--unit")
    nl.add_argument("--open", action="store_true", help="only entries whose pattern is not resolved")
    nl.add_argument("--limit", type=int, default=50)
    nl.set_defaults(func=cmd_nearmiss)
    c = sp.add_parser("cost")
    c.add_argument("--unit")
    c.set_defaults(func=cmd_cost)
    t = sp.add_parser("trace")
    t.add_argument("unit")
    t.set_defaults(func=cmd_trace)
