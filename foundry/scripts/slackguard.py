"""Slack send dedup and quiet hours, as pure functions over a tool call."""

import datetime as dt
import hashlib
import json
import re

from foundrylib import norm_pr, parse_ts, send_window, zone

# Any 7-40 hex run not glued to other ASCII letters/digits (Japanese text around it is fine).
# It must hold a digit, so English words made of a-f ("defaced") are not shas.
SHA_RE = re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}(?![0-9A-Za-z])")
MENTION_RE = re.compile(r"<@([UW][A-Z0-9]+)>|<!subteam\^([A-Z0-9]+)>")
REVIEW_ASK_RULE = "review-ask-rule"

KINDS = (
    ("review-ask", re.compile(r"レビュー.*(お願い|依頼)|review (please|request)|please review|리뷰.*(부탁|요청)", re.I)),
    ("review-remind", re.compile(r"リマインド|remind|再度", re.I)),
    ("review-done", re.compile(r"レビューコメントを残しました|approved|approve しました", re.I)),
    ("deploy", re.compile(r"デプロイ|deploy|リリース|release", re.I)),
    ("report", re.compile(r"報告|完了しました|done|finished", re.I)),
)

FAIL_RE = re.compile(r'"ok"\s*:\s*false|"is_?error"\s*:\s*true|^\s*(error|failed)\b', re.I)
SUCCESS_RE = re.compile(r'"ok"\s*:\s*true|\b\d{10}\.\d{6}\b|slack\.com/archives/|permalink', re.I)


SEND_RE = re.compile(r"(?i)^mcp__.+__slack_(send_message|schedule_message|complete_file_upload)$")
DRAFT_RE = re.compile(r"(?i)^mcp__.+__slack_send_message_draft$")
LINK_RE = re.compile(r"https://[\w.-]*slack\.com/[^\s\"'<>)]+")


def is_slack_send(tool_name):
    """Any MCP server (claude.ai's `Slack`, a gateway's `slack`, ...): the tool name, not the server, is the send."""
    return bool(SEND_RE.match(tool_name or ""))


def is_slack_draft(tool_name):
    """A draft saved in the owner's Slack (Drafts & Sent), never posted: format rules apply, send gates do not."""
    return bool(DRAFT_RE.match(tool_name or ""))


def draft_decision(tool_input):
    """The slack-post format rules for a draft: a top-level draft is linted as a message, a thread reply draft
    (thread_ts) as a reply."""
    import report
    text = tool_input.get("message") or tool_input.get("text") or ""
    errs = report.slack_lint([{"text": text, "reply": bool(tool_input.get("thread_ts"))}])
    return ("Fix the draft to the slack-post rules, then save it again: " + "; ".join(errs)) if errs else None


def draft_outcome(resp):
    """('drafted' | 'exists' | 'failed' | 'unknown', link or None) from a draft tool's response."""
    text = _text_of(resp) if not isinstance(resp, (dict, list)) else json.dumps(resp, ensure_ascii=False)
    link = (LINK_RE.search(text) or [None])[0]
    if "attached_draft_exists" in text:
        return "exists", link
    state = send_outcome(resp)
    return {"sent": "drafted"}.get(state, state), link


def kind_of(text):
    for name, rx in KINDS:
        if rx.search(text or ""):
            return name
    return "message"


def shas_in(text):
    return sorted(set(m.group(0)[:7] for m in SHA_RE.finditer(text or "")))


def idempotency_keys(tool_input):
    """Every ledger key this send claims, and its kind.

    1. The message key: channel | thread | PRs | head shas | recipients | kind | normalized body hash.
       The body hash is always in it, so two different messages about one PR (dev deploy, then prod)
       never collide.
    2. For a review ask, one key per (PR, reviewer, head sha) regardless of channel or wording:
       the same reviewer is asked about the same head at most once per review_ask_ttl_hours.
    """
    text = strip_footer(tool_input.get("message") or tool_input.get("text") or tool_input.get("initial_comment") or "")
    channel = tool_input.get("channel_id") or tool_input.get("channel") or ""
    thread = tool_input.get("thread_ts") or ""
    prs = sorted(norm_pr(text))
    shas = shas_in(text)
    recipients = sorted(set(a or b for a, b in MENTION_RE.findall(text)))
    kind = kind_of(text)
    files = tool_input.get("files") or tool_input.get("file_id") or tool_input.get("file_ids") or ""
    files = json.dumps(files, sort_keys=True) if files else ""  # two uploads with no comment are not one message
    body = hashlib.sha1((" ".join(text.lower().split()) + files).encode()).hexdigest()[:12]
    keys = ["|".join([channel, thread, ",".join(prs), ",".join(shas), ",".join(recipients), kind, body])]
    if kind == "review-ask":
        for pr in prs:
            for who in recipients or [""]:
                keys.append("|".join([REVIEW_ASK_RULE, pr, who, ",".join(shas)]))
    return keys, kind


def key_kind(key, kind):
    """The kind a ledger record is filed under: the review-ask rule has its own TTL."""
    return REVIEW_ASK_RULE if key.startswith(REVIEW_ASK_RULE + "|") else kind


def in_quiet(t, cfg):
    import prefs
    p = prefs.load()
    return prefs.in_window(t, p["slack_quiet"], prefs.tz(p))


def quiet_text():
    import prefs
    p = prefs.load()
    w = p["slack_quiet"]
    return w, w.split("-")[1]


def is_owner_dm(channel, cfg):
    owner = cfg.get("owner_slack_user_id")
    return bool(channel) and (channel in (cfg.get("owner_dm_channels") or []) or channel == owner)


def _footer():
    import report
    return report.footer()


def strip_footer(text, foot=None):
    """The text without a trailing footer line: the ledger keys of a message are the same with or without it."""
    foot = foot or _footer()
    lines = (text or "").rstrip().split("\n")
    return "\n".join(lines[:-1]).rstrip() if lines and lines[-1].strip() == foot else (text or "")


def with_footer(tool_input, foot=None):
    """The input with a blank line and prefs slack.footer appended to a top-level message that lacks it (passed to
    Claude Code as the PreToolUse updatedInput), or None when nothing changes: a thread reply, or the footer there."""
    key = "message" if "message" in tool_input else "text" if "text" in tool_input else None
    if not key or tool_input.get("thread_ts") or not isinstance(tool_input[key], str):
        return None
    foot = foot or _footer()
    text = tool_input[key].rstrip()
    if text.split("\n")[-1].strip() == foot:
        return None
    return {**tool_input, key: f"{text}\n\n{foot}"}


def broadcast_decision(tool_input):
    """A review ask / reminder posted into a thread uses reply_broadcast false (owner 2026-10-26)."""
    text = tool_input.get("message") or tool_input.get("text") or ""
    b = tool_input.get("reply_broadcast")
    if tool_input.get("thread_ts") and (b is True or str(b).lower() == "true") and kind_of(text) in (
            "review-ask", "review-remind"):
        return ("A review ask or reminder in a thread is sent with reply_broadcast false: the mention already "
                "notifies the reviewer, and the channel does not need it. Resend with reply_broadcast false.")
    return None


def quiet_decision(tool_name, tool_input, t, cfg, overrides, warn=lambda msg: None):
    """Return None to allow, or a deny reason. A malformed override is reported through warn and ignored."""
    channel = tool_input.get("channel_id") or tool_input.get("channel") or ""
    if is_owner_dm(channel, cfg):
        return None
    if tool_name.endswith("schedule_message"):
        post_at = tool_input.get("post_at")
        try:
            when = dt.datetime.fromtimestamp(int(float(post_at)), zone())
        except (TypeError, ValueError, OverflowError, OSError):
            return f"post_at must be a unix timestamp (seconds), got {post_at!r}."
        if in_quiet(when, cfg):
            w, end = quiet_text()
            return (f"Scheduled time {when:%H:%M} is inside the {w} quiet hours. "
                    f"Schedule it for {end} or later.")
        return None
    if not in_quiet(t, cfg):
        return None
    for o in overrides:
        until = parse_ts(o.get("until"))
        if until is None:
            warn(f"malformed override ignored: {o!r}")
            continue
        if until > t and o.get("channel") == channel:
            return None
    w, end = quiet_text()
    return (f"It is {w} quiet hours. Schedule this with slack_schedule_message for {end}. "
            "Only the owner can lift quiet hours for one channel (`foundry send allow-now` from a separate terminal "
            "tab, or an explicit send-now order in chat that they then approve at the permission prompt); do not try "
            "to lift it yourself.")


def post_time(tool_name, tool_input, now):
    """When a send reaches Slack: a scheduled send's post_at (one in the past posts now), else now. None for an
    unreadable post_at (quiet_decision refuses that one)."""
    if not str(tool_name or "").endswith("schedule_message"):
        return now
    try:
        when = dt.datetime.fromtimestamp(int(float(tool_input.get("post_at"))), zone())
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return max(when, now)


def landed(rec):
    """When a recorded send reached (or reaches) Slack: its post_at once it was scheduled, else when it was sent."""
    post = parse_ts(rec.get("post_at")) if rec.get("state") == "sent" else None
    return post or parse_ts(rec.get("ts"))


def dedup_decision(keys, sends, t, cfg, post=None):
    """`t` is now; `post` when this send reaches Slack (a schedule's post_at). The 24h review-ask rule compares
    post times on both sides (a send scheduled for tomorrow is judged as tomorrow's); every other key keeps the
    call time."""
    for key in keys:
        prev = sends.get(key)
        if not prev:
            continue
        # Post times only against a send that went out; one in flight or unknown keeps its short call-time window.
        rule = key.startswith(REVIEW_ASK_RULE + "|") and prev.get("state") == "sent"
        since = landed(prev) if rule else parse_ts(prev.get("ts"))
        at = (post or t) if rule else t
        if since is None or abs(at - since) >= send_window(prev, cfg):
            continue
        state = prev.get("state")
        if key.startswith(REVIEW_ASK_RULE + "|"):
            _, pr, who, sha = key.split("|", 3)
            return (f"Review ask blocked: {who or 'this channel'} was already asked to review {pr} at head "
                    f"{sha or '(no sha named)'} at {prev['ts']}. Ask again only after a new head commit "
                    "(name its sha in the message) or after 24h.")
        if state == "sent":
            return (f"Duplicate Slack send blocked: an identical message (key {key}) was already sent at {prev['ts']}. "
                    "Do not resend; if it truly must go again, change what it says (new PR head, new recipient).")
        if state == "unknown":
            return (f"Duplicate Slack send blocked: the same message (key {key}) was sent at {prev['ts']} and its "
                    "result could not be read. Check the channel first; a retry is allowed after a few minutes.")
        return f"Duplicate Slack send blocked: the same message (key {key}) is being sent right now."
    return None


def _text_of(resp):
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        return "\n".join(_text_of(b) for b in resp)
    if isinstance(resp, dict):
        if isinstance(resp.get("text"), str):
            return resp["text"]
        return json.dumps(resp, ensure_ascii=False)
    return ""


def send_outcome(resp):
    """'sent', 'failed' or 'unknown' from a tool_response: a dict, an MCP content list, or a string."""
    if isinstance(resp, dict):
        if resp.get("is_error") or resp.get("isError") or resp.get("ok") is False or resp.get("error"):
            return "failed"
        if resp.get("ok") is True:
            return "sent"
        if "content" in resp:
            return send_outcome(resp["content"])
    if isinstance(resp, list):
        parts = [send_outcome(b) for b in resp]
        if "failed" in parts:
            return "failed"
        if "sent" in parts:
            return "sent"
        return "unknown"
    text = _text_of(resp).strip()
    if text.startswith(("{", "[")):
        try:
            inner = json.loads(text)
        except ValueError:
            inner = None
        if isinstance(inner, (dict, list)) and inner != resp:
            got = send_outcome(inner)
            if got != "unknown":
                return got
    if FAIL_RE.search(text):
        return "failed"
    if SUCCESS_RE.search(text):
        return "sent"
    return "unknown"
