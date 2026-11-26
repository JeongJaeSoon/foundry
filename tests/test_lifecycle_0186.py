"""0.1.86: review asks are the owner's standing delegation: the foreman never asks whether to send one, and a held
review-ask batch that turns due later wakes the foreman through the sync stream (once), instead of waiting for the
next `outbox add` event."""

import os
import unittest

import test_foundry as tf

ROOT = os.path.dirname(tf.SCRIPTS)


class HeldBatchWakes(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "docs", "--title", "Docs")

    def sync_lines(self, when):
        self.env["FOUNDRY_NOW"] = when
        return self.foundry("watch", "stream", "--kind", "sync", "--once")[1] or ""

    def test_a_held_batch_turning_due_is_announced_once(self):
        self.env["FOUNDRY_NOW"] = "2026-11-18T10:00:00+09:00"
        self.foundry("outbox", "add", "docs", "--channel", "C0000000001", "--kind", "review-ask", "--to",
                     "U0000000002", "--pr", "example-org/gateway#1", "--text", "example-org/gateway#1 のレビューをお願いします")
        self.assertNotIn("outbox review-ask due", self.sync_lines("2026-11-18T10:10:00+09:00"))  # hold
        out = self.sync_lines("2026-11-18T12:01:00+09:00")  # past review.batch_wait_min (120)
        self.assertIn("outbox review-ask due: 1 for U0000000002 (now)", out)
        self.assertNotIn("outbox review-ask due", self.sync_lines("2026-11-18T12:06:00+09:00"))


class Text(unittest.TestCase):
    def test_no_send_approval_for_review_asks(self):
        with open(os.path.join(ROOT, "agents", "foreman.md")) as f:
            t = f.read()
        self.assertIn("never ask the owner whether to send one", t)
        self.assertIn("outbox review-ask due", t)
        with open(os.path.join(ROOT, "agents", "dispatcher.md")) as f:
            self.assertIn("send-now for that batch", f.read())
