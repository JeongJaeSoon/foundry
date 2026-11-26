"""0.1.114: a decision:<key> ref resolves when the owner answered that `decision ask` (a unit's or a top-level one),
not only a card. The live case: a unit blocked on another unit's push-email ask stayed blocked after the answer."""

import os
import sys

from test_graph import GraphBase

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import graph  # noqa: E402


class DecisionAskRefs(GraphBase):
    def setUp(self):
        super().setUp()
        self.new("a")
        self.new("b")

    def test_another_units_ask_unblocks_on_its_answer(self):
        self.foundry("decision", "ask", "--key", "a:push-email", "--unit", "a", "--question", "Push how?", ok=(10,))
        self.foundry("unit", "block", "b", "--on", "decision:a:push-email")
        self.assertEqual(self.unit("b")["blocked_by"], ["decision:a:push-email"])
        self.foundry("decision", "answer", "--key", "a:push-email", "--answer", "A: push as is")
        self.assertEqual(self.unit("b")["blocked_by"], [])
        self.assertIsNotNone(self.unit("b")["ready"])

    def test_a_top_level_ask_unblocks_on_its_answer(self):
        self.foundry("decision", "ask", "--key", "release-window", "--question", "Release today?", ok=(10,))
        self.foundry("unit", "block", "b", "--on", "decision:release-window")
        self.foundry("decision", "answer", "--key", "release-window", "--answer", "yes")
        self.assertEqual(self.unit("b")["blocked_by"], [])

    def test_an_open_ask_still_blocks(self):
        self.foundry("decision", "ask", "--key", "a:open", "--unit", "a", "--question", "Which?", ok=(10,))
        self.foundry("unit", "block", "b", "--on", "decision:a:open")
        snap = self.snap()
        self.assertFalse(graph.satisfied(snap, "decision:a:open", None))
        self.assertEqual(graph.resolve(snap), [])

    def test_a_withdrawn_ask_unblocks_like_a_withdrawn_card(self):
        self.foundry("decision", "ask", "--key", "a:who", "--unit", "a", "--question", "Who reviews?", ok=(10,))
        self.foundry("unit", "block", "b", "--on", "decision:a:who")
        self.foundry("decide", "withdraw", "a:who", "--by", "lead", "--reason", "PR merged")
        self.assertEqual(self.unit("b")["blocked_by"], [])
