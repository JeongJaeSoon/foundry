"""0.1.41: tracker comments are written in Japanese, Obsidian notes in Korean."""

import os
import unittest

import test_foundry as tf
from test_sync import SyncBase

AGENTS = os.path.join(os.path.dirname(tf.SCRIPTS), "agents")


class Language(SyncBase):
    def items(self):
        self.unit("u1")
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--text", "merged")
        self.foundry("unit", "report", "u1", "--text", "보고: done")
        return {i["target"]: i for i in self.drain()}

    def test_jira_report_rule_is_japanese(self):
        ins = self.items()["jira"]["instructions"]
        self.assertIn("1-3 Japanese lines of outcome", ins)
        self.assertIn("Write the whole comment in Japanese", ins)
        self.assertNotIn("Korean", ins)

    def test_obs_report_rule_is_korean(self):
        ins = self.items()["obs"]["instructions"]
        self.assertIn("1-3 Korean lines of outcome", ins)
        self.assertNotIn("Japanese", ins)

    def test_jira_without_report_still_japanese(self):
        self.unit("u1")
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--text", "merged")
        jira = next(i for i in self.drain() if i["target"] == "jira")
        self.assertIn("Write the whole comment in Japanese", jira["instructions"])


class Text(unittest.TestCase):
    def test_dispatcher_names_the_language_per_target(self):
        with open(os.path.join(AGENTS, "dispatcher.md")) as f:
            s = f.read()
        self.assertIn("in Japanese for `jira` and `linear`", s)
        self.assertIn("in Korean for `obs`", s)


if __name__ == "__main__":
    unittest.main()
