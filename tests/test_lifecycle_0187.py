"""0.1.87: the Slack hook appends prefs slack.footer to every top-level message (send, schedule, draft) through the
PreToolUse updatedInput, so no prompt, skill or lint asks anyone for it; a thread reply is left as is."""

import os
import re

import test_foundry as tf

ROOT = os.path.dirname(tf.SCRIPTS)
SEND = "mcp__plugin_acme_acme-slack__slack_send_message"
SCHED = "mcp__plugin_acme_acme-slack__slack_schedule_message"
DRAFT = "mcp__plugin_acme_acme-slack__slack_send_message_draft"
FOOT = "🤖 test footer"
BODY = ":kyouyuu-1:\n\n\n**[foundry] 일보**\n\n가동 라인 2"


class Footer(tf.Base):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            f.write('{"slack": {"footer": "%s"}}' % FOOT)

    def pre(self, tool, ti):
        return self.hook("PreToolUse", raw=True, tool_name=tool, tool_input=ti)

    def updated(self, out):
        return ((out or {}).get("hookSpecificOutput") or {}).get("updatedInput")

    def test_missing_footer_is_appended_without_a_permission_decision(self):
        for ch, tool, extra in (("C1", SEND, {}), ("C6", SCHED, {"post_at": "1795172400"}), ("C7", DRAFT, {})):
            out = self.pre(tool, {"channel_id": ch, "message": BODY, **extra})
            self.assertEqual(self.updated(out)["message"], f"{BODY}\n\n{FOOT}", tool)
            self.assertNotIn("permissionDecision", out["hookSpecificOutput"], tool)

    def test_present_footer_and_replies_are_unchanged(self):
        self.assertIsNone(self.updated(self.pre(SEND, {"channel_id": "C2", "message": f"{BODY}\n\n{FOOT}"})))
        self.assertIsNone(self.updated(self.pre(SEND, {"channel_id": "C3", "thread_ts": "1.2", "message": "- 세부"})))

    def test_owner_footer_from_prefs(self):
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            f.write('{"slack": {"footer": "— owner footer"}}')
        out = self.pre(SEND, {"channel_id": "C4", "message": "<@U0000000001> 확인 부탁드립니다"})
        self.assertTrue(self.updated(out)["message"].endswith("\n\n— owner footer"))

    def test_the_ledger_key_ignores_the_footer(self):
        self.pre(SEND, {"channel_id": "C5", "message": BODY})
        self.hook("PostToolUse", tool_name=SEND, tool_input={"channel_id": "C5", "message": f"{BODY}\n\n{FOOT}"},
                  tool_response={"ok": True, "ts": "1795440600.000100"})
        self.assertTrue(self.denied(self.pre(SEND, {"channel_id": "C5", "message": BODY})))  # a resend is a duplicate

    def test_no_prompt_mentions_the_slack_footer(self):
        rx = re.compile(r"slack\.footer|signature|が作成|서명|署名", re.I)
        hits = []
        for top in ("agents", "skills"):
            for d, _, files in os.walk(os.path.join(ROOT, top)):
                for n in files:
                    if n.endswith(".md"):
                        with open(os.path.join(d, n), encoding="utf-8") as f:
                            hits += [f"{n}:{i}" for i, ln in enumerate(f, 1) if rx.search(ln)]
        self.assertEqual(hits, [])
