"""0.1.39: a PR that turns DIRTY (or BEHIND) wakes its lead once per head to update, resolve, test, push and
re-request review; the merge gate says so instead of "wait"."""

import json
import os
import sys
import unittest

import test_foundry as tf
import test_gitpolicy as tg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = "example-org/app#47"


class ConflictWake(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.foundry("unit", "keys", "alpha", "--pr", REF)

    def gh(self, merge_state, head="aaaaaaa1111"):
        with open(os.path.join(self.dir, "gh.json"), "w") as f:
            json.dump({REF: {"state": "OPEN", "reviewDecision": "APPROVED", "mergeStateStatus": merge_state,
                             "headRefOid": head, "headRefName": "foundry/alpha", "title": "t",
                             "latestReviews": [{"author": {"login": "rev1"}, "state": "APPROVED"}],
                             "reviewRequests": [], "statusCheckRollup": []}}, f)

    def stream(self):
        _, out = self.foundry("watch", "stream", "--kind", "github", "--once")
        return out or ""

    def conflict_items(self):
        return [i for i in self.snap()["units"]["alpha"]["inbox"] if i["iid"].startswith("cfl-")]

    def test_dirty_wakes_the_lead_once_per_head_and_clean_retires_it(self):
        self.gh("DIRTY")
        self.assertIn("merge state DIRTY", self.stream())
        items = self.conflict_items()
        self.assertEqual(len(items), 1)
        for s in ("update the branch against the base", "run the tests", "re-request review", "never as shell"):
            self.assertIn(s, items[0]["text"])
        self.assertTrue(any(e.get("kind") == "pr-conflict" for e in self.snap()["events"]))
        sys.path.insert(0, tf.SCRIPTS)
        import launcher
        self.assertEqual(launcher.approval_wake(self.snap()["units"]["alpha"]), "PR conflicts with its base")
        # Same head: no second note.
        self.assertNotIn("merge state DIRTY", self.stream())
        self.assertEqual(len(self.conflict_items()), 1)
        # Clean again: the unread note is retired.
        self.gh("CLEAN")
        self.stream()
        self.assertTrue(self.conflict_items()[0].get("superseded"))
        # A new head that conflicts again fires again.
        self.gh("DIRTY", head="bbbbbbb2222")
        self.assertIn("merge state DIRTY", self.stream())
        self.assertEqual(len(self.conflict_items()), 2)

    def test_behind_names_update_branch(self):
        self.gh("BEHIND")
        self.stream()
        self.assertIn("gh pr update-branch 47 -R example-org/app", self.conflict_items()[0]["text"])


class GateText(tg.GitPolicy):
    def test_dirty_says_update_not_wait(self):
        self.gh_pr("example-org/webapp#12", mergeStateStatus="DIRTY")
        why = self.reason(self.pre("gh pr merge 12 -R example-org/webapp --squash --match-head-commit abc"))
        self.assertIn("update the branch against the base", why)
        self.assertIn("re-request review", why)
        self.assertNotIn("wait for CI", why)


class Text(unittest.TestCase):
    def test_lead_text(self):
        with open(os.path.join(ROOT, "agents/line-lead.md")) as f:
            t = f.read()
        for s in ("DIRTY (conflicts) or BEHIND is never a wait", "re-request review from the previous approver(s)",
                  "keeps the batching and quiet hours", "never as shell homework for the owner"):
            self.assertIn(s, t)


if __name__ == "__main__":
    unittest.main()
