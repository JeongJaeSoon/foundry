"""install.sh / uninstall.sh against a fake HOME and a fake `claude`: no LaunchAgents, trust journaled and rolled back."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE_CLAUDE = """#!/bin/sh
echo "$@" >> "$HOME/claude-calls.txt"
"""


class Install(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="foundry-install-")
        self.addCleanup(shutil.rmtree, self.home, True)
        plugins = os.path.join(self.home, ".local", "share", "claude-plugins")
        os.makedirs(plugins)
        shutil.copytree(ROOT, os.path.join(plugins, "foundry"), ignore=shutil.ignore_patterns("node_modules", ".git"))
        fakebin = os.path.join(self.home, "fakebin")
        os.makedirs(fakebin)
        with open(os.path.join(fakebin, "claude"), "w") as f:
            f.write(FAKE_CLAUDE)
        os.chmod(os.path.join(fakebin, "claude"), 0o755)
        self.repos = os.path.join(self.home, "repos")
        for r in ("webapp", "gateway"):  # notes-repo is missing on purpose
            subprocess.run(["git", "init", "-q", "-b", "main", os.path.join(self.repos, r)], check=True)
        self.cfg_dir = os.path.join(self.home, "cfg")
        os.makedirs(self.cfg_dir)
        with open(os.path.join(self.cfg_dir, ".claude.json"), "w") as f:
            json.dump({"numStartups": 1, "projects": {}}, f)
        self.env = {"HOME": self.home, "PATH": fakebin + os.pathsep + os.environ["PATH"],
                    "FOUNDRY_REPOS_DIR": self.repos, "CLAUDE_CONFIG_DIR": self.cfg_dir,
                    "FOUNDRY_INSTALL_REPOS": "webapp gateway notes-repo", "FOUNDRY_INSTALL_TRACKERS": "jira"}
        self.p = os.path.join(plugins, "foundry")

    def run_sh(self, name, *args):
        r = subprocess.run(["sh", os.path.join(self.p, name), *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r.stdout

    def projects(self):
        with open(os.path.join(self.cfg_dir, ".claude.json")) as f:
            return json.load(f)["projects"]

    def test_install_then_rollback(self):
        out = self.run_sh("install.sh")
        self.assertIn("--model opus --effort medium --permission-mode auto", out)
        self.assertIn("foreman effort: medium", out)
        state = os.path.join(self.home, ".local", "state", "foundry")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(state, "config.json")).st_mode), 0o600)
        with open(os.path.join(self.home, "claude-calls.txt")) as f:
            calls = f.read().splitlines()
        self.assertIn("plugin install foundry@local-foundry", calls)
        with open(os.path.join(state, "repos.json")) as f:
            self.assertEqual(sorted(json.load(f)), ["gateway", "webapp"])
        self.assertIn("skip repo notes-repo", out)
        with open(os.path.join(state, "prefs.json")) as f:
            # preflight materialized the org values once (0.1.65); the install's own choice is the trackers list
            self.assertEqual(json.load(f)["trackers"], ["jira"])
        self.assertTrue(os.path.exists(os.path.join(state, "prefs.materialized")))
        self.assertEqual({r["tracker"] for r in json.load(open(os.path.join(state, "repos.json"))).values()}, {"jira"})
        foreman_dir = os.path.realpath(os.path.join(self.home, "work", "foundry"))
        self.assertTrue(self.projects()[foreman_dir]["hasTrustDialogAccepted"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library", "LaunchAgents")))
        # The printed config dir and the foreman line use the same CLAUDE_CONFIG_DIR the trust entry went to.
        self.assertIn(f"Claude config used: {self.cfg_dir} (CLAUDE_CONFIG_DIR)", out)
        start = next(l for l in out.splitlines() if "claude --bg -n foreman" in l)
        self.assertIn(f'CLAUDE_CONFIG_DIR="{self.cfg_dir}" claude --bg', start)
        settings = json.loads(start.split("--settings '", 1)[1].rsplit("'", 1)[0])
        self.assertEqual(settings["env"], {"FOUNDRY_ROLE": "foreman", "CLAUDE_CONFIG_DIR": self.cfg_dir})

        agents = os.path.join(self.home, "Library", "LaunchAgents")
        os.makedirs(agents)
        open(os.path.join(agents, "com.local.foundry.watch.plist"), "w").close()
        open(os.path.join(agents, "com.other.thing.plist"), "w").close()
        self.run_sh("uninstall.sh")
        self.assertEqual(os.listdir(agents), ["com.other.thing.plist"])
        self.assertNotIn(foreman_dir, self.projects())
        with open(os.path.join(self.home, "claude-calls.txt")) as f:
            self.assertIn("plugin uninstall foundry@local-foundry", f.read())


class InstallGitHubOnly(Install):
    def test_no_tracker_unless_asked(self):
        """The README quickstart: install with no tracker, then `foundry init --tracker none`."""
        del self.env["FOUNDRY_INSTALL_TRACKERS"]
        self.run_sh("install.sh")
        state = os.path.join(self.home, ".local", "state", "foundry")
        with open(os.path.join(state, "repos.json")) as f:
            self.assertEqual({r["tracker"] for r in json.load(f).values()}, {"none"})
        r = subprocess.run([sys.executable, os.path.join(self.p, "scripts", "foundry.py"), "init", "--tracker", "none",
                            "--github-login", "octo"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(state, "prefs.json")) as f:
            self.assertEqual(json.load(f)["trackers"], [])
        r = subprocess.run([sys.executable, os.path.join(self.p, "scripts", "foundry.py"), "preflight", "--json"],
                           env=self.env, capture_output=True, text=True)
        self.assertTrue(json.loads(r.stdout)["checks"]["trackers"]["ok"], r.stdout)

class InstallWithoutConfigDir(Install):
    def test_install_then_rollback(self):
        del self.env["CLAUDE_CONFIG_DIR"]
        with open(os.path.join(self.home, ".claude.json"), "w") as f:
            json.dump({"projects": {}}, f)
        out = self.run_sh("install.sh")
        self.assertIn(f"Claude config used: {self.home}/.claude.json (CLAUDE_CONFIG_DIR not set)", out)
        with open(os.path.join(self.home, ".claude.json")) as f:
            self.assertIn(os.path.realpath(os.path.join(self.home, "work", "foundry")), json.load(f)["projects"])
        start = next(l for l in out.splitlines() if "claude --bg -n foreman" in l)
        self.assertNotIn("CLAUDE_CONFIG_DIR", start)


if __name__ == "__main__":
    unittest.main()
