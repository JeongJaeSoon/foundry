"""0.1.134: fewer process starts for endpoint security agents to inspect. The housekeeping scan reads the owner's
process cwds with one lsof per scan instead of one per worktree (a removal still reads lsof again right before it),
and the sessions stream runs that scan when a session changed state or every HK_SCAN_EVERY seconds, not every cycle."""

import os
import sys
import unittest

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import watchers  # noqa: E402


class OneLsofPerScan(tf.Housekeeping):
    def lsof_calls(self):
        log = os.path.join(self.dir, "lsof.log")
        if not os.path.exists(log):
            return 0
        with open(log) as f:
            return len(f.readlines())

    def test_a_scan_of_many_worktrees_runs_lsof_once(self):
        self.env["FAKE_LSOF_LOG"] = os.path.join(self.dir, "lsof.log")
        self.env["FAKE_LSOF_CWDS"] = os.path.join(self.dir, "merged-clean", "sub")
        cands = [c for c in self.script("housekeep.py", "scan") if c["cls"] == "worktree"]
        self.assertGreater(len(cands), 1)
        self.assertEqual(self.lsof_calls(), 1)
        by = {c["branch"]: c for c in cands}
        self.assertEqual(by["merged-clean"]["note"], "in use by claude pid 1000")  # the shared table still names it

    def test_a_removal_reads_lsof_again(self):
        self.env["FAKE_LSOF_LOG"] = os.path.join(self.dir, "lsof.log")
        target = os.path.join(self.dir, "merged-clean")
        self.foundry("sweep", "remove-worktree", target, "--by", "owner", "--reason", "x")
        self.assertEqual(self.lsof_calls(), 1)

    def test_a_failed_lsof_in_a_scan_fails_closed(self):
        self.env["FAKE_LSOF_FAIL"] = "1"
        cands = [c for c in self.script("housekeep.py", "scan") if c["cls"] == "worktree"]
        self.assertTrue(cands)
        self.assertTrue(all(not c["safe"] for c in cands), cands)


class ScanCadence(unittest.TestCase):
    def setUp(self):
        self.saved = watchers._LAST_SCAN[0]

    def tearDown(self):
        watchers._LAST_SCAN[0] = self.saved

    def test_first_cycle_scans_then_waits_unless_a_session_changed(self):
        watchers._LAST_SCAN[0] = float("-inf")
        self.assertTrue(watchers.scan_due(100.0, []))
        watchers._LAST_SCAN[0] = 100.0
        self.assertFalse(watchers.scan_due(100.0 + watchers.HK_SCAN_EVERY - 1, []))
        self.assertTrue(watchers.scan_due(101.0, [{"name": "lead-x"}]))
        self.assertTrue(watchers.scan_due(100.0 + watchers.HK_SCAN_EVERY, []))
