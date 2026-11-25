"""The Slack outbox: what a lead wants sent, filed as data instead of prose in a report.

A lead files `foundry outbox add <unit> --channel C… --thread 1731…123 --kind review-ask --text "…"`; the item
wakes the foreman (event kind `outbox`), who hands `foundry outbox list` to the dispatcher. The dispatcher sends
through the slack-post rules and the send ledger hook, then closes the item with `outbox done|failed`.
Only the dispatcher sends; filing an item never sends anything.
"""

import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

STATE = {"outbox": {}}
KINDS = ("review-ask", "review-remind", "message", "report")
CHANNEL_RE = re.compile(r"^[CDG][A-Z0-9]{8,}$")
THREAD_RE = re.compile(r"^\d{10}\.\d{6}$")
KEEP = 300


def _op_add(s, r, ts):
    if r["oid"] in s["outbox"]:
        return
    s["outbox"][r["oid"]] = {k: r.get(k) for k in ("oid", "unit", "channel", "thread", "kind", "text", "prs", "to", "rid",
                                                                 "replies", "send_at")} | {
        "state": "pending", "ts": ts, "closed": None, "sent_ts": None, "error": None}
    s["events"].append({"eid": f"ev-{r['oid']}", "unit": r.get("unit"), "wid": None, "kind": "outbox", "ref": r["oid"],
                        "summary": f"Slack {r.get('kind')} to {r.get('channel')}"
                                   f"{' thread ' + r['thread'] if r.get('thread') else ''}: {(r.get('text') or '')[:200]}",
                        "ts": ts, "consumed": None})
    done = [k for k, v in s["outbox"].items() if v["state"] != "pending"]
    for k in done[:max(0, len(s["outbox"]) - KEEP)]:
        s["outbox"].pop(k, None)


def _op_close(state):
    def red(s, r, ts):
        o = s["outbox"].get(r.get("oid"))
        if o and o["state"] == "pending":
            got = r.get("sent_ts")
            # A scheduled send closes with Slack's scheduled-message id, not a message ts: it is no thread parent
            # and the message is not out yet (outbox posted records the real ts once it is).
            real = got if got and THREAD_RE.match(str(got)) else None
            o.update({"state": state, "closed": ts, "sent_ts": real, "error": r.get("error"),
                      "scheduled_id": None if real else got, "post_at": r.get("post_at")})
    return red


def _op_posted(s, r, ts):
    o = s["outbox"].get(r.get("oid"))
    if o and o["state"] == "sent":
        o.update({"sent_ts": r.get("sent_ts"), "posted": ts})


OPS = {"outbox.add": _op_add, "outbox.done": _op_close("sent"), "outbox.failed": _op_close("failed"),
       "outbox.dropped": _op_close("dropped"), "outbox.posted": _op_posted}


def posted_at(o):
    """When a sent item reached Slack: its post_at for a scheduled send, else when it was closed; None while a
    scheduled send has no real message ts yet (pending) or the item never went."""
    if o.get("state") != "sent" or not (o.get("sent_ts") and THREAD_RE.match(str(o["sent_ts"]))):
        return None
    return cl.parse_ts(o.get("post_at")) or cl.parse_ts(o.get("closed"))


def scheduled_pending(o):
    """A sent item that closed with a scheduled id and no real ts recorded yet."""
    return o.get("state") == "sent" and not posted_at(o) and bool(o.get("scheduled_id") or o.get("sent_ts"))


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def fail(msg):
    print(msg, file=sys.stderr)
    return 2


def record(unit, channel, thread, kind, text, prs=(), to=(), rid=None, send_at=None, replies=None):
    """The outbox.add record (its oid is a hash of unit, channel, thread and text, so a repeat is the same item)."""
    oid = "ob-" + hashlib.sha1("|".join([unit or rid or "", channel, thread or "", " ".join(text.split())]).encode()
                               ).hexdigest()[:10]
    rec = {"oid": oid, "unit": unit, "channel": channel, "thread": thread or None, "kind": kind, "text": text,
           "prs": list(prs) or cl.norm_pr(text), "to": list(to)}
    return rec | {k: v for k, v in (("rid", rid), ("send_at", send_at), ("replies", list(replies or []) or None)) if v}


def approval_refusal(snap, kind, rid):
    """Why a report item may not be filed: a report (or anything carrying a report id) goes out only after the owner
    approved that draft (`report.decided` approved)."""
    if kind != "report" and not rid:
        return None
    x = (snap.get("reports") or {}).get(rid) if rid else None
    if not x or x.get("state") != "approved":
        return (f"report {rid or '(none)'} has no owner approval: a report is filed only by the owner's answer on its "
                "draft card (`foundry report daily --draft`), never directly")
    return None


def due(o, now=None):
    t = cl.parse_ts(o.get("send_at"))
    return not t or t <= (now or cl.now())


def add(unit, channel, thread, kind, text, prs=(), to=(), rid=None, send_at=None, replies=None):
    """(status, item): 'queued' or 'exists' (same unit, channel, thread and text). Raises ValueError when malformed.
    unit is None only for a report item, which needs its approved draft (rid)."""
    if not CHANNEL_RE.match(channel or ""):
        raise ValueError(f"--channel must be a Slack channel ID (C…/G…/D…), not {channel!r}: look it up, never a name")
    if thread and not THREAD_RE.match(thread):
        raise ValueError(f"--thread must be a message ts like 1731900000.123456, not {thread!r}")
    if kind not in KINDS:
        raise ValueError(f"--kind must be one of {KINDS}")
    if not (text or "").strip():
        raise ValueError("--text is the message to send")
    if unit is None and not rid:
        raise ValueError("an outbox item belongs to a unit")
    rec = record(unit, channel, thread, kind, text, prs, to, rid, send_at, replies)
    if kind == "review-ask":
        import gitpolicy
        snap = cl.load()
        for ref in rec.get("prs") or []:
            why = gitpolicy.bump_review_refusal(snap, unit, ref)
            if why:
                raise ValueError(why.removeprefix("refused: "))
    oid = rec["oid"]

    def decide(snap):
        if unit is not None and unit not in snap["units"]:
            raise ValueError(cl.unknown_unit(unit, snap))
        why = approval_refusal(snap, kind, rid)
        if why:
            raise ValueError(why)
        if oid in snap["outbox"]:
            return ("exists", snap["outbox"][oid]), []
        return ("queued", rec), [("outbox.add", rec)]

    return cl.transact(decide)[0]


def batches(snap, now=None, p=None):
    """Pending review asks grouped per reviewer (`to`), each with whether it is due and how to send it:
    due at `review.batch_min` asks or when the oldest waited `review.batch_wait_min`; inside the Slack quiet
    window a due batch is scheduled for the window's end, as one message."""
    import prefs
    p = p or prefs.load()
    now = now or cl.now()
    rv = p["review"]
    groups = {}
    for o in snap["outbox"].values():
        if o["state"] == "pending" and o["kind"] == "review-ask":
            groups.setdefault((o.get("channel"), tuple(sorted(o.get("to") or []))), []).append(o)
    out = []
    for (channel, to), items in sorted(groups.items(), key=lambda kv: min(o["ts"] for o in kv[1])):
        oldest = min(cl.parse_ts(o["ts"]) for o in items)
        age = int((now - oldest).total_seconds() // 60)
        due = len(items) >= rv["batch_min"] or age >= rv["batch_wait_min"]
        quiet = prefs.in_window(now, p["slack_quiet"], prefs.tz(p))
        when = (prefs.window_end(now, p["slack_quiet"], prefs.tz(p)).isoformat(timespec="minutes") if quiet else "now")
        threads = sorted({o["thread"] for o in items if o.get("thread")})
        out.append({"to": list(to), "channel": channel, "oids": [o["oid"] for o in items],
                    "prs": sorted({x for o in items for x in o.get("prs") or []}), "count": len(items),
                    "oldest_min": age, "due": due, "send": (("schedule " + when) if quiet else "now") if due else "hold",
                    "thread": threads[0] if len(threads) == 1 and len(items) == 1 else None,
                    "how": ("one message for every PR in the batch (slack-post bullets); in an existing request thread "
                            "reply with reply_broadcast false; close every oid with the one ts")})
    return out


def cmd_outbox(a):
    if a.sub == "batches":
        return emit(batches(cl.load())) or 0
    if a.sub == "add":
        try:
            status, item = add(a.unit, a.channel, a.thread, a.kind, a.text, a.pr, a.to)
        except ValueError as e:
            return fail(f"refused: {e}")
        return emit({"status": status, "oid": item["oid"],
                     "note": "Filed for the dispatcher; do not send it yourself or repeat it in your report."}) or 0
    snap = cl.load()
    if a.sub == "list":
        items = [o | ({"due": due(o)} if o.get("send_at") else {}) for o in snap["outbox"].values()
                 if a.all or o["state"] == "pending"]
        return emit(sorted(items, key=lambda o: o["ts"])) or 0
    o = snap["outbox"].get(a.oid)
    if a.sub == "posted":
        if not o or not scheduled_pending(o):
            return fail(f"no scheduled outbox item {a.oid} waiting for its posted ts")
        if not THREAD_RE.match(a.ts or ""):
            return fail("refused: --ts is the posted message's ts, like 1795061530.355009")
        cl.append("outbox.posted", oid=a.oid, sent_ts=a.ts)
        return emit({"posted": a.oid, "ts": a.ts}) or 0
    if not o or o["state"] != "pending":
        return fail(f"no pending outbox item {a.oid}")
    if a.sub == "done":
        post = None
        if a.post_at:
            t = cl.parse_ts(a.post_at)
            if t is None and str(a.post_at).replace(".", "", 1).isdigit():
                import datetime as dt
                t = dt.datetime.fromtimestamp(float(a.post_at), cl.zone())
            if t is None:
                return fail("refused: --post-at is the schedule's post_at (unix seconds or ISO time)")
            post = t.isoformat()
        cl.append("outbox.done", oid=a.oid, sent_ts=a.ts, **({"post_at": post} if post else {}))
    elif a.sub == "failed":
        cl.append("outbox.failed", oid=a.oid, error=a.error)
    else:
        cl.append("outbox.dropped", oid=a.oid, error=a.reason)
    return emit({a.sub: a.oid}) or 0


def add_cli(sp):
    s = sp.add_parser("outbox", help="Slack messages a lead wants sent; the dispatcher sends them").add_subparsers(
        dest="sub", required=True)
    ad = s.add_parser("add")
    ad.add_argument("unit")
    ad.add_argument("--channel", required=True, help="Slack channel ID (C…), never a name")
    ad.add_argument("--thread", help="the parent message ts, for a thread reply")
    ad.add_argument("--kind", choices=KINDS, default="message")
    ad.add_argument("--text", required=True)
    ad.add_argument("--pr", action="append", default=[], help="owner/repo#n the message is about")
    ad.add_argument("--to", action="append", default=[], help="Slack user ID of a recipient")
    ls = s.add_parser("list")
    ls.add_argument("--all", action="store_true")
    s.add_parser("batches", help="pending review asks per reviewer: due or hold, now or scheduled")
    dn = s.add_parser("done")
    dn.add_argument("oid")
    dn.add_argument("--ts", help="the sent message's ts, or a scheduled send's scheduled-message id")
    dn.add_argument("--post-at", help="a scheduled send's post_at (unix seconds or ISO)")
    po = s.add_parser("posted", help="a scheduled send went out: record its real message ts")
    po.add_argument("oid")
    po.add_argument("--ts", required=True)
    fl = s.add_parser("failed")
    fl.add_argument("oid")
    fl.add_argument("--error", required=True)
    dr = s.add_parser("drop")
    dr.add_argument("oid")
    dr.add_argument("--reason", required=True)
    for p in s.choices.values():
        p.set_defaults(func=cmd_outbox)


def board(snap, m):
    m["outbox_pending"] = sum(1 for o in (snap.get("outbox") or {}).values() if o["state"] == "pending")
