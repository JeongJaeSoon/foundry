"""0.1.50: `foundry decide show` prints the card as sections drawn through views.outline (raw JSON only with
`--format json`), and `--option KEY:LABEL` ends the key at the first colon, so a label with a time keeps it."""

import os
import subprocess
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import decide  # noqa: E402

QUESTION = "리뷰 리마인드 전송 시점\n- 확인됨\n  - #1655 는 19:47 에 의뢰\n- 막는 것: 리마인드 묶음"


class OptionLabels(tf.Base):
    def test_a_time_in_the_label_stays_in_the_label(self):
        self.assertEqual([(o["key"], o["label"], o["description"]) for o in decide.parse_options(
            ["A:09:00 에 전송", "B: 11-20 19:47 이후 묶음 전송 ", "go"], None)],
            [("A", "09:00 에 전송", None), ("B", "11-20 19:47 이후 묶음 전송", None), ("go", "go", None)])

    def test_the_real_cli_stores_the_whole_label(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "U1")
        _, out = self.foundry("decide", "open", "--question", "언제 보낼까요?", "--option", "A:09:00 에 전송",
                              "--option", "B:09:00 예약으로 보냄", "--asked-by", "lead", "--key", "u1:when", "--unit", "u1")
        self.assertEqual([o["label"] for o in out["card"]["options"]], ["09:00 에 전송", "09:00 예약으로 보냄"])


class ShowText(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "u1", "--title", "U1")
        qf = os.path.join(self.dir, "card.md")
        with open(qf, "w", encoding="utf-8") as f:
            f.write(QUESTION + "\n")
        _, out = self.foundry("decide", "open", "--question-file", qf, "--option", "A:09:00 에 전송",
                              "--option", "B:내일 저녁", "--recommend", "A", "--reason", "아침에 바로 리마인드",
                              "--asked-by", "lead", "--key", "u1:remind", "--unit", "u1")
        self.cid = out["card"]["id"]

    def show(self, *extra):
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "decide", "show", self.cid, *extra],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_the_default_is_sectioned_text_not_json(self):
        out = self.show()
        self.assertFalse(out.lstrip().startswith("{"), out)
        lines = out.splitlines()
        self.assertEqual(lines[0], f"{self.cid} · u1 · 열림")
        for want in ("질문", "  - 리뷰 리마인드 전송 시점", "  - 확인됨", "    - #1655 는 19:47 에 의뢰", "  - 막는 것: 리마인드 묶음",
                     "옵션", "  - A: 09:00 에 전송 (추천)", "  - B: 내일 저녁", "추천: A — 아침에 바로 리마인드"):
            self.assertIn(want, lines)

    def test_json_only_on_request(self):
        import json
        card = json.loads(self.show("--format", "json"))
        self.assertEqual(card["id"], self.cid)
        self.assertEqual(card["options"][0]["label"], "09:00 에 전송")

    def test_an_answered_card_says_who_answered(self):
        self.foundry("decide", "answer", self.cid, "A", "--by", "owner")
        self.assertTrue(self.show().splitlines()[0].startswith(f"{self.cid} · u1 · 답: A (owner, "))


if __name__ == "__main__":
    import unittest
    unittest.main()
