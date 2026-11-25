"""OSS readiness: with the shipped defaults alone (no org overlay, no tracker, no Slack ids), a GitHub-only user sets
up with `foundry init`, registers a repo, creates and dry-runs a unit, sees the board in each UI language, and
nothing queues work for Jira, Linear, Obsidian or Slack."""

import os
import subprocess
import sys

import test_foundry as tf

HANGUL = range(0xAC00, 0xD7A4)


class GitHubOnly(tf.Base):
    CREW = False

    def setUp(self):
        super().setUp()
        self.env.pop("FOUNDRY_PREFS_OVERLAY", None)
        self.gh({"api user": {"login": "octo"}})
        self.clone = os.path.join(self.dir, "src", "app")
        subprocess.run(["git", "init", "-q", "-b", "main", self.clone], check=True)

    def test_core_loop_without_integrations(self):
        _, out = self.foundry("init", "--worktree-root", os.path.join(self.dir, "wt"), "--foreman-dir",
                              os.path.join(self.dir, "fm"), "--tracker", "none", "--repo", f"app={self.clone}",
                              "--timezone", "UTC")
        self.assertEqual(out["repos"], ["app"])
        _, prefs = self.foundry("prefs", "show")
        self.assertEqual(prefs.get("trackers", (prefs.get("prefs") or {}).get("trackers")), [])
        self.foundry("unit", "new", "--id", "u1", "--title", "Fix login", "--repo", "app")
        _, plan = self.foundry("unit", "launch", "u1", "--dry-run")
        self.assertTrue(plan)
        self.foundry("sync", "enqueue", "u1", "--event", "manual", "--text", "progress")
        _, drained = self.foundry("sync", "drain", "--json")
        self.assertEqual(drained["items"], [])  # no Jira, Linear or Obsidian batch for a tracker-less install
        _, out = self.foundry("outbox", "list")
        self.assertFalse(out)
        _, text = self.foundry("board")
        self.assertIn("Queue 1", text)
        self.assertFalse([x for x in text.split("\n") if any(ord(c) in HANGUL for c in x)], text)
        for lang, word in (("ja", "キュー 1"), ("ko", "대기열 1")):
            self.foundry("prefs", "set", "ui.language", lang, "--by", "owner")
            _, t = self.foundry("board")
            self.assertIn(word, t)

    def test_html_and_a_decision_card_in_english(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "Fix login")
        self.foundry("decide", "open", "--question", "Ship it to production?", "--option", "A:ship", "--option",
                     "B:hold", "--asked-by", "lead", "--key", "u1:go", "--unit", "u1")
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), "html"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.dir, "board.html"), encoding="utf-8") as f:
            page = r.stdout if "<html" in r.stdout.lower() else f.read()
        self.assertFalse([c for c in page if ord(c) in HANGUL][:5])
        _, text = self.foundry("board")
        self.assertIn("Decisions (1)", text)


if __name__ == "__main__":
    import unittest
    unittest.main()
