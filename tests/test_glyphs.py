"""prefs `glyphs` and `foundry setup`: auto draws Nerd Font glyphs only after a recorded render test, the font
install needs consent, and nothing is installed without a prompt answered or --install."""

import json
import os
import subprocess
import sys

from test_foundry import SCRIPTS, Base


class Glyphs(Base):
    CREW = False

    def setUp(self):
        super().setUp()
        self.fonts = os.path.join(self.dir, "fonts")
        os.makedirs(self.fonts)
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        self.brew_log = os.path.join(self.dir, "brew.log")
        brew = os.path.join(self.bin, "brew")
        with open(brew, "w") as f:
            f.write(f'#!/bin/sh\necho "$@" >> "{self.brew_log}"\n'
                    f'touch "{self.fonts}/SymbolsNerdFontMono-Regular.ttf"\n')
        os.chmod(brew, 0o755)
        self.env.update(FOUNDRY_FONT_DIRS=self.fonts, PATH=self.bin + os.pathsep + self.env["PATH"])

    def setup_cmd(self, *args, stdin=None):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "setup", *args], env=self.env,
                           stdin=subprocess.DEVNULL if stdin is None else None, input=stdin,
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def glyphs(self):
        _, out = self.foundry("board", "--format", "json")
        return out["glyphs"]

    def test_auto_is_emoji_until_a_render_test_passes(self):
        self.assertEqual(self.glyphs(), "emoji")
        open(os.path.join(self.fonts, "JetBrainsMonoNerdFontMono-Regular.ttf"), "w").close()
        self.assertEqual(self.glyphs(), "emoji")  # installed is not enough: the terminal has to draw it
        rc, out = self.setup_cmd("--render-ok")
        self.assertEqual(rc, 0, out)
        self.assertIn("installed", out)
        self.assertEqual(self.glyphs(), "nerd")
        self.setup_cmd("--render-bad")
        self.assertEqual(self.glyphs(), "emoji")

    def test_explicit_prefs_win(self):
        self.foundry("prefs", "set", "glyphs", "nerd")
        self.assertEqual(self.glyphs(), "nerd")
        self.foundry("prefs", "set", "glyphs", "emoji")
        self.assertEqual(self.glyphs(), "emoji")
        rc, out = self.foundry("prefs", "set", "glyphs", "fancy", ok=(2,))
        self.assertEqual(rc, 2)

    def test_no_install_without_consent(self):
        rc, out = self.setup_cmd()
        self.assertEqual(rc, 0, out)
        self.assertIn("not installed", out)
        self.assertIn("foundry setup --install", out)
        self.assertFalse(os.path.exists(self.brew_log))
        self.assertNotIn("Render test", out)

    def test_install_with_consent_runs_brew_and_prints_terminal_steps(self):
        rc, out = self.setup_cmd("--install")
        self.assertEqual(rc, 0, out)
        with open(self.brew_log) as f:
            self.assertEqual(f.read().split(), ["install", "--cask", "font-symbols-only-nerd-font"])
        self.assertIn("iTerm2", out)
        self.assertIn("Render test", out)
        self.assertIn("--render-ok", out)  # no prompt possible here: the record waits for the owner
        self.assertFalse(os.path.exists(os.path.join(self.dir, "glyphs.json")))
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            self.assertIn("glyphs.font_installed", f.read())

    def test_installed_font_skips_brew(self):
        open(os.path.join(self.fonts, "SymbolsNerdFont-Regular.ttf"), "w").close()
        rc, out = self.setup_cmd("--install", "--render-ok")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.brew_log))
        with open(os.path.join(self.dir, "glyphs.json")) as f:
            self.assertTrue(json.load(f)["render_ok"])
