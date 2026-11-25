"""0.1.62: views.py and the pane's fixed words follow prefs ui.language too: the JSON model carries the pane
catalog, and a board with ordinary units renders without Korean under en."""

import os
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)

HANGUL = range(0xAC00, 0xD7A4)


def hangul(s):
    return [line for line in s.split("\n") if any(ord(ch) in HANGUL for ch in line)]


class EnglishBoard(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "u1", "--title", "Fix login")
        self.foundry("unit", "new", "--id", "u2", "--title", "Bump deps")
        self.record("unit.status", id="u2", status="in_progress")
        self.foundry("prefs", "set", "ui.language", "en", "--by", "owner")

    def test_the_model_carries_the_pane_catalog_in_the_chosen_language(self):
        r = self.run_board("json")
        self.assertEqual(r["i18n"]["lang"], "en")
        self.assertTrue(r["i18n"]["pane"])
        self.assertFalse(hangul(str(r["i18n"]["pane"])))

    def test_text_board_has_no_korean(self):
        _, out = self.foundry("board")
        self.assertEqual(hangul(out), [], out)

    def run_board(self, fmt):
        import json
        import subprocess
        p = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), fmt], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)


if __name__ == "__main__":
    import unittest
    unittest.main()
