"""0.1.61: prefs `ui.language` (ko | ja | en) picks the board's message catalog; every catalog namespace has the
same keys and placeholders in all three languages; a key missing in one language falls back to en. Also: `foundry
init` journals the value it replaced."""

import json
import os
import string
import sys
import tempfile
import unittest

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import i18n  # noqa: E402

HANGUL = range(0xAC00, 0xD7A4)


def fields(s):
    return sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)


class Catalogs(unittest.TestCase):
    def test_every_namespace_has_the_same_keys_and_placeholders_in_every_language(self):
        self.assertTrue(i18n.namespaces())
        for ns in i18n.namespaces():
            cats = {lang: i18n._file(lang, ns) for lang in i18n.LANGS}
            for lang, cat in cats.items():
                self.assertEqual(set(cat), set(cats["en"]), f"{lang}/{ns}.json keys differ from en")
                for k, v in cat.items():
                    self.assertEqual(fields(v), fields(cats["en"][k]), f"{lang}/{ns}.json {k}: placeholders")

    def test_en_and_ja_catalogs_carry_no_hangul(self):
        for ns in i18n.namespaces():
            for lang in ("en", "ja"):
                text = json.dumps(i18n._file(lang, ns), ensure_ascii=False)
                self.assertFalse(any(ord(ch) in HANGUL for ch in text), f"{lang}/{ns}.json")

    def test_missing_key_falls_back_to_en_then_to_the_key(self):
        d = tempfile.mkdtemp()
        for lang, cat in (("en", {"a": "A {n}", "b": "B"}), ("ko", {"a": "가 {n}"})):
            os.makedirs(os.path.join(d, lang))
            with open(os.path.join(d, lang, "x.json"), "w") as f:
                json.dump(cat, f)
        old, i18n.DIR = i18n.DIR, d
        i18n._CAT.clear()
        try:
            self.assertEqual(i18n.t("x.a", "ko", n=1), "가 1")
            self.assertEqual(i18n.t("x.b", "ko"), "B")
            self.assertEqual(i18n.t("x.c", "ko"), "x.c")
        finally:
            i18n.DIR = old
            i18n._CAT.clear()


class UiLanguage(tf.Base):
    def test_the_board_follows_ui_language(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "U1")
        _, ko = self.foundry("board")
        self.assertIn("대기열", ko)
        self.foundry("prefs", "set", "ui.language", "en", "--by", "owner")
        _, en = self.foundry("board")
        band = en.split("\n")[1]
        self.assertIn("Queue 1", band)
        self.assertFalse(any(ord(ch) in HANGUL for ch in band), band)
        self.foundry("prefs", "set", "ui.language", "ja", "--by", "owner")
        _, ja = self.foundry("board")
        self.assertIn("キュー 1", ja.split("\n")[1])

    def test_an_unknown_language_is_refused(self):
        self.foundry("prefs", "set", "ui.language", "fr", "--by", "owner", ok=(2,))


class InitJournalsOld(tf.Base):
    CREW = False

    def test_override_records_the_replaced_value(self):
        self.gh({"api user": {"login": "octo"}})
        self.foundry("init", "--timezone", "Europe/Berlin", "--worktree-root", os.path.join(self.dir, "wt"),
                     "--foreman-dir", os.path.join(self.dir, "fm"))
        self.foundry("init", "--timezone", "Asia/Seoul")
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            recs = [json.loads(x) for x in f if '"prefs.changed"' in x]
        self.assertEqual((recs[-1].get("old"), recs[-1].get("new")), ("Europe/Berlin", "Asia/Seoul"))

    def test_values_already_in_effect_are_not_rewritten(self):
        self.gh({"api user": {"login": "octo"}})
        _, out = self.foundry("init", "--dry-run", "--timezone", "Asia/Tokyo")
        self.assertNotIn("worktree_root", out["would_write"])


if __name__ == "__main__":
    unittest.main()
