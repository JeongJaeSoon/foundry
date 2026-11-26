"""0.1.88: one item, one board section. A decision is 결정 필요's alone (not 오너 대기, not 에스컬레이션); 오너 대기
is the owner's own to-dos and checks; 에스컬레이션 is only what no card, approval or to-do shows, and a report (a
note with nothing for the owner to do) is not on it. The counts follow, and toasts still see everything new."""

import test_foundry as tf


class OneSection(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "u1", "--title", "Group role column (#1637)")
        self.foundry("unit", "status", "u1", "waiting_owner", "--note", "PR-E announce?")
        self.foundry("decide", "open", "--id", "d-0901", "--unit", "u1", "--key", "u1:pr-e-announce",
                     "--option", "a:post", "--option", "b:hold", "--question", "PR-E を告知しますか?",
                     "--asked-by", "line-lead")
        self.foundry("unit", "report", "u1", "--text", "#1669 review is handled, with no code change")

    def model(self):
        return self.foundry("board", "--format", "json")[1]

    def test_a_decision_shows_once_in_decisions(self):
        m = self.model()
        self.assertEqual([c["id"] for c in m["cards"]], ["d-0901"])
        [own] = [s for s in m["sections"] if s["key"] == "waiting_owner"]
        self.assertEqual(own["items"], [])  # its unit waits on that card: shown there, not here
        self.assertFalse(any(x["kind"] == "decision" for x in m["escalations"]))
        self.assertEqual((m["summary"]["decisions"], m["summary"]["owner_wait"]), (1, 0))

    def test_a_report_is_no_escalation_but_still_a_toast(self):
        m = self.model()
        self.assertFalse(any(x["kind"] == "report" for x in m["escalations"]))
        kinds = {x["kind"] for x in m["toasts"]}
        self.assertTrue({"report"} <= kinds, kinds)

    def test_owner_todo_is_owner_wait_with_its_own_text(self):
        self.foundry("unit", "new", "--id", "u2", "--title", "Other unit title")
        self.foundry("unit", "status", "u2", "waiting_owner", "--note", "check the dashboard")
        m = self.model()
        [own] = [s for s in m["sections"] if s["key"] == "waiting_owner"]
        self.assertEqual([x["unit"] for x in own["items"]], ["u2"])
        self.assertNotIn("PR-E", " ".join(x["text"] + x["title"] for x in own["items"]))
        self.assertEqual(m["summary"]["owner_wait"], 1)

    def test_text_board_lists_the_decision_once(self):
        _, out = self.foundry("board", "--format", "text")
        text = out if isinstance(out, str) else ""
        self.assertEqual(text.count("d-0901"), 1, text)
