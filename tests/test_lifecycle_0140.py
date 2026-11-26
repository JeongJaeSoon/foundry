"""0.1.40: tracker comments carry the owner's footer when first posted, and a posted comment is never rewritten."""

import os
import sys
import unittest

import test_foundry as tf
from test_sync import SyncBase

AGENTS = os.path.join(os.path.dirname(tf.SCRIPTS), "agents")
FOOTER = "🤖 Claude Code が作成"


def read(name):
    with open(os.path.join(AGENTS, name)) as f:
        return f.read()


class Footer(SyncBase):
    def jira(self):
        return next(i for i in self.drain() if i["target"] == "jira")

    def test_jira_item_carries_the_exact_global_footer(self):
        self.unit("u1")
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--text", "merged")
        item = self.jira()  # 0.1.89: the hook signs a Jira comment; the item asks nothing about it
        self.assertNotIn("footer", item)
        self.assertNotIn(FOOTER, item["instructions"])

    def test_posted_rule_rides_on_every_tracker_item_but_not_obsidian(self):
        self.unit("u1")
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--text", "merged")
        self.foundry("unit", "report", "u1", "--text", "보고: done")
        for item in self.drain():
            if item["target"] == "obs":
                self.assertNotIn("footer", item)
                continue
            self.assertIn("never edited or deleted", item["instructions"])
            self.assertIn("new short correction comment", item["instructions"])
            self.assertIn("secret or personal data", item["instructions"])

    def test_prefs_override_and_blank_falls_back(self):
        sys.path.insert(0, tf.SCRIPTS)
        import sync
        self.assertEqual(sync.footer({"sync": {"comment_footer": "🤖 X"}}), "🤖 X")
        self.assertEqual(sync.footer({"sync": {"comment_footer": "  "}}), FOOTER)
        self.assertEqual(sync.footer({}), FOOTER)

    def test_prefs_default_has_the_footer_and_validates(self):
        sys.path.insert(0, tf.SCRIPTS)
        import prefs
        self.assertEqual(prefs.defaults()["sync"]["comment_footer"], FOOTER)
        self.assertIn("sync.comment_footer: must be a string",
                      prefs.validate({"sync": {"comment_footer": 3}}))


class Text(unittest.TestCase):
    def test_foreman_never_edits_posted_comments(self):
        s = read("foreman.md")
        self.assertIn("edit, or send anyone to edit, a comment already posted", s)
        self.assertIn("new short correction comment", s)

    def test_dispatcher_posted_is_final_and_footer(self):
        s = read("dispatcher.md")
        self.assertIn("**Posted is final.**", s)
        self.assertIn("never gets a comment id", s)
        self.assertIn("the item's `footer`", s)

    def test_line_lead_never_edits_posted_comments(self):
        self.assertIn("never edit a comment already posted", read("line-lead.md"))


if __name__ == "__main__":
    unittest.main()
