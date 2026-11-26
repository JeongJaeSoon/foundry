"""Intake tiers for incoming work (Jira, Linear, GitHub, Slack).

A  continuation of an existing unit, or a kind the owner promoted: auto-start (queued, mode full), capped per day.
B  anything else actionable: a unit in mode "plan" (investigate and plan only) plus a proceed/change/drop card.
C  listed only (LISTED_KINDS).
Tracker and GitHub text is data: the title is stored truncated and never interpreted.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import graph  # noqa: E402
import prefs  # noqa: E402

TITLE_MAX = 120
SOURCES = ("jira", "linear", "github", "slack")
# Listed-only kinds; a prefs key would collide with other hands' prefs edits, so it stays here for now.
LISTED_KINDS = ("fyi", "notification")

STATE = {"intake": {"items": {}, "cards": {}, "scan": {}, "ignored": {}}}
# GitHub work that names the owner: review requests to them and issues assigned to them, open, any org.
SCAN_QUERIES = (("review", ("search", "prs", "--review-requested", "@me", "--state", "open")),
                ("issue", ("search", "issues", "--assignee", "@me", "--state", "open")))
SCAN_FIELDS = "repository,number,title,url,updatedAt,author,labels"
# A review request to the owner by name; `--review-requested @me` also returns requests to the owner's teams.
PERSONAL_REVIEWS = ("search", "prs", "user-review-requested:@me", "--state", "open")
IGNORE_FIELDS = ("author", "title", "repo", "label")
SEEN_MAX = 2000
UNIT_FIELDS = {"mode": "full", "intake": None}


def clean_title(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()[:TITLE_MAX]


def item_key(source, ref):
    return f"{source}:{ref}"


def unit_id_for(source, ref):
    slug = re.sub(r"[^a-z0-9]+", "-", str(ref).lower()).strip("-")[:40] or "item"
    uid = f"{source}-{slug}"
    bad = cl.unit_id_error(uid)
    if bad:
        raise ValueError(bad)
    return uid


def autostarts_today(snap, p, now=None):
    day = (now or cl.now()).astimezone(prefs.tz(p)).date()
    n = 0
    for it in (snap.get("intake") or {}).get("items", {}).values():
        t = cl.parse_ts(it.get("ts"))
        if it.get("tier") == "A" and it.get("autostart") and t and t.astimezone(prefs.tz(p)).date() == day:
            n += 1
    return n


def tier_for(snap, p, kind, continues):
    """(tier, autostart, why)."""
    ip = p.get("intake") or {}
    if kind in LISTED_KINDS:
        return "C", False, f"kind {kind} is listed only"
    if continues and continues in snap["units"]:
        return "A", False, f"continues unit {continues}"
    if kind in (ip.get("promoted_kinds") or []):
        cap = ip.get("autostart_max_per_day", 10)
        if autostarts_today(snap, p) >= cap:
            return "B", False, f"auto-start cap {cap}/day reached"
        return "A", True, f"kind {kind} is promoted"
    return "B", False, "not promoted: plan first"


def open_card(uid, question, options, ref):
    """Through the decide module when present; else an `intake.card` record the board shows."""
    payload = {"unit": uid, "question": question, "recommended": "proceed", "key": f"intake:{ref}", "asked_by": "intake",
               "options": [{"key": k, "label": k, "description": d, "pros": None, "cons": None, "preview": None}
                           for k, d in options]}
    try:
        import decide
        if hasattr(decide, "open_card"):
            return decide.open_card(**payload)[1]["id"]
    except (ImportError, TypeError):
        pass
    cid = cl.new_id("ic", uid, ref)
    return cid, ("intake.card", {"id": cid, **payload})


def add(source, ref, title, kind, continues=None, scanned=False):
    p = prefs.load()
    title = clean_title(title)

    def decide_(snap):
        key = item_key(source, ref)
        prev = (snap.get("intake") or {}).get("items", {}).get(key)
        if prev:
            return dict(prev, duplicate=True), []
        tier, autostart, why = tier_for(snap, p, kind, continues)
        uid = continues if tier == "A" and continues in snap["units"] else (
            unit_id_for(source, ref) if tier != "C" else None)
        item = {"key": key, "source": source, "ref": ref, "title": title, "kind": kind, "tier": tier,
                "unit": uid, "autostart": autostart, "why": why, **({"scanned": True} if scanned else {})}
        recs = []
        if uid and uid not in snap["units"]:
            recs.append(("unit.new", {"id": uid, "kind": "project", "title": title or ref,
                                      "ticket": cl.norm_pr(str(ref)) if kind == "issue" else cl.tickets(str(ref)),
                                      "pr": [] if kind == "issue" else cl.norm_pr(str(ref))}))
        if uid:
            recs.append(("unit.mode", {"id": uid, "mode": "full" if tier == "A" else "plan",
                                       "intake": {"tier": tier, "kind": kind, "source": source}}))
        recs.append(("intake.item", item))
        return item, recs

    item, _ = cl.transact(decide_)
    if item.get("tier") == "B" and not item.get("duplicate"):
        res = open_card(item["unit"], f"[{source}] {item['title'] or ref}: proceed, change, or drop?",
                        [("proceed", "promote to full mode and start"), ("change", "reply with the change first"),
                         ("drop", "close the unit")], ref)
        if isinstance(res, tuple):
            cid, rec = res
            cl.append(rec[0], **rec[1])
            res = cid
        item["card"] = res
    return item


def promote(uid, kind_too=False):
    snap = cl.load()
    u = snap["units"].get(uid)
    if not u:
        return None, f"no unit {uid}"
    kind = (u.get("intake") or {}).get("kind")
    cl.append_many([("unit.mode", {"id": uid, "mode": "full"}), ("intake.promoted", {"unit": uid, "kind": kind})])
    if kind_too and kind:
        _promote_kind(kind)
    # One click: the open proceed/change/drop card is answered "proceed" by the owner.
    try:
        import decide
        for c in (snap.get("cards") or {}).values():
            if c.get("unit") == uid and str(c.get("key") or "").startswith("intake:") and c.get("answer") is None:
                decide.answer(c["id"], "proceed", "owner")
    except ImportError:
        pass
    return {"unit": uid, "mode": "full", "kind": kind, "kind_promoted": bool(kind_too and kind)}, None


def _promote_kind(kind):
    path = prefs.user_path()
    user = {}
    if os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
    ip = user.setdefault("intake", {})
    old = list(ip.get("promoted_kinds") or prefs.defaults()["intake"]["promoted_kinds"])
    if kind in old:
        return
    ip["promoted_kinds"] = old + [kind]
    cl.write_atomic(path, json.dumps(user, ensure_ascii=False, indent=1))
    cl.append("prefs.changed", key="intake.promoted_kinds", old=old, new=ip["promoted_kinds"])


def notify_ok(now=None):
    """Actionable toasts only outside the night window."""
    return not prefs.is_night(now)


# ---------- reducers ----------

def _op_item(s, r, ts):
    s["intake"]["items"][r["key"]] = {k: r.get(k) for k in ("key", "source", "ref", "title", "kind", "tier", "unit",
                                                            "autostart", "why", "scanned")} | {"ts": ts}


def _op_scan(s, r, ts):
    sc = s["intake"].setdefault("scan", {}).setdefault(r["source"], {"seen": [], "seeded": None, "last": None})
    sc["seen"] = (sc["seen"] + [x for x in r.get("seen") or [] if x not in sc["seen"]])[-SEEN_MAX:]
    sc["seeded"] = sc["seeded"] or ts
    sc["last"] = ts
    sc["last_result"] = {"seen": len(r.get("seen") or []), "proposed": len(r.get("proposed") or []),
                         "capped": len(r.get("capped") or []), "unread": list(r.get("unread") or []),
                         "team": r.get("team") or 0, "ignored": r.get("ignored") or 0}


def _op_ignored(s, r, ts):
    s["intake"].setdefault("ignored", {})[str(r["ref"]).lower()] = {"rule": r.get("rule"), "ts": ts}


def _glob(text, pat):
    """`*` and `?` only: brackets are literal, so "[Auto] *" matches titles that start with "[Auto] "."""
    rx = re.escape(pat).replace(r"\*", ".*").replace(r"\?", ".")
    return re.fullmatch(rx, text) is not None


def ignore_rule(row, rules):
    """The first prefs review.ignore rule a PR or issue row matches, else None. A rule names any of author, title,
    repo, label (globs); every field it names must match."""
    author = str((row.get("author") or {}).get("login") or "")
    repo = str((row.get("repository") or {}).get("nameWithOwner") or row.get("repo") or "")
    labels = [str(x.get("name") if isinstance(x, dict) else x) for x in row.get("labels") or []]
    for r in rules or []:
        checks = [_glob(author, r["author"]) if "author" in r else None,
                  _glob(str(row.get("title") or ""), r["title"]) if "title" in r else None,
                  _glob(repo, r["repo"]) if "repo" in r else None,
                  any(_glob(x, r["label"]) for x in labels) if "label" in r else None]
        checks = [c for c in checks if c is not None]
        if checks and all(checks):
            return r
    return None


def _ref(row):
    repo = (row.get("repository") or {}).get("nameWithOwner")
    return f"{repo}#{row.get('number')}" if repo else None


def review_requests(p, fields=SCAN_FIELDS):
    """Open review requests to the owner: {"mine": rows asked of the owner by name and no ignore rule, "ignored":
    [(row, rule)], "team": rows asked only of the owner's teams, "unread": bool}. When the personal search fails,
    every request counts as personal (the old behaviour) rather than none."""
    import factory
    rules = (p.get("review") or {}).get("ignore") or []
    every = factory.gh("search", "prs", "--review-requested", "@me", "--state", "open", "--json", fields,
                       "--limit", "100")
    mine = factory.gh(*PERSONAL_REVIEWS, "--json", fields, "--limit", "100")
    if not isinstance(every, list):
        every = mine if isinstance(mine, list) else None
    if every is None:
        return {"mine": [], "ignored": [], "team": [], "unread": True}
    named = {_ref(r) for r in mine} if isinstance(mine, list) else {_ref(r) for r in every}
    out = {"mine": [], "ignored": [], "team": [], "unread": False}
    for row in every:
        rule = ignore_rule(row, rules)
        if rule:
            out["ignored"].append((row, rule))
        elif _ref(row) in named:
            out["mine"].append(row)
        else:
            out["team"].append(row)
    return out


def held_refs(snap):
    """PR / issue refs some unit already carries (its PRs, keys, intake items): never proposed again."""
    out = set()
    for u in snap["units"].values():
        out |= {f"{p.get('repo')}#{p.get('number')}".lower() for p in u.get("prs") or [] if isinstance(p, dict)}
        out |= {str(x).lower() for x in (u.get("keys") or {}).get("pr") or []}
    out |= {str(i.get("ref")).lower() for i in (snap.get("intake") or {}).get("items", {}).values()}
    return out


def scan(p=None, now=None):
    """The GitHub intake watcher, run by the foreman (`foundry intake scan`, on its schedule): open review requests
    to the owner and issues assigned to them that no unit holds become intake items (tier B: a plan-mode unit
    blocked on the owner's proceed/change/drop card, so nothing starts by itself). The first scan only records
    what is already there; later scans propose what is new, at most prefs intake.scan_max_per_day a day."""
    import factory
    p = p or prefs.load()
    now = now or cl.now()
    snap = cl.load()
    sc = ((snap.get("intake") or {}).get("scan") or {}).get("github") or {}
    seen, held = set(sc.get("seen") or []), held_refs(snap)
    found, unread, ignored, team = [], [], [], []
    rq = review_requests(p)
    if rq["unread"]:
        unread.append("review")
    issues = factory.gh(*SCAN_QUERIES[1][1], "--json", SCAN_FIELDS, "--limit", "100")
    if not isinstance(issues, list):
        unread.append("issue")
        issues = []
    rules = (p.get("review") or {}).get("ignore") or []
    for kind, rows in (("review", rq["mine"]), ("issue", issues)):
        for row in rows:
            ref = _ref(row)
            if not ref:
                continue
            rule = ignore_rule(row, rules) if kind == "issue" else None
            if rule:
                ignored.append((ref, rule))
            else:
                found.append((kind, ref, row.get("title"), row.get("url")))
    ignored += [(_ref(row), rule) for row, rule in rq["ignored"] if _ref(row)]
    team = [_ref(row) for row in rq["team"] if _ref(row)]
    known = (snap.get("intake") or {}).get("ignored") or {}
    cl.append_many([("intake.ignored", {"ref": ref, "rule": rule}) for ref, rule in ignored
                    if ref.lower() not in known])
    withdrawn = withdraw_cards(cl.load(), {r.lower(): "무시 규칙" for r, _ in ignored} |
                               {r.lower(): "팀 리뷰 요청" for r in team})
    refs = [ref.lower() for _, ref, _, _ in found]
    if not sc.get("seeded"):
        cl.append("intake.scan", source="github", seen=refs, proposed=[], unread=unread, team=len(team),
                  ignored=len(ignored))
        return {"seeded": len(refs), "proposed": [], "unread": unread, "team": len(team), "ignored": len(ignored),
                "withdrawn": withdrawn}
    cap = int((p.get("intake") or {}).get("scan_max_per_day", 5))
    day = now.astimezone(prefs.tz(p)).date()
    today = sum(1 for i in (snap.get("intake") or {}).get("items", {}).values() if i.get("scanned")
                and (cl.parse_ts(i.get("ts")) or now).astimezone(prefs.tz(p)).date() == day)
    proposed, skipped = [], []
    for kind, ref, title, url in found:
        r = ref.lower()
        if r in seen or r in held:
            continue
        if today + len(proposed) >= cap:
            skipped.append(ref)
            continue
        item = add("github", ref, title, kind, scanned=True)
        proposed.append({"ref": ref, "kind": kind, "unit": item.get("unit"), "card": item.get("card"), "url": url})
    # A capped ref stays unseen, so the next scan proposes it.
    cl.append("intake.scan", source="github", seen=[r for r in refs if r not in {s.lower() for s in skipped}],
              proposed=[x["ref"] for x in proposed], capped=skipped, unread=unread, team=len(team),
              ignored=len(ignored))
    return {"proposed": proposed, "capped": skipped, "unread": unread, "team": len(team), "ignored": len(ignored),
            "withdrawn": withdrawn}


def withdraw_cards(snap, why_by_ref):
    """Close the open intake cards of refs that are now ignored or asked only of a team, as foundry, and cancel their
    plan-mode units. Returns the card ids closed."""
    import decide
    recs, out = [], []
    for cid, c in sorted((snap.get("cards") or {}).items()):
        ref = str(c.get("key") or "").removeprefix("intake:").lower()
        why = why_by_ref.get(ref) if str(c.get("key") or "").startswith("intake:") else None
        if not why or c.get("answer") is not None:
            continue
        recs.append(("decide.answer", {"id": cid, "key": decide.MOOT, "by": "foundry", "moot": why,
                                       "note": f"자동 종료: {why}"}))
        u = snap["units"].get(c.get("unit")) or {}
        if u and u.get("mode") == "plan" and u.get("status") not in graph.CLOSED:
            recs.append(("unit.status", {"id": u["id"], "status": "cancelled", "note": f"intake 철회: {why}"}))
        out.append(cid)
    if recs:
        cl.append_many(recs)
    return out


def _op_mode(s, r, ts):
    u = cl._unit(s, r["id"])
    u["mode"] = r.get("mode") or u.get("mode") or "full"
    if r.get("intake"):
        u["intake"] = r["intake"]
    u["updated"] = ts


def _op_promoted(s, r, ts):
    u = s["units"].get(r.get("unit"))
    if u and u.get("intake"):
        u["intake"]["tier"] = "A"
    for c in s["intake"]["cards"].values():
        if c.get("unit") == r.get("unit") and c.get("answer") is None:
            c["answer"] = "proceed"


def _op_card(s, r, ts):
    s["intake"]["cards"][r["id"]] = {k: r.get(k) for k in ("id", "unit", "question", "options", "recommended")} | {
        "asked": ts, "answer": None}


OPS = {"intake.item": _op_item, "unit.mode": _op_mode, "intake.promoted": _op_promoted, "intake.card": _op_card,
       "intake.scan": _op_scan, "intake.ignored": _op_ignored}


# ---------- CLI and board ----------

def next_fire(expr, now, zone, horizon_days=8):
    """The next minute `expr` (a 5-field cron, local time) fires after now, or None within the horizon."""
    import datetime as dt
    import report
    t = now.astimezone(zone).replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    for _ in range(horizon_days * 24 * 60):
        if report._cron_fires(expr, t):
            return t
        t += dt.timedelta(minutes=1)
    return None


def status(snap=None, p=None, now=None):
    """The GitHub intake watcher at a glance: last scan, what it saw / proposed / held back by the daily cap, today's
    proposals against the cap, and when the scan schedule fires next."""
    snap = snap or cl.load()
    p = p or prefs.load()
    now = now or cl.now()
    zone = prefs.tz(p)
    sc = ((snap.get("intake") or {}).get("scan") or {}).get("github") or {}
    day = now.astimezone(zone).date()
    today = sum(1 for i in (snap.get("intake") or {}).get("items", {}).values() if i.get("scanned")
                and (cl.parse_ts(i.get("ts")) or now).astimezone(zone).date() == day)
    sched = next((s for s in p.get("schedules") or [] if "intake scan" in str(s.get("run") or "")), None)
    nxt = next_fire(sched["cron"], now, zone) if sched else None
    return {"seeded": sc.get("seeded"), "last_scan": sc.get("last"), "seen_total": len(sc.get("seen") or []),
            "last": sc.get("last_result") or {}, "proposed_today": today,
            "cap": int((p.get("intake") or {}).get("scan_max_per_day", 5)),
            "schedule": sched.get("name") if sched else None, "next_run": cl.iso(nxt) if nxt else None}


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return 0


def cmd_intake(a):
    if a.sub == "add":
        # GitHub is a source of PRs and issues whether or not its issues are a tracker.
        if a.source in prefs.TRACKERS and a.source != "github" and a.source not in prefs.trackers():
            print(f"refused: source {a.source} is a tracker that is not enabled (prefs trackers = "
                  f"{prefs.trackers()})", file=sys.stderr)
            return 2
        if a.continues and a.continues not in cl.load()["units"]:
            print(f"refused: no unit {a.continues}", file=sys.stderr)
            return 2
        return _emit(add(a.source, a.ref, a.title, a.kind, a.continues))
    if a.sub == "promote":
        # Leaving plan mode answers the owner's proceed/change/drop card: the owner's act, never a session's.
        err = cl.require_owner(f"promoting {a.unit} out of plan mode")
        if err:
            print(err, file=sys.stderr)
            return 2
        res, err = promote(a.unit, a.kind_too)
        if err:
            print(err, file=sys.stderr)
            return 2
        return _emit(res)
    if a.sub == "scan":
        return _emit(scan())
    if a.sub == "status":
        return _emit(status())
    items = sorted((cl.load().get("intake") or {}).get("items", {}).values(), key=lambda x: x.get("ts") or "")
    return _emit({"items": items})


def add_cli(sp):
    t = sp.add_parser("intake").add_subparsers(dest="sub", required=True)
    ad = t.add_parser("add")
    ad.add_argument("--source", required=True, choices=SOURCES); ad.add_argument("--ref", required=True)
    ad.add_argument("--title", default=""); ad.add_argument("--kind", required=True); ad.add_argument("--continues")
    pr = t.add_parser("promote"); pr.add_argument("unit"); pr.add_argument("--kind-too", action="store_true")
    t.add_parser("list")
    t.add_parser("scan", help="propose units for new GitHub review requests and assigned issues (first run: seed only)")
    t.add_parser("status", help="last scan, seen / proposed / capped, today's proposals against the cap, next run")
    for p in t.choices.values():
        p.set_defaults(func=cmd_intake)


def board(snap, model):
    model["night"] = prefs.is_night()
    units = snap.get("units") or {}
    for sec in model.get("sections") or []:
        for row in sec.get("rows") or []:
            row["mode"] = (units.get(row.get("id")) or {}).get("mode") or "full"
    items = (snap.get("intake") or {}).get("items", {}).values()
    model["intake_listed"] = [i for i in items if i.get("tier") == "C"][-30:]
    st = status(snap)
    if st["last_scan"]:
        def hm(v):
            t = cl.parse_ts(v)
            return t.astimezone(cl.zone()).strftime("%m-%d %H:%M") if t else "-"
        model["intake_status"] = {"at": hm(st["last_scan"]), "proposed": st["last"].get("proposed", 0),
                                  "capped": st["last"].get("capped", 0), "next": hm(st["next_run"])}
    cards = model.setdefault("cards", [])
    for c in (snap.get("intake") or {}).get("cards", {}).values():
        if c.get("answer") is None:
            cards.append({k: c.get(k) for k in ("id", "unit", "question", "options", "recommended")}
                         | {"repo": None, "reason": None, "asked_by": "intake", "deadline": None, "default": None})
