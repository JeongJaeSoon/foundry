"""0.1.32: owner-facing advice names paths that work (the pane button, `/foundry …`, a separate terminal tab via
the ~/.local/bin/foundry shim) and never a shell escape inside a session; queued approvals stay answerable."""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import board  # noqa: E402
import foundrylib as cl  # noqa: E402
import shim  # noqa: E402

ROOT = os.path.dirname(tf.SCRIPTS)
# A shell escape that runs foundry (or a foundry script) from inside a Claude session.
BANG = re.compile(r"(?<![\w!=])!\s*`?\s*(?:foundry\b|sh\s+\S*foundry|python3\s+\S*foundry)")


def owner_facing_files():
    pats = ["agents/*.md", "skills/*/SKILL.md", "commands/*.md", "scripts/*.py", "hooks/*.ts", "hooks/*.tsx",
            "*.sh", "README*.md", "docs/*.md"]
    out = []
    for p in pats:
        out += [f for f in glob.glob(os.path.join(ROOT, p)) if not f.endswith((".test.ts", ".test.tsx"))]
    return sorted(set(out))


class NoShellEscapeAdvice(unittest.TestCase):
    def test_no_owner_facing_text_suggests_a_shell_escape(self):
        hits = []
        for f in owner_facing_files():
            for n, line in enumerate(open(f, encoding="utf-8"), 1):
                if BANG.search(line):
                    hits.append(f"{os.path.relpath(f, ROOT)}:{n}: {line.strip()[:120]}")
        self.assertEqual(hits, [])

    def test_the_pattern_catches_the_bad_shapes(self):
        for bad in ("run `! foundry approve allow ap-1 --by owner`", "! sh ~/x/foundry/install.sh", "(`! foundry prefs set`)"):
            self.assertTrue(BANG.search(bad), bad)
        for ok in ("if (!foundryReady)", "x != foundry", "`/foundry approve allow ap-1`"):
            self.assertFalse(BANG.search(ok), ok)

    def test_owner_how_names_the_pane_the_slash_command_and_a_terminal(self):
        t = cl.owner_how("approve allow ap-1", "[allow]")
        self.assertIn("board pane's [allow] button", t)
        self.assertIn("`/foundry approve allow ap-1`", t)
        self.assertRegex(t, r"separate terminal tab: `\S*foundry approve allow ap-1 --by owner`")
        self.assertNotIn("!", t)

    def test_the_refusal_inside_a_session_points_outside_it(self):
        env = dict(os.environ, FOUNDRY_ROLE="foreman", FOUNDRY_STATE_DIR=tempfile.mkdtemp(), HOME=tempfile.mkdtemp())
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "setup", "--shim-only"],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("is the owner's act", r.stderr)
        self.assertIn("`/foundry setup --shim-only`", r.stderr)
        self.assertIn("separate terminal tab", r.stderr)
        self.assertFalse(BANG.search(r.stderr), r.stderr)
        self.assertFalse(os.path.exists(os.path.join(env["HOME"], ".local", "bin", "foundry")))


class Shim(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.cfg = os.path.join(self.home, ".claude")
        self.state = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.state, ignore_errors=True)

    def registry(self, path, enabled=True):
        os.makedirs(os.path.join(self.cfg, "plugins"), exist_ok=True)
        with open(os.path.join(self.cfg, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"version": 2, "plugins": {"foundry@local-foundry": [
                {"scope": "user", "installPath": path, "version": "9.9.9"}]}}, f)
        with open(os.path.join(self.cfg, "settings.json"), "w") as f:
            json.dump({"enabledPlugins": {"foundry@local-foundry": enabled}}, f)

    def run_shim(self, *args):
        # A plain terminal: no plugin bin on PATH, no foundry session env, no CLAUDE_CONFIG_DIR.
        env = {"PATH": "/usr/bin:/bin", "HOME": self.home, "FOUNDRY_STATE_DIR": self.state}
        return subprocess.run([shim.shim_path(self.home), *args], env=env, capture_output=True, text=True)

    def test_install_hints_path_and_runs_the_enabled_plugin_from_a_plain_terminal(self):
        ok, lines = shim.install(self.home, cfg="", path_env="/usr/bin:/bin")
        self.assertTrue(ok)
        self.assertTrue(shim.installed(self.home))
        self.assertIn("is not on PATH", "\n".join(lines))
        self.assertEqual(shim.cli(self.home), "~/.local/bin/foundry")
        ok, lines = shim.install(self.home, cfg="", path_env=os.path.join(self.home, ".local", "bin"))
        self.assertNotIn("is not on PATH", "\n".join(lines))

        r = self.run_shim("board", "--format", "json")
        self.assertEqual(r.returncode, 127)
        self.assertIn("no enabled foundry plugin", r.stderr)

        self.registry(ROOT)
        r = self.run_shim("board", "--format", "json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sections", json.loads(r.stdout))

        self.registry(ROOT, enabled=False)
        self.assertEqual(self.run_shim("board").returncode, 127)

    def test_a_foreign_foundry_is_never_replaced(self):
        p = shim.shim_path(self.home)
        os.makedirs(os.path.dirname(p))
        with open(p, "w") as f:
            f.write("#!/bin/sh\necho mine\n")
        ok, lines = shim.install(self.home, cfg="")
        self.assertFalse(ok)
        self.assertIn("not the foundry shim", lines[0])
        with open(p) as f:
            self.assertEqual(f.read(), "#!/bin/sh\necho mine\n")
        self.assertTrue(shim.cli(self.home).endswith(os.path.join("bin", "foundry")))


class QueuedApprovals(unittest.TestCase):
    def snap(self):
        mk = lambda rid, unit, **f: {"rid": rid, "unit": unit, "tier": 3, "answer": None, "asked": f"2026-11-19T1{len(rid)}:00:00+09:00", **f}
        return {"units": {"live": {"status": "waiting_owner"}, "fin": {"status": "done"}},
                "approvals": {"ap-1": mk("ap-1", "live"), "ap-22": mk("ap-22", "live", gave_up="t"),
                              "ap-333": mk("ap-333", "fin", gave_up="t"),
                              "ap-4444": mk("ap-4444", "live", answer="allow")}}

    def test_queued_requests_stay_on_the_board_until_answered(self):
        with unittest.mock.patch.dict(os.environ, FOUNDRY_NOW="2026-11-19T15:00:00+09:00"):  # inside the 0.1.43 TTL
            rows = [board._approval(a) for a in board.open_approvals(self.snap())]
        self.assertEqual({r["rid"]: r["queued"] for r in rows}, {"ap-1": False, "ap-22": True})


if __name__ == "__main__":
    unittest.main()
