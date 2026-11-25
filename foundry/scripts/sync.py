"""Event-driven tracker/Obsidian sync: the journal says what happened, this module says what the outside
world (Jira, Linear, Obsidian) has not been told yet.

Unit events (status changes, PRs opened/merged, verify passes, reports, decision answers) become
`sync.needed` items per unit and target. `foundry sync drain` hands the dispatcher one coalesced batch per
unit per target; the dispatcher posts it through MCP tools and closes it with `sync done|failed <batch>`.
Repeated failures on a target raise one owner to-do to reconnect the MCP server, never a re-auth.
"""

import hashlib
import json
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

TARGETS = ("jira", "linear", "github", "obs")
TRACKER_TARGETS = ("jira", "linear", "github")
SEEN_MAX = 200
UNIT_FIELDS = {"sync": {}}
STATE = {"sync_batches": {}, "sync_targets": {}, "owner_todos": {}, "sync_last_drain": None}

# A lead's report is raw working text (drafts, notes to the foreman, Slack wording). It reaches the batch whole,
# never cut, and the dispatcher must rewrite it: that rule rides on every item that carries one.
# prefs sync.languages: a language code per target. Trackers are read by the team (Japanese by the owner's global
# rule); Obsidian is the owner's own notes (Korean). An unknown code is written as given.
DEFAULT_LANGUAGES = {"jira": "ja", "linear": "ja", "github": "en", "obs": "ko"}
LANGUAGE_NAMES = {"ja": "Japanese", "ko": "Korean", "en": "English"}
REPORT_RULE = ("Lines starting with `보고:` are a lead's raw report, not tracker text: rewrite each into 1-3 {lang} "
               "lines of outcome (what changed, what is next, links). Drop message drafts (Slack/Jira/mail), "
               "instructions or questions addressed to the foreman or the owner, and internal chatter. Never paste "
               "a report verbatim.")
# Failures the owner fixes with /mcp -> Reconnect. Anything else (a bad cloudId, a missing field, an unknown issue) is
# the dispatcher's own argument to fix, and three of those must not ask the owner to reconnect a healthy server.
CONNECTION_RE = re.compile(
    r"(?i)not connected|disconnect|connection (refused|reset|closed|error|lost)|ECONN\w*|timed? ?out|socket|"
    r"network|fetch failed|unauthori[sz]ed|\b401\b|authenticat\w* (failed|required|expired)|"
    r"(token|session|credentials?) (expired|invalid|revoked)|re-?auth|server (unavailable|not running|crashed)|"
    r"\b50[23]\b|tool[^.]* not (available|found)|no such tool|mcp server|mcp( server)? (is )?down")


def failure_shape(error):
    return "connection" if CONNECTION_RE.search(error or "") else "argument"


# A sent review ask puts the unit's Jira ticket in review. Transition names differ per workflow, so the
# dispatcher reads them; a failed transition is reported once, never retried.
IN_REVIEW_RULE = ("This batch carries a sent review ask: after the comment, move each ticket to the workflow's "
                  "in-review status. listJiraIssueTransitions, then take the transition whose target status is "
                  "検討中, レビュー中, 검토 중, In Review or Review (by name, never a hard-coded id; the site may answer "
                  "in Korean). Skip a ticket that already shows that status or a later one (Done, 完了, 해결됨, 완료, "
                  "Closed, Resolved). Status only: never edit "
                  "fields or the description. If there is no such transition or it fails, do not retry and do not "
                  "fail the batch: close it with `sync done <batch> --ticket-status '<status now>' "
                  "--transition-error '<what happened>'`, which files a unit report.")
NO_TICKET_RULE = ("This unit has no Jira ticket yet. First look for one: searchJiraIssuesUsingJql with "
                  "`project = {project} AND summary ~ \"<words from the unit title>\" ORDER BY created DESC`. If none is "
                  "this work, create one with createJiraIssue in project {project} (type Task, summary = the unit title), "
                  "assignee = the owner (atlassianUserInfo gives the account id). Record it with `foundry unit keys "
                  "<unit> --ticket <KEY>`, then post the comment there. A unit that is not {scope} gets no ticket: "
                  "close with `sync done <batch> --ticket-status 'no ticket'`.")
# The owner's global rule for anything posted outside: a blank line, then exactly this. Posted right the first time,
# so nothing ever goes back to fix a signature.
DEFAULT_FOOTER = "🤖 Claude Code が作成"
POSTED_RULE = ("A comment already posted is never edited or deleted (addOrEditJiraIssueComment with a comment id, a "
               "Linear comment update): not for its signature, wording, style or language. If a posted comment is "
               "factually wrong or stale and a reader would act on it, post a new short correction comment that "
               "names it. The only edit is removing a secret or personal data, and that is reported.")
# Status names as the Jira site shows them, in its UI language (this site answers in Korean: 검토 중, 해결됨).
IN_REVIEW_RE = re.compile(r"(?i)検討中|レビュー中|review|done|完了|closed?|resolved|검토\s?중|리뷰\s?중|해결됨|완료|닫힘|종료")

def _jira(p):
    """prefs sync.jira: the project new tickets go to, which work gets one, and the Atlassian site to use."""
    j = (p.get("sync") or {}).get("jira") or {}
    return {"project": j.get("project") or "<project>", "scope": j.get("scope") or "tracked work",
            "site": j.get("site") or "the configured site"}


def _jira_project(p):
    return ((p.get("sync") or {}).get("jira") or {}).get("project") or ""


NO_PROJECT_RULE = ("This unit has no Jira ticket and prefs sync.jira.project is empty, so no ticket is searched for "
                   "or created: close with `sync done <batch> --ticket-status 'no ticket'`.")


def instructions(target, p):
    return INSTRUCTIONS[target].format(**_jira(p)) if target == "jira" else INSTRUCTIONS[target]


INSTRUCTIONS = {
    "jira": ("Resolve the cloudId first: getAccessibleAtlassianResources, then use {site}'s id for every "
             "Jira call. Post ONE comment with these lines (addOrEditJiraIssueComment). Comments only: never edit the "
             "description (masked reads corrupt descriptions). Read the latest comments first; skip if the same "
             "lines are already there. Close with `sync done <batch> --ticket-status '<status the issue shows now>'` "
             "(getJiraIssue after any transition)."),
    "linear": ("Post ONE comment with these lines and, when `state` is set, move the issue to that state. "
               "Only through the Linear MCP; skip and report if it is not connected. Close with `sync done <batch> "
               "--ticket-status '<state the issue shows now>'`."),
    "github": ("GitHub Issues through `gh` only, one command per call, never the API: read the issue first (`gh issue "
               "view <n> -R <repo> --comments`) and skip if the same lines are already there; post ONE comment with "
               "`gh issue comment <n> -R <repo> --body-file <file>`, where <file> is `/tmp/foundry-<batch>.md` written by "
               "an earlier, separate Bash call (`cat > /tmp/foundry-<batch>.md <<'EOF'` ... `EOF`); add each of "
               "`labels` with `gh issue edit <n> -R <repo> --add-label <name>` when the issue lacks it; when `state` is "
               "`closed` and the issue is open, `gh issue close <n> -R <repo>`; when it is `open` and the issue is "
               "closed, `gh issue reopen <n> -R <repo>`. Close with `sync done <batch> --ticket-status '<open|closed "
               "as the issue shows now>'`."),
    "obs": ("Append the lines to `obs_path` with the Obsidian MCP only (mcp__obsidian__vault_append; create with "
            "vault_write if missing). Never the filesystem, reads included."),
}
GITHUB_CLOSED = ("done", "cancelled")
GITHUB_NO_TICKET = ("This unit has no GitHub issue: create one in `repo` (`gh issue create -R <repo> --title "
                    "<unit title> --body-file <file>`, the file written as for comments), record it with `foundry unit keys <unit> --ticket "
                    "<owner/repo#n>`, then comment on it. With no `repo`, close with `sync done <batch> "
                    "--ticket-status 'no issue'`.")
LINEAR_STATE = {"in_progress": "In Progress", "waiting_others": "In Review", "waiting_owner": "In Review",
                "done": "Done"}
def status_word(status):
    """A unit status in the UI language (`sync` catalog), the raw value when the catalog has no word for it."""
    import i18n
    key = f"status_{status}"
    return i18n.t("sync." + key) if key in i18n.catalog("sync") else status


def T(key, **kw):
    import i18n
    return i18n.t("sync." + key, **kw)


# ---------- reducers ----------

def _slot(u, target):
    sy = u.setdefault("sync", {})
    return sy.setdefault(target, {"last_event_at": None, "seen": [], "last_synced_at": None,
                                  "failures": 0, "pending": []})


def _unit_of(s, r):
    uid = r.get("unit") or r.get("id")
    return s["units"].get(uid) if uid else None


def _op_needed(s, r, ts):
    u = _unit_of(s, r)
    if not u or r.get("target") not in TARGETS:
        return
    sl = _slot(u, r["target"])
    key = r.get("key") or _key(r.get("event"), r.get("ts"), u["id"], r.get("text"))
    if key in sl["seen"]:
        return
    sl["seen"] = (sl["seen"] + [key])[-SEEN_MAX:]
    ets = r.get("ts") or ts
    if not sl["last_event_at"] or ets > sl["last_event_at"]:
        sl["last_event_at"] = ets
    sl["pending"].append({"key": key, "event": r.get("event"), "text": r.get("text") or "", "at": ets,
                          "queued": ts, "batch": None})


def _op_drain(s, r, ts):
    s["sync_last_drain"] = ts
    for b in r.get("batches") or []:
        s["sync_batches"][b["batch"]] = {"unit": b["unit"], "target": b["target"], "keys": b["keys"],
                                         "ts": ts, "state": "open", "error": None, "transition": b.get("transition")}
        u = s["units"].get(b["unit"])
        if not u:
            continue
        moved = set()
        for p in _slot(u, b["target"])["pending"]:
            if p["key"] in b["keys"]:
                if p["batch"]:
                    moved.add(p["batch"])
                p["batch"] = b["batch"]
        _supersede(s, u, b["target"], moved, ts)
    # Closed batches older than the newest 200 carry nothing a rule reads.
    closed = [k for k, v in s["sync_batches"].items() if v["state"] != "open"]
    for k in closed[:-200]:
        s["sync_batches"].pop(k, None)


def _supersede(s, u, target, batches, ts):
    """An open batch none of whose items still points at it was re-drained (its retry window passed, or it was
    never closed): it is superseded, so it no longer reads as in flight. `sync done` still closes it."""
    live = {p["batch"] for p in _slot(u, target)["pending"]}
    for bid in batches:
        ob = s["sync_batches"].get(bid)
        if ob and ob["state"] == "open" and bid not in live:
            ob["state"], ob["superseded"] = "superseded", ts


def _op_reconcile(s, r, ts):
    for bid in r.get("superseded") or []:
        b = s["sync_batches"].get(bid)
        if b and b["state"] == "open":
            b["state"], b["superseded"] = "superseded", ts


def _close(s, r, ts, ok):
    b = s["sync_batches"].get(r.get("batch"))
    if not b or b["state"] not in ("open", "superseded"):
        return
    b["state"] = "done" if ok else "failed"
    b["error"] = None if ok else r.get("error")
    t = s["sync_targets"].setdefault(b["target"], {"failures": 0, "last_error": None, "last_ok": None})
    u = s["units"].get(b["unit"])
    sl = _slot(u, b["target"]) if u else None
    if ok:
        t.update({"failures": 0, "last_ok": ts})
        if sl:
            sl["pending"] = [p for p in sl["pending"] if p["key"] not in b["keys"]]
            sl.update({"last_synced_at": ts, "failures": 0})
        for td in s["owner_todos"].values():
            if td.get("kind") == "mcp-reconnect" and td.get("target") == b["target"] and not td.get("closed"):
                td["closed"] = ts
    else:
        t["last_error"] = r.get("error")
        if (r.get("shape") or "connection") == "connection":
            t["failures"] += 1
        else:
            t["failures"] = 0  # the server answered: it is connected
        if sl:
            sl["failures"] += 1
            for p in sl["pending"]:
                if p["batch"] == r["batch"]:
                    p["batch"] = None


def _op_owner_todo(s, r, ts):
    tid = r.get("tid") or f'{r.get("kind")}:{r.get("target") or ""}'
    s["owner_todos"][tid] = {"tid": tid, "kind": r.get("kind"), "target": r.get("target"), "unit": r.get("unit"),
                             "text": r.get("text"), "title": r.get("title"), "by": r.get("by"), "why": r.get("why"),
                             "ts": ts, "closed": None}


def _op_owner_todo_title(s, r, ts):
    td = s["owner_todos"].get(r.get("tid"))
    if td:
        td["title"] = r.get("title")


def _op_owner_todo_done(s, r, ts):
    td = s["owner_todos"].get(r.get("tid"))
    if td and not td.get("closed"):
        td["closed"] = ts
        for k in ("by", "quote"):
            if r.get(k):
                td["closed_" + k] = r[k]


OPS = {"sync.needed": _op_needed, "sync.drain": _op_drain,
       "sync.done": lambda s, r, ts: _close(s, r, ts, True),
       "sync.failed": lambda s, r, ts: _close(s, r, ts, False),
       "owner.todo": _op_owner_todo, "owner.todo.done": _op_owner_todo_done,
       "owner.todo.title": _op_owner_todo_title, "sync.reconcile": _op_reconcile}


# ---------- deriving what the outside world has not been told ----------

def _key(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def _prefs():
    import prefs
    return prefs.load()


def enabled(target, p):
    """obs is governed by obs_enabled; a tracker target only when prefs `trackers` lists it."""
    return target == "obs" or target in (p.get("trackers") or [])


def targets_of(u, p):
    out = [u["tracker"]] if u.get("tracker") in TRACKER_TARGETS and enabled(u["tracker"], p) else []
    if (p.get("sync") or {}).get("obs_enabled", True):
        out.append("obs")
    return out


def _pr_ref(r):
    if r.get("url"):
        return r["url"]
    if r.get("repo") and r.get("number"):
        return f'{r["repo"]}#{r["number"]}'
    return r.get("pr") or r.get("ref") or ""


def describe(r, snap):
    """(unit id, event kind, line text) for a journal record worth syncing, else None."""
    op = r.get("op")
    uid = r.get("unit") or r.get("id")
    if op == "unit.status":
        note = f': {r["note"]}' if r.get("note") else ""
        return uid, "status", f'상태 → {status_word(r.get("status"))}{note}'
    if op == "pr.opened":
        return r.get("unit"), "pr.opened", f"PR 오픈: {_pr_ref(r)}"
    if op == "pr.merged":
        return r.get("unit"), "pr.merged", f"PR 머지: {_pr_ref(r)}"
    if op == "verify.pass":
        return uid, "verify.pass", "검증 통과" + (f': {r["evidence"]}' if r.get("evidence") else "")
    if op == "unit.report":
        return r.get("id"), "report", "보고: " + " ".join((r.get("text") or "").split())
    if op == "send.record" and r.get("state") == "sent" and r.get("kind") == "review-ask":
        prs, tks = set(r.get("prs") or []), set(r.get("tickets") or [])
        hits = [u for u in snap["units"].values() if u.get("kind") != "foreman"
                and (prs & set(u["keys"]["pr"]) or tks & set(u["keys"]["ticket"]))]
        if len(hits) != 1:
            return None  # no unit, or a message about several: nothing to move
        return hits[0]["id"], "review.asked", "리뷰 요청 발송: " + ", ".join(sorted(prs) or sorted(tks))
    if op in ("decide.answer", "decision.answer"):
        card = (snap.get("cards") or {}).get(r.get("id") or "") or (snap.get("decisions") or {}).get(r.get("key") or "") or {}
        uid = r.get("unit") or card.get("unit")
        q = card.get("question")
        return uid, "decide.answer", "결정: " + (f"{q} → " if q else "") + str(r.get("key") or r.get("answer") or "")
    return None


def pending_from_journal(snap, journal=None, unit=None, p=None):
    """`sync.needed` field dicts for unit events newer than each unit/target cursor and not yet queued."""
    p = p or _prefs()
    journal = cl.read_journal() if journal is None else journal
    out = []
    for r in journal:
        d = describe(r, snap)
        if not d or not d[0] or (unit and d[0] != unit):
            continue
        uid, event, text = d
        u = snap["units"].get(uid)
        if not u or u.get("kind") == "foreman":
            continue
        key = _key(r.get("op"), r.get("ts"), uid, text)
        for t in targets_of(u, p):
            sl = (u.get("sync") or {}).get(t) or {}
            last = sl.get("last_event_at")
            if (last and (r.get("ts") or "") < last) or key in (sl.get("seen") or []):
                continue
            out.append({"unit": uid, "target": t, "event": event, "text": text, "ts": r.get("ts"), "key": key})
    return out


def enqueue_for(unit=None):
    """Queue what is missing for one unit (or all). Cheap and lock-scoped; callers swallow errors."""
    p = _prefs()

    def decide(snap):
        recs = [("sync.needed", f) for f in pending_from_journal(snap, unit=unit, p=p)]
        return len(recs), recs

    n, _ = cl.transact(decide)
    return n


# ---------- drain, lag, cadence ----------

def _minutes(a, b):
    return (a - b).total_seconds() / 60


def _retry(p):
    return (p.get("sync") or {}).get("inflight_retry_min", 30)


def queued(sl, snap, t, p):
    """(due, inflight): the slot's items the next drain hands out, and those in an open batch still inside its
    retry window. The drain, the lag, the board and `sync status` all read this one split."""
    due, inflight = [], []
    for x in (sl or {}).get("pending") or []:
        (due if _drainable(x, snap, t, _retry(p)) else inflight).append(x)
    return due, inflight


def _target_lag(sl, t, snap, p):
    oldest = [cl.parse_ts(x.get("queued")) for x in queued(sl, snap, t, p)[0]]
    oldest = [x for x in oldest if x]
    return int(_minutes(t, min(oldest))) if oldest else None


def target_lags(snap, t=None, p=None):
    """{unit: {target: minutes}} for every enabled target with items the next drain would hand out: the one lag
    measure the board, the sync stream and `sync status` share. Items in a batch still in flight are not late."""
    p = p or _prefs()
    t = t or cl.now()
    out = {}
    for uid, u in snap["units"].items():
        for target, sl in (u.get("sync") or {}).items():
            lag = _target_lag(sl, t, snap, p) if enabled(target, p) else None
            if lag is not None:
                out.setdefault(uid, {})[target] = lag
    return out


def lag_min(u, t, p=None, snap=None):
    p = p or _prefs()
    snap = snap or cl.load()
    lags = [x for target, sl in (u.get("sync") or {}).items() if enabled(target, p)
            for x in [_target_lag(sl, t, snap, p)] if x is not None]
    return max(lags) if lags else None


def lagging(snap, t=None, p=None):
    p = p or _prefs()
    t = t or cl.now()
    warn = (p.get("sync") or {}).get("lag_warn_min", 30)
    return [uid for uid, u in snap["units"].items() if (lag_min(u, t, p, snap) or 0) >= warn]


def due(snap, t=None, p=None):
    """Whether the periodic drain should run now. Night does not stop it: tracker comments are not Slack."""
    p = p or _prefs()
    t = t or cl.now()
    if not any(queued(sl, snap, t, p)[0] for u in snap["units"].values() for target, sl in (u.get("sync") or {}).items()
               if enabled(target, p)):
        return False
    last = cl.parse_ts(snap.get("sync_last_drain"))
    return last is None or _minutes(t, last) >= (p.get("sync") or {}).get("drain_interval_min", 15)


def _drainable(x, snap, t, retry):
    if not x.get("batch"):
        return True
    b = snap["sync_batches"].get(x["batch"]) or {}
    return b.get("state") != "open" or _minutes(t, cl.parse_ts(b.get("ts")) or t) >= retry


def footer(p):
    """prefs sync.comment_footer, else the owner's global rule; a blank or non-string value falls back."""
    f = (p.get("sync") or {}).get("comment_footer")
    return f.strip() if isinstance(f, str) and f.strip() else DEFAULT_FOOTER


def language(target, p):
    """The instruction's language name for a target; a missing or invalid prefs value falls back to the default."""
    langs = (p.get("sync") or {}).get("languages")
    code = langs.get(target) if isinstance(langs, dict) else None
    code = code.strip() if isinstance(code, str) and code.strip() else DEFAULT_LANGUAGES[target]
    return LANGUAGE_NAMES.get(code, code)


def target_tickets(target, keys):
    """A unit's ticket keys that belong to a tracker: `owner/repo#n` is a GitHub issue, anything else Jira or Linear."""
    return [k for k in keys or [] if ("#" in k) == (target == "github")]


def _repo_slug(repo):
    """owner/name for a unit's repo: the registry entry's slug, or the key when it already is one."""
    if not repo:
        return None
    import launcher
    e = launcher.load_repos().get(repo) or {}
    slug = e.get("slug") if isinstance(e, dict) else None
    return slug or (repo if "/" in repo else None)


def build_batches(snap, t=None, p=None):
    """One item per unit per target, every pending line merged, plus a status line."""
    p = p or _prefs()
    t = t or cl.now()
    sp = p.get("sync") or {}
    zone = cl.zone()
    items = []
    for uid, u in sorted(snap["units"].items()):
        for target, sl in sorted((u.get("sync") or {}).items()):
            if not enabled(target, p):
                continue  # queued before the tracker was turned off: never handed to the dispatcher
            xs = queued(sl, snap, t, p)[0]
            if not xs:
                continue
            lines = []
            for x in xs:
                when = cl.parse_ts(x["at"])
                lines.append(f'- {when.astimezone(zone).strftime("%m-%d %H:%M") if when else "?"} {x["text"]}')
            status = u.get("status")
            item = {"batch": "sb-" + uuid.uuid4().hex[:10], "unit": uid, "target": target,
                    "title": u.get("title"), "keys": [x["key"] for x in xs], "lines": lines,
                    "status_line": T("status_line", status=status_word(status),
                                     at=t.astimezone(zone).strftime("%Y-%m-%d %H:%M")),
                    "instructions": instructions(target, p), "language": language(target, p)}
            if target == "jira":
                item["tickets"] = target_tickets(target, u["keys"]["ticket"])
                if not item["tickets"]:
                    item["needs_ticket"] = True
                    item["instructions"] = ((NO_TICKET_RULE.format(**_jira(p)) if _jira_project(p) else NO_PROJECT_RULE)
                                            + " " + item["instructions"])
                states = [((snap.get("refs") or {}).get(f"ticket:{k}") or {}).get("value") or "" for k in item["tickets"]]
                if any(x.get("event") == "review.asked" for x in xs) and item["tickets"] and \
                        not all(IN_REVIEW_RE.search(st) for st in states):
                    item["transition"] = "in-review"
                    item["instructions"] = instructions(target, p) + " " + IN_REVIEW_RULE
            if target == "linear":
                item["tickets"] = target_tickets(target, u["keys"]["ticket"])
                item["state"] = LINEAR_STATE.get(status)
            if target == "github":
                item["tickets"] = target_tickets(target, u["keys"]["ticket"])
                item["repo"] = _repo_slug(u.get("repo"))
                item["state"] = "closed" if status in GITHUB_CLOSED else "open"
                item["labels"] = list(((p.get("sync") or {}).get("github") or {}).get("labels") or [])
                if not item["tickets"]:
                    item["needs_ticket"] = True
                    item["instructions"] = GITHUB_NO_TICKET + " " + item["instructions"]
            if target == "obs":
                item["obs_path"] = f'{sp.get("obs_dir", "foundry")}/{uid}.md'
            if any(x.get("event") == "report" for x in xs):
                item["instructions"] += " " + REPORT_RULE.format(lang=language(target, p))
            if target == "obs":
                item["instructions"] += (f" Write the note in {language(target, p)}: translate any line in another "
                                         "language, keep links, ticket keys, PR refs, tags and identifiers as they are.")
            if target in TRACKER_TARGETS:
                item["instructions"] += (f" Write the whole comment in {language(target, p)}: translate the lines and the "
                                         "status line, keep links, ticket keys, PR refs, tags and identifiers as they are.")
                if target == "linear":  # Jira and GitHub comments get the footer from the hook (0.1.89)
                    item["footer"] = footer(p)
                    item["instructions"] += f" End the comment with a blank line, then exactly `{item['footer']}`."
                item["instructions"] += " " + POSTED_RULE
            items.append(item)
    return items


def drain(t=None):
    p = _prefs()

    def decide(snap):
        items = build_batches(snap, t, p)
        recs = [("sync.drain", {"batches": [{k: i.get(k) for k in ("batch", "unit", "target", "keys", "transition")} for i in items]})]
        return items, recs if items else []

    items, _ = cl.transact(decide)
    return items


def fail(batch, error):
    """Record a failure; the Nth consecutive one on a target opens one MCP-reconnect to-do (never a re-auth)."""
    p = _prefs()
    limit = (p.get("sync") or {}).get("fail_todo_after", 3)

    def decide(snap):
        b = snap["sync_batches"].get(batch)
        if not b or b["state"] != "open":
            return {"error": f"no open batch {batch}"}, []
        shape = failure_shape(error)
        recs = [("sync.failed", {"batch": batch, "error": error, "shape": shape})]
        target = b["target"]
        if shape != "connection":
            return {"batch": batch, "target": target, "failures": 0, "todo": False, "shape": shape,
                    "hint": "an argument error, not a connection: fix the call (for Jira, resolve the cloudId with "
                            "getAccessibleAtlassianResources) and retry on the next drain"}, recs
        n = ((snap["sync_targets"].get(target) or {}).get("failures") or 0) + 1
        open_todo = any(td.get("kind") == "mcp-reconnect" and td.get("target") == target and not td.get("closed")
                        for td in snap["owner_todos"].values())
        todo = n >= limit and not open_todo
        if todo:
            recs.append(("owner.todo", {"kind": "mcp-reconnect", "target": target, "tid": f"mcp-reconnect:{target}",
                                        "text": T("mcp_reconnect", target=target, n=n, error=error)}))
        return {"batch": batch, "target": target, "failures": n, "todo": todo, "shape": shape}, recs

    res, _ = cl.transact(decide)
    return res


# ---------- board and CLI ----------

def T_board(key, **kw):
    import i18n
    return i18n.t("board." + key, **kw)


def board(snap, model):
    p = _prefs()
    t = cl.now()
    warn = (p.get("sync") or {}).get("lag_warn_min", 30)
    late = []
    for sec in model.get("sections") or []:
        for row in sec.get("rows") or []:
            u = snap["units"].get(row.get("id")) or {}
            lag = lag_min(u, t, p, snap)
            row["sync_lag_min"] = lag
            row["sync_text"] = T_board("sync_lag", min=lag) if lag is not None and lag >= warn else None
            if row["sync_text"]:
                late.append(row["id"])
    model["sync_lagging"] = late
    model["owner_todos"] = model_todos(snap)


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def cmd_sync(a):
    if a.sub == "enqueue":
        if a.target:
            if not enabled(a.target, _prefs()):
                return _emit({"error": f"tracker {a.target} is not enabled (prefs trackers)"}) or 2
            if a.unit not in cl.load()["units"]:
                return _emit({"error": f"no unit {a.unit}"}) or 1
            ts = cl.iso()
            cl.append("sync.needed", unit=a.unit, target=a.target, event=a.event, text=a.text, ts=ts,
                      key=_key("manual", ts, a.unit, a.target, a.text))
            return _emit({"queued": 1})
        return _emit({"queued": enqueue_for(a.unit)})
    if a.sub == "drain":
        enqueue_for(None)
        items = drain()
        if a.json:
            return _emit({"items": items})
        for i in items:
            print(f'[{i["batch"]}] {i["unit"]} → {i["target"]}')
            print("\n".join(i["lines"] + [i["status_line"]]))
        return 0
    if a.sub == "done":
        snap = cl.load()
        b = snap["sync_batches"].get(a.batch) or {}
        if b.get("state") not in ("open", "superseded"):
            return _emit({"error": f"no open batch {a.batch}"}) or 1
        if b.get("transition") and not (a.ticket_status or a.transition_error):
            # The in-review move is only known done from the ticket's status: closing without it would leave
            # nobody able to tell whether the transition ran.
            return _emit({"error": f"batch {a.batch} carried the in-review transition: close it with "
                                   "--ticket-status '<status the ticket shows now>' (after the transition), or "
                                   "--transition-error '<what happened>' when there was none or it failed. Do not "
                                   "post the comment again."}) or 1
        recs = [("sync.done", {"batch": a.batch})]
        if a.ticket_status:
            if b.get("target") not in TRACKER_TARGETS:
                return _emit({"error": "--ticket-status is for tracker (jira/linear/github) batches"}) or 1
            u = snap["units"].get(b.get("unit")) or {}
            recs += [("ticket.state", {"key": k, "status": a.ticket_status, "unit": u.get("id"), "via": "sync"})
                     for k in target_tickets(b.get("target"), (u.get("keys") or {}).get("ticket"))]
        if a.transition_error:
            u = snap["units"].get(b.get("unit")) or {}
            text = T("jira_transition_failed", tickets=", ".join(u.get("keys", {}).get("ticket") or []) or "(no ticket)",
                     error=a.transition_error)
            recs.append(("unit.report", {"id": b.get("unit"), "text": text,
                                         "eid": cl.new_id("ev", b.get("unit"), "transition", a.batch)}))
        cl.append_many(recs)
        resolved = []
        if len(recs) > 1:
            import graph
            resolved = [r[1] for r in graph.apply_resolve()]
        return _emit({"done": a.batch, "ticket_states": sum(r[0] == "ticket.state" for r in recs), "resolved": resolved,
                      **({"reported": True} if a.transition_error else {})})
    if a.sub == "failed":
        res = fail(a.batch, a.error)
        _emit(res)
        return 1 if res.get("error") else 0
    if a.sub == "reconcile":
        return _emit(reconcile())
    snap = cl.load()
    t = cl.now()
    p = _prefs()
    units = {}
    for uid, u in snap["units"].items():
        pend, fly = {}, {}
        for k, v in (u.get("sync") or {}).items():
            d, f = queued(v, snap, t, p)
            if d:
                pend[k] = len(d)
            if f:
                fly[k] = sorted({x["batch"] for x in f})
        if pend or fly:
            units[uid] = {"lag_min": lag_min(u, t, p, snap), "pending": pend, "inflight": fly}
    return _emit({"due": due(snap, t, p), "lagging": lagging(snap, t, p), "units": units,
                  "targets": snap.get("sync_targets"), "owner_todos": model_todos(snap)})


def reconcile():
    """One-shot repair: every open batch no queue item points at any more (its items were re-drained into a newer
    batch, or closed through it) is superseded, so nothing reads it as in flight. Items in a batch drained but never
    closed were never confirmed posted: they stay, listed as `unclosed` (the next drain hands them out again once
    the retry window passes)."""
    def decide(snap):
        t = cl.now()
        pointed = {x.get("batch") for u in snap["units"].values() for sl in (u.get("sync") or {}).values()
                   for x in sl.get("pending") or []}
        dropped, unclosed = [], []
        for bid, b in sorted(snap["sync_batches"].items()):
            if b["state"] != "open":
                continue
            row = {"batch": bid, "unit": b["unit"], "target": b["target"], "keys": b["keys"], "drained": b.get("ts"),
                   "age_min": int(_minutes(t, cl.parse_ts(b.get("ts")) or t))}
            (unclosed if bid in pointed else dropped).append(row)
        recs = [("sync.reconcile", {"superseded": [d["batch"] for d in dropped]})] if dropped else []
        return {"dropped": dropped, "unclosed": unclosed}, recs

    res, _ = cl.transact(decide)
    return res


def model_todos(snap):
    return [td for td in (snap.get("owner_todos") or {}).values() if not td.get("closed")]


TODO_KINDS = ("secret", "credential", "info", "go", "other")


# What may reach the owner list: only acts a human alone can do. A configuration value foundry can find is set by
# the foreman; a prod order the owner already gave in chat is recorded from the quote, never asked again.
OWNER_ONLY_RE = re.compile(r"(?i)secret|비밀|秘密|credential|자격 ?증명|token|토큰|password|비밀번호|log ?in|로그인|2fa|otp|"
                           r"mfa|prod|운영|본番|approv|승인|order|지시|choice|choose|선택|decid|결정|click|클릭|human|사람만|"
                           r"oauth|consent|동의|purchase|결제|license|라이선스")
CONFIG_RE = re.compile(r"(?i)\bprefs\s+set\b|\bjira_base\b|config\.json|\bprefs\.json\b|owner_slack_user_id|"
                       r"slack (user |channel )?id|채널 id|설정값")
RECORD_ORDER_RE = re.compile(r"(?i)(record|기록|記録).{0,40}(order|지시|指示)|(order|지시|指示).{0,40}(record|기록|記録)")


def owner_gate(text, why):
    """Why an item may not join the owner list, or None. Config with a discoverable value, a request to record an
    order the owner already gave, and anything without a one-line reason only the owner can do it are refused."""
    if CONFIG_RE.search(text):
        return ("a configuration value is not the owner's chore: find it (jira_base from the Atlassian MCP's "
                "accessible resources or a ticket URL in the journal; Slack ids from prefs) and set it with "
                "`foundry prefs set <key> <value> --by foreman --evidence \"<where it came from>\"`; if it truly "
                "cannot be found, open a decision card with the value foundry proposes")
    if RECORD_ORDER_RE.search(text):
        return ("an order the owner already gave is recorded by the foreman from the owner's words "
                "(`foundry unit order <unit> --quote \"…\"`), never asked again")
    if not why or not OWNER_ONLY_RE.search(why):
        return ("--why-owner must say in one line why only the owner can do it: a secret, a login or 2FA, a prod "
                "approval or order, a real choice between options, or a click only a human can make")
    return None


LANG_NAMES = {"ko": "Korean", "ja": "Japanese", "en": "English"}


def title_hint(tid, text, title):
    """When the to-do text is in English but the owner reads another language (prefs `language`) and no title was
    given: the line asking the foreman for one. The stored text stays as written."""
    import prefs
    lang = prefs.load().get("language") or "en"
    if title or lang == "en" or any(ord(ch) > 0x2E7F for ch in text):
        return None
    return (f"the owner reads {LANG_NAMES.get(lang, lang)}: add a short title in it, "
            f"`foundry owner todo title {tid} --title '<one line>' --by foreman`")


def cmd_owner(a):
    """`foundry owner todo add|done|list`: an ask only the owner can do, recorded so the board counts it."""
    import graph
    snap = cl.load()
    todos = snap.get("owner_todos") or {}
    if a.sub == "list":
        return _emit(model_todos(snap)) or 0
    by, err = graph.check_by(a.by, "recording an owner to-do")
    if err or by not in ("owner", "foreman"):
        print(err or f"refused: owner to-dos are recorded by the foreman (or the owner), not {by}", file=sys.stderr)
        return 2
    if a.sub == "add":
        text = (a.text or "").strip()
        if not text:
            print("refused: --text names what the owner must do", file=sys.stderr)
            return 2
        why_not = owner_gate(text, (a.why_owner or "").strip())
        if why_not:
            print(f"refused: {why_not}", file=sys.stderr)
            return 2
        unit = None if a.none else a.unit
        if unit and unit not in snap["units"]:
            print(f"refused: no unit {unit}", file=sys.stderr)
            return 1
        dup = next((t for t in todos.values() if not t.get("closed") and t.get("unit") == unit
                    and (t.get("text") or "").strip() == text), None)
        if dup:
            return _emit({"tid": dup["tid"], "existing": True}) or 0
        tid = cl.new_id("ot", unit, text)
        title = (a.title or "").strip() or None
        cl.append("owner.todo", tid=tid, kind=a.kind, unit=unit, target=unit, text=text, by=by,
                  why=a.why_owner.strip(), title=title)
        out = {"tid": tid, "unit": unit, "kind": a.kind}
        hint = title_hint(tid, text, title)
        if hint:
            out["needs_title"] = hint
        return _emit(out) or 0
    td = todos.get(a.tid)
    if not td:
        print(f"refused: no owner to-do {a.tid}", file=sys.stderr)
        return 1
    if a.sub == "title":
        title = (a.title or "").strip()
        if not title:
            print("refused: --title is the short line the owner reads", file=sys.stderr)
            return 2
        cl.append("owner.todo.title", tid=a.tid, title=title, by=by)
        return _emit({"tid": a.tid, "title": title}) or 0
    if td.get("closed"):
        return _emit({"tid": a.tid, "closed": td["closed"], "existing": True}) or 0
    cl.append("owner.todo.done", tid=a.tid, by=by, quote=(a.quote or "").strip() or None)
    return _emit({"tid": a.tid, "closed": True}) or 0


def add_cli(sp):
    o = sp.add_parser("owner").add_subparsers(dest="what", required=True)
    td = o.add_parser("todo", help="asks only the owner can do (a secret, a credential, info, a go)") \
        .add_subparsers(dest="sub", required=True)
    ad = td.add_parser("add")
    who = ad.add_mutually_exclusive_group(required=True)
    who.add_argument("--unit")
    who.add_argument("--none", action="store_true", help="not tied to a unit")
    ad.add_argument("--text", required=True, help="what the owner must do")
    ad.add_argument("--title", help="a short line in the owner's language (prefs language) shown instead of the text")
    ad.add_argument("--kind", choices=TODO_KINDS, default="other")
    ad.add_argument("--why-owner", required=True,
                    help="one line: why only the owner can do it (a secret, a login/2FA, a prod approval or order, "
                         "a real choice, a human-only click)")
    ad.add_argument("--by", choices=("owner", "foreman"))
    dn = td.add_parser("done")
    dn.add_argument("tid")
    dn.add_argument("--by", choices=("owner", "foreman"), required=True)
    dn.add_argument("--quote", help="the owner's words, when the foreman closes it on the owner's report")
    tt = td.add_parser("title", help="set the owner-language title of a to-do (its text stays as written)")
    tt.add_argument("tid")
    tt.add_argument("--title", required=True)
    tt.add_argument("--by", choices=("owner", "foreman"), required=True)
    td.add_parser("list").set_defaults(by=None)
    for name in ("add", "done", "title", "list"):
        td.choices[name].set_defaults(func=cmd_owner)
    s = sp.add_parser("sync").add_subparsers(dest="sub", required=True)
    e = s.add_parser("enqueue")
    e.add_argument("unit", nargs="?")
    e.add_argument("--target", choices=TARGETS)
    e.add_argument("--event", default="manual")
    e.add_argument("--text", default="")
    d = s.add_parser("drain")
    d.add_argument("--json", action="store_true")
    dn = s.add_parser("done")
    dn.add_argument("batch")
    dn.add_argument("--ticket-status", help="the ticket status as read after this batch landed (ticket: refs)")
    dn.add_argument("--transition-error", help="the in-review transition failed or does not exist: filed as a unit report")
    f = s.add_parser("failed")
    f.add_argument("batch")
    f.add_argument("--error", required=True)
    s.add_parser("status")
    s.add_parser("reconcile", help="one-shot: supersede open batches no queue item points at (reported as "
                                   "dropped); list drained batches never closed")
    for name in ("enqueue", "drain", "done", "failed", "status", "reconcile"):
        s.choices[name].set_defaults(func=cmd_sync)
