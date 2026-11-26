"""0.1.127: install.sh runs `foundry setup --shim-only` (firstrun.base_setup) instead of its own copy of those steps,
so install.sh and `/foundry setup` set up the same files: two scratch HOMEs, a fake `claude`, the same tree."""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from test_foundry import ROOT, SCRATCH

FAKE_CLAUDE = """#!/bin/sh
echo "$@" >> "$HOME/claude-calls.txt"
"""
OWN = ("fakebin", "cfg", "claude-calls.txt", os.path.join(".local", "share", "claude-plugins"))


class InstallMatchesSetup(unittest.TestCase):
    def home(self):
        os.makedirs(SCRATCH, exist_ok=True)
        home = tempfile.mkdtemp(dir=SCRATCH, prefix="install-")
        self.addCleanup(shutil.rmtree, home, True)
        plugin = os.path.join(home, ".local", "share", "claude-plugins", "foundry")
        shutil.copytree(ROOT, plugin, ignore=shutil.ignore_patterns("node_modules", ".git"))
        fakebin = os.path.join(home, "fakebin")
        os.makedirs(fakebin)
        with open(os.path.join(fakebin, "claude"), "w") as f:
            f.write(FAKE_CLAUDE)
        os.chmod(os.path.join(fakebin, "claude"), 0o755)
        cfg = os.path.join(home, "cfg")
        os.makedirs(cfg)
        with open(os.path.join(cfg, ".claude.json"), "w") as f:
            json.dump({"projects": {}}, f)
        env = {"HOME": home, "PATH": fakebin + os.pathsep + os.environ["PATH"], "CLAUDE_CONFIG_DIR": cfg}
        return home, plugin, env

    def files(self, home):
        """{relative path: mode} of what was set up under `home`, minus this test's own fixtures."""
        out = {}
        for d, dirs, names in os.walk(home):
            rel = os.path.relpath(d, home)
            dirs[:] = [x for x in dirs if os.path.normpath(os.path.join(rel, x)) not in OWN]
            for x in [*dirs, *names]:
                r = os.path.normpath(os.path.join(rel, x))
                if r not in OWN:  # a backup's name carries its time
                    out[re.sub(r"\d{8}T\d+", "<ts>", r)] = stat.S_IMODE(os.lstat(os.path.join(d, x)).st_mode)
        return out

    def trusted(self, home):
        with open(os.path.join(home, "cfg", ".claude.json")) as f:
            return sorted(os.path.relpath(p, os.path.realpath(home)) for p in json.load(f)["projects"])

    def test_install_sh_and_setup_set_up_the_same_files(self):
        a, plugin_a, env_a = self.home()
        r = subprocess.run(["sh", os.path.join(plugin_a, "install.sh")], env=env_a, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertNotIn("setup --shim-only failed", r.stderr)
        b, plugin_b, env_b = self.home()
        r = subprocess.run([sys.executable, os.path.join(plugin_b, "scripts", "foundry.py"), "setup", "--shim-only"],
                           env=env_b, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        fa, fb = self.files(a), self.files(b)
        self.maxDiff = None
        self.assertEqual(fa, fb)
        for p in ("work/foundry", "work/wt", ".local/state/foundry/config.json", ".local/bin/foundry"):
            self.assertIn(p, fa)
        self.assertEqual(fa[".local/state/foundry/config.json"], 0o600)
        self.assertEqual(self.trusted(a), self.trusted(b))
        self.assertEqual(self.trusted(a), ["work/foundry"])
        for rel in (".local/bin/foundry", ".local/state/foundry/config.json",
                    "cfg/.claude.json"):  # 0.1.129: the same contents too, HOME aside
            with open(os.path.join(a, rel)) as f, open(os.path.join(b, rel)) as g:
                self.assertEqual(f.read().replace(os.path.realpath(a), "HOME").replace(a, "HOME"),
                                 g.read().replace(os.path.realpath(b), "HOME").replace(b, "HOME"), rel)

    def test_install_sh_no_longer_spells_the_steps_out(self):
        with open(os.path.join(ROOT, "install.sh")) as f:
            text = f.read()
        self.assertIn("scripts/foundry.py\" setup --shim-only", text)
        for step in ("trust.py\" register", "shim.py\" install", "mkdir -p", "chmod 600"):
            self.assertNotIn(step, text)


if __name__ == "__main__":
    unittest.main()
