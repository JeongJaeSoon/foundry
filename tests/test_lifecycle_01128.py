"""0.1.128: one source for unit status words. The board catalog's section and status labels (i18n.STATUS_KEYS,
i18n.status_word) are what the board, views, the pane payload and sync say, so a relabel is one catalog edit."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from test_foundry import SCRIPTS

sys.path.insert(0, SCRIPTS)
import board  # noqa: E402
import i18n  # noqa: E402
import sync  # noqa: E402
import views  # noqa: E402


class OneSource(unittest.TestCase):
    def test_a_relabel_in_the_board_catalog_reaches_every_surface(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        shutil.copytree(i18n.DIR, d, dirs_exist_ok=True)
        p = os.path.join(d, "en", "board.json")
        with open(p, encoding="utf-8") as f:
            cat = json.load(f)
        cat.update(section_queue="Backlog", status_owner_check="Owner look")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cat, f)
        old = i18n.DIR
        i18n.DIR = d
        i18n._CAT.clear()
        try:
            for st, word in (("queue", "Backlog"), ("waiting_owner", "Owner look")):
                self.assertEqual(i18n.status_word(st, "en"), word)
                self.assertEqual(board.pane_words("en")[f"status_{st}"], word)
            with mock.patch.object(i18n, "lang", return_value="en"):
                self.assertEqual(board.status_label("queue"), "Backlog")
                self.assertEqual(views.STATUS_KO["queue"], "Backlog")
                self.assertEqual(sync.status_word("waiting_owner"), "Owner look")
                self.assertEqual(sync.status_word("odd"), "odd")
        finally:
            i18n.DIR = old
            i18n._CAT.clear()

    def test_no_other_catalog_spells_a_status_word(self):
        for lang in i18n.LANGS:
            for ns in ("views", "pane", "sync"):
                keys = {f"status_{s}" for s in i18n.STATUS_KEYS} & set(i18n._file(lang, ns))
                self.assertEqual(keys, set(), f"{lang}/{ns}.json")
            for st in i18n.STATUS_KEYS:
                self.assertNotEqual(i18n.status_word(st, lang), i18n.STATUS_KEYS[st], f"{lang} {st}")


if __name__ == "__main__":
    unittest.main()
