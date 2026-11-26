"""0.1.82: the dispatcher can save a Slack draft (slack_send_message_draft) in the owner's Drafts & Sent. A draft is
linted to the slack-post rules but passes no send gate (it is never posted), and the journal records outbox.drafted;
send and schedule rights do not grow."""

import json
import os
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import mcpmap  # noqa: E402

ROOT = os.path.dirname(tf.SCRIPTS)
DRAFT = "mcp__plugin_acme_acme-slack__slack_send_message_draft"
SERVERS = ["plugin_acme_acme-slack", "plugin_acme_acme-atlassian"]
GOOD = ":kyouyuu-1:\n\n\n**[foundry] 일보**\n\n가동 라인 2 · 오늘 완료 4\n\n🤖 Claude Code が作成"


class Drafts(tf.Base):
    def draft(self, ti, resp=None):
        out = self.hook("PreToolUse", tool_name=DRAFT, tool_input=ti)
        if not (out and self.denied(out)):
            self.hook("PostToolUse", tool_name=DRAFT, tool_input=ti,
                      tool_response=resp or {"ok": True, "permalink": "https://x.slack.com/archives/C1/p1"})
        return out

    def drafted(self):
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            return [json.loads(ln) for ln in f if '"outbox.drafted"' in ln]

    def test_a_lint_clean_draft_passes_at_night_and_is_journaled(self):
        self.env["FOUNDRY_NOW"] = "2026-11-20T23:30:00+09:00"  # quiet hours: a draft is no send
        self.assertIsNone(self.draft({"channel_id": "C1", "message": GOOD}))
        rec = self.drafted()[-1]
        self.assertEqual((rec["channel"], rec["thread_ts"], rec["link"], rec["state"]),
                         ("C1", None, "https://x.slack.com/archives/C1/p1", "drafted"))

    def test_format_rules_apply(self):
        for bad in (GOOD.replace("**", "*"), GOOD + "\n| a | b |"):
            out = self.draft({"channel_id": "C1", "message": bad})
            self.assertTrue(self.denied(out), bad)
        self.assertEqual(self.drafted(), [])

    def test_a_reply_draft_needs_no_footer(self):
        self.assertIsNone(self.draft({"channel_id": "C1", "thread_ts": "1.2", "message": "- 세부 1"}))
        self.assertEqual(self.drafted()[-1]["thread_ts"], "1.2")

    def test_an_existing_draft_is_recorded_not_overwritten(self):
        self.draft({"channel_id": "C1", "message": GOOD}, resp={"ok": False, "error": "attached_draft_exists"})
        self.assertEqual(self.drafted()[-1]["state"], "exists")

    def test_only_the_dispatcher_gets_it_and_no_send_right_grows(self):
        specs = {s["name"]: s["tools"] for s in mcpmap.specs(mcpmap.mapping({}, set(SERVERS)))}
        self.assertIn(DRAFT, specs["dispatcher"])
        self.assertNotIn(DRAFT, specs["line-lead"])
        with open(os.path.join(ROOT, "agents", "dispatcher.md")) as f:
            text = f.read()
        self.assertEqual(text.count("slack_send_message_draft, "), 1)
        self.assertIn("attached_draft_exists", text)
        import foundrylib as cl
        self.assertNotIn("mcp__slack__slack_send_message_draft", cl.load_config()["lead_ask_rules"])
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as f:
            self.assertEqual(f.read().count("send_message_draft"), 3)
