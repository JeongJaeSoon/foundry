"""0.1.106: the approval card's data. A tier-3 request records why it is the owner's (the owner-only rule's class,
or the foreman's own session) and how long the hook waits before denying and queuing; `foundry approve note` attaches
evidence and the advisor's one line to an open request; the board model carries them, and `auto_deny` until the
request is queued."""

import json
import os
import subprocess
import sys

import test_approve as ta

ORCH = ta.ORCH


class Card(ta.Approver):
    def model_approval(self, rid):
        r = subprocess.run([sys.executable, os.path.join(ta.SCRIPTS, "board.py"), "json"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return next(a for a in json.loads(r.stdout)["approvals"] if a["rid"] == rid)

    def test_a_queued_lead_request_says_why_and_has_no_deadline(self):
        self.perm("Bash", {"command": "kubectl apply -f x.yaml --context prod"})
        a = self.open_rid()
        self.assertEqual((a["tier"], a["why"], a["wait_s"]), (3, "production", None))
        m = self.model_approval(a["rid"])
        self.assertEqual((m["why"], m.get("auto_deny"), m["queued"]), ("production", None, True))

    def test_a_waiting_foreman_request_has_its_auto_deny_time(self):
        self.config(approval_wait_seconds=6)
        p = self.start_perm("Bash", {"command": "terraform apply"}, who=ORCH)
        a = self.open_rid()
        self.assertEqual((a["tier"], a["wait_s"]), (3, 1))
        self.assertIn(a["why"], ("foreman session", "production"))
        self.assertTrue(self.model_approval(a["rid"]).get("auto_deny"))
        self.finish(p)

    def test_note_attaches_evidence_and_advisor(self):
        self.perm("Bash", {"command": "kubectl apply -f x.yaml --context prod"})
        rid = self.open_rid()["rid"]
        _, out = self.foundry("approve", "note", rid, "--evidence", "PR #12 merged; dev 09:40", "--advisor", "D1-D3 pass")
        self.assertEqual(out["noted"], rid)
        m = self.model_approval(rid)
        self.assertEqual((m["evidence"], m["advisor"]), ("PR #12 merged; dev 09:40", "D1-D3 pass"))
        self.assertIsNone(self.snap()["approvals"][rid]["answer"])  # a note never answers
        self.foundry("approve", "note", rid, ok=(2,))
        self.foundry("approve", "note", "ap-none", "--evidence", "x", ok=(2,))


class StallBand(ta.Base):
    def test_the_text_band_counts_stalled_leads_and_the_row_says_what_recovery_did(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "U1")
        self.record("unit.status", id="u1", status="in_progress")
        self.record("unit.stalled", unit="u1", kind="api_loop", detail="4 requests, 0 results", count=4)
        self.record("stall.recover", unit="u1", action="relaunch")
        r = subprocess.run([sys.executable, os.path.join(ta.SCRIPTS, "board.py"), "json"], env=self.env,
                           capture_output=True, text=True)
        row = next(x for sec in json.loads(r.stdout)["sections"] for x in sec["rows"] if x["id"] == "u1")
        self.assertEqual((row["stall"]["action"], row["stall"]["escalated"]), ("relaunch", False))
        text = subprocess.run([sys.executable, os.path.join(ta.SCRIPTS, "foundry.py"), "board"], env=self.env,
                              capture_output=True, text=True).stdout
        self.assertTrue(any("막힘 1" in x or "stalled 1" in x for x in text.splitlines()), text)


def load_tests(loader, tests, pattern):
    """Only this file's own tests: Approver's run once, in test_approve."""
    import unittest
    suite = unittest.TestSuite(Card(n) for n in sorted(vars(Card)) if n.startswith("test_"))
    suite.addTests(loader.loadTestsFromTestCase(StallBand))
    return suite
