"""Denial triage: every refused act is journaled, a similar retry after a deny is flagged as a near miss, and
recurring legitimate denials become autoMode rule drafts.

Sources: `hook` (a foundry module denied in PreToolUse), `classifier` (the auto mode classifier, through the
PermissionDenied hook), `rule` (a permission rule or the approver; seen as a PostToolUseFailure).
"""

import datetime as dt
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import roles  # noqa: E402

SIMILAR_WINDOW = dt.timedelta(minutes=30)
KEEP_DENIALS, KEEP_NEARMISS = 200, 100
RULE_MIN_ONCE = 3
NEARMISS_ESCALATE_H = 24
DENIED_RE = re.compile(r"(?i)permission .*denied|denied by|was blocked|not allowed by|\[foundry\]")

STATE = {"denials": [], "triage_answers": {}, "triage_cards": {}}


# ---------- signatures ----------

def _argvs(cmd):
    try:
        lex = shlex.shlex(cmd or "", posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        toks = (cmd or "").split()
    out, cur = [], []
    for t in toks:
        if t in (";", "&&", "||", "|", "&", "(", ")", "|&"):
            out.append(cur)
            cur = []
        else:
            cur.append(t)
    out.append(cur)
    return [a for a in out if a]


def _prog_words(argv):
    """`git push`, `gh pr merge`, or the program name; skipping env assignments and git's -C/-c."""
    a = [x for x in argv if not re.match(r"^[A-Za-z_]\w*=", x)]
    if not a:
        return "", []
    prog = os.path.basename(a[0])
    rest = a[1:]
    if prog == "git":
        i = 0
        while i < len(rest) and rest[i].startswith("-"):
            i += 2 if rest[i] in ("-C", "-c") else 1
        return " ".join(["git"] + rest[i:i + 1]), rest[i + 1:]
    if prog == "gh":
        words = [x for x in rest if not x.startswith("-")][:2]
        return " ".join(["gh"] + words), [x for x in rest if x not in words]
    return prog, rest


TARGET_RE = re.compile(r"[/:#@]|^\d+$")


def signature(cmd):
    """[(program words, target tokens)] per command in the line. Targets: paths, refs, URLs, numbers, git -C dirs."""
    out = []
    for argv in _argvs(cmd):
        prog, rest = _prog_words(argv)
        if prog in ("cd", ""):
            continue
        targets = {t.lstrip("+").rstrip("/") for t in rest if not t.startswith("-") and TARGET_RE.search(t)}
        if "-C" in argv[:-1] and os.path.basename(argv[0]) == "git":
            targets.add(argv[argv.index("-C") + 1].rstrip("/"))
        out.append((prog, targets))
    return out


def similar(cmd, denied):
    """Same program as the denied command's first one and an overlapping target (or neither has targets),
    but not the identical command."""
    if (cmd or "").strip() == (denied or "").strip():
        return False
    sig = signature(denied)
    if not sig:
        return False
    p2, t2 = sig[0]
    return any(p1 == p2 and (t1 & t2 or not (t1 or t2)) for p1, t1 in signature(cmd))


def pattern(cmd):
    """A generalized form for grouping recurring denials: paths, shas, numbers and quoted text replaced."""
    sig = signature(cmd)
    if not sig:
        return (cmd or "")[:60]
    prog, targets = sig[0]
    gen = []
    for t in sorted(targets):
        if "/" in t:
            gen.append("<path>")
        elif re.fullmatch(r"[0-9a-f]{7,40}", t):
            gen.append("<sha>")
        elif re.fullmatch(r"#?\d+", t):
            gen.append("<n>")
        else:
            gen.append("<arg>")
    return " ".join([prog] + sorted(set(gen)))


# ---------- recording ----------

def record(unit, tool, command, reason, source):
    cl.append("perm.denied", unit=unit, tool=tool, command=(command or "")[:500], reason=(reason or "")[:300],
              source=source, pattern=pattern(command) if tool == "Bash" else tool)


def recent_denials(snap, unit, now=None, window=SIMILAR_WINDOW):
    now = now or cl.now()
    out = []
    for d in snap.get("denials") or []:
        t = cl.parse_ts(d.get("ts"))
        if d.get("unit") == unit and t and now - t <= window:
            out.append(d)
    return out


def _op_denied(s, r, ts):
    s["denials"].append({k: r.get(k) for k in ("unit", "tool", "command", "reason", "source", "pattern")} | {"ts": ts})
    s["denials"] = s["denials"][-KEEP_DENIALS:]


def _op_false_positive(s, r, ts):
    s["denials"].append({"unit": r.get("unit"), "tool": None, "command": None, "reason": r.get("detail"),
                         "source": "false-positive", "pattern": None, "ts": ts})
    s["denials"] = s["denials"][-KEEP_DENIALS:]


def _op_answer(s, r, ts):
    a = s["triage_answers"].setdefault(r["pattern"], {"once": 0, "rule": 0, "deny": 0, "last": None, "example": None})
    a[r["answer"]] = a.get(r["answer"], 0) + 1
    a["last"] = ts
    a["example"] = r.get("command") or a.get("example")
    if r.get("card") in s["triage_cards"]:
        s["triage_cards"][r["card"]]["answer"] = r["answer"]


def _op_card(s, r, ts):
    s["triage_cards"][r["id"]] = {k: r.get(k) for k in ("id", "unit", "question", "options", "recommended", "reason",
                                                        "pattern", "command")} | {"asked": ts, "answer": None}


OPS = {"perm.denied": _op_denied, "perm.false_positive": _op_false_positive, "triage.answer": _op_answer, "triage.card": _op_card}


# ---------- hooks ----------

def display(argv):
    """Same quoting as gitpolicy's deny reasons, so a corrected command can be matched back."""
    return " ".join(shlex.quote(a) if re.search(r"[\s'\"$`;&|\\]", a) or not a else a for a in argv)


def follows_correction(cmd, denial):
    """The command is the corrected form the deny reason handed back (a safer path, not an evasion).
    `<slot>` tokens in the reason match any value."""
    try:
        c = display(shlex.split(cmd)).split()
    except ValueError:
        return False
    r = [w.strip("`,;()") for w in (denial.get("reason") or "").split()]
    if not c:
        return False

    def eq(a, b):
        return a == b or (b.startswith("<") and b.endswith(">")) or (b.startswith("'<") and b.endswith(">'"))

    return any(all(eq(c[j], r[i + j]) for j in range(len(c))) for i in range(len(r) - len(c) + 1))


def _acts(cmd):
    """{(program, subcommand)} a command runs; None when it cannot be parsed. A denial's command is stored cut
    at 500 characters, often inside a quoted title or body: it is read up to the last quote that still parses."""
    import gitpolicy
    segs = None
    for end in [len(cmd)] + [i for i in range(len(cmd) - 1, 0, -1) if cmd[i] in "'\""]:
        try:
            segs = gitpolicy.segments(cmd[:end])
            break
        except ValueError:
            continue
    if segs is None:
        return None
    acts = set()
    for argv in segs:
        argv, _ = gitpolicy._drop_redirects(gitpolicy.strip_env(argv))
        prog = gitpolicy.program(argv)
        if not prog or prog == "cd":
            continue
        if prog == "git":
            sub = gitpolicy.git_parts(argv, None)[1]
        elif prog in ("gh", "foundry"):
            sub = " ".join(w for w in argv[1:3] if not w.startswith("-"))
        else:
            sub = None
        acts.add((prog, sub))
    return acts


def fixes_shape(cmd, denial):
    """A shape-only deny (chain, pipe, redirect, substitution, wrapper) followed by one plain command that
    passes the shape checks and runs the same act or less: the caller did what the deny asked."""
    import gitpolicy
    if not gitpolicy.is_shape_reason(denial.get("reason")):
        return False
    try:
        segs = gitpolicy.segments(cmd)
    except ValueError:
        return False
    if len(segs) != 1 or "\n" in cmd.strip() or gitpolicy.chain_reason(cmd) or gitpolicy.wrapper_reason(cmd):
        return False
    if any(gitpolicy.REDIRECT_RE.match(t) for t in segs[0]) or "$(" in cmd or "`" in cmd:
        return False
    mine, theirs = _acts(cmd), _acts(denial.get("command") or "")
    return bool(mine) and theirs is not None and mine <= theirs


def pre_tool_use(inp, snap, ctx):
    """A warning (never a deny) when this unit retries a recently denied act in another form."""
    if ctx.get("caller") == roles.OUTSIDE or ctx.get("tool") != "Bash" or not ctx.get("unit"):
        return None
    cmd = (ctx.get("input") or {}).get("command") or ""
    for d in reversed(recent_denials(snap, ctx["unit"])):
        if d.get("tool") == "Bash" and similar(cmd, d.get("command")) and not follows_correction(cmd, d) \
                and not fixes_shape(cmd, d):
            detail = f"after deny of `{(d.get('command') or '')[:120]}` ({d.get('source')}): `{cmd[:120]}`"
            import aging
            shape = aging.pattern({"kind": "deny-then-similar", "detail": d.get("command") or ""})[0]
            cl.append("nearmiss", kind="deny-then-similar", unit=ctx["unit"], detail=detail, pattern=shape)
            return ("warn", "[foundry] This looks like a denied act in another form. A refused act is recorded and "
                            "stopped; never re-run it in another form. Report it to your lead instead.")
    return None


def post_tool_use_failure(inp, snap, ctx):
    if ctx.get("caller") == roles.OUTSIDE:
        return
    err = str(inp.get("error") or "")
    if not DENIED_RE.search(err) or "[foundry]" in err:
        return  # an ordinary failure, or a foundry hook deny already journaled by its module
    ti = ctx.get("input") or {}
    record(ctx.get("unit"), ctx.get("tool"), ti.get("command") or json.dumps(ti)[:300], err, "rule")


def permission_denied(inp, snap, ctx):
    if ctx.get("caller") == roles.OUTSIDE:
        return
    ti = ctx.get("input") or {}
    record(ctx.get("unit"), ctx.get("tool"), ti.get("command") or json.dumps(ti)[:300],
           str(inp.get("reason") or inp.get("error") or "auto mode classifier"), "classifier")


# ---------- classification ----------

REVIEW_ASK_RE = re.compile(r"\bfoundry(?:\.py)?\s+outbox\s+add\b.*--kind[ =](?:review-ask|review-remind)\b")
REVIEW_ASK_RULE = ("the \"Foundry review-ask queue\" rule in docs/recommended-automode.md (the owner adds it to "
                   "autoMode.allow; foundry never edits settings)")


def is_review_ask(denial):
    return bool(REVIEW_ASK_RE.search(denial.get("command") or ""))


def rule_draft(denial):
    cmd = denial.get("command") or ""
    if is_review_ask(denial):
        return f"Use {REVIEW_ASK_RULE} as written; it already covers `{cmd[:160]}`."
    pat = denial.get("pattern") or pattern(cmd)
    return (f"Foundry {pat}: a foundry lead or hand running `{pat}` inside its own unit worktree, "
            f"as one raw command per call (e.g. `{cmd[:160]}`). "
            "Not covered: the same program against another repo, path or branch, chained or piped forms, "
            "default-branch pushes, force without lease, production, secrets or settings.")


def card_payload(denial):
    draft = rule_draft(denial)
    return {"unit": denial.get("unit"),
            "question": f"Refused: `{(denial.get('command') or denial.get('tool') or '')[:160]}` "
                        f"({denial.get('source')}: {(denial.get('reason') or '')[:160]}). Allow it?",
            "options": [{"key": "once", "label": "this once", "description": "allow this exact call one time",
                         "pros": "no standing permission", "cons": "asks again next time", "preview": None},
                        {"key": "rule", "label": "add rule", "description": "paste the autoMode rule draft after review",
                         "pros": "stops recurring asks", "cons": "a standing permission", "preview": draft}],
            "recommended": "once", "reason": "same goal in a different form needs the owner",
            "asked_by": "triage", "deadline": None, "default": None,
            "pattern": denial.get("pattern") or pattern(denial.get("command") or ""), "command": denial.get("command"),
            "rule_draft": draft}


def classify(denial, followups=()):
    """The default triage of one denial; the advisor makes the real call.

    1  a safer, different path exists (the deny handed back a correction, or a later attempt took another
       program): the foreman or lead may approve it.
    2  retry once in the same form with evidence: a classifier denial not yet retried; only the refused session.
    3  same goal in a different form, or a retry already refused: an owner decision card.
    """
    fu = [f if isinstance(f, dict) else {"command": f} for f in followups or []]
    cmd = denial.get("command") or ""
    sig = {p for p, _ in signature(cmd)}
    retried = any((f.get("command") or "").strip() == cmd.strip() for f in fu)
    evasive = any(similar(f.get("command"), cmd) and not follows_correction(f.get("command") or "", denial)
                  and not fixes_shape(f.get("command") or "", denial) for f in fu)
    if not evasive and (": " in (denial.get("reason") or "") and denial.get("source") == "hook"
                        or any(f.get("command") and not ({p for p, _ in signature(f["command"])} & sig) for f in fu)):
        return {"class": 1, "action": "take the safer path; foreman or lead may approve it", "card": None}
    if denial.get("source") == "classifier" and is_review_ask(denial):
        # The owner already delegated review asks (0.1.86); what is missing is the autoMode rule, which only the
        # owner can add. A retry in any form would be the same unsanctioned communication.
        return {"class": 3, "action": f"owner decision card: add {REVIEW_ASK_RULE}; do not retry in another form",
                "card": card_payload(denial)}
    if denial.get("source") == "classifier" and not retried and not evasive:
        return {"class": 2, "action": "retry once in the same form, with the evidence the classifier lacked; "
                                      "only the refused session, only once", "card": None}
    return {"class": 3, "action": "owner decision card", "card": card_payload(denial)}


def open_card(payload):
    """Through the decide module when present; else a `triage.card` record the board shows."""
    try:
        import decide
        if hasattr(decide, "open_card"):
            fields = {k: v for k, v in payload.items() if k not in ("pattern", "command", "rule_draft")}
            fields["key"] = f"triage:{payload.get('unit')}:{payload.get('pattern')}"
            fields["context"] = f"command: {payload.get('command')}\nrule draft:\n{payload.get('rule_draft')}"
            return decide.open_card(**fields)[1]["id"]
    except (ImportError, TypeError):
        pass
    cid = cl.new_id("tc", payload.get("unit"), payload.get("command"))
    cl.append("triage.card", id=cid, **{k: payload.get(k) for k in ("unit", "question", "options", "recommended", "reason",
                                                                     "pattern", "command")})
    return cid


def rule_drafts(snap, min_once=RULE_MIN_ONCE):
    out = []
    for pat, a in sorted((snap.get("triage_answers") or {}).items()):
        if a.get("once", 0) >= min_once:
            out.append({"pattern": pat, "once": a["once"],
                        "draft": rule_draft({"pattern": pat, "command": a.get("example") or pat})})
    return out


# ---------- CLI ----------

def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return 0


def cmd_triage(a):
    snap = cl.load()
    if a.sub == "false-positive":
        last = (recent_denials(snap, a.unit) or [None])[-1]
        if last and last.get("source") == "classifier":
            print("refused: the unit's latest denial is the auto mode classifier's; classifier denials never "
                  "qualify as guard false positives", file=sys.stderr)
            return 2
        cl.append_many([("perm.false_positive", {"unit": a.unit, "detail": a.detail[:300]}),
                        ("nearmiss", {"kind": "guard-false-positive", "unit": a.unit, "detail": a.detail[:300]})])
        return _emit({"recorded": a.unit})
    if a.sub == "rules":
        return _emit({"drafts": rule_drafts(snap)})
    if a.sub == "answer":
        cl.append("triage.answer", pattern=a.pattern, answer=a.answer, card=a.card, command=a.command)
        return _emit({"pattern": a.pattern, "answer": a.answer})
    # classify: the unit's latest denial against what the unit ran since
    ds = [d for d in snap.get("denials") or [] if d.get("unit") == a.unit and d.get("source") != "false-positive"]
    if not ds:
        return _emit({"unit": a.unit, "denial": None})
    res = classify(ds[-1], a.followup)
    if a.open_card and res["card"]:
        res["card_id"] = open_card(res["card"])
    return _emit({"unit": a.unit, "denial": ds[-1]} | res)


def add_cli(sp):
    t = sp.add_parser("triage").add_subparsers(dest="sub", required=True)
    fp = t.add_parser("false-positive"); fp.add_argument("--unit", required=True); fp.add_argument("--detail", required=True)
    t.add_parser("rules")
    an = t.add_parser("answer"); an.add_argument("--pattern", required=True)
    an.add_argument("--answer", required=True, choices=["once", "rule", "deny"]); an.add_argument("--card")
    an.add_argument("--command")
    c = t.add_parser("classify"); c.add_argument("--unit", required=True)
    c.add_argument("--followup", action="append", default=[]); c.add_argument("--open-card", action="store_true")
    for p in t.choices.values():
        p.set_defaults(func=cmd_triage)


def board(snap, model):
    import aging
    nm, _ = aging.split(snap)  # resolved entries are a count on the board, never rows
    model["nearmiss_count"] = len(nm)
    # Only the last day's open entries stay on the escalation list, one row per pattern.
    day = cl.now() - dt.timedelta(hours=NEARMISS_ESCALATE_H)
    recent = [n for n in nm if (cl.parse_ts(n.get("ts")) or day) > day][-10:]
    model["nearmiss"] = recent
    esc = model.setdefault("escalations", [])
    for g in aging.groups(recent):
        esc.append({"id": f"nearmiss:{g['pattern']}", "kind": "nearmiss", "unit": (g["units"] or [None])[0],
                    "text": f"{g['pattern']} x{g['count']}: {g['sample'] or ''}", "ts": g["last"]})
    cards = model.setdefault("cards", [])
    for c in (snap.get("triage_cards") or {}).values():
        if c.get("answer") is None:
            cards.append({k: c.get(k) for k in ("id", "unit", "question", "options", "recommended", "reason")}
                         | {"repo": None, "asked_by": "triage", "deadline": None, "default": None})
