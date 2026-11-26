"""0.1.51: the 24h review-ask rule judges a scheduled send by its post_at (on both sides). (Its footer tests went
in 0.1.87: the Slack hook appends prefs slack.footer itself.)"""

import os
import unittest

import test_foundry as tf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEND = "mcp__plugin_acme_acme-slack__slack_send_message"
SCHED = "mcp__plugin_acme_acme-slack__slack_schedule_message"
ASK = {"channel_id": "C1", "thread_ts": "1.2",
       "message": "<@U0000000002> レビューお願いします https://github.com/example-org/webapp/pull/1655"}
REMIND = dict(ASK, message="<@U0000000002> レビューのリマインドです。ご確認をお願いします example-org/webapp#1655")
T_1120_2000 = 1795172400  # 2026-11-20 20:00 JST
T_1120_1946 = 1795171560  # 2026-11-20 19:46 JST
T_1120_0900 = T_1120_2000 - 11 * 3600


class PostAt(tf.Base):
    def send(self, now, msg, tool=SEND, ok=True):
        self.env["FOUNDRY_NOW"] = now
        out = self.hook("PreToolUse", tool_name=tool, tool_input=msg)
        if not (out and self.denied(out)):
            self.hook("PostToolUse", tool_name=tool, tool_input=msg, tool_response={"ok": ok})
        return out

    def ask_at_1947(self):
        self.assertIsNone(self.send("2026-11-19T19:47:00+09:00", ASK))

    def test_a_reminder_scheduled_past_24h_of_the_ask_passes(self):
        self.ask_at_1947()  # the case the foreman hit: 24h13m after the ask, called at 22:30 the evening before
        self.assertIsNone(self.send("2026-11-19T22:30:00+09:00", dict(REMIND, post_at=str(T_1120_2000)), SCHED))

    def test_a_post_at_inside_the_window_is_refused(self):
        self.ask_at_1947()
        out = self.send("2026-11-19T22:30:00+09:00", dict(REMIND, post_at=str(T_1120_1946)), SCHED)  # 23h59m
        self.assertTrue(self.denied(out))
        self.assertIn("Review ask blocked", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_an_immediate_send_still_uses_the_current_time(self):
        self.ask_at_1947()
        self.assertTrue(self.denied(self.send("2026-11-20T19:46:00+09:00", REMIND)))
        self.assertIsNone(self.send("2026-11-20T19:48:00+09:00", REMIND))

    def test_a_past_post_at_is_judged_as_now(self):
        self.ask_at_1947()
        self.assertTrue(self.denied(self.send("2026-11-20T10:00:00+09:00", dict(REMIND, post_at="1795000000"), SCHED)))

    def test_a_scheduled_ask_blocks_from_its_post_at_and_is_not_pruned_early(self):
        self.assertIsNone(self.send("2026-11-19T10:00:00+09:00", dict(ASK, post_at=str(T_1120_0900)), SCHED))
        # 25h after the call, 2h after the post: still inside the 24h window of the post.
        self.assertTrue(self.denied(self.send("2026-11-20T11:00:00+09:00", REMIND)))
        self.assertIsNone(self.send("2026-11-21T09:01:00+09:00", REMIND))

    def test_a_failed_schedule_can_be_retried(self):
        msg = dict(REMIND, post_at=str(T_1120_2000))
        self.assertIsNone(self.send("2026-11-19T19:00:00+09:00", msg, SCHED, ok=False))
        self.assertIsNone(self.send("2026-11-19T19:01:00+09:00", msg, SCHED))

    def test_other_duplicates_keep_the_call_time(self):
        note = {"channel_id": "C1", "message": "dev 環境の確認が終わりました"}
        self.assertIsNone(self.send("2026-11-19T10:00:00+09:00", note))
        later = dict(note, post_at=str(T_1120_2000 + 2 * 86400))  # scheduled days out: still the same message
        self.assertTrue(self.denied(self.send("2026-11-19T10:01:00+09:00", later, SCHED)))


# A signature with a model name: `Claude Code (<anything>) が作成`.


class ShortUnit(tf.Base):
    CASES = [("short-id", "short-id"), ("release-gateway-sample-svc-ja-example-org-gateway-7", "releas…teway-7"),
             ("PROJ-794-infra-facets-collector", "PROJ-794…ector"), ("release-sample-svc-PROJ-12", "releas…PROJ-12"),
             ("gateway-review-fix-for-#1655", "gateway-…#1655")]  # the pane's shortUnit test uses the same vectors

    def test_the_same_cut_as_the_pane(self):
        import sys
        sys.path.insert(0, tf.SCRIPTS)
        import views
        self.assertEqual([views.short_unit(i) for i, _ in self.CASES], [w for _, w in self.CASES])

    def test_board_html_caps_the_unit_cell_and_keeps_the_full_id_on_hover(self):
        import subprocess
        import sys
        uid = "release-gateway-sample-svc-ja-example-org-gateway-7"
        self.foundry("unit", "new", "--id", uid, "--title", "sample-svc 릴리스")
        self.foundry("decision", "ask", "--key", f"{uid}:go", "--unit", uid, "--question", "배포할까요?", ok=(10,))
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), "html"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(r.stdout.strip(), encoding="utf-8") as f:
            page = f.read()
        self.assertIn(f"<td title='{uid}'>releas…teway-7", page)
        self.assertIn("dt,dd{padding:3px 0;border-bottom", page)  # every fact row has its own rule


if __name__ == "__main__":
    unittest.main()
