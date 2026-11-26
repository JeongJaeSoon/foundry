"""0.1.117: `/foundry setup` (and `foundry setup` from a terminal) does what install.sh sets up besides the plugin
itself: the state, foreman and worktree directories, config.json (mode 600), the foreman directory's trust entry,
preflight and the ~/.local/bin/foundry shim, idempotently. A marketplace install that never ran it gets one
SessionStart line naming it; the hook itself sets nothing up."""

import json
import os
import stat
import subprocess
import sys
import tempfile

from test_foundry import SCRIPTS, Base

ROOT = os.path.dirname(SCRIPTS)


class Setup(Base):
    CREW = False

    def setUp(self):
        super().setUp()
        self.home = tempfile.mkdtemp()
        self.cfg = os.path.join(self.home, "cfg")
        os.makedirs(self.cfg)
        with open(os.path.join(self.cfg, ".claude.json"), "w") as f:
            json.dump({"projects": {}}, f)
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump({"foreman_dir": os.path.join(self.home, "work", "foundry"),
                       "worktree_root": os.path.join(self.home, "work", "wt")}, f)
        os.chmod(os.path.join(self.dir, "config.json"), 0o644)

    def setup_run(self):
        env = {k: v for k, v in self.env.items() if k != "FOUNDRY_ROLE"}
        env.update(HOME=self.home, CLAUDE_CONFIG_DIR=self.cfg)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "setup", "--shim-only"], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r.stdout

    def test_setup_does_what_install_sh_sets_up_and_reruns_cleanly(self):
        out = self.setup_run()
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, "config.json")).st_mode), 0o600)
        for d in ("foundry", "wt"):
            self.assertTrue(os.path.isdir(os.path.join(self.home, "work", d)))
        foreman = os.path.realpath(os.path.join(self.home, "work", "foundry"))
        with open(os.path.join(self.cfg, ".claude.json")) as f:
            self.assertTrue(json.load(f)["projects"][foreman]["hasTrustDialogAccepted"])
        self.assertIn("trust", out)
        self.assertIn("preflight:", out)
        self.assertTrue(os.path.exists(os.path.join(self.home, ".local", "bin", "foundry")))
        again = self.setup_run()
        self.assertIn(": present", again)

    def test_the_slash_command_offers_setup(self):
        with open(os.path.join(ROOT, "hooks", "slash.ts")) as f:
            self.assertIn("'setup'", f.read().split("SLASH_VERBS", 1)[1].split("\n", 1)[0])


class NotSetUp(Base):
    CREW = False

    def test_one_line_and_nothing_written(self):
        out = self.hook("SessionStart", session_id="S9", source="startup")
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], "foundry is not set up: run /foundry setup")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "config.json")))
