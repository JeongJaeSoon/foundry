"""0.1.60: `foundry init` fills config and prefs from what it can discover plus flags, keeps values already set,
registers repos, refuses inside a foundry session, and prints the foreman start command."""

import json
import os
import subprocess
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)


class Init(tf.Base):
    CREW = False

    def setUp(self):
        super().setUp()
        self.wt = os.path.join(self.dir, "wt")
        self.fm = os.path.join(self.dir, "fm")
        self.env["TZ"] = "Europe/Berlin"
        self.gh({"api user": {"login": "octo"}})

    def read(self, name):
        with open(os.path.join(self.dir, name)) as f:
            return json.load(f)

    def clone(self):
        p = os.path.join(self.dir, "clone")
        subprocess.run(["git", "init", "-q", p], check=True)
        return p

    def test_first_run_discovers_and_writes(self):
        _, out = self.foundry("init", "--worktree-root", self.wt, "--foreman-dir", self.fm, "--tracker", "jira",
                              "--repo", f"app={self.clone()}")
        cfg, prefs = self.read("config.json"), self.read("prefs.json")
        self.assertEqual(cfg["owner_github_login"], "octo")
        self.assertEqual((cfg["worktree_root"], cfg["foreman_dir"]), (self.wt, self.fm))
        self.assertEqual((prefs["timezone"], prefs["trackers"]), ("Europe/Berlin", ["jira"]))
        self.assertTrue(os.path.isdir(self.wt) and os.path.isdir(self.fm))
        self.assertEqual(oct(os.stat(os.path.join(self.dir, "config.json")).st_mode & 0o777), "0o600")
        self.assertEqual(out["repos"], ["app"])
        self.assertEqual(self.read("repos.json")["app"]["tracker"], "jira")
        self.assertIn(f"cd {self.fm} && ", out["foreman"])
        self.assertIn("--agent foundry:foreman", out["foreman"])

    def test_rerun_keeps_set_values_unless_the_flag_is_given(self):
        self.foundry("init", "--worktree-root", self.wt, "--foreman-dir", self.fm)
        self.gh({"api user": {"login": "someone-else"}})
        self.env["TZ"] = "UTC"
        _, out = self.foundry("init")
        self.assertEqual(out["wrote"], {})
        self.assertEqual(self.read("config.json")["owner_github_login"], "octo")
        self.assertEqual(self.read("prefs.json")["timezone"], "Europe/Berlin")
        self.foundry("init", "--timezone", "Asia/Seoul")
        self.assertEqual(self.read("prefs.json")["timezone"], "Asia/Seoul")

    def test_dry_run_writes_nothing(self):
        _, out = self.foundry("init", "--worktree-root", self.wt, "--dry-run")
        self.assertTrue(out["dry_run"])
        self.assertIn("worktree_root", out["would_write"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "prefs.json")))
        self.assertFalse(os.path.isdir(self.wt))

    def test_bad_input_is_refused(self):
        self.foundry("init", "--owner-slack-user", "U1", ok=(2,))
        self.foundry("init", "--timezone", "Mars/Olympus", ok=(2,))
        self.foundry("init", "--repo", "noequals", "--worktree-root", self.wt, ok=(2,))


class InitInSession(tf.Base):
    def test_a_foundry_session_cannot_run_init(self):
        self.env["FOUNDRY_ROLE"] = "line-lead"
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "init", "--dry-run"],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("owner's act", r.stderr)


if __name__ == "__main__":
    import unittest
    unittest.main()
