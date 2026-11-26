"""Reports the owner reads or approves: the daily report (공장 일일 가동현황), release gaps and review gaps.

`foundry report daily` gathers the day from the journal, GitHub (`gh search prs` across every org the token sees,
narrowed only by prefs `report.exclude`) and the owner's own Slack messages the dispatcher collected
(`report slack-add`), and renders it as Markdown or as Slack messages. `--draft` lints the Slack form, records it
and opens a decision card; nothing is sent until the owner approves the card, and then only through the outbox
(the dispatcher sends). `report review-gaps --remind --draft` does the same for one review reminder per reviewer.
A source that could not be read is named on a 읽지 못한 곳 line, never silently skipped.
"""

import datetime as dt
import fnmatch
import getpass
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import graph  # noqa: E402
import i18n  # noqa: E402

STATE = {"reports": {}, "report_slack": {}}
KEEP_DAYS = 14
# The report's words come from the `report` catalog in prefs ui.language (0.1.94).
SECTION_KEYS = ("lines", "done", "ops", "release_gaps", "review_gaps", "tonight", "tomorrow", "owner")


def T(key, **kw):
    return i18n.t("report." + key, **kw)


def section_label(k):
    return T(f"sec_{k}")


def gap_label(kind):
    return T(f"gap_{kind}")


SEARCH_FIELDS = "repository,number,title,state,url,isDraft,updatedAt,closedAt,createdAt,author"
GH_QUERIES = (("authored", ("--author", "@me", "--updated")), ("merged", ("--author", "@me", "--merged-at")),
              ("reviewed", ("--reviewed-by", "@me", "--updated")), ("involved", ("--involves", "@me", "--updated")))
ROLE_ORDER = ("merged", "authored", "reviewed", "involved", "requested")
# Review requests waiting on the owner (open PRs, any date): a count, with links when there are few.
REQUESTED = ("--review-requested", "@me", "--state", "open")
REQUEST_LINKS_MAX = 5


def other_group():
    return T("group_other")


PROD_ENVS = ("prod", "production", "prd")
DEV_ENVS = ("dev", "development", "develop")
REVIEW_GQL = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) {
    url title isDraft createdAt
    reviewRequests(first: 20) { nodes { requestedReviewer { __typename ... on User { login } ... on Team { slug } } } }
    reviews(last: 50) { nodes { author { __typename login } state submittedAt } }
    timelineItems(last: 50, itemTypes: [REVIEW_REQUESTED_EVENT]) { nodes { ... on ReviewRequestedEvent {
      createdAt requestedReviewer { __typename ... on User { login } } } } }
    commits(last: 1) { nodes { commit { oid committedDate } } }
    reviewThreads(first: 100) { nodes { isResolved comments(first: 1) { nodes { author { __typename login } } } } }
  } } }"""
MAX_REVIEW_PRS = 40
MAX_UNTRACKED_RELEASE = 30
SECTION_CAP = 15


# ---------- reducers ----------

def _op_draft(s, r, ts):
    for x in s["reports"].values():  # one live draft per kind and day: a newer one supersedes the pending one
        if x["kind"] == r.get("kind") and x["date"] == r.get("date") and x["state"] == "pending" and x["rid"] != r["rid"]:
            x["state"] = "superseded"
    s["reports"][r["rid"]] = {k: r.get(k) for k in ("rid", "kind", "date", "title", "messages", "channel", "thread",
                                                    "model", "card", "items", "lint")} | {"state": "pending", "ts": ts,
                                                                                  "decided": None, "by": None}
    cut = sorted(s["reports"].values(), key=lambda x: x["ts"])
    for x in cut[:max(0, len(cut) - 60)]:
        s["reports"].pop(x["rid"], None)


def _op_decided(s, r, ts):
    x = s["reports"].get(r.get("rid"))
    if x and x["state"] == "pending":
        x.update({"state": r.get("state"), "decided": ts, "by": r.get("by"), "quote": r.get("quote"),
                  "send_at": r.get("send_at")})


def _op_slack(s, r, ts):
    d = s["report_slack"].setdefault(r["date"], {"items": [], "gaps": [], "searched": None})
    if r.get("permalink") and all(i["permalink"] != r["permalink"] for i in d["items"]):
        d["items"].append({k: r.get(k) for k in ("channel", "permalink", "summary", "thread_ts")})
    if r.get("gap"):
        d["gaps"].append(r["gap"])
    if r.get("searched") is not None:
        d["searched"] = {"count": r["searched"], "ts": ts}
    for k in sorted(s["report_slack"])[:-KEEP_DAYS]:
        s["report_slack"].pop(k, None)


OPS = {"report.draft": _op_draft, "report.decided": _op_decided, "report.slack": _op_slack}


# ---------- helpers ----------

def _prefs():
    import prefs
    return prefs.load()


def _tz(p):
    import prefs
    return prefs.tz(p)


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def _fail(msg):
    print(msg, file=sys.stderr)
    return 2


# The report's day runs 05:00 to 05:00 local: a report run at 00:30 is still about the working day that ended.
DAY_START_H = 5


def _day(t, zone):
    return (t.astimezone(zone) - dt.timedelta(hours=DAY_START_H)).date().isoformat()


def today(p=None, t=None):
    p = p or _prefs()
    return _day(t or cl.now(), _tz(p))


def _on(ts, date, zone):
    t = cl.parse_ts(ts)
    return bool(t) and _day(t, zone) == date


def user_name(p):
    u = ((p.get("report") or {}).get("user") or "").strip()
    return u or re.split(r"[-._ ]", getpass.getuser())[0]


def title_of(p, date):
    tpl = (p.get("report") or {}).get("title") or T("title_default")
    return tpl.replace("{user}", user_name(p)).replace("{date}", date)


def channel_of(p):
    rp = p.get("report") or {}
    return rp.get("channel") or (p.get("review") or {}).get("channel") or ""


def excluded_repo(repo, p):
    globs = ((p.get("report") or {}).get("exclude") or {}).get("repos") or []
    return any(fnmatch.fnmatch(str(repo).lower(), g.lower()) for g in globs)


def excluded_channel(channel, p):
    globs = ((p.get("report") or {}).get("exclude") or {}).get("channels") or []
    c = str(channel or "").lower().lstrip("#")
    return any(fnmatch.fnmatch(c, g.lower().lstrip("#")) for g in globs)


DEFAULT_FOOTER = "🤖 Claude Code が作成"


def footer(p=None):
    """prefs slack.footer, else the default: the Slack hook appends it to every top-level message (0.1.87)."""
    f = ((p if p is not None else _prefs()).get("slack") or {}).get("footer")
    return f.strip() if isinstance(f, str) and f.strip() else DEFAULT_FOOTER


def link(url, label, fmt):
    if not url:
        return label
    return f"<{url}|{label}>" if fmt == "slack" else f"[{label}]({url})"


def _pr_url(repo, num, url=None):
    return url or (f"https://github.com/{repo}/pull/{num}" if repo and "/" in str(repo) else None)


def _age_h(t, now):
    return int((now - t).total_seconds() // 3600) if t else None


# ---------- collection ----------

def gh_activity(date, p):
    """({ref: row with roles}, gaps): every PR the owner authored, merged, reviewed or was involved in since `date`,
    across every org the gh token sees, minus prefs report.exclude.repos."""
    import factory
    out, gaps = {}, []
    for role, flags in GH_QUERIES:
        rows = factory.gh("search", "prs", *flags, f">={date}", "--json", SEARCH_FIELDS, "--limit", "100")
        if not isinstance(rows, list):
            gaps.append(f"GitHub ({role})")
            continue
        for row in rows:
            repo = (row.get("repository") or {}).get("nameWithOwner") or (row.get("repository") or {}).get("name")
            if not repo or excluded_repo(repo, p):
                continue
            ref = f"{repo}#{row.get('number')}"
            x = out.setdefault(ref, {"ref": ref, "repo": repo, "number": row.get("number"), "title": row.get("title"),
                                     "url": row.get("url"), "state": row.get("state"), "isDraft": row.get("isDraft"),
                                     "bot": is_bot(row.get("author"), p), "roles": []})
            if role not in x["roles"]:
                x["roles"].append(role)
    import intake
    rq = intake.review_requests(p, SEARCH_FIELDS + ",labels")
    if rq["unread"]:
        gaps.append("GitHub (requested)")
    for row in rq["team"]:
        repo = (row.get("repository") or {}).get("nameWithOwner")
        if repo and not excluded_repo(repo, p):
            ref = f"{repo}#{row.get('number')}"
            x = out.setdefault(ref, {"ref": ref, "repo": repo, "number": row.get("number"), "title": row.get("title"),
                                     "url": row.get("url"), "state": row.get("state"), "isDraft": row.get("isDraft"),
                                     "bot": is_bot(row.get("author"), p), "roles": []})
            x["team_request"] = True
    for row in rq["mine"]:
        repo = (row.get("repository") or {}).get("nameWithOwner")
        if repo and not excluded_repo(repo, p):
            ref = f"{repo}#{row.get('number')}"
            x = out.setdefault(ref, {"ref": ref, "repo": repo, "number": row.get("number"), "title": row.get("title"),
                                     "url": row.get("url"), "state": row.get("state"), "isDraft": row.get("isDraft"),
                                     "bot": is_bot(row.get("author"), p), "roles": []})
            if "requested" not in x["roles"]:
                x["roles"].append("requested")
    return out, gaps


def is_bot(author, p):
    """A PR nobody wrote by hand: a bot account, or a prefs merge.bump_pr_authors one (the version bump bot)."""
    a = author or {}
    login = str(a.get("login") or "")
    bump = (p.get("merge") or {}).get("bump_pr_authors") or []
    return a.get("type") == "Bot" or bool(a.get("is_bot")) or login.endswith("[bot]") or login in bump


def journal_merges(snap, date, zone, p):
    """{ref: row} PRs the journal saw merged on `date` (so a gh outage still shows them)."""
    import factory
    out = {}
    for ref, pr in factory.collect_prs(snap).items():
        if pr.get("merged") and _on(pr["merged"], date, zone) and not excluded_repo(pr.get("repo"), p):
            out[ref] = {"ref": ref, "repo": pr.get("repo"), "number": pr.get("number"), "title": pr.get("title"),
                        "url": pr.get("url"), "state": "merged", "roles": ["merged"], "unit": pr.get("unit")}
    return out


def _deploys(journal):
    return [r for r in journal if r.get("op") == "deploy.done"]


def _tag_covered(tag, tags):
    """A deploy of `tag`, or of a later tag with the same prefix, covers it."""
    import watchers
    pre = re.sub(r"\d.*$", "", str(tag))
    return any(t == tag or (re.sub(r"\d.*$", "", str(t)) == pre and watchers._natural(str(t)) > watchers._natural(str(tag)))
               for t in tags if t)


def release_gaps(snap, journal, p=None, now=None, untracked=True):
    """Merged PRs no release tag holds yet, and released PRs that reached dev but not prod, with their age."""
    import factory
    import release
    p = p or _prefs()
    now = now or cl.now()
    cut = now - dt.timedelta(days=(p.get("release") or {}).get("watch_days", 14))
    deploys = _deploys(journal)
    env = lambda r: str(r.get("env") or "").lower()  # noqa: E731
    prod_tags = [r.get("tag") for r in deploys if env(r) in PROD_ENVS]
    dev = [r for r in deploys if env(r) in DEV_ENVS]
    covered = {ref: tag for u in snap["units"].values() for ref, tag in
               (((u.get("release") or {}).get("released")) or {}).items()}
    rows, seen = [], set()
    for ref, pr in factory.collect_prs(snap).items():
        t = cl.parse_ts(pr.get("merged"))
        repo = pr.get("repo")
        if not t or t < cut or str(pr.get("state") or "merged").upper() not in ("MERGED", "") or excluded_repo(repo, p):
            continue
        seen.add(ref)
        state = factory.pr_release_state(dict(pr, state="MERGED"), p, None, snap, now)
        if state not in ("release-wait", "released"):
            continue
        tag = pr.get("released") if isinstance(pr.get("released"), str) and not cl.parse_ts(pr["released"]) else None
        tag = tag or covered.get(ref)
        base = {"ref": ref, "repo": repo, "unit": pr.get("unit"), "title": pr.get("title"),
                "url": _pr_url(repo, pr.get("number"), pr.get("url")), "merged": pr.get("merged")}
        if state == "release-wait":
            rows.append(base | {"gap": "unreleased", "age_h": _age_h(t, now),
                                "batch": release.batch_entry(repo, p) is not None})
            continue
        if not tag or release.batch_entry(repo, p) is None or _tag_covered(tag, prod_tags):
            continue
        devs = [r for r in dev if _tag_covered(tag, [r.get("tag")]) or r.get("tag") == tag]
        if devs:
            since = min(cl.parse_ts(r.get("ts")) or now for r in devs)
            rows.append(base | {"gap": "dev_only", "tag": tag, "age_h": _age_h(since, now), "batch": True})
    if untracked:
        rows += _untracked_release_gaps(seen, p, now, cut)
    return sorted(rows, key=lambda r: -(r.get("age_h") or 0))


def _untracked_release_gaps(seen, p, now, cut):
    """Merged PRs of the owner's that no unit tracks (any org), checked against their repo's tags. A repo with no
    tags at all has no release step and is skipped."""
    import factory
    import release
    rows = factory.gh("search", "prs", "--author", "@me", "--merged-at", f">={cut.date().isoformat()}", "--json",
                      SEARCH_FIELDS, "--limit", "100")
    out, notags = [], set()
    for row in (rows if isinstance(rows, list) else [])[:MAX_UNTRACKED_RELEASE]:
        repo = (row.get("repository") or {}).get("nameWithOwner")
        ref = f"{repo}#{row.get('number')}"
        if not repo or ref in seen or repo in notags or excluded_repo(repo, p):
            continue
        pattern = factory.release_tag(repo, p)
        if pattern is None:
            continue
        if not factory.gh("api", f"repos/{repo}/tags?per_page=1"):
            notags.add(repo)
            continue
        if release.tag_containing(repo, row.get("number"), pattern):
            continue
        t = cl.parse_ts(row.get("closedAt")) or cl.parse_ts(row.get("updatedAt"))
        out.append({"ref": ref, "repo": repo, "unit": None, "title": row.get("title"), "url": row.get("url"),
                    "merged": row.get("closedAt"), "gap": "unreleased", "age_h": _age_h(t, now),
                    "batch": release.batch_entry(repo, p) is not None})
    return out


def _human(author):
    return bool(author) and author.get("__typename") != "Bot" and not str(author.get("login") or "").endswith("[bot]")


def review_gaps(p=None, now=None):
    """({reviewer: [gap]}, gaps_unread): the owner's open PRs across orgs, per reviewer: no human reviewer, a request
    unanswered past report.review_wait_hours, an approval a newer push left stale or a dismissed one, and unresolved
    bot threads (filed under the owner, who answers those)."""
    import factory
    p = p or _prefs()
    now = now or cl.now()
    wait = float((p.get("report") or {}).get("review_wait_hours", 24))
    me = (factory.gh("api", "user") or {}).get("login")
    rows = factory.gh("search", "prs", "--author", "@me", "--state", "open", "--json", SEARCH_FIELDS, "--limit", "100")
    if not isinstance(rows, list):
        return {}, ["GitHub (open PRs)"]
    groups, unread = {}, []

    def add(who, gap):
        groups.setdefault(who, []).append(gap)

    for row in [r for r in rows if not r.get("isDraft")][:MAX_REVIEW_PRS]:
        repo = (row.get("repository") or {}).get("nameWithOwner")
        if not repo or excluded_repo(repo, p):
            continue
        owner, name = repo.split("/", 1)
        num = row.get("number")
        ref = f"{repo}#{num}"
        res = factory.gh("api", "graphql", "-f", f"query={REVIEW_GQL}", "-F", f"owner={owner}", "-F", f"name={name}",
                         "-F", f"number={num}")
        pr = (((res or {}).get("data") or {}).get("repository") or {}).get("pullRequest")
        if not pr:
            unread.append(f"GitHub ({ref})")
            continue
        base = {"ref": ref, "url": pr.get("url") or row.get("url"), "title": pr.get("title") or row.get("title")}
        commit = (((pr.get("commits") or {}).get("nodes") or [{}])[-1] or {}).get("commit") or {}
        head, pushed = commit.get("oid"), cl.parse_ts(commit.get("committedDate"))
        base["head"] = head
        reviews = [r for r in (pr.get("reviews") or {}).get("nodes") or [] if _human(r.get("author"))
                   and (r["author"].get("login") != me)]
        requested = [(n.get("requestedReviewer") or {}) for n in (pr.get("reviewRequests") or {}).get("nodes") or []]
        users = [r.get("login") for r in requested if r.get("__typename") == "User" and r.get("login")
                 and not str(r["login"]).endswith("[bot]")]
        teams = [r.get("slug") for r in requested if r.get("__typename") == "Team"]
        if not users and not teams and not reviews:
            add(T("who_unassigned"), base | {"kind": "no_reviewer", "age_h": _age_h(cl.parse_ts(pr.get("createdAt")), now)})
        asked = {}
        for ev in (pr.get("timelineItems") or {}).get("nodes") or []:
            who = (ev.get("requestedReviewer") or {}).get("login")
            if who:
                asked[who] = max(asked.get(who) or "", ev.get("createdAt") or "")
        for who in users:
            since = cl.parse_ts(asked.get(who)) or cl.parse_ts(pr.get("createdAt"))
            later = [r for r in reviews if r["author"].get("login") == who
                     and (cl.parse_ts(r.get("submittedAt")) or now) >= (since or now)]
            if since and not later and (now - since).total_seconds() >= wait * 3600:
                add(who, base | {"kind": "stale_request", "age_h": _age_h(since, now)})
        last = {}
        for r in reviews:
            last[r["author"]["login"]] = r
        for who, r in last.items():
            t = cl.parse_ts(r.get("submittedAt"))
            if r.get("state") == "DISMISSED" or (r.get("state") == "APPROVED" and pushed and t and pushed > t):
                if who not in users:
                    add(who, base | {"kind": "dismissed", "age_h": _age_h(pushed or t, now)})
        bots = sum(1 for th in (pr.get("reviewThreads") or {}).get("nodes") or [] if not th.get("isResolved")
                   and not _human((((th.get("comments") or {}).get("nodes") or [{}])[0] or {}).get("author")))
        if bots:
            add(T("who_owner"), base | {"kind": "bot_unresolved", "count": bots, "age_h": None})
    return groups, unread


# ---------- the daily report ----------

def _cron_fires(expr, t):
    def hit(field, v):
        for part in field.split(","):
            if part == "*":
                return True
            a, _, b = part.partition("-")
            if a.isdigit() and (int(a) <= v <= int(b) if b.isdigit() else int(a) == v):
                return True
        return False
    f = expr.split()
    if len(f) != 5:
        return False
    return (hit(f[0], t.minute) and hit(f[1], t.hour) and hit(f[2], t.day) and hit(f[3], t.month)
            and hit(f[4], (t.weekday() + 1) % 7))


def tonight(snap, p, now):
    """Schedules firing from now until the night window ends, then queued units."""
    import prefs
    end = prefs.window_end(now, p["night_window"], _tz(p))
    out, t = [], now.astimezone(_tz(p)).replace(second=0, microsecond=0)
    names = []
    while t <= end:
        for s in p.get("schedules") or []:
            if _cron_fires(s.get("cron") or "", t) and s["name"] not in names:
                names.append(s["name"])
                out.append(f"{t.strftime('%H:%M')} {s['name']}")
        t += dt.timedelta(minutes=1)
    out += [f"queue {u['id']}: {u.get('title') or ''}".rstrip() for u in snap["units"].values()
            if u.get("status") == "queue" and u.get("kind") != "foreman"]
    return out


def keyer(refs):
    """A key function: one PR per owner/repo#n (lowercased); a bare repo#n is that PR only when exactly one known
    owner/repo#n has the same name and number, so two orgs' x#12 stay two PRs."""
    bare = {}
    for r in refs:
        repo, _, num = str(r).rpartition("#")
        if "/" in repo:
            bare.setdefault(f"{repo.rsplit('/', 1)[-1].lower()}#{num}", set()).add(str(r).lower())

    def key(ref):
        ref = str(ref).lower()
        if "/" in ref.rpartition("#")[0]:
            return ref
        hit = bare.get(ref) or set()
        return next(iter(hit)) if len(hit) == 1 else ref
    return key


def _tag_of(pr):
    t = pr.get("released")
    return t if isinstance(t, str) and t and not cl.parse_ts(t) else None


def pr_state(pr, prod_tags, dev_tags):
    """The PR's latest state, in one word or two: prod 배포 / dev 배포 / 릴리스 / 머지됨 / 리뷰 중 / 초안 / 닫힘."""
    tag = _tag_of(pr)
    if tag and _tag_covered(tag, prod_tags):
        return T("state_prod", tag=tag)
    if tag and _tag_covered(tag, dev_tags):
        return T("state_dev", tag=tag)
    if tag or pr.get("released"):
        return T("state_released_tag", tag=tag) if tag else T("state_released")
    st = str(pr.get("state") or "").lower()
    if pr.get("merged") or st == "merged" or "merged" in (pr.get("roles") or []):
        return T("state_merged")
    if st == "closed":
        return T("state_closed")
    return T("state_draft") if pr.get("isDraft") else T("state_review")


def group_of(units, uid, repo=None):
    """(key, label) of the project group an item belongs to: the unit's epic or project, else its first ticket,
    else the unit itself, else the repo, else その他. Units carry no epic today, so the ticket usually decides."""
    u = units.get(uid) or {}
    if u.get("release"):  # a release unit belongs to its repo's group, not a group of its own
        r = (u.get("release") or {}).get("repo") or u.get("repo") or repo
        return (r, r) if r else (other_group(), other_group())
    if u:
        for f in ("epic", "project"):
            if u.get(f):
                return str(u[f]), str(u[f])
        tracker = u.get("tracker") if u.get("tracker") not in (None, "none") else None
        t = ((u.get("keys") or {}).get("ticket") or [None])[0] or tracker
        if t:  # named by the ticket's first unit, whichever unit the item came from
            first = min((x for x in units.values() if t in ((x.get("keys") or {}).get("ticket") or [])
                         or x.get("tracker") == t), key=lambda x: (str(x.get("created") or ""), x["id"]), default=u)
            title = first.get("title") if first.get("title") and first.get("title") != first.get("id") else None
            title = re.sub(rf"^{re.escape(t)}\s*[:：·-]?\s*", "", title or "") or None  # 「PROJ-1: x」 says PROJ-1 once
            return t, gist(f"{t} · {title}" if title else t, 60)
        title = u.get("title") if u.get("title") and u.get("title") != uid else None
        return uid, gist(f"{uid} · {title}" if title else uid, 60)
    return (repo, repo) if repo else (other_group(), other_group())


OWNER_KIND = {"card": "decision", "ask": "decision", "approval": "approval", "owner_todo": "todo",
              "waiting_owner": "check"}
OWNER_ORDER = ("decision", "approval", "todo", "check")
DECISION_KINDS = ("card", "ask")  # 결정 필요; everything else waiting on the owner is 오너 대기
# A line naming a secret-like thing can be masked on its way out (a guard prints [REDACTED]): the card holds it.
SECRET_WORDS = r"credential|token|secret|password|passwd|api[_ -]?key|자격 ?증명|비밀"
SECRETISH_RE = re.compile(SECRET_WORDS, re.I)
LOCAL_PATH_RE = re.compile(r"(?:/Users/|/home/|~/)\S*")


def board_only(o):
    """An owner item that stays on the board and out of Slack: it names a local path (trust:<path> and the like)."""
    return bool(str(o.get("id") or "").startswith("trust:") or LOCAL_PATH_RE.search(str(o.get("text") or "")))


# A to-do whose own words are all secret: say what kind of thing it is instead.
TODO_SECRET_WORDS = {'secret': '시크릿 설정', 'credential': '자격 증명'}  # the card holds the rest


def todo_kind_word(kind):
    if kind in TODO_SECRET_WORDS:
        return TODO_SECRET_WORDS[kind]
    return T(f"todo_{kind}") if kind in ("go", "info", "other") else ""


APPROVAL_RES = (
    (re.compile(r"\bgh pr merge (\d+)\b.*?(?:-R|--repo) (\S+)|\bgh pr merge\b.*?(?:-R|--repo) (\S+).*?\b(\d+)\b"), "merge"),
    (re.compile(r"\bgh pr review (\d+)\b.*?(?:-R|--repo) (\S+)"), "approve"),
)


def approval_what(text):
    """A tier-3 request as `<repo>#<n> 머지`, `argocd <app> sync`, ...; None when it is not one foundry can read."""
    t = str(text or "")
    for rx, verb in APPROVAL_RES:
        m = rx.search(t)
        if m:
            g = [x for x in m.groups() if x]
            n, repo = (g[0], g[1]) if g[0].isdigit() else (g[1], g[0])
            return f"{repo.rsplit('/', 1)[-1]}#{n} {T('verb_' + verb)}"
    m = re.search(r"\bargocd app (sync|rollback|delete) (\S+)", t)
    if m:
        return f"argocd {m.group(2)} {m.group(1)}"
    m = re.search(r"\b(kubectl|helm|terraform) (\S+)", t)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return None


def safe_subject(o, first):
    """The line's own words with the secret part replaced; failing that the unit's title or the to-do's kind word.
    Always ends `(카드 참조)`: the card has the rest."""
    s = re.sub(r"\[REDACTED\]\S*", "…", first)
    s = re.sub(rf"(?i)((?:{SECRET_WORDS})\s*[:=]).*", r"\1 …", s).strip()
    if len(re.sub(r"[\s…:=]|" + SECRET_WORDS, "", s, flags=re.I)) < 4:
        title = o.get("title")
        if title and o.get("where") and title.startswith(o["where"]):
            title = None  # the line already names the unit by its title
        s = title or todo_kind_word(o.get("todo_kind") or "") or T("todo_other")
    return f"{gist(s, 48)} {T('see_card')}"


def where_label(u, uid):
    """What an owner line names its unit by: the ticket, `<repo> release` for a release unit, else the first words
    of the unit's title (an id cut in the middle reads as noise), else the id."""
    ticket = ((u.get("keys") or {}).get("ticket") or [None])[0]
    if ticket:
        return ticket
    rel = (u.get("release") or {}).get("repo") or (u.get("repo") if str(uid or "").startswith("release-") else None)
    if rel:
        return f"{str(rel).rsplit('/', 1)[-1]} release"
    title = u.get("title") if u.get("title") and u.get("title") != u.get("id") else None
    if title:
        out = ""
        for w in title.split():
            if len(f"{out} {w}".strip()) > 24:
                break
            out = f"{out} {w}".strip()
        return out.rstrip(":：·-–— ") or gist(title, 24)
    return uid


def owner_line(o, fmt):
    """`<kind> · <ticket or unit> · <first line of the question, ≤60> · <links> (<id>)`: one line, never a card body.
    An approval reads as what it does (`webapp#1647 머지`); a secret-looking line keeps its unit and subject."""
    import views
    first = next((x["text"] for x in views.outline(o.get("text"))), "") or str(o.get("text") or "")
    first = re.split(r"\s+[-•]\s+", re.sub(r"^\s*[-*•]\s*", "", first))[0]  # a flattened card: its first line only
    what = approval_what(o.get("text")) if o.get("kind") == "approval" else None
    if what:
        first = what
    elif o.get("todo_title"):
        first = o["todo_title"]
        if "[REDACTED]" in first or SECRETISH_RE.search(first):
            first = safe_subject(o, first)
    elif "[REDACTED]" in first or SECRETISH_RE.search(first) or not first.strip():
        first = safe_subject(o, first)
    parts = [T("kind_" + OWNER_KIND.get(o.get("kind"), "check"))] + ([gist(o["where"], 24)] if o.get("where") else []) \
        + [gist(first, 60)]
    links = " ".join(link(u, r, fmt) for u, r in o.get("pr_links") or [])
    return " · ".join(parts + ([links] if links else [])) + f" ({o['id']})"


def owner_now(snap):
    """오너 대기 at render time: open items only, one per id and text, never a report's own draft card. Each carries
    the PRs it hinges on (its refs, card-key PRs, refs in its text), which the line names: they show nowhere else."""
    import decide
    cards, decs = snap.get("cards") or {}, snap.get("decisions") or {}
    out, seen = [], set()
    for o in decide.owner_items(snap):
        c = cards.get(o["id"])
        if str((c or {}).get("key") or "").startswith("report:"):
            continue
        text = " ".join(str(o.get("text") or "").split())
        if o["id"] in seen or (o.get("unit"), text) in seen:
            continue
        seen |= {o["id"], (o.get("unit"), text)}
        refs = decide.card_refs(c) if c else list((decs.get(o["id"]) or {}).get("refs") or [])
        prs = list(dict.fromkeys([r[3:] for r in refs if r.startswith("pr:")] + cl.norm_pr(text)))
        out.append({**o, "text": text, "prs": prs})
    return out


def gather(snap, journal, p, date, now=None, gh_rows=None):
    """The day as one parts structure: counts and 오너 대기 for the parent message, one group per project for the
    thread, release and review gaps, the rest as counts. Every PR is placed exactly once, by priority
    오너 대기 > 릴리스 누락 > 리뷰 누락 > its project group."""
    import factory
    now = now or cl.now()
    zone = _tz(p)
    gaps = []
    units = snap["units"]
    opened = [u for u in units.values() if u.get("kind") != "foreman" and u.get("status") not in graph.CLOSED]
    live = {u["id"] for u in opened if cl.lead_live(snap, u)}  # launching, working, or idle and resumable
    # 가동 라인 is now, not the day: every unit a lead is working (in progress, or with a live session).
    lines_ = [u for u in opened if (u["id"] in live or u.get("status") == "in_progress") and u.get("status") != "waiting_owner"]
    tomorrow = [u for u in opened if u["id"] not in live and u.get("status") not in ("queue", "waiting_owner")]
    if gh_rows is None:
        gh_rows, gh_gaps = gh_activity(date, p)
        gaps += gh_gaps
    deploys = _deploys(journal)
    env = lambda r: str(r.get("env") or "").lower()  # noqa: E731
    prod_tags = [r.get("tag") for r in deploys if env(r) in PROD_ENVS]
    dev_tags = [r.get("tag") for r in deploys if env(r) in DEV_ENVS]
    tracked_all = factory.collect_prs(snap)
    merges = journal_merges(snap, date, zone, p)
    owner = [o for o in owner_now(snap) if board_only(o) is False]
    for o in owner:
        u = units.get(o.get("unit")) or {}
        o["title"] = u.get("title") if u.get("title") and u.get("title") != u.get("id") else None
        o["where"] = where_label(u, o.get("unit"))
    _key = keyer(list(gh_rows) + list(tracked_all) + list(merges) + [r for o in owner for r in o["prs"]])
    tracked = {_key(ref): (ref, pr) for ref, pr in tracked_all.items()}
    maker = {}
    for uid, u in units.items():
        if u.get("release") or u.get("kind") == "foreman":
            continue
        refs = [f"{x.get('repo')}#{x.get('number')}" for x in u.get("prs") or [] if isinstance(x, dict)]
        for ref in refs + list((u.get("keys") or {}).get("pr") or []):
            maker.setdefault(_key(ref), uid)

    prs = {}

    def put(ref, row):
        k = _key(ref)
        cur = prs.setdefault(k, {"ref": ref, "roles": []})
        if "/" in ref and "/" not in cur["ref"]:
            cur["ref"] = ref
        for f in ("repo", "number", "title", "url", "state", "unit", "bot", "merged", "released", "isDraft",
                  "team_request"):
            if row.get(f) is not None and cur.get(f) in (None, False, ""):
                cur[f] = row[f]
        cur["roles"] = sorted(set(cur["roles"]) | set(row.get("roles") or []), key=ROLE_ORDER.index)

    for ref, row in gh_rows.items():  # GitHub's titles and states first, the journal fills the rest
        put(ref, row)
    for ref, row in merges.items():
        put(ref, row)
    released_today, ops = set(), []
    for r in journal:
        if not _on(r.get("ts"), date, zone):
            continue
        op = r.get("op")
        if op == "pr.released" and r.get("repo") and r.get("number") is not None:
            ref = f"{r['repo']}#{r['number']}"
            if not excluded_repo(r["repo"], p):
                put(ref, {"repo": r["repo"], "number": r["number"], "released": r.get("tag"), "unit": r.get("unit"),
                          "roles": ["merged"]})
            released_today.add((r["repo"], r.get("tag")))
        elif op in ("tag.pushed", "release.tag"):
            released_today.add((r.get("repo"), r.get("tag")))
            ops.append((r.get("unit"), r.get("repo"), " ".join(T("op_tag", repo=r.get("repo") or "",
                                                                       tag=r.get("tag")).split())))
        elif op == "deploy.done":
            ops.append((r.get("unit"), None, T("op_deploy", app=r.get("app"), env=r.get("env"),
                                               tag=r.get("tag") or "").rstrip()))
    done_today = {r.get("id") or r.get("unit") for r in journal if r.get("op") in ("unit.status", "unit.set")
                  and r.get("status") == "done" and _on(r.get("ts"), date, zone)}
    for k, cur in prs.items():
        ref0, t = tracked.get(k, (None, {}))
        if ref0 and "/" in ref0 and "/" not in cur["ref"]:
            cur["ref"] = ref0
        for f in ("unit", "released", "merged", "url", "title", "repo"):
            if cur.get(f) in (None, "") and t.get(f) is not None:
                cur[f] = t[f]
        if (units.get(cur.get("unit")) or {}).get("release") and k in maker:
            cur["unit"] = maker[k]
        cur["url"] = _pr_url(cur.get("repo") or cur["ref"].rpartition("#")[0], cur.get("number"), cur.get("url"))
        cur["state_text"] = pr_state(cur, prod_tags, dev_tags)

    taken = {_key(r) for o in owner for r in o["prs"]}
    for o in owner:  # the PRs an owner wait holds are named on its line, with their links
        o["pr_links"] = [((prs.get(_key(r)) or {}).get("url") or _pr_url(r.rpartition("#")[0], r.rpartition("#")[2]), r)
                         for r in o["prs"]]
    try:
        rg = [r for r in release_gaps(snap, journal, p, now, untracked=False) if _key(r["ref"]) not in taken]
    except Exception as e:  # noqa: BLE001 - a broken source is named, never fatal
        rg, gaps = [], gaps + [f"{section_label('release_gaps')} ({type(e).__name__})"]
    taken |= {_key(r["ref"]) for r in rg}
    rv, rv_gaps = review_gaps(p, now)
    gaps += rv_gaps
    rv = {who: [x for x in xs if _key(x["ref"]) not in taken] for who, xs in rv.items()}
    rv = {who: xs for who, xs in rv.items() if xs}
    taken |= {_key(x["ref"]) for xs in rv.values() for x in xs}

    groups, bots, requested, involved, team = {}, [], [], [], []

    def add(key_label, line, order):
        g = groups.setdefault(key_label[0], {"key": key_label[0], "label": key_label[1], "items": []})
        g["items"].append((order, line))

    pr_group = {}
    for k, cur in sorted(prs.items()):
        if k in taken:
            continue
        roles = set(cur.get("roles") or [])
        if cur.get("team_request") and not roles and not cur.get("unit"):
            team.append(cur)  # asked only of the owner's teams: a count, never the owner's own list
        elif cur.get("bot"):
            bots.append(cur)
        elif "requested" in roles and not roles & {"authored", "merged", "reviewed"} and not cur.get("unit"):
            requested.append(cur)
        elif roles <= {"involved", "requested"} and not cur.get("unit"):
            involved.append(cur)
        else:
            kl = group_of(units, cur.get("unit"), cur.get("repo") or cur["ref"].rpartition("#")[0])
            pr_group[k] = kl
            add(kl, ("pr", cur), 0)
    tickets = {}
    for uid in units:
        for t in (units[uid].get("keys") or {}).get("ticket") or []:
            tickets.setdefault(t, group_of(units, uid))
    sl = (snap.get("report_slack") or {}).get(date) or {}
    if not sl.get("searched") and not sl.get("items"):
        gaps.append(T("slack_no_record"))
    gaps += [f"{g.get('source')} ({g.get('why')})" for g in sl.get("gaps") or []]
    loose = {}
    for i in sl.get("items") or []:
        text = str(i.get("summary") or "")
        kl = next((pr_group[_key(r)] for r in cl.norm_pr(text) if _key(r) in pr_group), None) or next(
            (tickets[t] for t in cl.tickets(text) if t in tickets), None)
        if kl:
            add(kl, ("slack", i), 1)
        else:
            loose.setdefault(channel_name(i.get("channel")), []).append(i)
    for ch, xs in sorted(loose.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        add((other_group(), other_group()), ("slack", {**xs[0], "channel": ch, "summary": xs[0].get("summary") if len(xs) == 1 else
                                        T("slack_n", n=len(xs), summary=xs[0].get('summary'))}), 1)
    for uid, repo, line in dict.fromkeys(ops):
        add(group_of(units, uid, repo) if (uid or repo) else (other_group(), other_group()), ("op", line), 2)
    return {"date": date, "units_live": lines_, "tomorrow": tomorrow, "tonight": tonight(snap, p, now),
            "owner": owner, "release_gaps": rg, "review_gaps": rv, "groups": sorted(groups.values(), key=lambda g: (
                g["key"] == other_group(), g["label"])), "bots": bots, "requested": requested, "involved": involved, "team": team,
            "gaps": group_gaps(gaps),
            "counts": {"lines": len(lines_), "done": len(done_today - {None}),
                       "review": sum(1 for x in prs.values() if x.get("state_text") == T("state_review")
                                     and "authored" in (x.get("roles") or [])),
                       "decisions": sum(1 for o in owner if o.get("kind") in DECISION_KINDS),
                       "owner": sum(1 for o in owner if o.get("kind") not in DECISION_KINDS),
                       "released": len(released_today)}}


def group_gaps(gaps):
    """"GitHub (a)", "GitHub (b)" -> "GitHub (a · b)", in first-seen order."""
    out = {}
    for g in gaps:
        m = re.match(r"^(.*?) \((.*)\)$", g)
        src, why = (m.group(1), m.group(2)) if m else (g, None)
        xs = out.setdefault(src, [])
        if why and why not in xs:
            xs.append(why)
    return [f"{k} ({' · '.join(v)})" if v else k for k, v in out.items()]


def _capped(xs):
    return xs[:SECTION_CAP] + ([T("more_n", n=len(xs) - SECTION_CAP)] if len(xs) > SECTION_CAP else [])


def _pr_line(x, fmt):
    return f"{link(x.get('url'), x['ref'], fmt)} {x.get('title') or ''} — {x.get('state_text')}".replace("  ", " ")


def parts(g, p, fmt):
    """The parent message (title, counts, 오너 대기 as plain lines: the hook appends the footer after them) and one
    thread reply per project group, release gaps, review gaps, the rest, tonight and tomorrow."""
    keys = set((p.get("report") or {}).get("sections") or SECTION_KEYS)
    c = g["counts"]
    head = [f"**{title_of(p, g['date'])} ({g['date']})**", "",
            T("counts_lines", lines=c["lines"], done=c["done"]),
            f"{T('counts_review')} {c['review']} · {T('counts_decisions')} {c.get('decisions', 0)} · "
            f"{T('counts_owner')} {c['owner']}",
            T("counts_released", n=c["released"])]
    if "owner" in keys and g["owner"]:
        ordered = sorted(g["owner"], key=lambda o: (OWNER_ORDER.index(OWNER_KIND.get(o.get("kind"), "check")),
                                                    str(o.get("where") or ""), str(o["id"])))
        decs = [o for o in ordered if o.get("kind") in DECISION_KINDS]
        rest = [o for o in ordered if o.get("kind") not in DECISION_KINDS]
        head += (["", f"**{section_label('decisions')}**"] + [owner_line(o, fmt) for o in decs] if decs else [])
        head += (["", f"**{section_label('owner')}**"] + [owner_line(o, fmt) for o in rest] if rest else [])
    if g["gaps"]:
        head += ["", T("unread", list=", ".join(g["gaps"]))]
    replies = []
    if keys & {"done", "ops"}:
        for grp in g["groups"]:
            lines = []
            for _, (kind, x) in sorted(grp["items"], key=lambda it: (it[0], str(it[1][1].get("ref") if isinstance(
                    it[1][1], dict) else it[1][1]))):
                if kind == "pr":
                    lines.append(_pr_line(x, fmt))
                elif kind == "slack":
                    lines.append(f"{link(permalink_of(x), '#' + channel_name(x.get('channel')), fmt)} "
                                 f"{x.get('summary')}")
                else:
                    lines.append(x)
            replies.append((grp["label"], _capped(lines)))
    replies = collapse(replies, p, sum(1 for k, on in (("release_gaps", g["release_gaps"]), ("review_gaps", g["review_gaps"]),
                                                    ("tonight", g["tonight"]), ("tomorrow", g["tomorrow"]))
                                       if k in keys and on) + bool(g["bots"] or g["involved"] or g["requested"] or g.get("team")))
    if "release_gaps" in keys and g["release_gaps"]:
        replies.append((section_label("release_gaps"), _capped([
            f"{link(r.get('url'), r['ref'], fmt)} {release_gap_word(r)} ({r.get('age_h')}h)"
            for r in g["release_gaps"]])))
    if "review_gaps" in keys and g["review_gaps"]:
        per = {}  # one line per PR, naming each reviewer's gap
        for who, xs in sorted(g["review_gaps"].items()):
            for x in xs:
                extra = f" {T('count_n', n=x['count'])}" if x.get("count") else (f" ({x['age_h']}h)" if x.get("age_h") is not None else "")
                per.setdefault(x["ref"], (x, []))[1].append(f"{who} {gap_label(x['kind'])}{extra}")
        replies.append((section_label("review_gaps"), _capped(
            [f"{link(x.get('url'), ref, fmt)} {' · '.join(ws)}" for ref, (x, ws) in sorted(per.items())])))
    rest, links = [], []
    if g["bots"]:
        rest.append(T("rest_bots", n=len(g["bots"])))
    if g["involved"]:
        rest.append(T("rest_involved", n=len(g["involved"])))
    if g["requested"]:
        rest.append(T("rest_requested", n=len(g["requested"])))
        if len(g["requested"]) <= REQUEST_LINKS_MAX:
            links = [_pr_line(x, fmt) for x in g["requested"]]
    if g.get("team"):
        rest.append(T("rest_team", n=len(g["team"])))
    if rest:
        replies.append((section_label("other"), rest + ([""] if links else []) + links))
    if "tonight" in keys and g["tonight"]:
        replies.append((section_label("tonight"), _capped(g["tonight"])))
    if "tomorrow" in keys and g["tomorrow"]:
        replies.append((section_label("tomorrow"), _capped(
            [f"{u['id']}: {u.get('title') or ''} ({u.get('status')})" for u in g["tomorrow"]])))
    return head, replies


def release_gap_word(r):
    return T("dev_only", tag=r.get("tag")) if r["gap"] == "dev_only" else T("unreleased")


def collapse(groups, p, fixed):
    """Project replies within the thread cap (prefs report.max_replies, 10, counting the fixed section replies):
    a group of report.collapse_lines (2) lines or fewer, and the smallest groups past the cap, become one line each
    in a single その他の作業 reply (the label, its first line with its link, +N)."""
    rp = p.get("report") or {}
    small, cap = int(rp.get("collapse_lines", 2)), int(rp.get("max_replies", 10))
    room = max(1, cap - fixed - 1)
    big = [g for g in groups if len(g[1]) > small]
    keep = {id(g) for g in sorted(big, key=lambda g: -len(g[1]))[:room]}
    out, rest = [], []
    for g in groups:
        (out if id(g) in keep else rest).append(g)
    if len(rest) == 1 and len(out) < room + 1:
        return groups
    if rest:
        out.append((T("group_collapsed"), [f"{label} · {lines[0]}" + (f" (+{len(lines) - 1})" if len(lines) > 1 else "")
                                for label, lines in rest]))
    return out


def gist(text, n):
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


def plain_prefixes():
    """The count lines of the 기타 reply that stay plain (not bullets): each template up to its first placeholder."""
    return tuple(T(k).split("{")[0] for k in ("rest_bots", "rest_involved", "rest_requested"))


def list_last(lines):
    """A reply body: its plain lines first, a blank line, then the bullets. Slack folds whatever follows a list into
    its last item, so no plain line may come after one."""
    plain_ = plain_prefixes()
    plain = [x for x in lines if x and x.startswith(plain_)]
    bullets = [f"- {x}" for x in lines if x and not x.startswith(plain_)]
    return plain + ([""] if plain and bullets else []) + bullets


def render(g, p, fmt="md"):
    """slack: [parent message, one thread reply each]. md: the same messages, links in Markdown, separated so the
    owner approves the parent and its replies together."""
    head, replies = parts(g, p, fmt)
    emoji = (p.get("report") or {}).get("kind_emoji") or ":kyouyuu-1:"
    msgs = [{"text": "\n".join([emoji, "", ""] + head)}]
    for label, lines in replies:
        msgs.append({"text": "\n".join([f"**{label}**"] + list_last(lines)), "reply": True})
    if fmt == "slack":
        return msgs
    return "\n\n".join([msgs[0]["text"]] + [f"{T('reply_n', i=i)}\n\n{m['text']}" for i, m in enumerate(msgs[1:], 1)])


# ---------- Slack lint ----------

SINGLE_BOLD = re.compile(r"(?<![*A-Za-z0-9_])\*(?![*\s])[^*\n]+?(?<![*\s])\*(?![*A-Za-z0-9_])")
CODE = re.compile(r"`[^`\n]*`")
BULLET = re.compile(r"^\s*(?:[-•◦*]\s|\d+\.\s)")
MENTION = re.compile(r"^(<@[UW][A-Z0-9]+>|<!subteam\^[A-Z0-9]+>|<!(?:here|channel)>)")


def slack_lint(messages):
    """Why these Slack messages break the slack-post rules (empty: fine). messages[0] is the top-level message;
    the rest are its thread replies."""
    errs = []
    for i, m in enumerate(messages):
        text = m["text"] if isinstance(m, dict) else m
        top = i == 0 and not (isinstance(m, dict) and m.get("reply"))
        where = "message" if top else f"reply {i}"
        lines = text.split("\n")
        if SINGLE_BOLD.search(CODE.sub("", text)):
            errs.append(f"{where}: single-asterisk bold *x* turns italic through the Slack MCP: write **x**")
        if any(ln.lstrip().startswith("###") for ln in lines):
            errs.append(f"{where}: ### headings do not render: use a **label** line")
        if any(re.match(r"^\s*\|.*\|\s*$", ln) for ln in lines):
            errs.append(f"{where}: markdown tables do not render: use bullets")
        if top:
            if not MENTION.match(lines[0]) and not (
                    re.fullmatch(r":[\w+\-]+:", lines[0].strip()) and len(lines) > 3 and not lines[1].strip()
                    and not lines[2].strip() and lines[3].strip()):
                errs.append(f"{where}: a share without a mention starts with the kind emoji alone on line 1, "
                            "then two blank lines")
        seen = False
        for ln in lines:
            if BULLET.match(ln):
                seen = True
            elif ln.strip() and seen:
                errs.append(f"{where}: {ln.strip()[:40]!r} follows a bullet list and would be swallowed into its last "
                            "item: end the message with the list, move the rest to a thread reply")
                break
    return errs


# ---------- drafts and approval ----------

def _hash(obj):
    return hashlib.sha1(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:8]


def draft(kind, date, title, messages, channel, thread, items=None, question=None, lint="ok"):
    """Record a draft and open its decision card. Returns (status, card, rid). Older pending drafts of the same kind
    and day are superseded and their cards closed as foundry."""
    import decide
    h = _hash(messages)
    rid = f"rp-{kind}-{date}-{h}"
    ctx = f"\n\n{T('reply_sep')}\n\n".join(m["text"] for m in messages)
    status, card = decide.open_card(
        key=f"report:{kind}:{date}:{h}", unit=None, repo=None, asked_by="foundry",
        question=question or T("draft_question", title=title, date=date), context=ctx,
        options=[{"key": "post", "label": T("opt_post"), "description": T("opt_post_desc"),
                  "pros": [], "cons": [], "preview": None},
                 {"key": "hold", "label": T("opt_hold"), "description": T("opt_hold_desc"), "pros": [], "cons": [],
                  "preview": None}],
        recommended="post", reason=T("draft_reason"), deadline=None, default=None, refs=None)

    def decide_(snap):
        recs = []
        if rid not in snap["reports"]:
            recs.append(("report.draft", {"rid": rid, "kind": kind, "date": date, "title": title, "messages": messages,
                                          "channel": channel, "thread": thread, "card": card["id"],
                                          "items": items, "lint": lint}))
        for x in snap["reports"].values():
            c = (snap.get("cards") or {}).get(x.get("card")) or {}
            if (x["kind"] == kind and x["date"] == date and x["rid"] != rid and x["state"] == "pending"
                    and c and c.get("answer") is None):
                recs.append(("decide.answer", {"id": c["id"], "key": decide.MOOT, "by": "foundry",
                                               "moot": T("draft_moot"), "note": T("draft_closed", id=card['id'])}))
        return None, recs

    cl.transact(decide_)
    return status, card, rid


def _send_at(p, now, at=None):
    """None (send now), else the ISO time to send: the next `at` (HH:MM) when given, pushed past quiet hours."""
    import prefs
    zone = _tz(p)
    t = None
    if at:
        h, m = (int(x) for x in at.split(":"))
        t = now.astimezone(zone).replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= now:
            t += dt.timedelta(days=1)
        while t.weekday() >= 5:
            t += dt.timedelta(days=1)
    probe = t or now
    if prefs.in_window(probe, p["slack_quiet"], zone):
        t = prefs.window_end(probe, p["slack_quiet"], zone)
    return t.isoformat(timespec="minutes") if t else None


OWNER_ID_RE = re.compile(r" \(([^()\s]+)\)$")
# The parent's two owner blocks and the count each one carries in the counts line (0.1.88: a decision is 결정 필요's).
def owner_blocks():
    """(block heading, count pattern) in every UI language: a draft keeps the language it was written in."""
    out = []
    for lang in i18n.LANGS:
        t = lambda k: i18n.t("report." + k, language=lang)  # noqa: E731
        out += [(f"**{t('sec_decisions')}**", re.compile(rf"(· {re.escape(t('counts_decisions'))} )(\d+)")),
                (f"**{t('sec_owner')}**", re.compile(rf"({re.escape(t('counts_owner'))} )(\d+)"))]
    return tuple(out)


def refresh_owner(text, open_ids):
    """The parent message with every 결정 필요 / 오너 대기 line whose item is no longer open removed and its count
    lowered to match; a block goes when it empties. Returns (text, dropped ids). Lines and items opened after the
    draft are not added: the owner approved what they read."""
    all_dropped = []
    for head, count_re in owner_blocks():
        lines = text.split("\n")
        if head not in lines:
            continue
        i = lines.index(head)
        end = next((j for j in range(i + 1, len(lines)) if not lines[j].strip()), len(lines))
        dropped, keep = [], []
        for ln in lines[i + 1:end]:
            m = OWNER_ID_RE.search(ln)
            if m and m.group(1) not in open_ids:
                dropped.append(m.group(1))
            else:
                keep.append(ln)
        if not dropped:
            continue
        block = [head] + keep if keep else []
        start = i - 1 if not keep and i > 0 and not lines[i - 1].strip() else i
        text = "\n".join(lines[:start] + block + lines[end:])
        text = count_re.sub(lambda m: m.group(1) + str(max(0, int(m.group(2)) - len(dropped))), text, count=1)
        all_dropped += dropped
    return text, all_dropped


def on_answer(cid):
    """After a card answer: a report card answered `post` by the owner (or the foreman quoting the owner) files the
    draft into the outbox; anything else files nothing. Idempotent."""
    import outbox
    snap = cl.load()
    c = (snap.get("cards") or {}).get(cid) or {}
    x = next((r for r in snap["reports"].values() if r.get("card") == cid), None)
    if not x or x["state"] != "pending" or c.get("answer") is None:
        return {}
    by, quote = c.get("answered_by"), c.get("answer_quote")
    if c["answer"] != "post" or not (by == "owner" or (by == "foreman" and quote)):
        cl.append("report.decided", rid=x["rid"], state="held", by=by, quote=quote)
        return {"report": x["rid"], "state": "held"}
    p = _prefs()
    now = cl.now()
    remind = x["kind"] == "remind"
    send_at = _send_at(p, now, (p.get("report") or {}).get("remind_time") if remind else None)
    cl.append("report.decided", rid=x["rid"], state="approved", by=by, quote=quote, send_at=send_at)
    filed, dropped = [], []
    if remind:
        refs = {ref for it in x.get("items") or [] for ref in it.get("prs") or []}
        for o in cl.load()["outbox"].values():
            if o["state"] == "pending" and o["kind"] in ("review-ask", "review-remind") and not o.get("rid") \
                    and refs & set(o.get("prs") or []):
                cl.append("outbox.dropped", oid=o["oid"], error=f"superseded by review reminder {x['rid']}")
                dropped.append(o["oid"])
        for it in x.get("items") or []:
            _, o = outbox.add(None, x["channel"], it.get("thread"), "review-remind", it["text"], it.get("prs") or [],
                              [it["slack"]] if it.get("slack") else [], rid=x["rid"], send_at=send_at)
            filed.append(o["oid"])
    else:
        msgs = x["messages"]
        parent, dropped_ids = refresh_owner(msgs[0]["text"], {o["id"] for o in owner_now(cl.load())})
        if dropped_ids:  # answered since the draft: the line and the count go, and the journal says so
            cl.append("report.refreshed", rid=x["rid"], dropped=dropped_ids, before=msgs[0]["text"], after=parent)
        _, o = outbox.add(None, x["channel"], x.get("thread"), "report", parent, (), (), rid=x["rid"],
                          send_at=send_at, replies=[m["text"] for m in msgs[1:]])
        filed.append(o["oid"])
    return {"report": x["rid"], "state": "approved", "send_at": send_at or "now", "outbox": filed,
            "superseded": dropped}


# ---------- review reminders ----------

def remind_items(groups, p, snap, now=None):
    """One reminder per reviewer with a Slack id: their unanswered requests and stale approvals, skipping a PR at
    a head this reviewer was already reminded about and any PR reminded to them in the last 24 hours."""
    now = now or cl.now()
    people = {x.get("login"): x for x in (p.get("review") or {}).get("people") or []}
    sent = []
    for r in snap["reports"].values():
        if r["kind"] == "remind" and r["state"] == "approved":
            for it in r.get("items") or []:
                sent.append((it.get("login"), it.get("heads") or {}, cl.parse_ts(r.get("decided"))))
    out, skipped = [], []
    for login, gaps in sorted(groups.items()):
        person = people.get(login)
        ignored = (snap.get("intake") or {}).get("ignored") or {}  # prefs review.ignore matches
        xs = [g for g in gaps if g["kind"] in ("stale_request", "dismissed") and str(g["ref"]).lower() not in ignored]
        if not xs:
            continue
        if not person or not person.get("slack"):
            skipped += [f"{login}: {g['ref']} ({T('no_slack_id')})" for g in xs]
            continue
        keep = []
        for g in xs:
            again = [t for who, heads, t in sent if who == login and g["ref"] in heads
                     and (heads[g["ref"]] == g.get("head") or (t and (now - t).total_seconds() < 24 * 3600))]
            (skipped.append(f"{login}: {g['ref']} ({T('already_reminded')})") if again else keep.append(g))
        if keep:
            out.append({"login": login, "slack": person["slack"], "prs": [g["ref"] for g in keep],
                        "heads": {g["ref"]: g.get("head") for g in keep}, "gaps": keep})
    return out, skipped


def remind_text(item, p):
    gaps = item["gaps"]
    lead = ("レビューのリマインドです。ご確認をお願いします" if len(gaps) == 1
            else f"レビューのリマインドです。以下 {len(gaps)} 件のご確認をお願いします")
    links = [f"<{g.get('url')}|{g['ref']}>" for g in gaps]
    return "\n".join([f"<@{item['slack']}>", "", lead] + links)


# ---------- CLI ----------

def cmd_daily(a):
    p = _prefs()
    date = a.date or today(p)
    fmt = "slack" if a.draft else a.format
    snap = cl.load()
    g = gather(snap, cl.read_journal(), p, date)
    out = render(g, p, fmt)
    if fmt == "md":
        return _emit({"text": out, "gaps": g["gaps"]}) if a.json else print(out) or 0
    errs = slack_lint(out)
    if errs:
        return _fail("refused: the Slack draft breaks the slack-post rules:\n- " + "\n- ".join(errs))
    if not a.draft:
        return _emit({"messages": out, "gaps": g["gaps"]}) or 0
    ch = channel_of(p)
    if not ch:
        return _fail("refused: no channel: set prefs report.channel (or review.channel)")
    status, card, rid = draft("daily", date, title_of(p, date), out, ch, (p.get("report") or {}).get("thread") or None)
    return _emit({"draft": rid, "card": card["id"], "status": status, "lint": "ok", "messages": out, "gaps": g["gaps"],
                  "next": T("draft_next")}) or 0


def cmd_release_gaps(a):
    p = _prefs()
    rows = release_gaps(cl.load(), cl.read_journal(), p)
    if a.json:
        return _emit({"gaps": rows, "propose": sorted({r["repo"] for r in rows if r.get("batch")})}) or 0
    lines = [f"- {r['ref']} {release_gap_word(r)} ({r.get('age_h')}h)"
             + (f" · {T('batch_candidate')}" if r.get("batch") else "") for r in rows]
    print("\n".join([f"# {section_label('release_gaps')} ({len(rows)})"] + (lines or [f"- {T('none')}"])))
    return 0


def cmd_review_gaps(a):
    p = _prefs()
    groups, unread = review_gaps(p)
    if not a.remind:
        if a.json:
            return _emit({"reviewers": groups, "unread": unread}) or 0
        out = [f"# {section_label('review_gaps')} ({sum(len(v) for v in groups.values())})"]
        for who, xs in sorted(groups.items()):
            out += [f"## {who}"] + [f"- {x['ref']} {gap_label(x['kind'])}" for x in xs]
        if unread:
            out.append(T("unread", list=", ".join(unread)))
        print("\n".join(out))
        return 0
    snap = cl.load()
    items, skipped = remind_items(groups, p, snap)
    for it in items:
        it["text"] = remind_text(it, p)
        it.pop("gaps")
    msgs = [{"text": it["text"]} for it in items]
    errs = [e for m in msgs for e in slack_lint([m])]
    if errs:
        return _fail("refused: the reminder breaks the slack-post rules:\n- " + "\n- ".join(errs))
    if not a.draft or not items:
        return _emit({"reminders": items, "skipped": skipped, "unread": unread}) or 0
    ch = (p.get("review") or {}).get("channel")
    if not ch:
        return _fail("refused: no channel: set prefs review.channel")
    date = today(p)
    status, card, rid = draft("remind", date, T("remind_title"), msgs, ch, None, items,
                              question=T("remind_question", n=len(items), who=", ".join(i["login"] for i in items),
                                         at=(p.get("report") or {}).get("remind_time") or "09:00"))
    return _emit({"draft": rid, "card": card["id"], "status": status, "lint": "ok", "reminders": items, "skipped": skipped,
                  "unread": unread}) or 0


PERMALINK_RE = re.compile(r"^https://[\w.-]+\.slack\.com/archives/([A-Z0-9]+)/p\d{16}(\?.*)?$")
THREAD_TS_RE = re.compile(r"^\d{10}\.\d{6}$")
# Slack's search hands some channel names with an unresolved placeholder (`team-<ORGANIZATION>`): drop the token.
PLACEHOLDER_RE = re.compile(r"<[A-Z][A-Z_]*>")
# A line about the owner names nobody by pronoun: work facts only (the collector contract in dispatcher.md).
PRONOUN_RE = re.compile(r"(?i)\b(he|she|him|her|his|hers|himself|herself)\b|彼女|彼は|彼が|彼の|彼を|그녀|그는|그가|그의|그를|그에게")


def channel_name(name):
    c = PLACEHOLDER_RE.sub("", str(name or "")).strip().lstrip("#")
    return re.sub(r"[-_]{2,}", "-", c).strip("-_") or "channel"


def permalink_of(i):
    """The message's link, its thread built here: the collector stores the bare permalink and thread_ts as fields
    (a URL with `&` in a shell command is refused by a guard, and the query is not something to weaken it for)."""
    base = str(i.get("permalink") or "")
    m = PERMALINK_RE.match(base)
    if i.get("thread_ts") and m and "?" not in base:
        return f"{base}?thread_ts={i['thread_ts']}&cid={m.group(1)}"
    return base or None


def cmd_slack(a):
    p = _prefs()
    date = a.date or today(p)
    if a.sub == "slack-query":
        uid = cl.load_config().get("owner_slack_user_id")
        if not uid:
            return _fail("refused: config.json has no owner_slack_user_id: the search needs the owner's id")
        return _emit({"query": f"from:<@{uid}> on:{date}", "tool": "slack_search_public", "date": date,
                      "exclude_channels": ((p.get("report") or {}).get("exclude") or {}).get("channels") or [],
                      "how": "page through every result; for each message run `foundry report slack-add` with a "
                             "one-line summary in your own words of what the owner did (never the text itself, never "
                             "health, family or leave reasons); then `foundry report slack-done --count N`; on a "
                             "tool error `foundry report slack-gap --source slack --why \"<error>\"`"}) or 0
    if a.sub == "slack-add":
        summary = " ".join((a.summary or "").split())
        link = (a.permalink or "").split("?", 1)[0]
        if not PERMALINK_RE.match(link):
            return _fail("refused: --permalink is a Slack message permalink (https://<ws>.slack.com/archives/C…/p…), "
                         "without the query; a reply's thread goes in --thread-ts")
        if a.thread_ts and not THREAD_TS_RE.match(a.thread_ts):
            return _fail("refused: --thread-ts is the parent message ts, like 1795061530.355009")
        if not summary or len(summary) > 120:
            return _fail("refused: --summary is one line of at most 120 characters, in your own words")
        if PRONOUN_RE.search(summary):
            return _fail("refused: --summary names no one by pronoun (he/she/彼/그녀...): write the work fact only, "
                         "with a name or no subject, and never a personal remark")
        channel = channel_name(a.channel)
        if excluded_channel(channel, p):
            return _emit({"excluded": channel}) or 0
        cl.append("report.slack", date=date, channel=channel, permalink=link, summary=summary,
                  **({"thread_ts": a.thread_ts} if a.thread_ts else {}))
        return _emit({"added": link, "date": date}) or 0
    if a.sub == "slack-gap":
        cl.append("report.slack", date=date, gap={"source": a.source, "why": " ".join(a.why.split())[:120]})
        return _emit({"gap": a.source, "date": date}) or 0
    cl.append("report.slack", date=date, searched=a.count)
    return _emit({"searched": a.count, "date": date}) or 0


def cmd_report(a):
    if a.kind in ("handover", "kaizen"):
        import factory
        return factory.cmd_report(a)
    return {"daily": cmd_daily, "release-gaps": cmd_release_gaps, "review-gaps": cmd_review_gaps}[a.kind](a)


def add_cli(sp):
    r = sp.add_parser("report", help="handover, kaizen, the daily report, release and review gaps").add_subparsers(
        dest="kind", required=True)
    for k in ("handover", "kaizen"):
        x = r.add_parser(k)
        x.add_argument("--json", action="store_true")
        x.set_defaults(func=cmd_report)
    d = r.add_parser("daily", help="the day's report; --draft lints it and asks the owner before anything is sent")
    d.add_argument("--date", help="YYYY-MM-DD (default today, prefs timezone)")
    d.add_argument("--format", choices=["md", "slack"], default="md")
    d.add_argument("--draft", action="store_true")
    d.add_argument("--model", help="ignored: the footer is prefs slack.footer (kept so older schedules still run)")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_report)
    rg = r.add_parser("release-gaps", help="merged PRs no release holds, and releases on dev but not prod")
    rg.add_argument("--json", action="store_true")
    rg.set_defaults(func=cmd_report)
    vg = r.add_parser("review-gaps", help="the owner's open PRs whose review is stuck, per reviewer")
    vg.add_argument("--remind", action="store_true", help="one reminder per reviewer (sent at report.remind_time)")
    vg.add_argument("--draft", action="store_true")
    vg.add_argument("--model", help="ignored: the footer is prefs slack.footer")
    vg.add_argument("--json", action="store_true")
    vg.set_defaults(func=cmd_report)
    sq = r.add_parser("slack-query", help="the Slack search for the owner's own messages of the day")
    sq.add_argument("--date")
    sa = r.add_parser("slack-add", help="one of the owner's Slack messages: channel, permalink, a one-line summary")
    sa.add_argument("--date")
    sa.add_argument("--channel", required=True)
    sa.add_argument("--permalink", required=True)
    sa.add_argument("--summary", required=True)
    sa.add_argument("--thread-ts", help="a reply's parent ts (the link gets ?thread_ts=…&cid=… at render time)")
    sg = r.add_parser("slack-gap", help="a Slack source that could not be read")
    sg.add_argument("--date")
    sg.add_argument("--source", required=True)
    sg.add_argument("--why", required=True)
    sd = r.add_parser("slack-done", help="the Slack search finished (count of messages added)")
    sd.add_argument("--date")
    sd.add_argument("--count", type=int, required=True)
    for k in ("slack-query", "slack-add", "slack-gap", "slack-done"):
        r.choices[k].set_defaults(func=cmd_slack, sub=k)


def board(snap, model):
    pend = [x for x in (snap.get("reports") or {}).values() if x["state"] == "pending"]
    model["report_drafts"] = [{"rid": x["rid"], "kind": x["kind"], "date": x["date"], "card": x.get("card")}
                              for x in sorted(pend, key=lambda x: x["ts"])]
