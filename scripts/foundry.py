"""foundry: the only writer of foundry state. Agents call this; they never edit state files."""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import prefs  # noqa: E402

EXIT_ANSWERED, EXIT_PENDING, EXIT_ESCALATE = 0, 3, 10
LEAD_FOR_KIND = {"project": "foundry:line-lead", "repo": "foundry:shop-boss"}
# Modules that add their own subcommands through add_cli(subparsers); each sets `func` on its parsers.
EXTENSIONS = cl.MODULES


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "unit"


# ---------- units ----------

def find_units(snap, text="", ticket=(), pr=(), unit=None, topic=None, repo=None):
    """Units an instruction is about, strongest key first: id, ticket/PR, topic, shop-boss."""
    units = snap["units"]
    if unit:
        return [units[unit]] if unit in units else []
    tks = set(ticket) | set(cl.tickets(text))
    prs = set(pr) | set(cl.norm_pr(text))
    hits = [u for u in units.values() if u["kind"] != "foreman"
            and (tks & set(u["keys"]["ticket"]) or prs & set(u["keys"]["pr"]))]
    if hits:
        return hits
    words = [topic] if topic else []
    if words:
        hits = [u for u in units.values() if any(w and (w in u["keys"]["topic"] or w.lower() in u["title"].lower()) for w in words)]
    return hits


def new_unit_dups(snap, a):
    """(units refusing `unit new`, parent id). A PR held elsewhere, or the same ticket in the same repo, is a
    duplicate. The same ticket in another repo is that ticket's next repo: the new unit becomes a child of the
    first unit holding it (or of `--parent`), with its own lead and PR."""
    hits = find_units(snap, a.title, a.ticket, a.pr)
    prs = set(a.pr) | set(cl.norm_pr(a.title))
    dup, other = [], []
    for u in hits:
        same_pr = bool(prs & set(u["keys"]["pr"]))
        (dup if same_pr or not a.repo or not u.get("repo") or u.get("repo") == a.repo else other).append(u)
    parent = a.parent
    if not parent and other:
        first = sorted(other, key=lambda u: (u.get("created") or "", u["id"]))[0]
        parent = first.get("parent") or first["id"]
    return dup, parent


def cmd_unit(a):
    snap = cl.load()
    if a.sub == "new":
        uid = a.id or slug(a.title)
        bad = cl.unit_id_error(uid)
        if bad:
            return emit({"error": bad}) or 2
        if uid in snap["units"]:
            return emit({"error": f"unit {uid} exists", "unit": snap["units"][uid]}) or 1
        if a.parent and a.parent not in snap["units"]:
            return emit({"error": f"unknown parent unit {a.parent}"}) or 2
        if uid.startswith("release-") and a.repo:
            import release
            if release.batch_entry(a.repo, prefs.load()) is not None:
                return emit({"error": f"{a.repo} releases through foundry: `foundry release new {a.repo} --pr "
                                      "<owner/repo#n> ...` makes the release unit (its release record gates the dev "
                                      "stage and the prod deploy approval); never a hand-made unit"}) or 2
        dup, parent = new_unit_dups(snap, a)
        if dup and not a.force:
            emit({"error": "an existing unit already holds this ticket/PR in the same repo; attach to it instead "
                           "(foundry inbox route) or pass --force", "units": [u["id"] for u in dup]})
            return 1
        why = prefs.tracker_refusal(a.tracker)
        if why:
            print(why, file=sys.stderr)
            return 2
        lead = a.lead or LEAD_FOR_KIND[a.kind]
        recs = [("unit.new", {"id": uid, "kind": a.kind, "title": a.title, "repo": a.repo, "lead": lead,
                              "ticket": a.ticket, "pr": a.pr, "topic": a.topic,
                              **({"parent": parent} if parent else {})})]
        if a.tracker:
            recs.append(("unit.tracker", {"id": uid, "tracker": a.tracker}))
        if a.status:  # e.g. queue / waiting_owner for work that should not start yet
            recs.append(("unit.status", {"id": uid, "status": a.status, "note": a.note or "created"}))
        elif a.note:
            recs.append(("unit.checkpoint", {"id": uid, "note": a.note}))
        cl.append_many(recs)
        return emit({"unit": uid, "lead": lead, "tracker": a.tracker, **({"parent": parent} if parent else {}),
                     "next": f"foundry unit launch {uid}"}) or 0
    if a.sub == "import":
        import import_units
        argv = [a.file, "--source", a.source] + (["--dry-run"] if a.dry_run else [])
        return import_units.main(argv)
    if a.sub == "keys":
        cl.append("unit.keys", id=a.id, ticket=a.ticket, pr=a.pr, topic=a.topic)
        return 0
    if a.sub == "status":
        if a.status is None:
            import launcher
            return launcher.cmd_status(a)
        import graph  # alias of `unit set <id> --status S`: same role scope and verify gate
        return graph.set_status(a.id, a.status, a.by, a.note, quote=a.quote)
    if a.sub == "report":
        # A lead's report must survive a dead foreman: the journal event wakes `foundry events wait`.
        if a.id not in snap["units"]:
            print(cl.unknown_unit(a.id, snap), file=sys.stderr)
            return 2
        cl.append("unit.report", id=a.id, text=a.text, eid=cl.new_id("ev", a.id, "report", a.text))
        import decide
        flag = decide.uncarded_question(a.text, snap, a.id)
        if flag:  # recorded all the same: a report must never be lost to this check
            print(f"[foundry] {flag}", file=sys.stderr)
        return emit({"reported": a.id, **({"flag": flag} if flag else {})}) or 0
    if a.sub == "tracker":
        why = prefs.tracker_refusal(a.tracker)
        if why:
            print(why, file=sys.stderr)
            return 2
        cl.append("unit.tracker", id=a.id, tracker=a.tracker)
        return 0
    if a.sub == "checkpoint":
        cl.append("unit.checkpoint", id=a.id, note=a.note)
        return 0
    if a.sub == "find":
        return emit([u["id"] for u in find_units(snap, a.text or "", a.ticket, a.pr, topic=a.topic)]) or 0
    if a.sub == "show":
        return emit(snap["units"].get(a.id)) or 0
    if a.sub == "list":
        return emit({u["id"]: [u["status"], u["title"]] for u in snap["units"].values()}) or 0
    if a.sub == "brief":
        print(brief(snap, a.id))
        return 0


def brief(snap, uid):
    """The (re-)spawn prompt for a lead is its handoff: one renderer for every restart."""
    import handoff
    return handoff.render(snap, uid)


# ---------- handoff ----------

def cmd_handoff(a):
    import handoff
    snap = cl.load()
    if a.sub == "write":
        bad = None if a.id == handoff.ORCH else cl.unit_id_error(a.id)
        if bad:
            print(bad, file=sys.stderr)
            return 2
        if a.id != handoff.ORCH and a.id not in snap["units"]:
            print(cl.unknown_unit(a.id, snap), file=sys.stderr)
            return 2
        recs = [("handoff.write", {"id": a.id, "goal": a.goal, "next": a.next, "files": a.file, "summary": a.summary})]
        if a.respawn:
            recs.append(("event", {"eid": cl.new_id("ev", a.id, "respawn"), "unit": a.id, "wid": None, "kind": "respawn",
                                   "ref": None, "summary": "phase finished: re-spawn the lead fresh from its handoff"}))
        snap = cl.append_many(recs)
        p = handoff.save(snap, a.id, "respawn" if a.respawn else "write")
        return emit({"handoff": p, "respawn": a.respawn}) or 0
    if a.sub == "show":
        print(handoff.render(snap, a.id))
        return 0


# ---------- waits and events ----------

def cmd_wait(a):
    if a.sub == "add":
        if a.until is not None and cl.parse_ts(a.until) is None:
            print(f"refused: --until {a.until!r} is not an ISO timestamp (e.g. 2026-11-19T09:00:00+09:00)", file=sys.stderr)
            return 2
        import watch
        why = watch.wait_problem(a.kind, a.ref, a.until, watch.known_apps(cl.load()))
        if why:
            print(f"refused: {why}", file=sys.stderr)
            return 2
        if a.kind == "ci":
            try:
                done = watch.ci_bucket(watch.gh_pr(a.ref).get("statusCheckRollup"))
            except Exception:  # unreadable now: the watcher looks again
                done = None
            if done in ("success", "failure", "needs_approval"):
                hint = ("`foundry wait add {id} --kind mergeable --ref {ref}` when the merge state is UNKNOWN or "
                        "BLOCKED, or `--kind pr-review` for a review" if done == "success" else "fix or rerun the checks")
                print(f"refused: CI on {a.ref} has already finished ({done}); a ci wait would never fire: "
                      + hint.format(id=a.id, ref=a.ref), file=sys.stderr)
                return 2
        wid = a.wid or f"{a.kind}:{a.ref or a.until}"
        cl.append("wait.add", id=a.id, wid=wid, kind=a.kind, ref=a.ref, until=a.until, note=a.note, fingerprint=None)
        return emit({"wait": wid}) or 0
    if a.sub == "clear":
        cl.append("wait.clear", id=a.id, wid=a.wid)
        return 0
    if a.sub == "list":
        snap = cl.load()
        return emit({u["id"]: u["waits"] for u in snap["units"].values() if u["waits"]}) or 0


EVENTS_WAIT_MAX_S = 3600


def cmd_events(a):
    if a.sub == "pending":
        return emit([e for e in cl.load()["events"] if not e["consumed"]]) or 0
    if a.sub == "consume":
        cl.append("event.consumed", eids=a.eids)
        return 0
    if a.sub == "wait":
        # Exits (waking the foreman's background task) once an unconsumed event exists. One per machine, and never
        # longer than EVENTS_WAIT_MAX_S: a newer wait takes over, so an orphaned one cannot run forever.
        deadline = time.time() + min(a.timeout, EVENTS_WAIT_MAX_S)
        cl.claim_instance("events-wait")
        try:
            while time.time() < deadline:
                if not cl.owns_instance("events-wait"):
                    print("superseded by a newer events wait")
                    return 0
                ev = [e for e in cl.load()["events"] if not e["consumed"]]
                if ev:
                    return emit(ev) or 0
                time.sleep(min(a.interval, max(0.0, deadline - time.time())))
        finally:
            cl.release_instance("events-wait")
        print("no events")
        return 0


# ---------- owner feedback routing ----------

def cmd_inbox(a):
    snap = cl.load()
    if a.sub == "route":
        hits = find_units(snap, a.text, a.ticket, a.pr, unit=a.unit, topic=a.topic)
        if len(hits) == 1:
            u = hits[0]
            iid = cl.new_id("in", u["id"], a.text)
            cl.append("inbox.add", id=u["id"], iid=iid, text=a.text, **{"from": a.source})
            # A finished unit's old session is never woken: the inbox is applied on its next launch.
            live = u["status"] != "done" and cl.lead_live(snap, u)
            return emit({"attached": u["id"], "iid": iid, "lead_running": live, "session": u.get("session") if live else None,
                         "next": (f"SendMessage(to={u['session']!r}) with the same text" if live
                                  else f"queued; applied on the next `foundry unit launch|resume {u['id']}`")}) or 0
        if len(hits) > 1:
            emit({"ambiguous": [h["id"] for h in hits], "next": "re-run with --unit <id>"})
            return 4
        if a.new:
            uid = a.new
            bad = cl.unit_id_error(uid)
            if bad:
                emit({"error": bad})
                return 2
            cl.append_many([("unit.new", {"id": uid, "kind": a.kind, "title": a.title or a.text[:60], "repo": a.repo,
                                          "lead": LEAD_FOR_KIND[a.kind], "ticket": a.ticket, "pr": a.pr,
                                          "topic": [a.topic] if a.topic else []}),
                            ("inbox.add", {"id": uid, "iid": cl.new_id("in", uid, a.text), "text": a.text, "from": a.source})])
            return emit({"created": uid}) or 0
        emit({"no_match": True, "units": {u["id"]: u["title"] for u in snap["units"].values() if u["status"] != "done"},
              "next": "pick one with --unit, or pass --new <id> only if this is really new work"})
        return 5
    if a.sub == "supersede":
        import graph
        by, err = graph.check_by(a.by, "superseding an inbox instruction")
        if err:
            print(err, file=sys.stderr)
            return 2
        hits = [(u["id"], i) for u in snap["units"].values() for i in u.get("inbox") or []
                if i["iid"] in a.iids and (not a.unit or u["id"] == a.unit)]
        if not hits:
            emit({"error": f"no inbox item {', '.join(a.iids)}" + (f" in {a.unit}" if a.unit else "")})
            return 2
        if by in ("lead", "shop-boss") and any(not graph.own_unit(snap, by, uid) for uid, _ in hits):
            emit({"error": f"refused: a {by} supersedes only its own unit's inbox"})
            return 2
        by_unit = {}
        for uid, i in hits:
            by_unit.setdefault(uid, []).append(i["iid"])
        cl.append_many([("inbox.supersede", {"id": uid, "iids": iids, "by": by, "reason": a.reason})
                        for uid, iids in sorted(by_unit.items())])
        return emit({"superseded": by_unit, "by": by, "reason": a.reason}) or 0
    if a.sub == "take":
        u = snap["units"][a.id]
        pending = [i for i in u["inbox"] if not i["delivered"]]
        if pending:
            cl.append("inbox.delivered", id=a.id, iids=[i["iid"] for i in pending])
        return emit(pending) or 0


# ---------- decisions ----------

def cmd_decision(a):
    snap = cl.load()
    if a.sub == "ask" and getattr(a, "question_file", None):
        import decide
        try:
            a.question = decide.read_text(a.question_file, "--question-file")
        except ValueError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
    if a.sub == "ask":
        d = snap["decisions"].get(a.key)
        if d and d["answer"] is not None:
            emit({"status": "answered", "key": a.key, "answer": d["answer"], "answered": d["answered"],
                  "note": "Already decided by the owner. Use this answer; do not ask again."})
            return EXIT_ANSWERED
        if d:
            emit({"status": "pending", "key": a.key, "asked": d["asked"],
                  "note": "Already asked and waiting. Do not ask again; continue the unblocked parts."})
            return EXIT_PENDING
        why = cl.card_text_refusal("question", a.question, snap)
        if why:
            print(why, file=sys.stderr)
            return 2
        import decide
        try:
            refs = decide.parse_refs(a.ref)
        except ValueError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        cl.append("decision.ask", key=a.key, unit=a.unit, question=a.question, **({"refs": refs} if refs else {}))
        emit({"status": "escalate", "key": a.key,
              "next": "SendMessage(to='main') with this question; the foreman relays it and sends a push."})
        return EXIT_ESCALATE
    if a.sub == "answer":
        import decide
        import graph
        by, err = graph.check_by(None, f"answering decision {a.key}")
        if err or by not in ("owner", "foreman"):
            print(err or f"refused: answering decision {a.key} is the owner's act; only the foreman may relay "
                  "the owner's answer (with --quote), not " + str(by), file=sys.stderr)
            return 2
        quote = (a.quote or "").strip() or None
        if by == "foreman" and not quote:
            print(f"refused: the foreman records decision {a.key} only as a relay: pass --quote with the owner's "
                  "exact words from chat", file=sys.stderr)
            return 2
        note = decide.RELAY_NOTE if by == "foreman" else None
        if a.key in (snap.get("cards") or {}):
            rc, out = decide.answer(a.key, a.answer, by, note, quote)
        else:
            rc, out = decide.answer_ask(a.key, a.answer, by, note, quote)
        emit(out)
        return rc
    if a.sub == "list":
        return emit(list(snap["decisions"].values())) or 0


# ---------- sends ----------

def cmd_send(a):
    if a.sub == "allow-now":
        err = cl.require_owner("lifting Slack quiet hours (send allow-now)")
        if err:
            print(err, file=sys.stderr)
            return 2
        until =(cl.now() + dt.timedelta(minutes=a.minutes)).isoformat(timespec="seconds")
        cl.append("send.override", channel=a.channel, until=until, reason=a.reason)
        return emit({"override_until": until}) or 0
    if a.sub == "list":
        return emit(cl.load()["sends"]) or 0


def cmd_board(a):
    import board
    if a.action == "archive":
        rc = board.cmd_archive(a)
        if not rc and not a.dry_run:
            board.write_all(cl.load())
        return rc
    snap = cl.load()
    if a.format == "text" and getattr(a, "tab", "main") == "pr":
        print(board.pr_tab_text(board.model(snap)))
    elif a.format == "text":
        print(board.text_of(board.model(snap, archived_view=a.archived)))
    elif a.format == "json":
        print(json.dumps(board.model(snap, archived_view=a.archived), ensure_ascii=False))
    else:
        print(board.write_all(snap))
    return 0


def cmd_compact(a):
    snap = cl.compact()
    print(f"compacted: {len(snap['units'])} units, {len(snap['sends'])} live sends")
    return 0


def cmd_rebuild(a):
    snap = cl.rebuild()
    import board
    board.write_all(snap)
    print(f"rebuilt: {len(snap['units'])} units, {len(snap['agents'])} agents")
    return 0


def graph_roles():
    import graph
    return graph.ROLES


def parser():
    p = argparse.ArgumentParser(prog="foundry")
    p.add_argument("--version", action="version", version=f"foundry {cl.plugin_version()}")
    sp = p.add_subparsers(dest="cmd", required=True)

    u = sp.add_parser("unit").add_subparsers(dest="sub", required=True)
    n = u.add_parser("new")
    n.add_argument("--id"); n.add_argument("--title", required=True)
    n.add_argument("--kind", choices=["project", "repo"], default="project")
    n.add_argument("--lead", choices=cl.LEAD_TYPES); n.add_argument("--repo")
    n.add_argument("--ticket", action="append", default=[]); n.add_argument("--pr", action="append", default=[])
    n.add_argument("--topic", action="append", default=[]); n.add_argument("--force", action="store_true")
    n.add_argument("--parent", help="the unit this one is a repo of: same ticket, its own lead and PR")
    n.add_argument("--tracker", choices=["jira", "linear", "github", "none"])
    n.add_argument("--status", choices=cl.STATUSES); n.add_argument("--note")
    im = u.add_parser("import"); im.add_argument("file"); im.add_argument("--source", default="import")
    im.add_argument("--dry-run", action="store_true")
    rp = u.add_parser("report"); rp.add_argument("id"); rp.add_argument("--text", required=True)
    tr = u.add_parser("tracker"); tr.add_argument("id"); tr.add_argument("tracker", choices=["jira", "linear", "github", "none"])
    k = u.add_parser("keys"); k.add_argument("id")
    k.add_argument("--ticket", action="append", default=[]); k.add_argument("--pr", action="append", default=[])
    k.add_argument("--topic", action="append", default=[])
    # `unit status <id> <status>` records a status; `unit status [<id>] [--refresh]` lists lead sessions (launcher).
    s = u.add_parser("status"); s.add_argument("id", nargs="?"); s.add_argument("status", nargs="?"); s.add_argument("--note")
    s.add_argument("--refresh", action="store_true")
    s.add_argument("--by", choices=graph_roles())
    s.add_argument("--quote", help="the foreman acting on the owner's word: the owner's exact chat words")
    c = u.add_parser("checkpoint"); c.add_argument("id"); c.add_argument("--note", required=True)
    f = u.add_parser("find"); f.add_argument("--text"); f.add_argument("--topic")
    f.add_argument("--ticket", action="append", default=[]); f.add_argument("--pr", action="append", default=[])
    for name in ("show", "brief"):
        u.add_parser(name).add_argument("id")
    u.add_parser("list")
    for mod in cl.modules():
        if hasattr(mod, "add_unit_cli"):
            mod.add_unit_cli(u)

    w = sp.add_parser("wait").add_subparsers(dest="sub", required=True)
    wa = w.add_parser("add"); wa.add_argument("id")
    wa.add_argument("--kind", required=True, choices=["pr-review", "pr-merge", "ci", "mergeable", "jira", "time", "team", "deploy-window"])
    wa.add_argument("--ref"); wa.add_argument("--until"); wa.add_argument("--note"); wa.add_argument("--wid")
    wc = w.add_parser("clear"); wc.add_argument("id"); wc.add_argument("--wid")
    w.add_parser("list")

    e = sp.add_parser("events").add_subparsers(dest="sub", required=True)
    e.add_parser("pending")
    ec = e.add_parser("consume"); ec.add_argument("eids", nargs="+")
    ew = e.add_parser("wait"); ew.add_argument("--timeout", type=int, default=3300); ew.add_argument("--interval", type=int, default=20)

    i = sp.add_parser("inbox").add_subparsers(dest="sub", required=True)
    r = i.add_parser("route"); r.add_argument("--text", required=True); r.add_argument("--unit"); r.add_argument("--topic")
    r.add_argument("--ticket", action="append", default=[]); r.add_argument("--pr", action="append", default=[])
    r.add_argument("--new", metavar="UNIT_ID"); r.add_argument("--kind", choices=["project", "repo"], default="project")
    r.add_argument("--title"); r.add_argument("--repo"); r.add_argument("--source", default="owner")
    i.add_parser("take").add_argument("id")
    sup = i.add_parser("supersede", help="retire stale inbox instructions so a resume never re-reads them")
    sup.add_argument("iids", nargs="+"); sup.add_argument("--unit"); sup.add_argument("--reason", required=True)
    sup.add_argument("--by", choices=("owner", "foreman", "lead", "shop-boss"))

    d = sp.add_parser("decision").add_subparsers(dest="sub", required=True)
    da = d.add_parser("ask"); da.add_argument("--key", required=True); da.add_argument("--unit")
    dq = da.add_mutually_exclusive_group(required=True); dq.add_argument("--question")
    dq.add_argument("--question-file", help="the question as written in a file: line breaks and `- ` bullets kept (개조식)")
    da.add_argument("--ref", action="append", default=[], metavar="pr:OWNER/REPO#N|approval:RID|unit:ID",
                    help="what the question hinges on: once every ref is settled foundry withdraws it as moot")
    dn = d.add_parser("answer"); dn.add_argument("--key", required=True); dn.add_argument("--answer", required=True)
    dn.add_argument("--quote", help="the owner's exact words from chat: required when the foreman relays them")
    d.add_parser("list")

    se = sp.add_parser("send").add_subparsers(dest="sub", required=True)
    # Owner-only: no autoMode rule covers it, so a model's call always reaches a human permission prompt.
    an = se.add_parser("allow-now"); an.add_argument("--channel", required=True); an.add_argument("--reason", required=True)
    an.add_argument("--minutes", type=int, default=10)
    se.add_parser("list")

    h = sp.add_parser("handoff").add_subparsers(dest="sub", required=True)
    hw = h.add_parser("write"); hw.add_argument("id"); hw.add_argument("--goal"); hw.add_argument("--next")
    hw.add_argument("--file", action="append", default=[]); hw.add_argument("--summary")
    hw.add_argument("--respawn", action="store_true")
    h.add_parser("show").add_argument("id")

    b = sp.add_parser("board"); b.add_argument("--format", choices=["text", "json", "html"], default="text")
    b.add_argument("--tab", choices=["main", "pr"], default="main", help="pr: the PR tab")
    b.add_argument("--archived", action="store_true", help="the archive view: finished units moved off the board")
    b.add_argument("action", nargs="?", choices=["archive"],
                   help="archive: move 릴리스 완료/완료/취소 units to the archive view (records only, deletes nothing)")
    b.add_argument("--older", metavar="3d|12h", help="archive: units closed at least this long ago")
    b.add_argument("--all-done", action="store_true", help="archive: every finished unit, any age")
    b.add_argument("--dry-run", action="store_true"); b.add_argument("--by"); b.add_argument("--quote")
    sp.add_parser("rebuild")
    sp.add_parser("compact")
    for mod in cl.modules():
        if hasattr(mod, "add_cli"):
            mod.add_cli(sp)
    return p


def with_owner_by(argv):
    """argv plus `--by owner` when the mod ran this for the owner and the verb takes --by but none was given."""
    if not cl.owner_attested() or "--by" in argv:
        return argv
    import contextlib
    import io
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            _, extra = parser().parse_known_args(argv + ["--by", "owner"])
    except SystemExit:
        return argv
    return argv + ["--by", "owner"] if not extra else argv


def main(argv=None):
    a = parser().parse_args(with_owner_by(list(sys.argv[1:] if argv is None else argv)))
    if getattr(a, "func", None):
        rc = a.func(a) or 0
        import board
        board.write_all(cl.load())
        return rc
    fn = {"unit": cmd_unit, "wait": cmd_wait, "events": cmd_events, "inbox": cmd_inbox, "decision": cmd_decision,
          "send": cmd_send, "board": cmd_board, "rebuild": cmd_rebuild, "handoff": cmd_handoff,
          "compact": cmd_compact}[a.cmd]
    rc = fn(a) or 0
    if a.cmd not in ("board", "rebuild", "compact"):
        import board
        board.write_all(cl.load())
    return rc


if __name__ == "__main__":
    sys.exit(main())
