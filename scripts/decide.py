"""Decision cards: one question with labelled options, a recommendation, and an optional deadline default.

A card is asked once per dedup key; asking again returns the stored card (and answer). Opening a card
for a unit blocks that unit on `decision:<id>`; an answer (button: `decide answer`, chat: `decide chat
"d-0123 B"`, or the deadline default applied by graph.resolve) resolves it. The older `foundry decision`
log in foundry.py is separate and untouched.
"""

import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

STATE = {"cards": {}}
FIELDS = ("id", "unit", "repo", "question", "context", "options", "recommended", "reason", "asked_by",
          "deadline", "default", "key", "refs")
# What a card hinges on. Once every ref is settled the card is moot and foundry closes it (close_moot), never the
# owner: pr:<owner/repo#n> merged or closed, approval:<rid> answered, unit:<id> done or cancelled.
REF_RE = re.compile(r"^(pr|approval|unit):(\S+)$")
PR_REF_RE = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")
# Cards foundry opens about one PR hinge on it by their key, refs or not (cards opened before refs existed).
KEY_PR_PREFIXES = ("pr-aging:", "pr-orphan:")
MOOT = "moot"
CHAT_RE = re.compile(r"^\s*(d-[\w-]+)\s+(\S+)\s*$")
RELAY_NOTE = cl.RELAY_NOTE


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def fail(msg, code=2):
    print(msg, file=sys.stderr)
    return code


def _op_open(s, r, ts):
    s["cards"][r["id"]] = {k: r.get(k) for k in FIELDS} | {"opened": ts, "answer": None, "answered_by": None,
                                                         "answered": None}


def _op_answer(s, r, ts):
    c = s["cards"].get(r["id"])
    if not c or c["answer"] is not None:
        return
    c.update({"answer": r["key"], "answered_by": r.get("by"), "answered": ts, "answer_note": r.get("note")})
    if r.get("quote"):
        c["answer_quote"] = r["quote"]
    if r.get("moot"):
        c["moot"] = r["moot"]
    label = next((o.get("label") for o in c["options"] if o["key"] == r["key"]), r["key"])
    s["events"].append({"eid": f"ev-{r['id']}-answered", "unit": c.get("unit"), "wid": None,
                        "kind": "decision.answered", "ref": f"decision:{r['id']}",
                        "summary": f"{r['id']} answered {r['key']} ({label}) by {r.get('by')}: {c['question']}"[:300],
                        "ts": ts, "consumed": None})


def _op_refs(s, r, ts):
    c = s["cards"].get(r["id"]) or (s.get("decisions") or {}).get(r["id"])
    if c is not None:
        c["refs"] = sorted(set(c.get("refs") or []) | set(r.get("refs") or []))


OPS = {"decide.open": _op_open, "decide.answer": _op_answer, "decide.refs": _op_refs}


def parse_refs(refs):
    """Normalized refs (`rid:` is accepted for `approval:`), or ValueError naming the bad one."""
    out = []
    for raw in refs or []:
        ref = str(raw).strip()
        if ref.startswith("rid:"):
            ref = "approval:" + ref[4:]
        m = REF_RE.match(ref)
        if not m or (m.group(1) == "pr" and not PR_REF_RE.match(m.group(2))):
            raise ValueError(f"ref {raw!r} is not pr:<owner/repo#n>, approval:<rid> or unit:<id>")
        out.append(ref)
    return sorted(set(out))


def card_refs(c):
    refs = list(c.get("refs") or [])
    key = str(c.get("key") or "")
    for pre in KEY_PR_PREFIXES:
        if key.startswith(pre) and PR_REF_RE.match(key[len(pre):]):
            refs.append("pr:" + key[len(pre):])
    return sorted(set(refs))


def pr_states(snap):
    """{owner/repo#n: MERGED|CLOSED|OPEN} from the PR records the github stream keeps."""
    out = {}
    for u in snap["units"].values():
        for pr in u.get("prs") or []:
            if isinstance(pr, dict) and pr.get("repo") and pr.get("number") is not None:
                out[f"{pr['repo']}#{pr['number']}"] = "MERGED" if pr.get("merged") else str(pr.get("state") or "").upper()
    return out


def moot_reason(snap, c, live=None):
    """Why an open card no longer needs anyone (Korean, for the record and 인계), or None. Every ref must be
    settled; a card with no ref is never moot."""
    refs = card_refs(c)
    if not refs or c.get("answer") is not None:
        return None
    states = {**pr_states(snap), **(live or {})}
    why = []
    for ref in refs:
        kind, val = ref.split(":", 1)
        if kind == "pr":
            st = str(states.get(val) or "").upper()
            if st not in ("MERGED", "CLOSED"):
                return None
            why.append(f"{val} {'머지됨' if st == 'MERGED' else '닫힘'}")
        elif kind == "approval":
            a = (snap.get("approvals") or {}).get(val) or {}
            if a.get("answer") is None:
                return None
            why.append(f"승인 요청 {val} 답변됨")
        else:
            st = ((snap.get("units") or {}).get(val) or {}).get("status")
            if st not in ("done", "cancelled"):
                return None
            why.append(f"unit {val} {'완료' if st == 'done' else '취소'}")
    return ", ".join(why)


def moot_records(snap, live=None):
    """Records closing every open card, and withdrawing every open `decision ask`, that became moot, as foundry
    (never queued for the owner)."""
    recs = []
    for cid, c in sorted((snap.get("cards") or {}).items()):
        why = moot_reason(snap, c, live)
        if why:
            recs.append(("decide.answer", {"id": cid, "key": MOOT, "by": "foundry", "moot": why,
                                           "note": f"자동 종료: {why}"}))
    for key, d in sorted((snap.get("decisions") or {}).items()):
        why = moot_reason(snap, d, live)
        if why:
            recs.append(("decision.answer", {"key": key, "answer": f"withdrawn: 자동 종료: {why}", "unit": d.get("unit"),
                                             "by": "foundry", "withdrawn": why, "moot": why}))
    return recs


def _open_items(snap):
    return [c for c in (snap.get("cards") or {}).values() if c.get("answer") is None] + [
        d for d in (snap.get("decisions") or {}).values() if d.get("answer") is None]


def untracked_pr_refs(snap):
    """PR refs open cards hinge on that no unit's PR record knows: the github stream reads these once a pass."""
    known = pr_states(snap)
    return sorted({r[3:] for c in _open_items(snap) for r in card_refs(c) if r.startswith("pr:") and r[3:] not in known})


def close_moot(live=None):
    """Close the moot cards under the journal lock and resolve what they unblocked. Returns the closed card ids."""
    recs = cl.transact(lambda snap: (lambda rs: (rs, rs))(moot_records(snap, live)))[0]
    if recs:
        import graph
        graph.apply_resolve()
    return [r[1].get("id") or r[1].get("key") for r in recs]


def _next_id(cards):
    nums = [int(m.group(1)) for m in (re.match(r"^d-(\d+)$", c) for c in cards) if m]
    return f"d-{(max(nums) + 1 if nums else 1):04d}"


def _match_key(card, key):
    return next((o["key"] for o in card["options"] if o["key"].lower() == str(key).lower()), None)


def open_card(**f):
    """Open a card (or return the one already asked under f['key']). Returns (status, card).

    status: 'opened' | 'pending' | 'answered'. Raises ValueError on a malformed card."""
    opts = f.get("options") or []
    keys = [o["key"] for o in opts]
    if not f.get("question") or not f.get("key") or len(opts) < 2 or len(set(keys)) != len(keys):
        raise ValueError("a card needs a question, a dedup key, and at least two distinct options")
    for k in ("recommended", "default"):
        if f.get(k) is not None and f[k] not in keys:
            raise ValueError(f"{k} {f[k]!r} is not one of the options {keys}")
    if f.get("deadline") and cl.parse_ts(f["deadline"]) is None:
        raise ValueError(f"deadline {f['deadline']!r} is not an ISO timestamp")
    f["refs"] = parse_refs(f.get("refs")) or None

    def decide(snap):
        cards = snap.get("cards") or {}
        old = next((c for c in cards.values() if c.get("key") == f["key"]), None)
        if old:
            return ("answered" if old["answer"] is not None else "pending", old), []
        cid = f.get("id") or _next_id(cards)
        if cid in cards:
            raise ValueError(f"card {cid} exists under another key")
        if f.get("unit") and f["unit"] not in snap["units"]:
            raise ValueError(cl.unknown_unit(f['unit'], snap))
        rec = {k: f.get(k) for k in FIELDS} | {"id": cid}
        recs = [("decide.open", rec)]
        if f.get("unit"):
            recs.append(("unit.block", {"unit": f["unit"], "refs": [f"decision:{cid}"]}))
        return ("opened", rec), recs

    (status, card), snap = cl.transact(decide)
    return status, (snap["cards"][card["id"]] if snap else card)


def answer(cid, key, by, note=None, quote=None):
    """Record an answer and resolve what it unblocks. Returns (exit code, payload)."""
    def decide(snap):
        c = (snap.get("cards") or {}).get(cid)
        if not c:
            return (2, {"error": f"unknown card {cid}"}), []
        k = _match_key(c, key)
        if not k:
            return (2, {"error": f"{key!r} is not an option of {cid}: {[o['key'] for o in c['options']]}"}), []
        if c["answer"] is not None:
            same = c["answer"] == k
            return (0 if same else 2, {"answered": cid, "answer": c["answer"], "by": c["answered_by"],
                                       **({} if same else {"error": "already answered differently"})}), []
        rec = {"id": cid, "key": k, "by": by, **({"note": note} if note else {}), **({"quote": quote} if quote else {})}
        return (0, {"answered": cid, "answer": k, "by": by, **({"note": note} if note else {})}), [("decide.answer", rec)]

    (rc, out), _ = cl.transact(decide)
    if rc == 0 and "error" not in out:
        import graph
        out["resolved"] = [r[1] for r in graph.apply_resolve()]
        import report
        out.update(report.on_answer(cid))
    return rc, out


# ---------- CLI ----------

def parse_options(specs, extra_json):
    opts = []
    for spec in specs:
        # Only the first colon ends the key: a label keeps its own colons (「09:00 에 전송」). Descriptions go in
        # --option-json.
        key, _, label = spec.partition(":")
        opts.append({"key": key.strip(), "label": label.strip() or key.strip(), "description": None,
                     "pros": [], "cons": [], "preview": None})
    if extra_json:
        extra = json.loads(extra_json)
        items = extra if isinstance(extra, list) else [{"key": k, **v} for k, v in extra.items()]
        for item in items:
            o = next((x for x in opts if x["key"] == item.get("key")), None)
            if o is None:
                o = {"key": item["key"], "label": item.get("label") or item["key"], "description": None,
                     "pros": [], "cons": [], "preview": None}
                opts.append(o)
            o.update({k: v for k, v in item.items() if k in ("label", "description", "pros", "cons", "preview")})
    return opts


def asks(snap, everything=False):
    """`foundry decision ask` questions (free-text answers, no options) as rows of the same list."""
    return [{"id": d["key"], "kind": "ask", "unit": d.get("unit"), "question": d.get("question") or d["key"],
             "options": [], "asked": d.get("asked"), "answer": d.get("answer"), "answered": d.get("answered")}
            for d in sorted((snap.get("decisions") or {}).values(), key=lambda d: d.get("asked") or "")
            if everything or d.get("answer") is None]


# A line that asks the owner something: ends in a question mark, or a Korean/Japanese question ending.
QUESTION_RE = re.compile(r"(?:[?？]|까요|할지|ますか|でしょうか|しますか)\s*[.。)*_`]*\s*$")


def owner_question(text):
    """The first prose line of text that asks a question (code blocks and quotes skipped), else None."""
    fenced = False
    for line in str(text or "").splitlines():
        t = line.strip()
        if t.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not t or t.startswith(">") or t.startswith("|"):
            continue
        if QUESTION_RE.search(t):
            return t[:160]
    return None


def open_questions(snap, unit=None, since=None):
    """Unanswered cards and `decision ask` questions (of `unit` when given, opened at or after `since` when given)."""
    rows = [(c.get("unit"), c.get("opened")) for c in (snap.get("cards") or {}).values() if c.get("answer") is None]
    rows += [(d.get("unit"), d.get("asked")) for d in (snap.get("decisions") or {}).values() if d.get("answer") is None]
    return [r for r in rows if (unit is None or r[0] == unit) and (since is None or (r[1] or "") >= since)]


def uncarded_question(text, snap, unit=None, since=None):
    """Why a message needs a decision card: it asks the owner a question and no open card or ask backs it."""
    q = owner_question(text)
    if not q or open_questions(snap, unit, since):
        return None
    return (f"this message asks the owner a question (\"{q}\") but no decision card is open"
            + (f" for {unit}" if unit else "") + ": owner questions go on a card — `foundry decide open --key <key> "
            "--question ... --option A:... --option B:... --recommend A` (or `foundry decision ask`) — so they "
            "reach the board and survive a restart. If it is not a question for the owner, say it as a statement.")


def withdraw(key, by, reason, quote=None):
    """Withdraw an open lead question that no longer needs an answer (the asker, the foreman or the owner), or
    close a stale card: the owner, or the foreman relaying the owner's words (`--quote`)."""
    def decide(snap):
        c = (snap.get("cards") or {}).get(key)
        if c:
            relay = cl.relay_quote(by, quote)
            if by != "owner" and not relay:
                return (2, {"error": f"{key} is a card: answer it, let its deadline default apply, or close it on "
                                     "the owner's word with --by foreman --quote \"<the owner's exact words>\""}), []
            if c["answer"] is not None:
                return (0, {"withdrawn": key, "already": c["answer"]}), []
            rec = {"id": key, "key": "withdrawn", "by": by, "note": f"withdrawn: {reason}"
                   + (f" ({RELAY_NOTE})" if relay else "")} | ({"quote": relay} if relay else {})
            return (0, {"withdrawn": key, "by": by, "reason": reason} | ({"quote": relay} if relay else {})), [
                ("decide.answer", rec)]
        d = (snap.get("decisions") or {}).get(key)
        if not d:
            return (2, {"error": f"unknown decision {key}"}), []
        if d.get("answer") is not None:
            return (0, {"withdrawn": key, "already": d["answer"]}), []
        why = f"{reason} ({by})"
        return (0, {"withdrawn": key, "by": by, "reason": reason}), [
            ("decision.answer", {"key": key, "answer": f"withdrawn: {why}", "unit": d.get("unit"), "by": by,
                                 "withdrawn": why})]

    (rc, out), _ = cl.transact(decide)
    return rc, out


def answer_ask(key, text, by, note=None, quote=None):
    """Answer an open `decision.ask` and deliver the answer to its unit's inbox. Returns (exit code, payload)."""
    def decide(snap):
        d = (snap.get("decisions") or {}).get(key)
        if not d:
            return (2, {"error": f"unknown decision {key}"}), []
        if d.get("answer") is not None:
            same = d["answer"] == text
            return (0 if same else 2, {"answered": key, "answer": d["answer"],
                                       **({} if same else {"error": "already answered differently"})}), []
        uid = d.get("unit")
        recs = [("decision.answer", {"key": key, "answer": text, "unit": uid, "by": by, **({"note": note} if note else {}),
                                     **({"quote": quote} if quote else {})})]
        if uid and uid in snap["units"]:
            recs.append(("inbox.add", {"id": uid, "iid": cl.new_id("in", key), "text": f"Owner decision {key}: {text}",
                                       "from": "owner"}))
        return (0, {"answered": key, "answer": text, "by": by, "unit": uid, "delivered_to": uid}), recs

    (rc, out), _ = cl.transact(decide)
    if rc == 0 and "error" not in out:
        import graph
        out["resolved"] = [r[1] for r in graph.apply_resolve()]
    return rc, out


DIGEST = "owner-digest.json"


def owner_items(snap):
    """Everything waiting on the owner: open cards and asks, waiting_owner units, tier-3 permission requests and
    open owner to-dos. One row each, in a stable order, so the same state always hashes the same. A card whose
    refs are all settled is foundry's to close, never the owner's, so it is left out even before it is closed."""
    rows = [{"id": c["id"], "kind": "card", "unit": c.get("unit"), "text": c.get("question")}
            for c in (snap.get("cards") or {}).values() if c.get("answer") is None and not moot_reason(snap, c)]
    decs = snap.get("decisions") or {}
    rows += [{"id": d["id"], "kind": "ask", "unit": d["unit"], "text": d["question"]} for d in asks(snap)
             if not moot_reason(snap, decs.get(d["id"]) or {})]
    asked = {r["unit"] for r in rows}  # a unit waiting on its own open question is that question, listed once
    rows += [{"id": u["id"], "kind": "waiting_owner", "unit": u["id"],
              "text": (u.get("notes") or [{}])[-1].get("text") or u.get("title")}
             for u in (snap.get("units") or {}).values() if u.get("status") == "waiting_owner" and u["id"] not in asked]
    rows += [{"id": a["rid"], "kind": "approval", "unit": a.get("unit"), "text": a.get("summary")}
             for a in (snap.get("approvals") or {}).values() if a.get("answer") is None and a.get("tier") == 3]
    rows += [{"id": t["tid"], "kind": "owner_todo", "unit": t.get("unit"), "todo_kind": t.get("kind"),
              "todo_title": t.get("title"), "text": t.get("text") or t.get("kind")}
             for t in (snap.get("owner_todos") or {}).values() if not t.get("closed")]
    return sorted(rows, key=lambda r: (r["kind"], str(r["id"])))


def digest(owner_message=False):
    """The owner-question list, but only when it is news: its ids changed since the last list printed, or the
    owner just wrote. Otherwise one line, so a no-event wake costs no list (prefs-free: the side file holds it)."""
    items = owner_items(cl.load())
    h = hashlib.sha256(json.dumps(sorted((r["kind"], str(r["id"])) for r in items)).encode()).hexdigest()[:16]
    try:
        with open(cl.path(DIGEST)) as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = {}
    reason = "owner-message" if owner_message else ("changed" if last.get("hash") != h else None)
    if not reason:
        return {"changed": False, "count": len(items), "since": last.get("at"),
                "line": f"owner list unchanged ({len(items)} open since {last.get('at')}): print nothing"}
    cl.write_atomic(cl.path(DIGEST), json.dumps({"hash": h, "at": cl.iso()}))
    return {"changed": True, "reason": reason, "count": len(items), "items": items}


def read_text(path, flag):
    """A card text written to a file (multi-line text never goes inline in a Bash call), trailing space trimmed."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(20000)
    except OSError as e:
        raise ValueError(f"{flag}: cannot read {path}: {e.strerror}")
    text = "\n".join(x.rstrip() for x in text.strip("\n").splitlines())
    if not text.strip():
        raise ValueError(f"{flag}: {path} is empty")
    return text


def show(key):
    """The full card or question behind a board headline: question, context, options, reason, answer."""
    snap = cl.load()
    c = (snap.get("cards") or {}).get(key) or next(
        (c for c in (snap.get("cards") or {}).values() if c.get("key") == key), None)
    import views
    c = c or (snap.get("decisions") or {}).get(key)
    if not c:
        return None
    lines = views.outline_text(c.get("question"), "") + views.outline_text(c.get("context"), "")
    return c | {"outline": lines} if len(lines) > 1 else c


def show_text(c):
    """A card or question as the owner reads it: a status line, then 질문 / 배경 / 옵션 / 추천 / 답 sections, the
    question and context drawn through views.outline."""
    import i18n
    import views
    tr = lambda k, **kw: i18n.t("cli." + k, **kw)  # noqa: E731
    cid = c.get("id") or c.get("key")
    if c.get("answer") is None:
        state = tr("show_open")
    else:
        who = c.get("answered_by") or c.get("by")
        state = (tr("show_answered_by", answer=c["answer"], who=who, at=c.get("answered")) if who
                 else tr("show_answered", answer=c["answer"]))
    head = " · ".join(str(x) for x in (cid, c.get("unit"), c.get("repo"), state) if x)
    out = [head]
    for title, text in ((tr("show_question"), c.get("question")), (tr("show_context"), c.get("context"))):
        if views.outline(text):
            out += [title, *views.outline_text(text)]
    if c.get("options"):
        out.append(tr("show_options"))
        for o in c["options"]:
            desc = f" — {o['description']}" if o.get("description") else ""
            line = f"{o.get('key')}: {o.get('label') or o.get('key')}{desc}"
            if o.get("key") == c.get("recommended"):
                line = tr("show_option_recommended", option=line)
            out.append(f"  - {line}")
            out += [f"    + {x}" for x in o.get("pros") or []] + [f"    - {x}" for x in o.get("cons") or []]
    if c.get("recommended"):
        out.append(tr("show_recommend_reason", option=c["recommended"], reason=c["reason"]) if c.get("reason")
                   else tr("show_recommend", option=c["recommended"]))
    if c.get("deadline"):
        out.append(tr("show_deadline_default", deadline=c["deadline"], fallback=c["default"]) if c.get("default")
                   else tr("show_deadline", deadline=c["deadline"]))
    if c.get("answer_quote") or c.get("quote"):
        out.append(tr("show_owner_quote", quote=c.get("answer_quote") or c.get("quote")))
    refs = card_refs(c) if c.get("options") is not None else sorted(c.get("refs") or [])
    if refs:
        out.append(tr("show_refs", refs=", ".join(refs)))
    return "\n".join(out)


def cmd_decide(a):
    if a.sub == "show":
        found = show(a.id)
        if not found:
            print(f"no card or question {a.id!r}", file=sys.stderr)
            return 2
        if a.format == "json":
            return emit(found) or 0
        print(show_text(found))
        return 0
    if a.sub == "digest":
        return emit(digest(a.owner_message)) or 0
    if a.sub == "open":
        try:
            if a.question_file:
                a.question = read_text(a.question_file, "--question-file")
            if a.context_file:
                a.context = read_text(a.context_file, "--context-file")
        except ValueError as e:
            return fail(f"refused: {e}")
        texts = [("question", a.question), ("context", a.context)] + [("option", o) for o in a.option or []]
        why = next((w for f, t in texts for w in [cl.card_text_refusal(f, t)] if w), None)
        if why:
            return fail(why)
        try:
            status, card = open_card(id=a.id, unit=a.unit, repo=a.repo, question=a.question, context=a.context,
                                     options=parse_options(a.option, a.option_json), recommended=a.recommend,
                                     reason=a.reason, asked_by=a.asked_by, deadline=a.deadline,
                                     default=a.default, key=a.key, refs=a.ref)
        except ValueError as e:
            return fail(f"refused: {e}")
        return emit({"status": status, "card": card}) or 0
    if a.sub == "refs":
        try:
            refs = parse_refs(a.ref)
        except ValueError as e:
            return fail(f"refused: {e}")
        snap = cl.load()
        kind = "cards" if a.id in (snap.get("cards") or {}) else "decisions"
        if a.id not in (snap.get(kind) or {}):
            return fail(f"unknown card or question {a.id}")
        cl.append("decide.refs", id=a.id, refs=refs)
        closed = close_moot()
        return emit({"id": a.id, "refs": card_refs(cl.load()[kind][a.id]), "closed_moot": closed}) or 0
    if a.sub == "list":
        snap = cl.load()
        cards = [c | {"kind": "card"} for c in (snap.get("cards") or {}).values() if a.all or c["answer"] is None]
        return emit(cards + asks(snap, a.all)) or 0
    import graph
    if a.sub == "withdraw":
        by, err = graph.check_by(a.by, "withdrawing a question")
        if err:
            return fail(err)
        rc, out = withdraw(a.key, by, a.reason, a.quote)
        if rc == 0 and out.get("withdrawn") and not out.get("already"):
            out["resolved"] = [r[1] for r in graph.apply_resolve()]
        emit(out)
        return rc
    by, err = graph.check_by(a.by, "answering a decision card")
    if err or by not in ("owner", "foreman"):
        return fail(err or f"refused: only the owner or the foreman answers a card, not {by}")
    # The foreman only relays what the owner said in chat; the record says it was a relay and quotes the owner.
    note = RELAY_NOTE if by == "foreman" else None
    quote = (a.text if a.sub == "chat" else (a.quote or "")).strip() or None
    if by == "foreman" and not quote:
        return fail("refused: the foreman answers only as a relay: pass --quote with the owner's exact words from chat")
    if a.sub == "answer" and a.id not in (cl.load().get("cards") or {}):
        rc, out = answer_ask(a.id, a.key, by, note, quote)
    elif a.sub == "answer":
        rc, out = answer(a.id, a.key, by, note, quote)
    else:
        m = CHAT_RE.match(a.text)
        if not m:
            return fail('chat answer must look like "d-0123 B"')
        rc, out = answer(m.group(1), m.group(2), by, note, quote)
    emit(out)
    return rc


def add_cli(sp):
    d = sp.add_parser("decide", help="decision cards with options, a recommendation and a deadline default")
    s = d.add_subparsers(dest="sub", required=True)
    o = s.add_parser("open")
    o.add_argument("--id"); o.add_argument("--unit"); o.add_argument("--repo")
    oq = o.add_mutually_exclusive_group(required=True); oq.add_argument("--question")
    oq.add_argument("--question-file", help="the question as written in a file: line breaks and `- ` bullets kept (개조식)")
    o.add_argument("--context"); o.add_argument("--context-file", help="the context from a file, line breaks kept")
    o.add_argument("--option", action="append", default=[], metavar="KEY:LABEL",
                   help="the label is everything after the first colon, colons included; descriptions: --option-json")
    o.add_argument("--option-json", help="pros/cons/preview: a list of {key,...} or {KEY: {...}}")
    o.add_argument("--recommend"); o.add_argument("--reason")
    o.add_argument("--asked-by", required=True); o.add_argument("--deadline"); o.add_argument("--default")
    o.add_argument("--key", required=True, help="dedup key: asking again returns the stored card")
    o.add_argument("--ref", action="append", default=[], metavar="pr:OWNER/REPO#N|approval:RID|unit:ID",
                   help="what the card hinges on: once every ref is settled foundry closes the card as moot")
    rf = s.add_parser("refs", help="attach refs to an open card or question (already settled: closed as moot now)")
    rf.add_argument("id"); rf.add_argument("--ref", action="append", required=True)
    an = s.add_parser("answer", help="answer a card (d-0001 B) or a lead's question (<decision key> \"<answer text>\")")
    an.add_argument("id", metavar="CARD_OR_DECISION", help="a card id (d-0001) or a decision key from `decide list`")
    an.add_argument("key", metavar="OPTION_OR_ANSWER", help="the card's option key, or the answer text for a question")
    an.add_argument("--by", choices=("owner", "foreman"))
    an.add_argument("--quote", help="the owner's exact words from chat: required with --by foreman")
    ch = s.add_parser("chat")
    ch.add_argument("text"); ch.add_argument("--by", choices=("owner", "foreman"))
    ls = s.add_parser("list"); ls.add_argument("--all", action="store_true")
    sh = s.add_parser("show", help="the full card or question behind a board headline")
    sh.add_argument("id")
    sh.add_argument("--format", choices=("text", "json"), default="text",
                    help="text (default): sectioned bullets for the owner; json: the raw record")
    sh.set_defaults(func=cmd_decide)
    dg = s.add_parser("digest", help="the owner-question list only when it changed (or --owner-message); else one line")
    dg.add_argument("--owner-message", action="store_true", help="the owner just wrote: always return the list")
    wd = s.add_parser("withdraw", help="withdraw a lead's open question that became moot (decision key)")
    wd.add_argument("key", metavar="DECISION")
    wd.add_argument("--by", choices=("lead", "foreman", "owner"))
    wd.add_argument("--reason", required=True)
    wd.add_argument("--quote", help="closing a card as the foreman: the owner's exact words from chat")
    for p in s.choices.values():
        p.set_defaults(func=cmd_decide)


def board(snap, m):
    cards = [c for c in (snap.get("cards") or {}).values() if c.get("answer") is None and not moot_reason(snap, c)]
    import views
    m["cards"] = [{"id": c["id"], "unit": c.get("unit"), "repo": c.get("repo"), "question": c["question"],
                   "context": c.get("context"),
                   "outline": views.outline(c["question"]) + views.outline(c.get("context")),
                   "options": c["options"], "recommended": c.get("recommended"), "reason": c.get("reason"),
                   "asked_by": c.get("asked_by"), "opened": c.get("opened"), "deadline": c.get("deadline"), "default": c.get("default")}
                  for c in sorted(cards, key=lambda c: c.get("opened") or "")]
    m["cards_open"] = len(cards)
