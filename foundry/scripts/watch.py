"""Model-free watcher: poll every open wait once, record an event for each change.

The foreman runs it as Monitor streams (`watch.py stream --kind ...`, same as `foundry watch stream`;
see watchers.py). `poll` is the one-shot form: the waits plus the sweep domain watchers (sweep.cycle).
It never spawns a model; the foreman's `foundry events wait` wakes on the events it writes.
"""

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402


# The PR state each poll saw, by ref: poll_waits reads it to clear the waits of a merged or closed PR.
PR_STATE = {}
PR_WAITS = ("pr-review", "pr-merge", "ci")
DONE_PR = ("MERGED", "CLOSED")
PR_REF_RE = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")
DEPLOY_REF_RE = re.compile(r"^deploy:([^@\s]+)@([^@\s]+)$")
TICKET_REF_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def known_apps(snap=None):
    """App names a deploy wait may name: registry repos (short names), each entry's `apps`, and every app a
    `foundry unit deployed` already recorded. Empty when nothing is registered (then no name is refused)."""
    try:
        import launcher
        rs = launcher.load_repos()
    except Exception:
        rs = {}
    out = set()
    for k, v in rs.items():
        out.add(k.rsplit("/", 1)[-1])
        out.update((v or {}).get("apps") or [] if isinstance(v, dict) else [])
    for k in ((snap or {}).get("refs") or {}):
        m = DEPLOY_REF_RE.match(k)
        if m:
            out.add(m.group(1))
    return out


def wait_problem(kind, ref, until, apps=None):
    """Why no watcher would ever resolve this wait, or None."""
    if kind == "approval":
        return None if re.fullmatch(r"ap-[\w-]+", ref or "") else f"--kind approval needs --ref ap-<id> (got {ref!r})"
    if kind in PR_WAITS:
        if not ref or not PR_REF_RE.match(ref):
            return f"--kind {kind} needs --ref owner/repo#<number> (got {ref!r}): the watcher polls that PR"
        return None
    if until:
        return None  # the watcher records an event when it is due
    if kind == "deploy-window":
        m = DEPLOY_REF_RE.match(ref or "")
        if not m:
            return ("--kind deploy-window needs --until <ISO time>, or --ref deploy:<app>@<env> that "
                    f"`foundry unit deployed` resolves (got {ref!r})")
        apps = known_apps() if apps is None else apps
        if apps and m.group(1) not in apps:
            return (f"unknown app {m.group(1)!r} in {ref}: no `foundry unit deployed --app` would match it; use one of "
                    f"{', '.join(sorted(apps))}, or --until <ISO time>")
        return None
    if kind == "jira" and TICKET_REF_RE.match(ref or "") and os.environ.get("FOUNDRY_JIRA_STATUS_CMD"):
        return None
    return f"--kind {kind} without --until is never looked at again: add --until <ISO time> for when to check"


def _t(key):
    import i18n
    return i18n.t("board." + key)


def unwatched(snap, u, apps=None):
    """Why a waiting_* unit has nothing that will move it, or None: a waiting_others unit with no wait (or only
    waits no watcher resolves), a waiting_owner unit with no open question, card, permission request or owner to-do."""
    if u.get("held") or u.get("blocked_by"):
        return None
    if u["status"] == "waiting_others":
        if not u["waits"]:
            return _t("unwatched_no_target")
        apps = known_apps(snap) if apps is None else apps
        if all(wait_problem(w["kind"], w.get("ref"), w.get("until"), apps) for w in u["waits"]):
            return _t("unwatched_unwatched")
        return None
    if u["status"] == "waiting_owner":
        uid = u["id"]
        open_q = any(d.get("unit") == uid and d.get("answer") is None for d in (snap.get("decisions") or {}).values())
        open_q = open_q or any(c.get("unit") == uid and c.get("answer") is None
                               for k in ("cards", "triage_cards") for c in (snap.get(k) or {}).values())
        open_q = open_q or any(a.get("unit") == uid and a.get("answer") is None and not a.get("gave_up")
                               for a in (snap.get("approvals") or {}).values())
        open_q = open_q or any(t.get("unit") == uid and not t.get("closed") for t in (snap.get("owner_todos") or {}).values())
        return None if open_q else _t("unwatched_no_question")
    return None


def gh_pr(ref):
    repo, num = ref.split("#", 1)
    gh = shlex.split(os.environ.get("FOUNDRY_GH", "gh"))
    r = subprocess.run([*gh, "pr", "view", num, "-R", repo, "--json",
                        "state,reviewDecision,headRefOid,headRefName,statusCheckRollup,latestReviews,title,createdAt,"
                        "isDraft,reviewRequests,mergeStateStatus"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    out = json.loads(r.stdout)
    PR_STATE[ref] = out.get("state")
    return out


RED = ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE")


def latest_checks(rollup):
    """One entry per check (workflow + name, or status context): its newest run by start then completion time.
    A run cancelled because a later run of the same check replaced it (a re-push, a label event) is not that
    check's result; the newest one is."""
    newest = {}
    for c in rollup or []:
        key = (c.get("__typename"), c.get("workflowName"), c.get("name") or c.get("context"))
        when = (c.get("startedAt") or "", c.get("completedAt") or "")
        if key not in newest or when >= newest[key][0]:
            newest[key] = (when, c)
    return [c for _, c in newest.values()]


def ci_bucket(rollup, human=None):
    """none | pending | failure | needs_approval | success. `needs_approval`: every red check is one of prefs
    review.human_approval_checks (validate-review): a reviewer's approval clears it, not a fix."""
    if human is None:
        import review
        human = review.human_checks()
    states, red = [], []
    for c in latest_checks(rollup):
        s = (c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
        if s == "CANCELLED":
            s = "PENDING"  # the newest run of this check was cancelled and nothing reran it yet: not a failure
        states.append(s)
        if s in RED:
            red.append(c.get("name") or c.get("context") or "")
    if not states:
        return "none"
    if any(s in ("", "PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING") for s in states):
        return "pending"
    if red:
        return "needs_approval" if all(n in human for n in red) else "failure"
    return "success"


def observe(w, now, snap=None):
    """Return (fingerprint, summary_if_event_worthy) for one wait, or (None, None) when it can't be polled."""
    kind = w["kind"]
    if kind == "approval":  # answered on the board / `foundry approve`; approve.wake_records clears it at once
        a = ((snap or {}).get("approvals") or {}).get(w.get("ref") or "") or {}
        if a.get("answer"):
            return "answered", f"{w['ref']} answered {a['answer']} by {a.get('by') or '?'}"
        return "open", None
    if kind == "deploy-window" and not w.get("until") and DEPLOY_REF_RE.match(w.get("ref") or ""):
        app, _, env = w["ref"][len("deploy:"):].rpartition("@")
        if ((snap or {}).get("refs") or {}).get(f"deploy:{app}@{cl.norm_env(env)}"):
            return "deployed", f"{w['ref']} is recorded as deployed"
        return "not-deployed", None
    if kind in ("pr-review", "pr-merge", "ci"):
        pr = gh_pr(w["ref"])
        if kind == "pr-merge":
            return pr["state"], f"{w['ref']} is {pr['state']}"
        if kind == "ci":
            b = ci_bucket(pr.get("statusCheckRollup"))
            fp = f"{b}@{pr['headRefOid'][:7]}"
            return fp, (f"CI on {w['ref']} ({pr['headRefOid'][:7]}): {b}"
                        if b in ("success", "failure", "needs_approval") else None)
        reviews = sorted(f"{r['author']['login']}:{r['state']}" for r in pr.get("latestReviews") or [])
        fp = f"{pr['state']}|{pr.get('reviewDecision')}|{','.join(reviews)}"
        return fp, f"{w['ref']} review changed: {pr.get('reviewDecision') or 'no decision'} ({', '.join(reviews) or 'no reviews'}), PR {pr['state']}"
    if kind in ("time", "deploy-window", "team", "jira") and w.get("until"):
        due = cl.parse_ts(w["until"])
        if due is None:  # stored before `wait add` validated it: say so once, never raise on every poll
            return "invalid-until", (f"{kind} wait {w.get('ref') or ''} has an unreadable until {w['until']!r}: "
                                     "re-add it with an ISO time (foundry wait add ... --until 2026-11-19T09:00)")
        if due.tzinfo is None:
            due = due.replace(tzinfo=cl.zone())
        if now >= due:
            return "due", f"{kind} wait {w.get('ref') or ''} reached {w['until']}: {w.get('note') or ''}".strip()
        return "not-due", None
    if kind == "jira":
        cmd = os.environ.get("FOUNDRY_JIRA_STATUS_CMD")  # optional: a command printing the issue's status
        if not cmd:
            return None, None
        r = subprocess.run([cmd, w["ref"]], capture_output=True, text=True, timeout=60)
        st = r.stdout.strip()
        return st, f"{w['ref']} is now {st}"
    return None, None


def _moot_asks(snap, uid, ref, state):
    """`decision.answer` records withdrawing the unit's open "who reviews <ref>" asks once that PR is done."""
    num = "#" + ref.split("#", 1)[-1]
    out = []
    for d in (snap.get("decisions") or {}).values():
        q = d.get("question") or ""
        if d.get("answer") is None and d.get("unit") == uid and d["key"].endswith(":reviewer") and \
                (ref in q or f"{num} " in q + " " or f"{num})" in q):
            why = f"{ref} {state.lower()}"
            out.append(("decision.answer", {"key": d["key"], "answer": f"withdrawn: {why}", "unit": uid,
                                            "by": "foundry", "withdrawn": why}))
    return out


def poll_waits(snap, now, errors=None):
    """(records, new events) for every open wait whose observation changed.

    A merged or closed PR also clears the unit's review / merge / CI waits on it (the change event is still
    recorded) and withdraws the unit's open reviewer ask about it."""
    records = []
    errors = [] if errors is None else errors
    done = set()
    for u in snap["units"].values():
        for w in u["waits"]:
            try:
                fp, summary = observe(w, now, snap)
            except Exception as ex:  # one bad PR must not stop the rest
                errors.append(f"{u['id']}/{w['wid']}: {ex}")
                continue
            finished = w["kind"] in PR_WAITS and PR_STATE.get(w.get("ref")) in DONE_PR
            if fp in ("deployed", "answered"):
                records.append(("wait.clear", {"id": u["id"], "wid": w["wid"]}))
                finished = True
            elif finished:
                records.append(("wait.clear", {"id": u["id"], "wid": w["wid"]}))
                if (u["id"], w["ref"]) not in done:
                    done.add((u["id"], w["ref"]))
                    records += _moot_asks(snap, u["id"], w["ref"], PR_STATE[w["ref"]])
            if fp is None or fp == w.get("fingerprint"):
                continue
            records.append(("wait.seen", {"id": u["id"], "wid": w["wid"], "fingerprint": fp}))
            first_look = w.get("fingerprint") is None and w["kind"] in ("pr-review", "pr-merge")
            if summary and not first_look:
                eid = cl.new_id("ev", u["id"], w["wid"], fp)
                records.append(("event", {"eid": eid, "unit": u["id"], "wid": w["wid"], "kind": w["kind"],
                                          "ref": w.get("ref"), "summary": summary}))
            parent = u.get("parent")
            if parent and parent in snap["units"] and (fp == "deployed" or (finished and PR_STATE.get(w.get("ref")) == "MERGED")):
                records.append(("event", {"eid": cl.new_id("ev", parent, "child", u["id"], w["wid"], fp),
                                          "unit": parent, "wid": None, "kind": "child", "ref": u["id"],
                                          "summary": f"child unit {u['id']}: {summary or w.get('ref')}"}))
            if finished:  # the clear goes after this wait's event
                records.remove(("wait.clear", {"id": u["id"], "wid": w["wid"]}))
                records.append(("wait.clear", {"id": u["id"], "wid": w["wid"]}))
    return records, [r for op, r in records if op == "event"]


def poll(notify=False):
    snap = cl.load()
    errors = []
    records, new = poll_waits(snap, cl.now(), errors)
    if records:
        cl.append_many(records)
    changed = bool(records)
    try:  # the domain watchers (sessions, worktrees, docker, PC pressure): cheap, no model
        import sweep
        res = sweep.cycle(notify)
        if res["event"]:
            new.append(res["event"])
        changed = changed or bool(res["event"] or res["cleanup"]) or res["level"] != snap["pressure"]["level"]
    except Exception as ex:  # a broken sweep must not stop the wait polling
        errors.append(f"sweep: {ex}")
    if changed:
        import board
        board.write_all(cl.load())
    with open(cl.path("watch.log"), "a") as f:
        f.write(json.dumps({"ts": cl.iso(), "events": len(new), "errors": errors}, ensure_ascii=False) + "\n")
    if notify and new:
        msg = f"foundry: {len(new)} event(s) for " + ", ".join(sorted({e['unit'] for e in new}))
        cl.desktop_notify("foundry", msg)
    return new, errors


def main():
    if sys.argv[1:2] == ["stream"]:
        import foundry
        return foundry.main(["watch", *sys.argv[1:]])
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["poll"])
    p.add_argument("--notify", action="store_true")
    a = p.parse_args()
    new, errors = poll(a.notify)
    print(json.dumps({"events": new, "errors": errors}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
