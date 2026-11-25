"""0.1.23: sync status, lag and board read the drain's own split (due vs in flight); a re-drained batch is
superseded; `foundry sync reconcile` supersedes open batches nothing points at and lists batches never closed."""

import unittest

import test_sync as ts


class SyncStatusMatchesDrain(ts.SyncBase):
    def jira_items(self):
        return self.snap()["units"]["u1"]["sync"]["jira"]["pending"]

    def status(self):
        return self.foundry("sync", "status")[1]

    def jira_batch(self):
        return next(i["batch"] for i in self.drain() if i["target"] == "jira")

    def setUp(self):
        super().setUp()
        self.unit("u1")
        self.at("10:00")
        self.rec("sync.needed", unit="u1", target="jira", key="k1", event="manual", text="first")
        self.rec("sync.needed", unit="u1", target="jira", key="k2", event="manual", text="second")

    def test_status_counts_only_what_the_drain_hands_out_and_done_clears_both(self):
        self.at("10:20")
        self.assertEqual(self.status()["units"]["u1"]["pending"]["jira"], 2)
        batch = self.jira_batch()
        st = self.status()["units"]["u1"]
        self.assertNotIn("jira", st["pending"])  # handed out: in flight, not late
        self.assertEqual(st["inflight"]["jira"], [batch])
        self.at("10:45")  # in flight past lag_warn_min but inside the retry window: not on the board as late
        _, m = self.foundry("board", "--format", "json")
        self.assertNotIn("u1", m["sync_lagging"])
        self.foundry("sync", "done", batch)
        st = self.status()["units"].get("u1") or {"pending": {}, "inflight": {}}
        self.assertEqual((st["pending"].get("jira"), st["inflight"].get("jira")), (None, None))
        self.assertEqual(self.drain(), [i for i in self.drain() if i["target"] != "jira"])  # drain agrees

    def test_a_batch_never_closed_comes_back_due_and_its_redrain_supersedes_it(self):
        self.at("10:05")
        old = self.jira_batch()
        self.at("10:50")  # past inflight_retry_min (30): status and drain both show the items again
        self.assertEqual(self.status()["units"]["u1"]["pending"]["jira"], 2)
        self.assertEqual(self.status()["units"]["u1"]["lag_min"], 50)
        new = self.jira_batch()
        batches = self.snap()["sync_batches"]
        self.assertEqual((batches[old]["state"], batches[new]["state"]), ("superseded", "open"))
        self.foundry("sync", "done", new)
        self.assertEqual(self.jira_items(), [])
        self.foundry("sync", "done", old)  # a late close of the superseded batch is accepted and changes nothing
        self.assertEqual(self.jira_items(), [])

    def test_reconcile_supersedes_batches_nothing_points_at_and_lists_unclosed_ones(self):
        # Pre-0.1.23 journals: a batch re-drained without being superseded stays open with no item behind it.
        self.rec("sync.drain", batches=[{"batch": "sb-ghost", "unit": "u1", "target": "jira", "keys": ["gone"]}])
        ghost = "sb-ghost"
        self.at("10:05")
        live = self.jira_batch()
        self.at("10:20")
        _, out = self.foundry("sync", "reconcile")
        self.assertEqual([d["batch"] for d in out["dropped"]], [ghost])
        self.assertEqual([u["batch"] for u in out["unclosed"]], [live])
        self.assertEqual(out["unclosed"][0]["age_min"], 15)
        self.assertEqual(self.snap()["sync_batches"][ghost]["state"], "superseded")
        self.assertEqual(len(self.jira_items()), 2)  # never-confirmed items are kept
        _, again = self.foundry("sync", "reconcile")
        self.assertEqual(again["dropped"], [])  # one-shot: nothing left


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(SyncStatusMatchesDrain(n) for n in sorted(vars(SyncStatusMatchesDrain)) if n.startswith("test_"))
    return suite
