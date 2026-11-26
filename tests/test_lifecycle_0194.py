"""0.1.94: the daily report, release and review gaps, the sync status line, the prod card's options and the board's
"nothing will move this unit" reasons come from the i18n catalogs (`report`, `sync`, `release`, `board`) in prefs
ui.language. Rendered under en or ja, the board, its model, the daily report (md and Slack), the gaps, the handover,
kaizen and a decision card carry no Hangul; under ko they are byte-for-byte what 0.1.93 rendered
(tests/fixtures/render_ko_0194.json, made by `python3 tests/render_probe.py ko` on the 0.1.93 tree; a later change
that means to alter the ko render regenerates it the same way)."""

import json
import os
import re
import subprocess
import sys
import unittest

import test_foundry as tf
import render_probe

HANGUL = re.compile("[가-힣]")
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "render_ko_0194.json")


def hangul_lines(renders):
    return {k: [x for x in v.split("\n") if HANGUL.search(x)][:5] for k, v in renders.items()
            if HANGUL.search(v)}


def only_keys_of(cur, was):
    """cur cut down to the dict keys `was` has, at every depth: fields added later are left out of the compare."""
    if isinstance(was, dict) and isinstance(cur, dict):
        return {k: only_keys_of(cur.get(k), v) for k, v in was.items()}
    if isinstance(was, list) and isinstance(cur, list) and len(was) == len(cur):
        return [only_keys_of(c, w) for c, w in zip(cur, was)]
    return cur


class Renders(unittest.TestCase):
    def test_en_renders_carry_no_hangul(self):
        self.assertEqual(hangul_lines(render_probe.renders("en")), {})

    def test_ja_renders_carry_no_hangul(self):
        self.assertEqual(hangul_lines(render_probe.renders("ja")), {})

    def test_ko_renders_are_unchanged(self):
        with open(FIXTURE, encoding="utf-8") as f:
            before = json.load(f)
        now = render_probe.renders("ko")
        for k in before:
            if k == "model":  # fields a later version adds are not a change in what the ko render says
                was, cur = json.loads(before[k]), json.loads(now[k])
                self.assertEqual(only_keys_of(cur, was), was, k)
                continue
            self.assertEqual(now[k], before[k], k)

    def test_en_daily_report_reads_in_english(self):
        md = render_probe.renders("en")["daily_md"]
        for words in ("Active lines 1 · Done today 1", "**Decisions needed**", "**Awaiting owner**",
                      "--- Thread reply 1 ---", "**Release gaps**", "**Review gaps**", "app#7 merge (ap-1)"):
            self.assertIn(words, md)


class Catalogued(tf.Base):
    def lang(self, value):
        self.foundry("prefs", "set", "ui.language", value, "--by", "owner")

    def py(self, code):
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); " + code, tf.SCRIPTS],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_an_english_draft_is_refreshed_like_a_korean_one(self):
        self.lang("en")
        text = "\n".join(["**t**", "", "Awaiting review 0 · Decisions 2 · Awaiting owner 1", "",
                          "**Decisions needed**", "Decision · a · q (d-1)", "Decision · b · q (d-2)", "",
                          "**Awaiting owner**", "Check · c · x (u-3)"])
        out = json.loads(self.py("import report, json; print(json.dumps(report.refresh_owner(sys.argv[2], {'d-2'})))"
                                 .replace("sys.argv[2]", json.dumps(text))))
        self.assertIn("Decisions 1 · Awaiting owner 0", out[0])
        self.assertNotIn("**Awaiting owner**", out[0])
        self.assertEqual(sorted(out[1]), ["d-1", "u-3"])

    def test_sync_status_line_and_reconnect_text_follow_the_language(self):
        self.lang("en")
        self.assertEqual(self.py("import sync; print(sync.status_word('in_progress'), sync.status_word('odd'))"),
                         "in progress odd")
        self.assertTrue(self.py("import sync; print(sync.T('status_line', status='done', at='x'))").startswith("Status:"))
        self.lang("ko")
        self.assertEqual(self.py("import sync; print(sync.T('status_line', status=sync.status_word('done'), at='x'))"),
                         "상태: 완료 (x)")

    def test_prod_card_options_follow_the_language(self):
        self.lang("en")
        self.assertEqual(self.py("import i18n; print(i18n.t('release.prod_go'), '/', i18n.t('release.prod_hold'))"),
                         "deploy to prod / hold")
        with open(os.path.join(tf.SCRIPTS, "release.py"), encoding="utf-8") as f:
            self.assertNotIn("prod 배포", f.read())
