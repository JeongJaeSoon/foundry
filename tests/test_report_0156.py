"""0.1.56: small report groups collapse into one その他の作業 reply and the thread is capped (prefs report.collapse_lines
/ max_replies); no reply puts a plain line after a list (property test); Slack links are stored as fields and built at
render time; `<ORGANIZATION>`-style placeholders leave channel names; collector lines name nobody by pronoun; the send
hook refuses a model-named signature."""

import json
import os
import random
import sys
import unittest

import test_foundry as tf
import test_report_0150 as base
from test_report_0150 import DAY, row

sys.path.insert(0, tf.SCRIPTS)
import report  # noqa: E402

LINK = "https://example.slack.com/archives/C0TESTCHAN1/p1731900000123456"


class Collapse(unittest.TestCase):
    def test_small_groups_collapse_and_the_thread_is_capped(self):
        groups = [(f"G{i}", [f"line {i}.{k}" for k in range(n)]) for i, n in enumerate([5, 1, 2, 4, 3, 6, 1])]
        p = {"report": {"collapse_lines": 2, "max_replies": 5}}
        out = report.collapse(groups, p, fixed=2)  # 5 - 2 fixed - 1 collapsed reply = 2 big groups kept
        self.assertEqual([label for label, _ in out], ["G0", "G5", report.T("group_collapsed")])
        rest = out[-1][1]
        self.assertEqual(len(rest), 5)
        self.assertIn("G3 · line 3.0 (+3)", rest)
        self.assertIn("G1 · line 1.0", rest)

    def test_nothing_collapses_when_every_group_is_big_and_fits(self):
        groups = [("A", ["a", "b", "c"]), ("B", ["d", "e", "f"])]
        self.assertEqual(report.collapse(groups, {"report": {}}, fixed=2), groups)


class ListLast(unittest.TestCase):
    def test_no_plain_line_ever_follows_a_list(self):
        rnd = random.Random(7)
        pool = ["자동 PR 3건 (봇·버전 bump)", "관여만 한 PR 1건", "내가 리뷰할 PR 2건", "a PR line", "상황: x", "", "more"]
        for _ in range(500):
            lines = [rnd.choice(pool) for _ in range(rnd.randint(0, 8))]
            text = "**L**\n" + "\n".join(report.list_last(lines))
            self.assertEqual([e for e in report.slack_lint([{"text": text, "reply": True}]) if "follows a bullet" in e],
                             [], text)


class Fields(base.Base):
    def test_a_thread_reply_link_is_built_at_render_and_placeholders_drop(self):
        self.foundry("report", "slack-add", "--channel", "team-<ORGANIZATION>", "--permalink", LINK + "?x=1",
                     "--thread-ts", "1731900000.000001", "--summary", "배포 일정을 공유함")
        self.foundry("report", "slack-done", "--count", "1")
        item = self.snap()["report_slack"][DAY]["items"][0]
        self.assertEqual((item["channel"], item["permalink"], item["thread_ts"]),
                         ("team", LINK, "1731900000.000001"))
        _, out = self.foundry("report", "daily", "--json")
        self.assertIn(f"[#team]({LINK}?thread_ts=1731900000.000001&cid=C0TESTCHAN1) 배포 일정을 공유함", out["text"])

    def test_pronouns_and_bad_thread_ts_are_refused(self):
        for summary in ("Said he would retest", "She approved it", "彼が確認した", "그녀가 승인", "그는 확인함"):
            self.foundry("report", "slack-add", "--channel", "team", "--permalink", LINK, "--summary", summary, ok=(2,))
        self.foundry("report", "slack-add", "--channel", "team", "--permalink", LINK, "--thread-ts", "x&y",
                     "--summary", "ok", ok=(2,))
        self.foundry("report", "slack-add", "--channel", "team", "--permalink", LINK,
                     "--summary", "그 PR 의 리뷰를 요청함")  # 「그 PR」 is a determiner, not a person


class SendHook(tf.Base):
    TOOL = "mcp__plugin_acme_acme-slack__slack_send_message"

    def test_a_send_is_not_refused_for_its_signature(self):  # 0.1.87: the hook appends the footer, never judges one
        ok = self.hook("PreToolUse", tool_name=self.TOOL,
                       tool_input={"channel_id": "C1", "message": "x\n\n\U0001f916 Claude Code が作成"})
        self.assertIsNone(ok)


class Defaults(unittest.TestCase):
    def test_prefs_carry_the_thresholds(self):
        import prefs
        rp = prefs.defaults()["report"]
        self.assertEqual((rp["collapse_lines"], rp["max_replies"]), (2, 10))


if __name__ == "__main__":
    unittest.main()
