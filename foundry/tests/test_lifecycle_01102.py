"""0.1.102: scripts/smoke.sh, the end-to-end smoke a person runs from a plain terminal. Never run here (it launches a
real lead session); these checks read it: it parses, every real-resource call comes after the throwaway HOME,
CLAUDE_CONFIG_DIR and state dir are exported, the four steps are there, the lead is stopped on any exit, and GitHub
is only read."""

import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SMOKE = os.path.join(ROOT, "scripts", "smoke.sh")


class Smoke(unittest.TestCase):
    def setUp(self):
        with open(SMOKE) as f:
            self.src = f.read()
        self.code = [x for x in self.src.splitlines() if x.strip() and not x.lstrip().startswith("#")]

    def test_it_parses(self):
        r = subprocess.run(["sh", "-n", SMOKE], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_throwaway_dirs_come_first(self):
        first_real = next(i for i, x in enumerate(self.code) if re.search(r"\$F |install\.sh\"|\bclaude |\bgh ", x))
        exported = [i for i, x in enumerate(self.code)
                    if re.match(r"\s*export (HOME|CLAUDE_CONFIG_DIR|FOUNDRY_STATE_DIR)=", x)]
        self.assertEqual(len(exported), 3)
        self.assertTrue(all(i < first_real for i in exported))
        self.assertIn('mktemp -d', self.src)

    def test_the_four_steps(self):
        for step in ('install.sh"', "prefs set ui.language en", "$F board", "gh issue view", "unit launch smoke-lead",
                     "mcp agent foundry:line-lead", "unit stop smoke-lead"):
            self.assertIn(step, self.src)

    def test_the_lead_is_stopped_on_every_exit(self):
        self.assertRegex(self.src, r"trap cleanup EXIT")
        body = self.src.split("cleanup() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("unit stop", body)

    def test_github_is_only_read(self):
        self.assertFalse([x for x in self.code if re.search(r"\bgh (issue|pr) (comment|edit|close|reopen|create|merge)", x)])
