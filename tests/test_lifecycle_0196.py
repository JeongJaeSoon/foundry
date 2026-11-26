"""0.1.96: housekeeping scans worktrees only under `worktree_root` (or FOUNDRY_WORKTREE_DIRS), and
`foundry prefs unset <key>` removes a key from the user's prefs.json, even one a newer version no longer knows."""

import json
import os
import sys
import unittest

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import housekeep  # noqa: E402


class Gone(unittest.TestCase):
    def test_worktrees_are_scanned_only_under_worktree_root(self):
        old = os.environ.pop("FOUNDRY_WORKTREE_DIRS", None)
        try:
            self.assertEqual(housekeep.worktree_dirs({"worktree_root": "/w"}), ["/w"])
            self.assertEqual(housekeep.worktree_dirs({}), ["~/work/wt"])
        finally:
            if old is not None:
                os.environ["FOUNDRY_WORKTREE_DIRS"] = old


class Unset(tf.Base):
    def write(self, d):
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump(d, f)

    def read(self):
        with open(os.path.join(self.dir, "prefs.json")) as f:
            return json.load(f)

    def test_an_unknown_key_is_refused_until_unset(self):
        dropped = "retired_key"
        self.write({dropped: "term_x", "handover_time": "09:30"})
        _, out = self.foundry("prefs", "validate", ok=(2,))
        self.assertFalse(out["valid"])
        _, out = self.foundry("prefs", "unset", dropped)
        self.assertEqual(out, {"unset": dropped, "changed": True})
        self.assertEqual(self.read(), {"handover_time": "09:30"})
        self.assertTrue(self.foundry("prefs", "validate")[1]["valid"])
        self.assertEqual(self.foundry("prefs", "unset", dropped)[1]["changed"], False)

    def test_a_nested_key_goes_back_to_its_default(self):
        self.foundry("prefs", "set", "report.collapse_lines", "5", "--by", "owner")
        self.foundry("prefs", "unset", "report.collapse_lines")
        self.assertNotIn("collapse_lines", self.read().get("report") or {})
