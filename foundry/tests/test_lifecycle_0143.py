"""0.1.43: a queued approval request that can never be valid again leaves the board and is never applied late."""

import os
import sys
import unittest
import unittest.mock

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import approve  # noqa: E402
import board  # noqa: E402
import foundrylib as cl  # noqa: E402
import prefs  # noqa: E402

NOW = "2026-11-19T18:00:00+09:00"
OLD = "4f1d2c3b5a6e7f8091a2b3c4d5e6f708192a3b4c"
NEW = "e80f7c1c38d864232162cb7dba0508f605f557ee"


def req(rid, asked, summary, **f):
    return {"rid": rid, "unit": "u1", "session": "s1", "tool": "Bash", "tier": 3, "answer": None,
            "asked": asked, "gave_up": asked, "summary": summary, **f}


def merge_cmd(sha):
    return f"Bash: gh pr merge 1647 -R example-org/app --squash --match-head-commit {sha}"


def snap(approvals, head=OLD, state="OPEN"):
    return {"units": {"u1": {"id": "u1", "status": "waiting_owner",
                             "waits": [{"wid": f"approval:{r}"} for r in approvals],
                             "prs": [{"repo": "example-org/app", "number": 1647, "head": head, "state": state,
                                      "merged": None}]}},
            "approvals": approvals}


def open_rids(s, now=NOW):
    with unittest.mock.patch.dict(os.environ, FOUNDRY_NOW=now):
        return [a["rid"] for a in board.open_approvals(s)]


class Stale(unittest.TestCase):
    def test_fresh_queued_request_still_shows(self):
        s = snap({"ap-1": req("ap-1", "2026-11-19T16:00:00+09:00", merge_cmd(OLD))})
        self.assertEqual(open_rids(s), ["ap-1"])
        self.assertEqual(approve.stale_records(s, {"approvals": {"queued_ttl_hours": 6}}, cl.parse_ts(NOW)), [])

    def test_a_newer_request_of_the_same_family_supersedes(self):
        s = snap({"ap-1": req("ap-1", "2026-11-19T16:00:00+09:00", merge_cmd(OLD)),
                  "ap-2": req("ap-2", "2026-11-19T17:00:00+09:00", merge_cmd(NEW))}, head=None)
        self.assertEqual(open_rids(s), ["ap-2"])
        why = approve.stale_reason(s["approvals"]["ap-1"], s, 6, cl.parse_ts(NOW))
        self.assertEqual(why, "superseded by ap-2")

    def test_another_session_or_command_does_not_supersede(self):
        s = snap({"ap-1": req("ap-1", "2026-11-19T16:00:00+09:00", merge_cmd(OLD)),
                  "ap-2": req("ap-2", "2026-11-19T17:00:00+09:00", merge_cmd(NEW), session="s2"),
                  "ap-3": req("ap-3", "2026-11-19T17:30:00+09:00", "Bash: make deploy-docs")}, head=None)
        self.assertEqual(sorted(open_rids(s)), ["ap-1", "ap-2", "ap-3"])

    def test_merge_pinned_to_an_old_head_is_stale(self):
        s = snap({"ap-1": req("ap-1", "2026-11-19T16:00:00+09:00", merge_cmd(OLD[:7]))}, head=NEW)
        self.assertEqual(open_rids(s), [])
        why = approve.stale_reason(s["approvals"]["ap-1"], s, 6, cl.parse_ts(NOW))
        self.assertIn("head is now e80f7c1c38d8", why)

    def test_merge_of_a_merged_or_closed_pr_is_stale(self):
        for st in ("MERGED", "CLOSED"):
            s = snap({"ap-1": req("ap-1", "2026-11-19T16:00:00+09:00", merge_cmd(OLD))}, state=st)
            self.assertEqual(open_rids(s), [], st)

    def test_older_than_the_ttl_is_stale_and_ttl_is_a_pref(self):
        s = snap({"ap-1": req("ap-1", "2026-11-19T11:00:00+09:00", "Bash: make deploy-docs")})
        self.assertEqual(open_rids(s), [])
        self.assertEqual(prefs.defaults()["approvals"]["queued_ttl_hours"], 6)
        recs = approve.stale_records(s, {"approvals": {"queued_ttl_hours": 8}}, cl.parse_ts(NOW))
        self.assertEqual(recs, [])
        self.assertIn("approvals.queued_ttl_hours: must be a number >= 0",
                      prefs.validate({"approvals": {"queued_ttl_hours": "x"}}))

    def test_the_drop_is_one_journal_mark_with_its_reason_and_the_wait_clears(self):
        s = snap({"ap-1": req("ap-1", "2026-11-19T16:00:00+09:00", merge_cmd(OLD))}, head=NEW)
        recs = approve.stale_records(s, {}, cl.parse_ts(NOW))
        self.assertEqual(recs[0][0], "approval.mark")
        self.assertEqual(recs[0][1]["field"], "stale")
        self.assertIn("head is now", recs[0][1]["reason"])
        self.assertEqual(recs[1], ("wait.clear", {"id": "u1", "wid": "approval:ap-1"}))
        journal = [{"ts": "2026-11-19T16:00:00+09:00", "op": "approval.request", "rid": "ap-1", "unit": "u1",
                    "session": "s1", "tool": "Bash", "summary": merge_cmd(OLD), "tier": 3},
                   {"ts": "2026-11-19T16:00:01+09:00", "op": "approval.mark", "rid": "ap-1", "field": "gave_up"},
                   {"ts": "2026-11-19T18:00:00+09:00", "op": "approval.mark", "rid": "ap-1", "field": "stale",
                    "reason": recs[0][1]["reason"]}]
        a = cl.replay(journal)["approvals"]["ap-1"]
        self.assertEqual(a["stale"], "2026-11-19T18:00:00+09:00")
        self.assertIn("head is now", a["stale_reason"])
        self.assertEqual(approve.stale_records({"units": {}, "approvals": {"ap-1": a}}, {}, cl.parse_ts(NOW)), [])

    def test_a_stale_answered_request_is_not_applied_late(self):
        a = req("ap-1", "2026-11-19T16:00:00+09:00", merge_cmd(OLD), answer="allow")
        s = snap({"ap-1": a}, head=NEW)
        self.assertTrue(approve.stale_reason(a, s, 6, cl.parse_ts(NOW)))
        self.assertIsNone(approve.stale_reason(dict(a, gave_up=None), s, 6, cl.parse_ts(NOW)))


import test_approve  # noqa: E402


class LateHook(tf.Base):
    """Through the PermissionRequest hook: a stale late answer no longer allows the command."""
    def setUp(self):
        super().setUp()
        self.wt = os.path.join(self.dir, "wt-alpha")
        os.makedirs(self.wt)
        self.env["FOUNDRY_NOTIFY_CMD"] = f"{sys.executable} -c pass"
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.record("unit.worktree", id="alpha", repo="r", path=self.wt, branch="b")
        self.start_lead("alpha")

    config = test_approve.Approver.config
    perm = test_approve.Approver.perm
    decision = staticmethod(test_approve.Approver.decision)

    def test_stale_late_answer_is_not_applied(self):
        self.config(approval_wait_seconds=1)
        cmd = {"command": "make deploy-docs"}
        rid = self.decision(self.perm("Bash", cmd))["message"].split("queued as ")[1].split(".")[0]
        self.foundry("approve", "allow", rid)
        self.env["FOUNDRY_NOW"] = "2099-01-01T00:00:00+09:00"  # far past approvals.queued_ttl_hours
        self.assertEqual(self.decision(self.perm("Bash", cmd))["behavior"], "deny")
        _, a = self.foundry("approve", "show", rid)
        self.assertFalse(a.get("used"))


if __name__ == "__main__":
    unittest.main()
