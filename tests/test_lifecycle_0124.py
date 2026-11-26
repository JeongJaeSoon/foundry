"""0.1.24: the owner-question list is printed only when it changed or the owner just wrote (`foundry decide digest`)."""

import unittest

import test_foundry as tf


class OwnerDigest(tf.Base):
    def digest(self, *extra):
        return self.foundry("decide", "digest", *extra)[1]

    def ask(self, key, unit="alpha"):
        self.foundry("decision", "ask", "--key", key, "--unit", unit, "--question", f"{key}?", ok=(0, 10))

    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")

    def test_the_same_list_is_printed_once_until_it_changes(self):
        self.ask("alpha:one")
        first = self.digest()
        self.assertTrue(first["changed"])
        self.assertEqual([(i["kind"], i["id"]) for i in first["items"]], [("ask", "alpha:one")])
        quiet = self.digest()  # an idle wake: one line, no items
        self.assertEqual((quiet["changed"], quiet["count"]), (False, 1))
        self.assertNotIn("items", quiet)
        self.ask("alpha:two")
        self.assertEqual(self.digest()["count"], 2)
        self.assertFalse(self.digest()["changed"])
        self.foundry("decide", "answer", "alpha:one", "yes", "--by", "owner")
        after = self.digest()  # an answered question leaves: that is news too
        self.assertEqual([i["id"] for i in after["items"]], ["alpha:two"])

    def test_an_owner_message_always_returns_the_list(self):
        self.ask("alpha:one")
        self.digest()
        again = self.digest("--owner-message")
        self.assertEqual((again["changed"], again["reason"], again["count"]), (True, "owner-message", 1))
        self.assertFalse(self.digest()["changed"])

    def test_waiting_owner_units_count_and_text_edits_do_not(self):
        self.digest()
        self.foundry("unit", "status", "alpha", "waiting_owner", "--note", "needs a call")
        d = self.digest()
        self.assertEqual([(i["kind"], i["id"]) for i in d["items"]], [("waiting_owner", "alpha")])
        self.foundry("unit", "checkpoint", "alpha", "--note", "still waiting")  # same ids: not news
        self.assertFalse(self.digest()["changed"])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(OwnerDigest(n) for n in sorted(vars(OwnerDigest)) if n.startswith("test_"))
    return suite
