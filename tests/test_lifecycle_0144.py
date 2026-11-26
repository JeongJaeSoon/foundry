"""0.1.44: owner asks the foreman makes are recorded (`foundry owner todo`), and 오너 대기 counts every one of them."""

import os
import sys
import unittest

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import board  # noqa: E402

AGENTS = os.path.join(os.path.dirname(tf.SCRIPTS), "agents")


def model(cards=(), asks=(), todos=(), waiting=()):
    return {"sections": [{"key": "waiting_owner", "rows": [{"id": u} for u in waiting]},
                         {"key": "in_progress", "rows": []}],
            "cards": [{"id": f"d-{u}", "unit": u} for u in cards],
            "asks": [{"key": f"{u}:q", "unit": u} for u in asks],
            "owner_todos": [{"tid": f"ot-{i}", "unit": u} for i, u in enumerate(todos)],
            "approvals": [], "halts": [], "foreman_down": False}


class Count(unittest.TestCase):
    def test_count_is_the_union(self):
        m = model(cards=["a"], asks=["b"], todos=["c", None, "c"], waiting=["d", "e"])
        sm = board.summary(m)
        self.assertEqual(sm["owner_wait"], 3 + 2)  # 0.1.88: the card and the ask are 결정 필요's
        self.assertEqual(sm["decisions"], 2)
        self.assertTrue(sm["attention"])

    def test_waiting_owner_unit_with_a_card_or_todo_counts_once(self):
        self.assertEqual(board.summary(model(cards=["a"], waiting=["a"]))["owner_wait"], 0)  # in 결정 필요 only
        self.assertEqual(board.summary(model(todos=["a"], waiting=["a"]))["owner_wait"], 1)
        self.assertEqual(board.summary(model(asks=["a"], waiting=["a", "b"]))["owner_wait"], 1)

    def test_band_text_shows_owner_wait(self):
        m = model(todos=[None], waiting=["a"])
        m["pressure"] = {"level": "ok"}
        m["sections"][0]["label"] = "오너 확인"
        m["sections"][1]["label"] = "진행 중"
        self.assertIn("오너 대기 2", board.band(m))


class TodoCli(tf.Base):
    def test_add_done_round_trip_counts_on_the_board(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "U1")
        _, r = self.foundry("owner", "todo", "add", "--unit", "u1", "--text", "sops secret 등록", "--kind", "secret", "--why-owner", "secret 값은 오너만 안다",
                            "--by", "foreman")
        tid = r["tid"]
        _, again = self.foundry("owner", "todo", "add", "--unit", "u1", "--text", "sops secret 등록", "--by", "foreman",
                                "--why-owner", "secret 값은 오너만 안다")
        self.assertEqual(again, {"tid": tid, "existing": True})
        self.foundry("owner", "todo", "add", "--none", "--text", "정리 제안 검토", "--by", "foreman",
                     "--why-owner", "무엇을 지울지는 오너의 선택")
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["summary"]["owner_wait"], 2)
        td = self.snap()["owner_todos"][tid]
        self.assertEqual((td["unit"], td["kind"], td["by"]), ("u1", "secret", "foreman"))
        self.foundry("owner", "todo", "done", tid, "--by", "foreman", "--quote", "등록했어")
        td = self.snap()["owner_todos"][tid]
        self.assertTrue(td["closed"])
        self.assertEqual((td["closed_by"], td["closed_quote"]), ("foreman", "등록했어"))
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["summary"]["owner_wait"], 1)
        _, rows = self.foundry("owner", "todo", "list")
        self.assertEqual([x["text"] for x in rows], ["정리 제안 검토"])

    def test_refusals(self):
        self.foundry("owner", "todo", "add", "--unit", "nope", "--text", "x", "--by", "foreman",
                     "--why-owner", "오너의 선택", ok=(1,))
        self.foundry("owner", "todo", "done", "ot-missing", "--by", "foreman", ok=(1,))
        self.foundry("owner", "todo", "add", "--unit", "u1", "--none", "--text", "x", ok=(2,))
        self.foundry("owner", "todo", "add", "--none", "--text", "  ", "--by", "foreman", ok=(2,))


class Text(unittest.TestCase):
    def test_foreman_records_every_owner_item(self):
        with open(os.path.join(AGENTS, "foreman.md")) as f:
            s = f.read()
        self.assertIn("foundry owner todo add --unit <id>", s)
        self.assertIn("rendered from `foundry board --format json`", s)
        self.assertIn("never written as prose", s)
        self.assertIn("is not an owner item", s)


if __name__ == "__main__":
    unittest.main()
