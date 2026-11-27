"""Who reviews a PR, and the Slack ask that goes with it (owner rules 2026-10-07, 2026-10-26, 2026-11-04).

prefs `review` holds the team pool (`people`: GitHub login, Slack id), the team channel and per-repo rules
(`repos`, first glob match wins: `pool`, optional `approvers`, optional `channel`). `foundry review assign
<owner/repo#n> --unit <id>` picks, among pool members who can approve in that repo (the rule's `approvers`, else
write access read from GitHub) and are not the author, the most relevant one first (CODEOWNERS of the changed paths,
reviewers of the unit's or ticket's earlier PRs, their reviews and commits there in the last 90 days) and the least
loaded on a tie; anyone over `load_cap` is skipped unless they are the only CODEOWNER. It sets them as reviewer AND
assignee, records the reason (`review.pick`, shown on the PR row), and files a `review-ask` outbox item for the
dispatcher. The sessions never ask the owner who reviews.

Load has one definition, `load_rows`, read by the pane, board.html, `foundry review load` and the assignment:
"내 의뢰" = the owner's open PRs that request that person directly, by the owner's or foundry's own ask (never a
CODEOWNERS / team auto-assignment or a bot: `split_reviewers`); "참고" = their other direct requests in tracked
repos, bots excluded, cached in reviewer-load.json and refreshed by the github stream.

The dispatcher sends review asks per reviewer (`foundry outbox batches`): bundled once a reviewer has
`batch_min` asks or the oldest waited `batch_wait_min`, scheduled for the end of the Slack quiet window at night,
thread replies with reply_broadcast false (slackguard), one ask per PR head (slackguard's ledger).
"""

import fnmatch
import json
import os
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

WRITE = ("admin", "maintain", "write")
UNIT_FIELDS = {"review_picks": {}}


def _op_review_pick(s, r, ts):
    u = (s.get("units") or {}).get(r.get("unit"))
    if u is not None:
        u.setdefault("review_picks", {})[r.get("ref")] = {"reviewer": r.get("reviewer"), "reason": r.get("reason"),
                                                          "ts": ts}


OPS = {"review.pick": _op_review_pick}


def _prefs(p=None):
    if p is not None:
        return p
    import prefs
    return prefs.load()


def rule(slug, p=None):
    rv = _prefs(p)["review"]
    name = slug.rsplit("/", 1)[-1]
    return next((r for r in rv.get("repos") or [] if fnmatch.fnmatch(slug, r["repo"]) or fnmatch.fnmatch(name, r["repo"])),
                None)


def slack_of(login, p=None):
    return next((x.get("slack") for x in _prefs(p)["review"].get("people") or [] if x.get("login") == login), None)


def human_checks(p=None):
    try:
        return set(_prefs(p)["review"].get("human_approval_checks") or [])
    except Exception:
        return set()


def _gh(*args):
    import factory
    return factory.gh(*args)


def is_collaborator(slug, login):
    """`GET repos/<slug>/collaborators/<login>` answers 204 for a collaborator and 404 otherwise; a reviewer who is
    not one makes `gh pr edit --add-reviewer` fail with 422."""
    gh = shlex.split(os.environ.get("FOUNDRY_GH", "gh"))
    try:
        r = subprocess.run([*gh, "api", f"repos/{slug}/collaborators/{login}"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def can_approve(slug, login, r):
    if not is_collaborator(slug, login):
        return False
    if r.get("approvers"):
        return login in r["approvers"]
    res = _gh("api", f"repos/{slug}/collaborators/{login}/permission")
    return isinstance(res, dict) and res.get("permission") in WRITE


# ---------- reviewer load: one definition for the pane, board.html, the foreman and the assignment ----------
#
# mine   open PRs foundry tracks for the owner (their own, or opened by foundry for them), in review or approved by
#        someone else, that request this person directly and that they have not approved (a team request does not
#        count).
# others open PRs in the tracked repos that request this person directly, authored by anyone but the owner,
#        bot authors excluded (GitHub search, cached in reviewer-load.json, refreshed by the github stream).

LOAD_FILE = "reviewer-load.json"
LOAD_TTL_MIN = 30
DAYS = 90


def _is_bot(user):
    login = str((user or {}).get("login") or "")
    return (user or {}).get("type") == "Bot" or login.endswith("[bot]") or login.startswith("app/")


def _direct(revs):
    return [x if isinstance(x, str) else x.get("login") for x in revs or []
            if isinstance(x, str) or (isinstance(x, dict) and x.get("login"))]


# Who the owner actually asked. GitHub lists every requested reviewer, but CODEOWNERS and team auto-assignment
# request people (and machine accounts) nobody chose; the timeline tells them apart: those requests land in one
# batch with the team requests, at the same second, or come from a bot or an app. Only the rest, plus anyone in
# the configured pool or picked by `foundry review assign`, counts as 내 의뢰, waits on a PR row, or fills a load bar.

def owner_login():
    """The owner's GitHub login: config `owner_github_login`, else `gh api user` (read once per process)."""
    global _OWNER_LOGIN
    if _OWNER_LOGIN is None:
        try:
            login = cl.load_config().get("owner_github_login")
        except Exception:
            login = None
        if not login:
            u = _gh("api", "user")
            login = u.get("login") if isinstance(u, dict) else None
        _OWNER_LOGIN = login or ""
    return _OWNER_LOGIN or None


_OWNER_LOGIN = None


def auto_requested(events, owner=None):
    """Logins whose latest `review_requested` event was automatic: a bot or app actor, a bot reviewer, or the
    same second as a team request (CODEOWNERS and team auto-assignment request people alongside their teams).
    A request the owner made by hand counts as asked even beside a team request; only the owner's batches that
    open the review (the first request second, or a ready-for-review second) are CODEOWNERS' auto requests."""
    evs = events or []
    team_ts = {ev.get("created_at") for ev in evs if ev.get("event") == "review_requested" and ev.get("requested_team")}
    asks = sorted(ev.get("created_at") or "" for ev in evs if ev.get("event") == "review_requested")
    opening = {asks[0]} if asks else set()
    opening |= {ev.get("created_at") for ev in evs if ev.get("event") == "ready_for_review"}
    last = {}
    for ev in evs:
        login = (ev.get("requested_reviewer") or {}).get("login")
        if ev.get("event") != "review_requested" or not login:
            continue
        ts = ev.get("created_at")
        by_owner = bool(owner) and str((ev.get("actor") or {}).get("login") or "").lower() == owner.lower()
        last[login] = (_is_bot(ev.get("actor")) or bool(ev.get("performed_via_github_app"))
                       or _is_bot(ev.get("requested_reviewer"))
                       or (ts in team_ts and not (by_owner and ts not in opening)))
    return sorted(k for k, auto in last.items() if auto)


def read_auto(ref):
    """auto_requested for one PR from its timeline (read-only), or None when GitHub could not be read."""
    repo, num = ref.split("#", 1)
    ev = _gh("api", f"repos/{repo}/issues/{num}/timeline?per_page=100")
    return auto_requested(ev, owner_login()) if isinstance(ev, list) else None


def split_reviewers(pr, pool=None, picked=()):
    """(asked, auto) for a PR's requested reviewers. Teams and bots are always auto. With the timeline read
    (`auto_reviewers` recorded), everyone else the owner or foundry asked counts; without it, only the pool and
    foundry's own picks do: fail closed, so an unexplained name never fills the owner's load bars."""
    pool = set(pool_logins() if pool is None else pool)
    known = pr.get("auto_reviewers")
    asked, auto = [], []
    for x in pr.get("reviewers") or pr.get("reviewRequests") or []:
        login = x if isinstance(x, str) else x.get("login")
        name = login or (x.get("slug") or x.get("name") if isinstance(x, dict) else None)
        if not name:
            continue
        if not login or _is_bot({"login": login, "type": None if isinstance(x, str) else x.get("type")}):
            auto.append(name)
        elif login in pool or login in picked:
            asked.append(login)
        elif known is not None and login not in known:
            asked.append(login)
        else:
            auto.append(login)
    return asked, auto


def _picked(snap, ref, unit=None):
    us = (snap or {}).get("units") or {}
    rows = [us.get(unit)] if unit in us else us.values()
    return {((u or {}).get("review_picks") or {}).get(ref, {}).get("reviewer") for u in rows} - {None}


def tracked_repos(snap=None):
    """owner/name of every repo foundry tracks: the registry's slugs and the repos of units and their PRs."""
    import gitpolicy
    snap = snap or cl.load()
    out = {v.get("slug") or k for k, v in (gitpolicy.repos() or {}).items() if "/" in str(v.get("slug") or k)}
    for u in snap["units"].values():
        out |= {str(r) for r in [u.get("repo")] + [pr.get("repo") for pr in u.get("prs") or []] if "/" in str(r or "")}
    return sorted(out)


def pool_logins(p=None):
    rv = _prefs(p).get("review") or {}
    out = [x.get("login") for x in rv.get("people") or [] if x.get("login")]
    for r in rv.get("repos") or []:
        out += [x for x in r.get("pool") or [] if x not in out]
    return out


def _cache():
    try:
        with open(cl.path(LOAD_FILE)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def others_query(login, repos):
    q = "+".join(["is:pr", "is:open", f"user-review-requested:{login}", "-author:@me"] + [f"repo:{r}" for r in repos])
    return f"search/issues?q={q}&per_page=100"


def refresh_others(logins, repos=None, now=None, force=False):
    """Re-read `others` from GitHub for these logins (at most every LOAD_TTL_MIN unless forced); the cache file."""
    import datetime as dt
    now = now or cl.now()
    repos = tracked_repos() if repos is None else repos
    c = _cache()
    last = cl.parse_ts(c.get("ts"))
    if not force and last and now - last < dt.timedelta(minutes=LOAD_TTL_MIN) and set(logins) <= set(c.get("others") or {}):
        return c
    others = dict(c.get("others") or {})
    for login in logins:
        res = _gh("api", others_query(login, repos)) if repos else {"items": []}
        if isinstance(res, dict):
            others[login] = sum(1 for it in res.get("items") or [] if not _is_bot(it.get("user")))
    c = {"ts": cl.iso(now), "repos": repos, "others": others}
    cl.write_atomic(cl.path(LOAD_FILE), json.dumps(c, ensure_ascii=False))
    return c


def load_rows(snap=None, prs=None, p=None, logins=None):
    """[{login, mine, others, total}] sorted by mine, then others: THE reviewer load. `prs` is factory.collect_prs's
    map when the caller already has it (the inventory passes its own, live listing included)."""
    import factory
    snap = snap or cl.load()
    prs = factory.collect_prs(snap) if prs is None else prs
    import aging
    pool = pool_logins(p)
    mine = {}
    for ref, pr in prs.items():
        # Approved by someone else still waits on the others asked; one who already approved (✓) is done.
        if aging.is_dropped(snap, ref) or factory.stage_of(pr) not in factory.OPEN_STAGES:
            continue
        done = {str(x).lower() for x in pr.get("approvers") or []}
        for login in split_reviewers(pr, pool, _picked(snap, ref, pr.get("unit")))[0]:
            if login.lower() not in done:
                mine[login] = mine.get(login, 0) + 1
    others = _cache().get("others") or {}
    names = list(dict.fromkeys(list(logins if logins is not None else pool) + list(mine)))
    rows = [{"login": x, "mine": mine.get(x, 0), "others": others.get(x),
             "total": mine.get(x, 0) + (others.get(x) or 0)} for x in names]
    return sorted(rows, key=lambda r: (-r["mine"], -(r["others"] or 0), r["login"]))


def load_text(r):
    import i18n
    return i18n.t("cli.review_load", mine=r["mine"], others="-" if r.get("others") is None else r["others"])


# ---------- relevance ----------

def _codeowners(slug):
    import base64
    for path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        res = _gh("api", f"repos/{slug}/contents/{path}")
        if isinstance(res, dict) and res.get("content"):
            try:
                return base64.b64decode(res["content"]).decode("utf-8", "replace")
            except ValueError:
                return ""
    return ""


def _co_match(pat, path):
    if pat in ("*", "/*", "**"):
        return True
    anchored, p = pat.startswith("/"), pat.strip("/")
    if pat.endswith("/"):
        return path.startswith(p + "/") or (not anchored and f"/{p}/" in f"/{path}")
    if "/" not in p and not anchored:
        return any(fnmatch.fnmatch(seg, p) for seg in path.split("/")) or fnmatch.fnmatch(path, p)
    return fnmatch.fnmatch(path, p) or path.startswith(p + "/") or fnmatch.fnmatch(path, p + "/*")


def code_owners(text, files):
    """Logins CODEOWNERS names for any of these paths (the last matching rule wins per path; teams skipped)."""
    rules = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            pat, *owners = line.split()
            rules.append((pat, [o[1:] for o in owners if o.startswith("@") and "/" not in o]))
    out = set()
    for f in files:
        hit = None
        for pat, owners in rules:
            if _co_match(pat, f):
                hit = owners
        out |= set(hit or [])
    return out


def _dirs(files, n=3):
    out = []
    for f in files:
        d = os.path.dirname(f) or "."
        if d not in out:
            out.append(d)
    return out[:n]


def _earlier(snap, unit, ref):
    """Logins that reviewed or were asked on the unit's other PRs, or on PRs of units with the same ticket."""
    if not unit or unit not in snap["units"]:
        return set()
    u = snap["units"][unit]
    tickets = set((u.get("keys") or {}).get("ticket") or [])
    near = [x for x in snap["units"].values()
            if x["id"] == unit or (tickets and tickets & set((x.get("keys") or {}).get("ticket") or []))]
    out = set()
    for x in near:
        for pr in x.get("prs") or []:
            if f"{pr.get('repo')}#{pr.get('number')}" != ref:
                out |= set(_direct(pr.get("reviewers"))) | set(pr.get("approvers") or [])
    return out


def relevance(slug, login, files, owners, earlier, since):
    """({codeowner, earlier, reviews, commits}, score): explainable GitHub signals, read-only."""
    res = _gh("api", f"search/issues?q=is:pr+repo:{slug}+reviewed-by:{login}+-author:{login}+updated:>={since}&per_page=1")
    reviews = res.get("total_count") or 0 if isinstance(res, dict) else 0
    commits = 0
    for d in _dirs(files):
        rows = _gh("api", f"repos/{slug}/commits?author={login}&since={since}T00:00:00Z&path={d}&per_page=30")
        commits += len(rows) if isinstance(rows, list) else 0
    sig = {"codeowner": login in owners, "earlier": login in earlier, "reviews": reviews, "commits": commits}
    score = 5 * sig["codeowner"] + 3 * sig["earlier"] + min(reviews, 10) + min(commits, 10)
    return sig, score


def reason_text(row):
    sig = row.get("signals") or {}
    parts = (["CODEOWNERS"] if sig.get("codeowner") else []) + (["이전 PR 리뷰어"] if sig.get("earlier") else []) \
        + ([f"최근 리뷰 {sig['reviews']}건"] if sig.get("reviews") else []) \
        + ([f"최근 커밋 {sig['commits']}건"] if sig.get("commits") else [])
    out = f"선정: {' + '.join(parts) or '관련 신호 없음 (부하 순)'} · 내 의뢰 {row.get('mine', 0)}"
    return out + (" · 상한 초과지만 유일한 CODEOWNER" if row.get("over_cap") else "")


def choose(slug, author, p=None, files=None, unit=None, ref=None, snap=None):
    """(login or None, rows): relevance first (CODEOWNERS, earlier PRs of the unit/ticket, recent reviews and
    commits on the changed paths in the last DAYS days), load (load_rows' total) as the tie-break. Candidates come
    only from the rule's pool, must be able to approve and not be the author; one above review.load_cap is skipped
    unless they are the only CODEOWNER who can approve."""
    import datetime as dt
    p = _prefs(p)
    r = rule(slug, p)
    if not r:
        return None, []
    snap = snap or cl.load()
    pool = [x for x in r.get("pool") or [] if x != author]
    refresh_others(pool, force=True)  # a choice is rare and acts: read the numbers fresh
    loads = {x["login"]: x for x in load_rows(snap, p=p, logins=pool)}
    files = list(files or [])
    owners = code_owners(_codeowners(slug), files) if files else set()
    earlier = _earlier(snap, unit, ref)
    since = (cl.now() - dt.timedelta(days=DAYS)).strftime("%Y-%m-%d")
    cap = int((p.get("review") or {}).get("load_cap") or 0)
    rows = []
    for i, login in enumerate(pool):
        ok = can_approve(slug, login, r)
        ld = loads.get(login) or {"mine": 0, "others": None, "total": 0}
        row = {"login": login, "approve": ok, "load": ld["total"], "mine": ld["mine"], "others": ld["others"],
               "order": i}
        if ok:
            row["signals"], row["score"] = relevance(slug, login, files, owners, earlier, since)
        rows.append(row)
    able = [x for x in rows if x["approve"]]
    sole = [x["login"] for x in able if x["signals"]["codeowner"]]
    for x in able:
        x["over_cap"] = bool(cap) and x["load"] > cap
        x["skipped"] = x["over_cap"] and sole != [x["login"]]
    pick = sorted((x for x in able if not x["skipped"]), key=lambda x: (-x["score"], x["load"], x["order"]))
    if not pick:
        return None, rows
    pick[0]["reason"] = reason_text(pick[0])
    return pick[0]["login"], rows


def _edit(slug, n, login):
    gh = shlex.split(os.environ.get("FOUNDRY_GH", "gh"))
    r = subprocess.run([*gh, "pr", "edit", str(n), "-R", slug, "--add-reviewer", login, "--add-assignee", login],
                       capture_output=True, text=True, timeout=60)
    return None if r.returncode == 0 else (r.stderr or r.stdout).strip()[:300]


def ask_text(slack, pr):
    return (f"<@{slack}>\n\n{pr.get('title') or 'PR'} のレビューをお願いします（head {(pr.get('headRefOid') or '')[:7]}）\n"
            f"{pr.get('url')}")


def assign(ref, unit, p=None, dry_run=False):
    """(exit code, payload). Picks and sets reviewer == assignee and files the outbox ask; idempotent."""
    p = _prefs(p)
    m = cl.norm_pr(ref)
    if not m:
        return 2, {"error": f"{ref!r} is not owner/repo#n"}
    slug, n = m[0].split("#")
    if unit:
        import gitpolicy
        why = gitpolicy.bump_review_refusal(cl.load(), unit, f"{slug}#{n}")
        if why:
            return 2, {"error": why, "ref": f"{slug}#{n}"}
    r = rule(slug, p)
    if not r:
        return 2, {"error": f"no prefs review.repos rule matches {slug}: add one (pool of GitHub logins)"}
    pr = _gh("pr", "view", n, "-R", slug, "--json",
             "author,reviewRequests,latestReviews,assignees,headRefOid,isDraft,title,url,state,files")
    if not isinstance(pr, dict):
        return 1, {"error": f"could not read {slug}#{n} from GitHub"}
    if pr.get("state") != "OPEN" or pr.get("isDraft"):
        return 2, {"error": f"{slug}#{n} is {'a draft' if pr.get('isDraft') else pr.get('state')}: no review ask"}
    author = (pr.get("author") or {}).get("login")
    asked = [x.get("login") for x in pr.get("reviewRequests") or [] if x.get("login")]
    reviewed = [(x.get("author") or {}).get("login") for x in pr.get("latestReviews") or []]
    assignees = [x.get("login") for x in pr.get("assignees") or []]
    if asked or [x for x in reviewed if x and x != author]:
        who = asked[0] if asked else reviewed[0]
        out = {"existing": who, "ref": f"{slug}#{n}"}
        if who not in assignees and not dry_run:
            err = _edit(slug, n, who)
            out["assignee_added" if not err else "error"] = who if not err else err
        return (1 if "error" in out else 0), out
    files = [f.get("path") for f in pr.get("files") or [] if f.get("path")]
    login, rows = choose(slug, author, p, files=files, unit=unit, ref=f"{slug}#{n}")
    if not login:
        return 3, {"error": f"no pool member of the {r['repo']} rule can approve {slug}#{n} under the load cap",
                   "candidates": rows}
    slack = slack_of(login, p)
    reason = next(x["reason"] for x in rows if x["login"] == login)
    out = {"reviewer": login, "assignee": login, "ref": f"{slug}#{n}", "reason": reason, "candidates": rows}
    if dry_run:
        return 0, out | {"dry_run": True}
    err = _edit(slug, n, login)
    if err:
        return 1, out | {"error": f"gh pr edit failed: {err}"}
    if unit and unit in cl.load()["units"]:  # the wait a lead would otherwise type by hand (and a guard may refuse)
        ref = f"{slug}#{n}"
        cl.append("review.pick", unit=unit, ref=ref, reviewer=login, reason=reason)
        cl.append("wait.add", id=unit, wid=f"pr-review:{ref}", kind="pr-review", ref=ref, until=None,
                  note=f"review by {login}", fingerprint=None)
        out["wait"] = f"pr-review:{ref}"
    if not slack:
        return 0, out | {"note": f"no Slack id for {login} in prefs review.people: Slack ask not filed"}
    import outbox
    status, item = outbox.add(unit, r.get("channel") or p["review"]["channel"], None, "review-ask", ask_text(slack, pr),
                              prs=[f"{slug}#{n}"], to=[slack])
    return 0, out | {"outbox": item["oid"], "outbox_status": status}


def cmd_review(a):
    if a.sub == "assign":
        rc, out = assign(a.ref, a.unit, dry_run=a.dry_run)
    elif a.sub == "load":
        logins = a.login or pool_logins()
        if a.refresh:
            refresh_others(logins, force=True)
        rows = [r | {"text": load_text(r)} for r in load_rows(logins=a.login or None)]
        rc, out = 0, {"definition": "mine = open PRs foundry tracks for the owner requesting this person directly; "
                                    "others = their other direct requests in tracked repos, bots excluded",
                      "reviewers": rows}
    else:
        m = cl.norm_pr(a.ref)
        if not m:
            print(f"{a.ref!r} is not owner/repo#n", file=sys.stderr)
            return 2
        slug = m[0].split("#")[0]
        login, rows = choose(slug, a.author)
        rc, out = (0 if login else 3), {"reviewer": login, "candidates": rows}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return rc


def add_cli(sp):
    s = sp.add_parser("review", help="pick a reviewer from prefs review and file the Slack ask").add_subparsers(
        dest="sub", required=True)
    a = s.add_parser("assign")
    a.add_argument("ref", help="owner/repo#n")
    a.add_argument("--unit", required=True)
    a.add_argument("--dry-run", action="store_true")
    ld = s.add_parser("load", help="reviewer load: 내 의뢰 (mine) · 참고 (others); the number the foreman quotes")
    ld.add_argument("--login", action="append")
    ld.add_argument("--refresh", action="store_true")
    c = s.add_parser("pick")
    c.add_argument("ref")
    c.add_argument("--author")
    for x in s.choices.values():
        x.set_defaults(func=cmd_review)


# ---------- the reviewer == assignee rule, at the tool call ----------

def _flag_values(argv, *flags):
    vals = []
    for i, tok in enumerate(argv):
        for f in flags:
            if tok == f and i + 1 < len(argv):
                vals += argv[i + 1].split(",")
            elif tok.startswith(f + "="):
                vals += tok.split("=", 1)[1].split(",")
    return [v.strip() for v in vals if v.strip()]


def assignee_gap(cmd):
    """The reviewers a `gh pr create|edit` names without also naming them assignee; [] when none."""
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return []
    for i in range(len(argv) - 2):
        if argv[i] == "gh" and argv[i + 1] == "pr" and argv[i + 2] in ("create", "edit"):
            sub = argv[i:]
            revs = _flag_values(sub, "--reviewer", "-r", "--add-reviewer")
            who = set(_flag_values(sub, "--assignee", "-a", "--add-assignee"))
            return [x for x in revs if x not in who and "/" not in x and x != "@me"]
    return []


def pre_tool_use(inp, snap, ctx):
    if ctx.get("tool") != "Bash":
        return None
    import roles
    if ctx.get("caller") == roles.OUTSIDE:
        return None
    gap = assignee_gap((ctx.get("input") or {}).get("command") or "")
    if gap:
        return (f"Reviewer and assignee are the same person: add `--add-assignee {','.join(gap)}` "
                f"(or `--assignee` on create). `foundry review assign <owner/repo#n> --unit <id>` picks the reviewer "
                "from prefs review and does both.")
    return None


# ---------- the github stream: open PRs with nobody asked ----------

def reviewless_lines(snap, prs, said, p=None):
    """[(key, line)] for each unit PR that is open, ready, has no reviewer and a review rule."""
    out = []
    for ref, (uid, pr) in prs.items():
        if pr.get("state") != "OPEN" or pr.get("isDraft") or "reviewRequests" not in pr:
            continue
        if pr.get("reviewRequests") or pr.get("latestReviews") or not rule(ref.split("#")[0], p):
            continue
        key = f"{ref}@{(pr.get('headRefOid') or '')[:7]}"
        if key in said:
            continue
        out.append((key, f"review {ref} ({uid}): ready with no reviewer; run `foundry review assign {ref} --unit {uid}` "
                         "now (prefs review picks by load and approve rights; never ask the owner who reviews)"))
    return out
