"""0.1.69: an owner to-do can carry a title in the owner's language (its text stays as written; English text without
one asks the foreman for it), and owner lines name a unit by its ticket, `<repo> release`, or its title's first
words, never a cut id."""

import sys

import test_foundry as tf
import test_report_0150 as base

sys.path.insert(0, tf.SCRIPTS)
import report  # noqa: E402


class TodoTitle(base.Base):
    def owner(self):
        _, out = self.foundry("report", "daily", "--format", "slack")
        lines = out["messages"][0]["text"].split("\n")
        i = lines.index("**오너 대기**")
        return lines[i + 1:lines.index("", i)]

    def test_english_text_asks_for_a_title_and_the_title_is_shown(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "n8n runner", "--ticket", "PROJ-771")
        _, out = self.foundry("owner", "todo", "add", "--unit", "u1", "--by", "foreman", "--kind", "other",
                              "--text", "Set the runner flag in the stg app", "--why-owner", "사람만 클릭")
        self.assertIn("owner todo title", out["needs_title"])
        self.foundry("owner", "todo", "title", out["tid"], "--title", "stg 앱에 runner 플래그 설정", "--by", "foreman")
        self.assertIn(f"할 일 · PROJ-771 · stg 앱에 runner 플래그 설정 ({out['tid']})", self.owner())
        _, todos = self.foundry("owner", "todo", "list")
        self.assertEqual(todos[0]["text"], "Set the runner flag in the stg app")

    def test_a_title_given_up_front_or_korean_text_needs_no_ask(self):
        _, a = self.foundry("owner", "todo", "add", "--none", "--by", "foreman", "--text", "Click approve",
                            "--title", "승인 클릭", "--why-owner", "사람만 클릭")
        _, b = self.foundry("owner", "todo", "add", "--none", "--by", "foreman", "--text", "승인 버튼 클릭",
                            "--why-owner", "사람만 클릭")
        self.assertNotIn("needs_title", a)
        self.assertNotIn("needs_title", b)


class Where(tf.Base):
    def test_ticket_release_title_then_id(self):
        self.assertEqual(report.where_label({"keys": {"ticket": ["PROJ-1"]}, "title": "x"}, "u"), "PROJ-1")
        self.assertEqual(report.where_label({"release": {"repo": "example-org/gateway"}}, "r"), "gateway release")
        self.assertEqual(report.where_label({"repo": "gateway", "title": "Release gateway: x"}, "release-gateway-7"),
                         "gateway release")
        self.assertEqual(report.where_label({"id": "w", "title": "worktree content check: prove dirty"}, "w"),
                         "worktree content check")
        self.assertEqual(report.where_label({"id": "w"}, "w"), "w")


if __name__ == "__main__":
    import unittest
    unittest.main()
