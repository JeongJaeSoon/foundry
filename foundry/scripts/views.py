"""What the owner reads: one Korean headline per decision, a PR tab, and a unit detail that leads with status,
next action and who acts. Built once into the board model, so the pane, board.html and the text board agree."""

import os
import re
import sys
from collections.abc import Mapping

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import i18n  # noqa: E402


def _tr(k, **kw):
    return i18n.t("views." + k, **kw)


class Labels(Mapping):
    """code → its label in the UI language, looked up on every read (never at import); unknown codes raise
    KeyError, so `.get(code, default)` behaves as on a dict."""

    def __init__(self, keys, ns="views"):
        self._keys = dict(keys)
        self._ns = ns

    def __getitem__(self, code):
        return i18n.t(f"{self._ns}.{self._keys[code]}")

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)


SHA = re.compile(r"\b[0-9a-f]{12,40}\b")
HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
SENT = re.compile(r"(?<=[.!?。])\s+")
GREEN = ("success", "pass", "none")
WHO_KO = Labels({"foundry": "who_foundry", "reviewer": "reviewer", "owner": "who_owner", "others": "who_others"})
PR_STATE_KO = Labels({"draft": "draft", "review": "review_wait", "approved": "approved_ci_wait",
                      "merge-wait": "merge_wait", "release-wait": "release_wait"})
WAIT_KO = Labels({"pr-review": "review", "pr-merge": "merge", "ci": "ci", "jira": "jira", "time": "time",
                  "team": "other_team", "deploy-window": "deploy_window"})
STATUS_KO = Labels({"queue": "status_queue", "in_progress": "status_in_progress",
                    "waiting_others": "status_waiting_others", "waiting_owner": "owner_check", "done": "status_done",
                    "parked": "status_parked", "blocked": "status_blocked", "cancelled": "status_cancelled"})
OWNER_RE = re.compile(r"\b(?:owner (?:must|needs? to|should|has to)|needs? (?:the )?owner|for the owner to)\b|오너가", re.I)
DISPATCH_RE = re.compile(r"\bdispatcher\b|디스패처", re.I)


def short_sha(text):
    return SHA.sub(lambda m: m.group(0)[:7], str(text or ""))


def gist(text, n=90):
    """The first sentence, without a `[report]` tag, SHAs shortened, cut at n characters."""
    t = re.sub(r"^\s*(?:\[[\w -]+\]\s*)+", "", " ".join(short_sha(text).split()))
    s = SENT.split(t, 1)[0]
    return s if len(s) <= n else s[:n - 1] + "…"


def _h(since, t):
    ts = cl.parse_ts(since)
    return None if not ts else max(0, round((t - ts).total_seconds() / 3600))


# ---------- decisions ----------

def blocks_of(c, snap):
    key = c.get("key") or ""
    for pre in ("pr-aging:", "pr-orphan:"):
        if key.startswith(pre):
            return f"PR {key[len(pre):]}"
    if key.startswith("nearmiss:"):
        return _tr("blocks_nearmiss")
    u = (snap.get("units") or {}).get(c.get("unit") or "")
    if u:
        uid = u.get("id") or c.get("unit")
        return _tr("blocks_unit_progress", unit=uid) if u.get("status") == "waiting_owner" else uid
    return _tr("none")


def headline(c, snap):
    """`unit · question (one sentence) · 추천 X · 막는 것 Y`: the full text is `foundry decide show <id>`."""
    rec = c.get("recommended")
    label = next((o.get("label") for o in c.get("options") or [] if o.get("key") == rec), None)
    if not rec:
        rec_t = _tr("answer_needed")
    elif label and label != rec:
        rec_t = _tr("rec_label", rec=rec, label=gist(label, 24))
    else:
        rec_t = _tr("rec", rec=rec)
    first = next((x for x in str(c.get("question") or "").splitlines() if x.strip()), "") or c.get("id")
    return _tr("headline", question=gist(BULLET_RE.sub('', first), 80), rec=rec_t, blocks=blocks_of(c, snap))


# Section labels a card's prose is cut at when it was written as one paragraph (old cards, terse leads).
OUTLINE_LABELS = ("확인 불가", "확인됨", "미확인", "미검증", "참고", "배경", "근거", "이유", "현황", "영향", "질문",
                  "옵션 [A-Z]", "선택지 [A-Z]", "추천", "막는 것", "다음")
LABEL_RE = re.compile(r"(?:^|(?<=\s))(" + "|".join(OUTLINE_LABELS) + r")\s*:\s*")
ENUM_RE = re.compile(r"(?:^|\s)\((\d{1,2})\)\s+")
BULLET_RE = re.compile(r"^(\s*)(?:[-*•◦・]|\d{1,2}[.)])\s+")


def _top_split(text, seps):
    """text cut at seps outside brackets and quotes."""
    parts, depth, cur, i = [], 0, "", 0
    while i < len(text):
        ch = text[i]
        depth += ch in "([{「" and 1 or 0
        depth -= ch in ")]}」" and depth > 0 and 1 or 0
        sep = next((x for x in seps if depth == 0 and text.startswith(x, i)), None)
        if sep:
            parts.append(cur)
            cur, i = "", i + len(sep)
            continue
        cur += ch
        i += 1
    return [x.strip() for x in parts + [cur] if x.strip()]


def _items(body):
    """A label's body as items: its (1) (2) enumeration, else `;`-separated parts, else three or more long
    comma-separated facts; else the body itself, one line."""
    body = body.strip().rstrip(".。").strip()
    marks = list(ENUM_RE.finditer(body))
    if len(marks) >= 2:
        head = body[:marks[0].start()].strip()
        cuts = [m.end() for m in marks] + [None]
        starts = [m.start() for m in marks[1:]] + [len(body)]
        items = [body[a:b].strip().rstrip(".,;。").strip() for a, b in zip(cuts, starts)]
        return head, [x for x in items if x]
    for seps, least, longest in (([";", "；"], 2, 0), ([", ", "、"], 3, 15)):
        parts = _top_split(body, seps)
        if len(parts) >= least and all(len(x) >= longest for x in parts):
            return "", [x.rstrip(".。").strip() for x in parts]
    return body, []


def outline(text):
    """A card's text as an outline, [{depth, text}], for every renderer to draw as bullets (개조식). Text written
    with line breaks keeps them: `- ` / `* ` / `1.` lines are bullets, two spaces of indent per level. A single
    paragraph is cut at its section labels (확인됨, 확인 불가, 참고, 옵션 A, 추천, 막는 것, ...) and at (1) (2)
    enumerations: each label on its own line, its items one level in. Plain prose with neither: one line."""
    text = str(text or "").strip()
    if not text:
        return []
    if "\n" in text:
        out = []
        for line in text.splitlines():
            if not line.strip():
                continue
            m = BULLET_RE.match(line)
            indent = len(m.group(1).expandtabs(2)) if m else len(line) - len(line.lstrip())
            out.append({"depth": min(indent // 2, 3), "text": line[m.end():].strip() if m else line.strip()})
        return out
    labels = list(LABEL_RE.finditer(text))
    if not labels and len(list(ENUM_RE.finditer(text))) < 2:
        return [{"depth": 0, "text": text}]
    out = []
    head = text[:labels[0].start()] if labels else text
    if head.strip():
        lead, items = _items(head) if not labels else (head.strip().rstrip(".。").strip(), [])
        out += [{"depth": 0, "text": lead}] if lead else []
        out += [{"depth": 1, "text": x} for x in items]
    for m, nxt in zip(labels, labels[1:] + [None]):
        lead, items = _items(text[m.end():nxt.start() if nxt else len(text)])
        out.append({"depth": 0, "text": f"{m.group(1)}: {lead}" if lead else m.group(1)})
        out += [{"depth": 1, "text": x} for x in items]
    return out


def outline_text(text, indent="  "):
    """The outline as plain text lines, `- ` bullets, two spaces per level."""
    return [f"{indent}{'  ' * x['depth']}- {x['text']}" for x in outline(text)]


# ---------- PRs ----------

GH_KO = Labels({"open": "gh_open", "draft": "draft", "merged": "gh_merged", "closed": "gh_closed"})
GH_REVIEW_KO = Labels({"approved": "approved", "changes": "changes_requested", "pending": "review_wait"})


def gh_state(stage=None, state=None, draft=None):
    """GitHub's own lifecycle word for a PR, the one its colours follow: open / draft / merged / closed."""
    st = str(state or "").upper()
    if st == "MERGED" or stage in ("merged", "release-wait", "released", "deployed-dev", "prod-waiting", "deployed"):
        return "merged"
    if st == "CLOSED":
        return "closed"
    if draft or stage == "draft" or st == "DRAFT":
        return "draft"
    return "open"


def gh_review(review=None, stage=None):
    """The review accent: approved, changes requested, or pending while a review is asked for."""
    rv = str(review or "").upper()
    if rv == "CHANGES_REQUESTED":
        return "changes"
    if rv == "APPROVED" or stage in ("approved", "merge-wait"):
        return "approved"
    return "pending" if stage in ("review", "ci-running", "ci-failing") or rv == "REVIEW_REQUIRED" else None


def pr_state(stage, ci):
    if stage == "approved":
        return "merge-wait" if (ci or "").lower() in GREEN else "approved"
    return {"merged": "release-wait"}.get(stage, stage)


def next_action(state, act):
    """A fixed phrase and who acts: the reviewer's name has its own column, a wait's note never reaches a row."""
    if act == "adopt-or-drop":
        return _tr("act_adopt_or_drop"), "owner"
    if act == "owner-decision":
        return _tr("act_owner_decision"), "owner"
    if act == "remind":
        return _tr("act_remind"), "foundry"
    if act == "ask-pending":
        return _tr("act_ask_pending"), "foundry"
    if act == "no-ask-thread":
        return _tr("act_no_ask_thread"), "foundry"
    k, who = {"review": ("review", "reviewer"),
              "approved": ("act_ci_pass", "foundry"), "merge-wait": ("merge", "foundry"),
              "release-wait": ("release", "foundry"),
              "draft": ("act_finish_draft", "foundry")}.get(state, (None, "foundry"))
    return (_tr(k) if k else "-"), who


def reviewer_of(r):
    """Reviewers the owner or foundry asked, else who approved (✓): only what gh reported for the PR. CODEOWNERS,
    team and bot requests ride along as `reviewer_auto`, drawn dim, never here (review.split_reviewers)."""
    return ", ".join(r.get("reviewers") or []) or ", ".join(f"✓{x}" for x in r.get("approvers") or [])


def unit_url(snap, uid):
    """The unit's tracker page (a GitHub issue, or Jira under config jira_base): its first ticket key (the unit id itself when it is one), under config jira_base."""
    u = (snap.get("units") or {}).get(uid) or {}
    keys = list(((u.get("keys") or {}).get("ticket")) or []) + [uid]
    for k in keys:
        m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", str(k))
        url = f"https://github.com/{m.group(1)}/issues/{m.group(2)}" if m else jira_url(k)
        if url:
            return url
    return None


def pr_rows(snap, m):
    import aging
    inv = m.get("inventory") or {}
    acts = aging.actions(snap, inv=inv) if inv.get("repos") else {}
    out = []
    ignored = set(((snap.get("intake") or {}).get("ignored") or {}))
    m["pr_ignored"] = 0
    for rows in (inv.get("repos") or {}).values():
        for r in rows:
            if r["kind"] != "pr":
                continue
            if str(r["ref"]).lower() in ignored:  # prefs review.ignore: hidden, counted
                m["pr_ignored"] += 1
                continue
            state = pr_state(r.get("review_stage") or r.get("stage"), r.get("ci"))
            revs = reviewer_of(r)
            act, who = next_action(state, acts.get(r["ref"]))
            pick = (((snap.get("units") or {}).get(r.get("unit")) or {}).get("review_picks") or {}).get(r["ref"]) or {}
            out.append({"ref": r["ref"], "title": r.get("title") or "", "unit": r.get("unit"), "state": state,
                        "gh": r.get("gh") or gh_state(r.get("stage")), "gh_review": r.get("gh_review"),
                        "pick": pick.get("reason"),
                        "state_ko": PR_STATE_KO.get(state, state or "-"), "reviewer": revs or "-",
                        "reviewer_auto": list(r.get("auto_reviewers") or []), "url": r.get("url"),
                        "repo": r["ref"].rpartition("#")[0], "short": short_ref(r["ref"]),
                        "unit_url": unit_url(snap, r.get("unit")),
                        "age_h": r.get("age_h"), "stale": bool(r.get("stale")), "action": act, "who": who,
                        "who_ko": WHO_KO[who]})
    return sorted(out, key=lambda x: -(x["age_h"] or 0))


# The PR counts every surface shows (재고's line, the PR tab's stage chart, the board.html tab, the handover):
# one function over the PR tab's rows, so no two of them can disagree.
# Each bucket: (key, catalog key of its label, the PR states it counts).
PR_BUCKETS = (("review", "review_wait", ("review",)), ("merge", "merge_wait", ("approved", "merge-wait")),
              ("release", "release_wait", ("release-wait",)), ("draft", "draft", ("draft",)))


def pr_counts(rows):
    """{total, stale, buckets: [{key, label, n}], text: "PR 16 · 리뷰 대기 11 · 머지 대기 1 · 릴리스 대기 4"}."""
    rows = list(rows or [])
    buckets = [{"key": k, "label": _tr(label), "n": sum(1 for r in rows if r.get("state") in states)}
               for k, label, states in PR_BUCKETS]
    buckets = [b for b in buckets if b["n"]]
    text = " · ".join([f"PR {len(rows)}"] + [f"{b['label']} {b['n']}" for b in buckets])
    return {"total": len(rows), "stale": sum(1 for r in rows if r.get("stale")), "buckets": buckets, "text": text}


# ---------- repo blocks, links, inventory state ----------

# A repo's accent: a soft background with a darker text of the same hue (every pair ≥ 4.5:1, tested), picked by a
# stable hash of the repo name. hooks/palette.ts carries the same list and hash, so the pane, board.html and the
# filter tabs give a repo one colour everywhere.
REPO_ACCENT = (("#DFE9F6", "#2B5E9E"), ("#DEF0E4", "#24704A"), ("#E6E0F8", "#5A40A8"), ("#F6E0F0", "#9A2E7A"),
               ("#D8EFF2", "#14646F"), ("#EEE5D3", "#74552B"), ("#F4F0C4", "#6E5A06"), ("#FBE3D0", "#8F4A12"))


def repo_hash(name):
    """djb2 over the name's UTF-16 code units, unsigned 32-bit: palette.ts repoHash computes the same number."""
    b = str(name or "").encode("utf-16-le")
    h = 5381
    for i in range(0, len(b), 2):
        h = (h * 33 + int.from_bytes(b[i:i + 2], "little")) & 0xFFFFFFFF
    return h


def _pair(i):
    bg, fg = REPO_ACCENT[i % len(REPO_ACCENT)]
    return {"bg": bg, "fg": fg}


def _override(v):
    """repos.json `accent`: a palette index (0-7), or one of the palette's hex colours (bg or fg). None if unusable."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v % len(REPO_ACCENT)
    if isinstance(v, str):
        for i, pair in enumerate(REPO_ACCENT):
            if v.strip().upper() in pair:
                return i
    return None


def accent_map(rs=None):
    """{name: accent} for the registered repos: an `accent` override first, then each repo in registration order
    takes the first colour no other registered repo holds, so up to 8 registered repos never share one. Keys are
    the registry name, the slug and the slug's short name, so `org/x`, `x` and the registry key all resolve."""
    if rs is None:
        try:
            import launcher
            rs = launcher.load_repos()
        except Exception:
            rs = {}
    idx, used = {}, set()
    for name, r in rs.items():
        i = _override((r or {}).get("accent") if isinstance(r, dict) else None)
        if i is not None:
            idx[name] = i
            used.add(i)
    for name in rs:
        if name in idx:
            continue
        free = next((i for i in range(len(REPO_ACCENT)) if i not in used), None)
        if free is None:
            break  # more than 8 registered: the rest fall back to the hash
        idx[name] = free
        used.add(free)
    out = {}
    for name, i in idx.items():
        r = rs.get(name) if isinstance(rs.get(name), dict) else {}
        for k in {name, short_repo(name), r.get("slug") or "", short_repo(r.get("slug") or "")} - {""}:
            out.setdefault(k, _pair(i))
    return out


def repo_accent(name, amap=None):
    """A registered repo's allocated accent (accent_map); an unregistered one's by the name's hash."""
    hit = (amap or {}).get(name) or (amap or {}).get(short_repo(name))
    return dict(hit) if hit else _pair(repo_hash(name))


def short_repo(name):
    """`owner/repo` → `repo`: the owner is the same on every row; the number after `#` is never cut."""
    return str(name or "").rsplit("/", 1)[-1]


def short_ref(ref):
    repo, _, num = str(ref or "").rpartition("#")
    return f"{short_repo(repo)}#{num}" if repo else str(ref or "")


def jira_base():
    """config.json `jira_base`, else prefs `jira_base` (the non-secret settings file); None when neither names it.
    Preflight's trackers check names the missing key when a Jira tracker is in use."""
    base = cl.load_config().get("jira_base")
    if not base:
        try:
            import prefs
            base = prefs.load().get("jira_base")
        except Exception:
            base = None
    return base if isinstance(base, str) and base.startswith("https://") else None


def jira_url(key, base=None):
    """A ticket's Jira page under jira_base (jira_url_base); None otherwise (no link drawn)."""
    if base is None:
        base = jira_base()
    if not base or not key or not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", str(key)):
        return None
    return f"{str(base).rstrip('/')}/browse/{key}"


def age_tone(h):
    """경과's colour: fresh (< 4h) dim, over a day warning, between plain."""
    if h is None:
        return "dim"
    return "dim" if h < 4 else ("warn" if h > 24 else None)


def inv_state(it):
    """What a 재고 row is doing, in place of 「리드 대기」: a branch says its PR's state and how far it is ahead of the
    default branch; a worktree says its unit's status, and a finished unit's worktree its reclaim state."""
    if it.get("kind") == "branch":
        ahead = it.get("ahead")
        tail = f" · +{ahead}" if ahead else ""
        if it.get("gh"):
            return f"{PR_STATE_KO.get(pr_state(it.get('stage'), None), it.get('stage') or '-')}{tail}", it.get("gh")
        return f"{_tr('no_pr')}{tail}", None
    if it.get("kind") == "worktree":
        st = it.get("status")
        if it.get("reclaim"):
            return it["reclaim"], st
        return STATUS_KO.get(st, st or "-"), st
    return PR_STATE_KO.get(pr_state(it.get("review_stage") or it.get("stage"), None), it.get("stage") or "-"), None


def repo_blocks(inv, prs, repo=None, amap=None):
    """One block per repo for 재고, the PR tab and board.html: name, accent and 「PR 3 · 브랜치 5 · 워크트리 6」."""
    names = set(inv.get("repos") or {}) | {r["ref"].rpartition("#")[0] for r in prs or [] if "#" in r["ref"]}
    out = []
    for name in sorted(n for n in names if n):
        if repo and repo != "all" and name != repo:
            continue
        items = (inv.get("repos") or {}).get(name) or []
        n_pr = sum(1 for r in prs or [] if r["ref"].rpartition("#")[0] == name)
        n_br = sum(1 for it in items if it.get("kind") == "branch")
        n_wt = sum(1 for it in items if it.get("kind") == "worktree")
        counts = " · ".join(f"{k} {n}" for k, n in (("PR", n_pr), (_tr("branch"), n_br), (_tr("worktree"), n_wt)) if n)
        out.append({"repo": name, "short": short_repo(name), "accent": repo_accent(name, amap), "counts": counts,
                    "pr": n_pr, "branch": n_br, "worktree": n_wt})
    return out


# ---------- unit detail ----------

def _reclaim_note(x):
    """The sweep's word on a finished unit's worktree: why it stays, or that it went."""
    if not x:
        return None
    return _tr("kept", why=x["kept"]) if x.get("kept") else _tr("reclaimed")


def _cmd_of(a):
    s = a.get("summary") or ""
    tool = a.get("tool") or ""
    while tool and s.startswith(f"{tool}: "):
        s = s[len(tool) + 2:]
    return s


def readiness(a, prs):
    """(ready, reasons): a `gh pr merge` approval is ready only when its PR is approved, green and at that head."""
    cmd = _cmd_of(a)
    mm = re.search(r"\bgh\s+pr\s+merge\s+(\d+)\b.*?-R\s+(\S+)", cmd)
    if not mm:
        return True, []
    pr = next((p for p in prs if str(p.get("number")) == mm.group(1) and p.get("repo") == mm.group(2)), None)
    if not pr:
        return False, [_tr("pr_state_unknown")]
    why = []
    if str(pr.get("review") or "").upper() != "APPROVED":
        why.append(_tr("why_review", review=pr.get("review") or _tr("incomplete")))
    if (pr.get("ci") or "").lower() not in ("success", "pass"):
        why.append(_tr("why_ci", ci=pr.get("ci") or _tr("unknown")))
    if pr.get("merge_state") and pr["merge_state"] not in ("CLEAN", "HAS_HOOKS"):
        why.append(_tr("why_merge_state", state=pr["merge_state"]))
    pin = re.search(r"--match-head-commit\s+([0-9a-f]{7,40})", cmd)
    if pin and pr.get("head") and not pr["head"].startswith(pin.group(1)[:7]):
        why.append(_tr("why_head_moved", pinned=pin.group(1)[:7], head=pr["head"][:7]))
    return not why, why


def wait_stale(w, prs):
    """The sha a wait's note pins, when it is not the PR's current head (the stream re-pins it)."""
    pr = next((p for p in prs if f"{p.get('repo')}#{p.get('number')}" == w.get("ref")), None)
    head = (pr or {}).get("head") or ""
    pinned = [h for h in HEX.findall(w.get("note") or "") if not h.isdigit()]
    return next((h[:7] for h in pinned if head and not head.startswith(h[:7])), None), head[:7]


def _sentences(notes, rx):
    out = []
    for n in notes:
        for s in SENT.split(" ".join(str(n.get("text") or "").split())):
            mm = rx.search(s)
            if mm:
                # A long sentence keeps the clause that asks: from the last `;` or `(` before the match.
                cut = max(s.rfind(";", 0, mm.start()), s.rfind("(", 0, mm.start()))
                g = gist(s[cut + 1:].strip() if len(s) > 140 and cut >= 0 else s, 140)
                if g not in out:
                    out.append(g)
    return out


UNIT_CELL = 14
UNIT_KEY = re.compile(r"[A-Z][A-Z0-9]+-\d+|#\d+")


def _cols(x):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in x)


def _cut(x, w):
    """The first `w` columns of x (at least one character)."""
    out, n = "", 0
    for c in x:
        if n + _cols(c) > w and out:
            break
        out, n = out + c, n + _cols(c)
    return out


def short_unit(uid, w=UNIT_CELL):
    """A unit id for a table's 단위 column: at most `w` columns, cut in the middle, keeping a ticket key (PROJ-794)
    or a PR number (#12) and the id's end. The same rule as the pane's shortUnit (hooks/board-pane.tsx)."""
    uid = str(uid or "")
    if _cols(uid) <= w:
        return uid
    m = UNIT_KEY.search(uid)
    if m:
        head, tail = uid[:m.end()], uid[m.end():]
        if _cols(head) <= w - 2:
            room = max(1, w - 1 - _cols(head))
            return head + "…" + tail[-room:].lstrip("-")
        if _cols(uid[m.start():]) <= w - 2:
            return _cut(uid, max(1, w - 1 - _cols(uid[m.start():]))) + "…" + uid[m.start():]
    right, tail = (w - 1 + 1) // 2, ""
    for c in reversed(uid):
        if _cols(c + tail) > right:
            break
        tail = c + tail
    return _cut(uid, w - 1 - _cols(tail)) + "…" + tail


PR_NUM = re.compile(r"(?:[\w.-]+/)?[\w.-]+#(\d+)|(?<![\w/])#(\d+)\b")
QUIET_RE = re.compile(r"nothing outbound|no(?:thing)? (?:outbound|to send)|보낼 것 없음", re.I)
DONE_PR = ("MERGED", "CLOSED")


def pr_url(p):
    """A PR's page: the recorded url, else GitHub's page for an owner/repo slug; None when the repo is a bare name."""
    if p.get("url"):
        return p["url"]
    repo = str(p.get("repo") or "")
    return f"https://github.com/{repo}/pull/{p.get('number')}" if "/" in repo and p.get("number") else None


def _pr_brief(p):
    return {"ref": f"{p.get('repo')}#{p.get('number')}", "url": pr_url(p), "title": p.get("title"),
            "gh": gh_state(None, p.get("state"), p.get("isDraft")), "gh_review": gh_review(p.get("review")),
            "ci": p.get("ci"), "merge_state": p.get("merge_state")}


def wait_unit(snap, uid, w):
    """The open unit a team/unit wait is waiting on: the ref itself when it names a unit, else a unit this one's
    inbox or notes name that shares its ticket, else the newest open unit sharing its ticket; None when unsure."""
    units = snap.get("units") or {}
    ref = str(w.get("ref") or "")
    if ref in units and ref != uid:
        return ref
    if w.get("kind") not in ("team", "unit"):
        return None
    me = units.get(uid) or {}
    tickets = set((me.get("keys") or {}).get("ticket") or []) | {me.get("tracker")} - {None, "none"}
    live = {k: u for k, u in units.items() if k != uid and u.get("kind") != "foreman"
            and u.get("status") not in ("done", "cancelled")
            and tickets & set((u.get("keys") or {}).get("ticket") or [])}
    text = " ".join(str(x.get("text") or "") for x in (me.get("inbox") or []) + (me.get("notes") or []))
    named = [k for k in live if k in text]
    pool = named or list(live)
    return max(pool, key=lambda k: str(live[k].get("created") or "")) if pool else None


def dispatcher_resolved(text, prs):
    """A dispatcher sentence nobody has to act on: one saying there is nothing to send, or one about PRs that are
    all merged or closed by now (a review ask for a merged PR)."""
    if QUIET_RE.search(text):
        return True
    nums = {a or b for a, b in PR_NUM.findall(text)}
    done = {str(p.get("number")) for p in prs if str(p.get("state") or "").upper() in DONE_PR}
    return bool(nums) and nums <= done


def unit_view(snap, r, t):
    u = (snap.get("units") or {}).get(r["id"]) or {}
    prs = [p for p in u.get("prs") or [] if isinstance(p, dict)]
    open_prs = [p for p in prs if str(p.get("state") or "").upper() == "OPEN"]
    waits = r.get("waits") or []
    status = r["status"]
    who, nxt, line = "foundry", "-", STATUS_KO.get(status, status)
    rw = next((w for w in waits if w["kind"] == "pr-review"), None)
    ci = next((w for w in waits if w["kind"] == "ci"), None)
    pr0 = open_prs[0] if open_prs else None
    if r.get("held"):
        line, nxt, who = _tr("held", reason=gist(r['held'].get('reason'), 60)), _tr("unhold"), "owner"
    elif status == "waiting_owner":
        q = (r.get("open_decisions") or [{}])[0].get("question")
        line, who = _tr("owner_decision_wait"), "owner"
        nxt = _tr("decide", question=gist(q, 70)) if q else _tr("owner_check")
    elif pr0 and str(pr0.get("review") or "").upper() == "APPROVED" and (pr0.get("ci") or "") in ("success", "pass"):
        line, nxt = _tr("mergeable", n=pr0.get('number')), _tr("merge")
        if any((a.get("tier") or 0) >= 3 for a in r.get("approvals") or []):
            nxt, who = _tr("merge_approve"), "owner"
    elif rw or (pr0 and str(pr0.get("review") or "").upper() != "APPROVED" and status == "waiting_others"):
        revs = ", ".join((pr0 or {}).get("reviewers") or []) or (
            re.sub(r"^review(?:er)?s?\s*(?:by)?\s*", "", (rw or {}).get("note") or "").strip() or _tr("reviewer"))
        age = _h((rw or {}).get("since") or (pr0 or {}).get("opened"), t)
        line = _tr("review_wait_who", who=revs) if age is None else _tr("review_wait_who_age", who=revs, age=age)
        nxt, who = _tr("review"), "reviewer"
    elif ci:
        age = _h(ci.get("since"), t)
        ref = (ci.get('ref') or '').split('/')[-1]
        line = _tr("ci_wait_ref", ref=ref) if age is None else _tr("ci_wait_ref_age", ref=ref, age=age)
        nxt = _tr("ci_done_wait")
    elif waits:
        w = waits[0]
        line = _tr("wait_paren", kind=WAIT_KO.get(w['kind'], w['kind']), ref=w.get('ref') or w.get('until') or '-')
        nxt, who = line, "others" if w["kind"] in ("jira", "team") else "foundry"
    elif status == "in_progress":
        nxt = _tr("lead_working")
    elif status == "queue":
        nxt = _tr("start_wait")
    blocked = [_tr("stalled", text=r['stall'].get('text') or r['stall'].get('kind')) for _ in [0] if r.get("stall")]
    blocked += [_tr("blocked_by", unit=b) for b in r.get("blocked_by") or []]
    blocked += [_tr("wait_ref", kind=WAIT_KO.get(w['kind'], w['kind']),
                    ref=w.get('ref') or w.get('until') or '').strip() for w in waits]

    wv = []
    for w in waits:
        old, head = wait_stale(w, prs)
        age = _h(w.get("since"), t)
        tu = wait_unit(snap, r["id"], w)
        tun = (snap.get("units") or {}).get(tu) or {}
        tprs = [p for p in tun.get("prs") or [] if isinstance(p, dict)]
        wv.append({"kind": w["kind"], "ref": w.get("ref"), "text": f"{WAIT_KO.get(w['kind'], w['kind'])} "
                   f"{w.get('ref') or w.get('until') or ''}".strip(), "note": gist(w.get("note"), 80),
                   "age_h": age, "stale": bool(old),
                   "stale_text": _tr("stale_sha", old=old, head=head) if old else None,
                   "unit": tu, "unit_status": tun.get("status") if tu else None,
                   "unit_status_ko": STATUS_KO.get(tun.get("status"), tun.get("status")) if tu else None,
                   "unit_prs": [_pr_brief(p) for p in tprs]})
    av = []
    for a in r.get("approvals") or []:
        ready, why = readiness(a, prs)
        av.append({"rid": a["rid"], "tier": a.get("tier"), "command": _cmd_of(a), "ready": ready,
                   "why": _tr("not_yet", why=", ".join(why)) if why else None})
    closed = status in ("done", "cancelled")
    todos = [gist(td.get("title") or td.get("text") or td.get("kind"), 140) for td in (snap.get("owner_todos") or {}).values()
             if not td.get("closed") and r["id"] in str(td.get("target") or "") + str(td.get("text") or "")]
    if not closed:  # a finished unit's old notes are history, not asks: only open to-dos stay
        todos += [s for s in _sentences(u.get("notes") or [], OWNER_RE) if s not in todos]
    decs = []
    for d in r.get("decisions") or []:
        at = cl.parse_ts(d.get("answered"))
        decs.append({"key": d.get("key"), "open": d.get("answer") is None, "answer": gist(d.get("answer"), 60),
                     "answered": at.astimezone(cl.zone()).strftime("%m-%d %H:%M") if at else None,
                     "outline": outline(d.get("question")) if d.get("answer") is None else []})
    related = []
    if not prs and not r.get("prs"):  # no PR recorded: the ones the unit's own notes and questions name
        texts = [n.get("text") for n in u.get("notes") or []] + [d.get("question") for d in r.get("decisions") or []]
        related = list(dict.fromkeys(ref for x in texts for ref in cl.norm_pr(str(x or ""))))[:5]
    disp = [f"{tgt}: {gist(x.get('text') or x.get('event'), 120)}" for tgt, sl in (u.get("sync") or {}).items()
            for x in (sl or {}).get("pending") or []]
    disp += [f"{o.get('kind')}: {gist(o.get('text'), 120)}" for o in (snap.get("outbox") or {}).values()
             if o.get("unit") == r["id"] and o.get("state") == "pending"]
    disp += [s for s in _sentences(u.get("notes") or [], DISPATCH_RE) if s not in disp and not dispatcher_resolved(s, prs)]
    seen, timeline = set(), []
    for n in reversed(u.get("notes") or []):
        g = gist(n.get("text"))
        if g in seen:
            continue
        seen.add(g)
        timeline.append({"ts": n.get("ts"), "gist": g, "text": short_sha(n.get("text"))})
    hands = r.get("hands") or []
    running = [h for h in hands if str(h.get("state") or "") not in ("stopped", "done", "finished", "cancelled")]
    wt = r.get("worktree") or {}
    ch = r.get("chain") or {}
    up = ch.get("upstream") or ch.get("up") or [] if isinstance(ch, dict) else []
    down = ch.get("downstream") or ch.get("down") or [] if isinstance(ch, dict) else []
    ups, downs = ", ".join(map(str, up)), ", ".join(map(str, down))
    return {
        "status_line": line, "next": nxt, "who": who, "who_ko": WHO_KO[who], "blocked": blocked,
        "tracker": r.get("tracker") or ", ".join(r.get("tickets") or []) or None,
        "chain": " → ".join(filter(None, [ups and _tr("chain_up", units=ups),
                                          downs and _tr("chain_down", units=downs)])) or None,
        "worktree": wt.get("path"), "branch": wt.get("branch"),
        "worktree_note": _reclaim_note(r.get("reclaim")), "worktree_kept": bool((r.get("reclaim") or {}).get("kept")),
        "prs": [{"ref": f"{p.get('repo')}#{p.get('number')}", "url": pr_url(p), "title": p.get("title"),
                 "state": p.get("state"), "head": (p.get("head") or "")[:7] or None, "ci": p.get("ci"),
                 "review": p.get("review"), "merge_state": p.get("merge_state"),
                 "gh": gh_state(None, p.get("state"), p.get("isDraft")), "gh_review": gh_review(p.get("review")),
                 "reviewers": p.get("reviewers") or [],
                 "pick": ((u.get("review_picks") or {}).get(f"{p.get('repo')}#{p.get('number')}") or {}).get("reason")}
                for p in prs],
        "waits": wv, "approvals": av, "owner_todos": todos, "dispatcher": disp, "timeline": timeline,
        "decisions": decs, "closed": closed,
        "related_prs": [{"ref": x, "url": f"https://github.com/{x.replace('#', '/pull/')}" if "/" in x else None}
                        for x in related],
        "hands_running": running, "hands_stopped": len(hands) - len(running),
        "hands_stopped_list": [{"name": h.get("name"), "role": h.get("role"), "state": h.get("state"), "ts": h.get("ts")}
                               for h in hands if h not in running],
    }


# ---------- board hook ----------

# A title's trailing state ("… — dev 検証待ち") that the unit's real status has overtaken.
TITLE_STATE_RE = re.compile(r"\s+[—–-]\s+[^—–-]*(?:待ち|待機|대기|중|중단|waiting|pending|in progress|blocked|verify|review)"
                            r"[^—–-]*$", re.I)


def display_title(title, status):
    """The title lists show: a finished unit's title without a stale trailing state; the detail keeps the original."""
    t = str(title or "")
    return TITLE_STATE_RE.sub("", t) if status in ("done", "cancelled") and TITLE_STATE_RE.search(t) else t


def board(snap, m):
    t = cl.now()
    for c in m.get("cards") or []:
        c["headline"] = headline(c, snap)
        c["detail_cmd"] = f"foundry decide show {c.get('id')}"
    for sec in m["sections"]:
        for r in sec["rows"]:
            r["view"] = unit_view(snap, r, t)
            shown = display_title(r.get("title"), r.get("status"))
            if shown != r.get("title"):
                r["title_full"], r["title"] = r.get("title"), shown
    m["pr_tab"] = pr_rows(snap, m)
    # A merged PR waiting on a release names the release unit carrying it and that unit's stage.
    carry = {ref: x for x in m.get("releases") or [] for ref in x["prs"]}
    for r in m["pr_tab"]:
        x = carry.get(r["ref"])
        if r["state"] == "release-wait":
            r["release"] = f"{x['stage_ko']} · {x['unit']}" if x else _tr("no_release_unit")
            r["action"] = _tr("release_action", release=r["release"])
            r["who"], r["who_ko"] = "foundry", WHO_KO["foundry"]
    m["pr_summary"] = pr_counts(m["pr_tab"])
    amap = accent_map()
    m["repo_blocks"] = repo_blocks(m.get("inventory") or {}, m["pr_tab"], amap=amap)
    m["repo_accents"] = amap
    m["pr_count"] = m["pr_summary"]["total"]
    m["cost_usd"] = round(sum((v or {}).get("usd") or 0 for v in (snap.get("costs") or {}).values()), 2)


def pr_table_text(rows):
    """The PR tab as text: one line per PR, columns fixed."""
    out = [f"{'PR':<26} {_tr('th_state'):<12} {'unit':<18} {_tr('reviewer'):<16} {_tr('th_age'):>5}  "
           f"{_tr('th_next_who')}  {_tr('th_title')}"]
    for x in rows:
        age = f"{x['age_h']}h" if x.get("age_h") is not None else "-"
        ref = x.get("short") or x["ref"]
        out.append(f"{ref:<26} {x['state_ko'][:12]:<12} {str(x['unit'] or '-')[:18]:<18} "
                   f"{x['reviewer'][:16]:<16} {age:>5}  {x['action']} ({x['who_ko']})  {x['title'][:50]}"
                   + (" " + _tr("stale") if x.get("stale") else ""))
    return "\n".join(out)
