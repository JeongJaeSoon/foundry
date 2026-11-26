"""0.1.71: a daily draft answered after an owner item closed sends without that item (line dropped, count lowered,
diff journaled); `--draft` says `"lint": "ok"` and the report.draft record carries it."""

import json
import os
import sys

import test_report_0150 as base

sys.path.insert(0, base.tf.SCRIPTS)
import report  # noqa: E402


class StaleOwnerLines(base.Base):
    draft = base.Approval.draft

    def test_an_answered_card_leaves_the_sent_parent(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "U1", "--ticket", "PROJ-5")
        _, c1 = self.foundry("decide", "open", "--question", "올릴까요?", "--option", "A:올림", "--option", "B:보류",
                             "--asked-by", "lead", "--key", "u1:go", "--unit", "u1")
        self.foundry("decide", "open", "--question", "지울까요?", "--option", "A:지움", "--option", "B:보류",
                     "--asked-by", "lead", "--key", "u1:rm", "--unit", "u1")
        d = self.draft()
        self.assertEqual(d["lint"], "ok")
        parent = d["messages"][0]["text"]
        self.assertIn("결정 2 · 오너 대기 0", parent)  # 0.1.88: decisions are their own block
        c1 = c1["card"]
        self.foundry("decide", "answer", c1["id"], "A", "--by", "owner")
        self.foundry("decide", "answer", d["card"], "post", "--by", "owner")
        [o] = self.outbox()
        self.assertNotIn(f"({c1['id']})", o["text"])
        self.assertIn("결정 1 · 오너 대기 0", o["text"])
        self.assertIn("지울까요?", o["text"])
        self.assertEqual(report.slack_lint([{"text": o["text"]}]), [])
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            rec = [json.loads(x) for x in f if '"report.refreshed"' in x]
        self.assertEqual(rec[0]["dropped"], [c1["id"]])
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            drafts = [json.loads(x) for x in f if '"report.draft"' in x]
        self.assertEqual(drafts[-1]["lint"], "ok")


class Refresh(base.Base):
    def test_unit(self):
        text = "**t**\n\n가동 라인 0 · 오늘 완료 0\n리뷰 대기 0 · 오너 대기 2\n오늘 릴리스 0\n\n**오너 대기**\n" \
               "결정 · a · x (d-1)\n할 일 · b · y (ot-2)\n\n🤖 f"
        out, dropped = report.refresh_owner(text, {"ot-2"})
        self.assertEqual(dropped, ["d-1"])
        self.assertIn("오너 대기 1", out)
        self.assertNotIn("(d-1)", out)
        out, dropped = report.refresh_owner(text, set())
        self.assertNotIn("**오너 대기**", out)
        self.assertIn("오너 대기 0", out)
        self.assertNotIn("\n\n\n", out)
        self.assertEqual(report.refresh_owner(text, {"d-1", "ot-2"}), (text, []))


if __name__ == "__main__":
    import unittest
    unittest.main()
