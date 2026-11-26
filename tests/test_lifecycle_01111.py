"""0.1.111: docs/concepts.md draws its diagrams as standalone SVG files in docs/img. Each file carries its own style
and arrow markers and loads nothing from outside, so it renders the same through an image link anywhere."""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET

import test_foundry as tf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
SVG = "{http://www.w3.org/2000/svg}"
FIGURES = ("hierarchy", "components", "sequence", "approval-tiers", "sweeper", "monitor-cron", "dependency-graph",
           "day-timeline")


def read(*p):
    with open(os.path.join(*p), encoding="utf-8") as f:
        return f.read()


class Figures(unittest.TestCase):
    def test_concepts_link_every_figure_and_every_link_resolves(self):
        links = re.findall(r"!\[[^\]]+\]\((img/[^)]+\.svg)\)", read(DOCS, "concepts.md"))
        self.assertEqual(links, [f"img/{n}.svg" for n in FIGURES])
        for rel in links:
            self.assertTrue(os.path.isfile(os.path.join(DOCS, rel)), rel)

    def test_each_figure_is_self_contained(self):
        for n in FIGURES:
            text = read(DOCS, "img", f"{n}.svg")
            root = ET.fromstring(text)
            self.assertEqual(root.tag, SVG + "svg", n)
            self.assertTrue(root.get("aria-label"), n)
            style = root.find(SVG + "style")
            self.assertIsNotNone(style, n)
            self.assertIn("prefers-color-scheme: dark", style.text, n)
            self.assertIsNotNone(root.find(SVG + "rect[@class='bg']"), n)
            ids = {e.get("id") for e in root.iter() if e.get("id")}
            for ref in re.findall(r"url\(#([\w-]+)\)", text):
                self.assertIn(ref, ids, f"{n}: #{ref}")
            self.assertEqual([e.tag for e in root.iter() if e.tag in (SVG + "image", SVG + "foreignObject")], [], n)
            self.assertNotIn("@import", text, n)
            self.assertIsNone(re.search(r"href=|url\((?!#)", text), n)

    def test_figure_text_is_english(self):
        for n in FIGURES:
            self.assertIsNone(re.search("[　-鿿가-힯＀-￯]", read(DOCS, "img", f"{n}.svg")), n)


class PrefsIsolation(unittest.TestCase):
    """The suite never reads the user's own state: their ui.language=en must not change what the tests render."""

    def test_a_real_looking_user_prefs_file_does_not_reach_the_suite(self):
        home = tempfile.mkdtemp(dir=tf.SCRATCH)
        self.addCleanup(shutil.rmtree, home, True)
        real = os.path.join(home, ".local", "state", "foundry")
        os.makedirs(real)
        with open(os.path.join(real, "prefs.json"), "w") as f:
            json.dump({"ui": {"language": "en"}}, f)
        sys.path.insert(0, tf.SCRIPTS)
        import report
        with unittest.mock.patch.dict(os.environ, {"HOME": home}):
            self.assertTrue(os.environ["FOUNDRY_STATE_DIR"].startswith(tf.SESSION_DIR))
            self.assertNotEqual(tf.REAL_HOME, tf.SESSION_DIR)
            self.assertTrue(os.environ["CLAUDE_CONFIG_DIR"].startswith(tf.SESSION_DIR))
            self.assertEqual(report.approval_what("Bash: gh pr merge 1647 -R example-org/webapp --squash"),
                             "webapp#1647 \uba38\uc9c0")
        self.assertFalse(os.path.exists(os.path.join(os.environ["FOUNDRY_STATE_DIR"], "prefs.json")))


    def test_the_whole_run_has_a_throwaway_home(self):
        self.assertTrue(os.path.expanduser("~").startswith(tf.SESSION_DIR))
        self.assertNotEqual(os.path.expanduser("~"), tf.REAL_HOME)

    def test_the_shim_never_points_a_real_home_at_a_test_config(self):
        sys.path.insert(0, tf.SCRIPTS)
        import shim
        home = tempfile.mkdtemp()  # outside the scratch root, like a real home
        self.addCleanup(shutil.rmtree, home, True)
        ok, lines = shim.install(home, cfg=os.path.join(tf.SESSION_DIR, "claude"))
        self.assertFalse(ok)
        self.assertIn("throwaway HOME", lines[0])
        self.assertFalse(os.path.exists(shim.shim_path(home)))
        ok, _ = shim.install(home, cfg=os.path.join(home, ".claude"))
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
