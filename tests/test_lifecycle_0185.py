"""0.1.85: a unit whose branch is already checked out (a worktree made under an older layout, worktree_root/<owner>/
<repo>/<uid>, or recorded under the repo's slug) launches in that worktree instead of failing on `git worktree add`;
the unit record follows it. A checkout outside worktree_root is refused; a new unit keeps worktree_root/<repo>/<uid>."""

import json
import os
import subprocess
import sys

from test_foundry import SCRIPTS
from test_launcher import Launcher


class ReuseCheckout(Launcher):
    test_launch_builds_the_command_and_the_worktree = None  # inherited tests run in test_launcher

    def dry(self, uid):
        return self.foundry("unit", "launch", uid, "--dry-run")[1]

    def test_an_older_layout_checkout_is_reused_and_recorded(self):
        old = os.path.join(self.wt_root, "o", "app", "alpha")
        self.git("worktree", "add", "-q", "-b", "foundry/alpha", old, cwd=self.repo)
        self.assertEqual(self.dry("alpha")["cwd"], old)
        self.claude({"bg_id": "a1", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-a", "status": "busy"}]})
        _, out = self.foundry("unit", "launch", "alpha")
        self.assertEqual(out["launched"], "lead-alpha")
        self.assertEqual(self.snap()["units"]["alpha"]["worktree"],
                         {"repo": "app", "path": old, "branch": "foundry/alpha"})

    def test_a_record_under_the_slug_is_the_same_repo(self):
        old = os.path.join(self.wt_root, "o", "app", "alpha")
        self.git("worktree", "add", "-q", "-b", "foundry/alpha", old, cwd=self.repo)
        p = os.path.join(self.dir, "repos.json")
        with open(p) as f:
            repos = json.load(f)
        repos["app"]["slug"] = "o/app"
        with open(p, "w") as f:
            json.dump(repos, f)
        self.record("unit.worktree", id="alpha", repo="o/app", path=old, branch="foundry/alpha")
        self.assertEqual(self.dry("alpha")["cwd"], old)

    def test_a_checkout_outside_worktree_root_is_refused(self):
        self.git("worktree", "add", "-q", "-b", "foundry/alpha", os.path.join(self.dir, "elsewhere"), cwd=self.repo)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "unit", "launch", "alpha", "--dry-run"],
                           env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("outside worktree_root", r.stderr + r.stdout)

    def test_a_new_unit_keeps_the_layout(self):
        self.assertEqual(self.dry("alpha")["cwd"], os.path.join(self.wt_root, "app", "alpha"))


def load_tests(loader, tests, pattern):
    """Only this file's tests: the inherited Launcher ones already run in test_launcher."""
    import unittest
    return unittest.TestSuite(ReuseCheckout(n) for n in sorted(vars(ReuseCheckout)) if n.startswith("test_")
                              and callable(getattr(ReuseCheckout, n)))
