"""decide.py: cards with options, dedup by key, answers by button and chat, deadline defaults, unit unblocking."""

import json

import test_graph


class Cards(test_graph.GraphBase):
    def setUp(self):
        super().setUp()
        self.new("a")

    def open(self, key="k1", *extra, unit="a"):
        args = ["decide", "open", "--question", "Which DB?", "--context", "migration", "--option", "A:Postgres",
                "--option", "B:SQLite", "--recommend", "B", "--reason", "no ops", "--asked-by", "lead",
                "--key", key, *extra]
        if unit:
            args += ["--unit", unit]
        _, out = self.foundry(*args)
        return out

    def test_open_blocks_unit_and_button_answer_readies_it(self):
        out = self.open()
        self.assertEqual(out["status"], "opened")
        cid = out["card"]["id"]
        self.assertEqual(cid, "d-0001")
        self.assertEqual(self.unit("a")["blocked_by"], [f"decision:{cid}"])
        _, out = self.foundry("decide", "answer", cid, "B", "--by", "owner")
        self.assertEqual(out["answer"], "B")
        snap = self.snap()
        self.assertEqual(snap["cards"][cid]["answered_by"], "owner")
        self.assertEqual(snap["units"]["a"]["blocked_by"], [])
        kinds = [e["kind"] for e in snap["events"]]
        self.assertIn("decision.answered", kinds)
        self.assertIn("unit.ready", kinds)
        self.assertIn("decide.answer", [r["op"] for r in self.journal()])

    def test_chat_answer_is_case_insensitive(self):
        cid = self.open()["card"]["id"]
        _, out = self.foundry("decide", "chat", f"{cid} a")
        self.assertEqual(out["answer"], "A")
        self.assertEqual(self.unit("a")["blocked_by"], [])
        self.assertEqual(self.run_f("decide", "chat", "pick A please")[0], 2)

    def test_dedup_returns_stored_card_and_answer(self):
        first = self.open()
        again = self.open()
        self.assertEqual(again["status"], "pending")
        self.assertEqual(again["card"]["id"], first["card"]["id"])
        self.assertEqual(len(self.snap()["cards"]), 1)
        self.foundry("decide", "answer", first["card"]["id"], "A")
        third = self.open()
        self.assertEqual((third["status"], third["card"]["answer"]), ("answered", "A"))
        self.assertEqual(self.open("k2", unit=None)["card"]["id"], "d-0002")

    def test_bad_answers_and_callers_refused(self):
        cid = self.open()["card"]["id"]
        self.assertEqual(self.run_f("decide", "answer", cid, "Z")[0], 2)
        self.assertEqual(self.run_f("decide", "answer", "d-9999", "A")[0], 2)
        lead = {"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "a"}
        self.assertEqual(self.run_f("decide", "answer", cid, "A", env=lead)[0], 2)
        self.assertEqual(self.run_f("decide", "answer", cid, "A", "--by", "foreman", "--quote", "A로 가자",
                                    env={"FOUNDRY_ROLE": "foreman"})[0], 0)
        self.assertEqual(self.run_f("decide", "answer", cid, "B")[0], 2)  # already answered differently
        self.assertEqual(self.run_f("decide", "answer", cid, "A")[0], 0)

    def test_deadline_default_auto_answers(self):
        cid = self.open("k1", "--deadline", "2026-11-18T12:00:00+09:00", "--default", "A")["card"]["id"]
        self.foundry("graph", "resolve")
        self.assertIsNone(self.snap()["cards"][cid]["answer"])
        self.set_now("2026-11-18T12:00:00+09:00")
        self.foundry("graph", "resolve")
        snap = self.snap()
        self.assertEqual((snap["cards"][cid]["answer"], snap["cards"][cid]["answered_by"]), ("A", "deadline"))
        self.assertEqual(snap["units"]["a"]["blocked_by"], [])

    def test_malformed_cards_refused(self):
        base = ["decide", "open", "--question", "Q", "--asked-by", "lead", "--key", "x"]
        self.assertEqual(self.run_f(*base, "--option", "A:a")[0], 2)
        self.assertEqual(self.run_f(*base, "--option", "A:a", "--option", "B:b", "--recommend", "C")[0], 2)
        self.assertEqual(self.run_f(*base, "--option", "A:a", "--option", "B:b", "--deadline", "soon")[0], 2)
        self.assertEqual(self.run_f(*base, "--option", "A:a", "--option", "B:b", "--unit", "nope")[0], 2)
        self.assertEqual(self.snap()["cards"], {})

    def test_board_cards_with_option_details(self):
        extra = json.dumps({"A": {"pros": ["fast"], "cons": ["ops"], "preview": "docker run pg"},
                            "B": {"description": "simpler"}})  # descriptions come only through --option-json
        self.open("k1", "--option-json", extra)
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["cards_open"], 1)
        card = m["cards"][0]
        self.assertEqual(card["recommended"], "B")
        self.assertTrue(card["opened"])  # the board's 경과 column
        opt = {o["key"]: o for o in card["options"]}
        self.assertEqual(opt["A"]["pros"], ["fast"])
        self.assertEqual(opt["B"]["description"], "simpler")
        self.foundry("decide", "answer", card["id"], "B")
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["cards_open"], 0)
