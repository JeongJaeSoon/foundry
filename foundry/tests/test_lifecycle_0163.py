"""0.1.63: render-time CLI text (handover, kaizen, `decide show`, stall and release labels) follows prefs
ui.language through the `cli` catalog. A stored handover keeps the language it was written in, and the board
still reads it after the owner switches language: section words are recognised in every language."""

import json
import os
import subprocess
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)

HANGUL = range(0xAC00, 0xD7A4)
DECISION_TITLES = {"결정 대기", "Awaiting decision", "決定待ち"}
NONE_WORDS = {"없음", "None", "なし"}


def hangul(s):
    return [line for line in s.split("\n") if any(ord(ch) in HANGUL for ch in line)]


class HandoverAnyLanguage(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "u1", "--title", "Fix login")
        self.foundry("unit", "new", "--id", "u2", "--title", "Bump deps")
        self.foundry("decide", "open", "--unit", "u2", "--question", "Ship the fix now?", "--option", "go:Go",
                     "--option", "hold:Hold", "--asked-by", "line-lead", "--key", "u2:ship")

    def lang(self, value):
        self.foundry("prefs", "set", "ui.language", value, "--by", "owner")

    def handover(self):
        _, out = self.foundry("report", "handover")
        return out

    def sections(self):
        p = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), "json"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)["handover"]["sections"]

    def assert_parsed(self, secs, live_title, empty_title):
        decisions = [s for s in secs if s["title"] in DECISION_TITLES]
        self.assertEqual(len(decisions), 1, secs)  # the stored section is found, not doubled by a live one
        self.assertEqual(decisions[0]["title"], live_title)
        self.assertTrue(decisions[0]["live"])
        self.assertEqual([x["unit"] for x in decisions[0]["items"]], ["u2"])
        empty = next(s for s in secs if s["title"] == empty_title)
        self.assertEqual((empty["items"], empty["count"]), ([], 0))  # its 「none」 line is skipped
        self.assertFalse([x for s in secs for x in s["items"] if (x.get("line") or "").strip() in NONE_WORDS])
        prs = [s for s in secs if "PR" in s["title"]]
        self.assertEqual([s.get("link") for s in prs], ["pr"])  # the stored PR section gives way to the live one

    def test_a_korean_handover_still_parses_after_switching_to_english(self):
        text = self.handover()
        self.assertIn("## 결정 대기 (1)", text)
        self.assertIn("- 없음", text)
        self.lang("en")
        self.assert_parsed(self.sections(), "Awaiting decision", "자동 종료")

    def test_an_english_handover_parses_in_english_and_after_switching_to_korean(self):
        self.lang("en")
        text = self.handover()
        self.assertIn("## Awaiting decision (1)", text)
        self.assertIn("- None", text)
        self.assertIn("## PR changes (0)", text)
        self.assert_parsed(self.sections(), "Awaiting decision", "Auto-closed")
        self.lang("ko")
        self.assert_parsed(self.sections(), "결정 대기", "Auto-closed")


class KeptFlag(tf.Base):
    def test_a_kept_worktree_is_flagged_in_the_inventory_and_the_unit_view_in_any_language(self):
        path = os.path.join(self.dir, "wt-u1")
        os.makedirs(path)
        self.foundry("unit", "new", "--id", "u1", "--title", "Fix login", "--repo", "o/r")
        self.record("unit.worktree", id="u1", repo="o/r", path=path, branch="b")
        self.record("unit.status", id="u1", status="done")
        with open(os.path.join(self.dir, "reclaim.json"), "w") as f:
            json.dump({"u1": {"path": path, "kept": "dirty"}}, f)
        self.foundry("prefs", "set", "ui.language", "en", "--by", "owner")
        p = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), "json"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        m = json.loads(p.stdout)
        inv = [r for r in m["inventory"]["repos"]["o/r"] if r["kind"] == "worktree"]
        self.assertEqual([(r["reclaim"], r["reclaim_kept"]) for r in inv], [("Kept: dirty", True)])
        view = next(r for s in m["sections"] for r in s["rows"] if r["id"] == "u1")["view"]
        self.assertEqual((view["worktree_note"], view["worktree_kept"]), ("Kept: dirty", True))


class EnglishCli(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "u1", "--title", "Fix login")
        self.foundry("prefs", "set", "ui.language", "en", "--by", "owner")

    def test_decide_show_labels_have_no_korean(self):
        _, res = self.foundry("decide", "open", "--unit", "u1", "--question", "Ship the fix now?",
                              "--context", "The fix is reviewed.", "--option", "go:Go", "--option", "hold:Hold",
                              "--recommend", "go", "--reason", "it is green", "--deadline", "2026-11-19T10:00:00+09:00",
                              "--default", "hold", "--asked-by", "line-lead", "--key", "u1:ship", "--ref", "unit:u1")
        _, out = self.foundry("decide", "show", res["card"]["id"])
        self.assertEqual(hangul(out), [], out)
        for word in ("open", "Question", "Context", "Options", "(recommended)", "Recommended: go", "Deadline:",
                     "(then hold)", "Hinges on: unit:u1"):
            self.assertIn(word, out)

    def test_kaizen_has_no_korean(self):
        self.record("nearmiss", kind="stall", unit="u1")
        self.record("pr.state", unit="u1", repo="example-org/app", number=1, state="CHANGES_REQUESTED")
        _, out = self.foundry("report", "kaizen")
        self.assertEqual(hangul(out), [], out)
        self.assertIn("## Near misses (1)", out)
        self.assertIn("## Rework rate", out)
        self.assertIn("u1:", out)


if __name__ == "__main__":
    import unittest
    unittest.main()
