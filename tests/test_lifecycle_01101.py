"""0.1.101: no single-machine paths. The foreman's home and the main-checkout root are config keys (`foreman_dir`,
`repos_dir`, beside `worktree_root`) whose defaults are the old values; `foundry init` sets them; install.sh and
uninstall.sh find the plugin from their own location and every other path from the config."""

import json
import os
import re
import subprocess
import sys

import test_foundry as tf
import test_install

sys.path.insert(0, tf.SCRIPTS)
ROOT = os.path.dirname(tf.SCRIPTS)


class Config(tf.Base):
    def test_defaults_are_the_old_values_and_init_sets_them(self):
        code = "import sys, json; sys.path.insert(0, sys.argv[1]); import foundrylib as cl; c = cl.load_config(); " \
               "print(json.dumps([c['foreman_dir'], c['repos_dir'], c['worktree_root']]))"
        run = lambda: json.loads(subprocess.run([sys.executable, "-c", code, tf.SCRIPTS], env=self.env,  # noqa: E731
                                                capture_output=True, text=True).stdout)
        self.assertEqual(run(), ["~/work/foundry", "~/src", "~/work/wt"])
        home = os.path.join(self.dir, "fh")
        self.foundry("init", "--foreman-dir", home, "--repos-dir", "/r", "--github-login", "x", "--timezone", "UTC")
        self.assertEqual(run()[:2], [home, "/r"])
        self.assertTrue(os.path.isdir(home))

    def test_scripts_hold_no_home_paths_outside_the_config_defaults(self):
        hits = []
        for name in ("install.sh", "uninstall.sh"):
            with open(os.path.join(ROOT, name)) as f:
                hits += [f"{name}: {x.strip()}" for x in f if not x.lstrip().startswith("#")
                         and re.search(r"~/work|~/src|\$HOME/(work|src|\.local)", x)]
        with open(os.path.join(tf.SCRIPTS, "firstrun.py")) as f:
            hits += [f"firstrun.py: {x.strip()}" for x in f if "~/work" in x]
        self.assertEqual(hits, [])


class InstallUsesConfig(test_install.Install):
    def test_foreman_home_from_config(self):
        state = os.path.join(self.home, ".local", "state", "foundry")
        os.makedirs(state)
        home = os.path.join(self.home, "elsewhere", "foreman")
        with open(os.path.join(state, "config.json"), "w") as f:
            json.dump({"foreman_dir": home}, f)
        out = self.run_sh("install.sh")
        self.assertTrue(os.path.isdir(home))
        self.assertIn(f'cd {home} && ', out)
        self.assertFalse(os.path.exists(os.path.join(self.home, "work", "foundry")))


def load_tests(loader, tests, pattern):
    import unittest
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(Config))
    suite.addTest(InstallUsesConfig("test_foreman_home_from_config"))
    return suite
