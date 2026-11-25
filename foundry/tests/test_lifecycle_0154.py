"""0.1.54: paths that never ran for real get a way to run. `foundry intake scan` proposes new GitHub review requests
and assigned issues as blocked plan units (the first scan only seeds); `foundry report kaizen` lands on the board;
a sync batch that carried the Jira in-review transition closes only with the ticket status (or the error), and the
Korean Jira status names count as in review."""

import json
import os
import subprocess
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import sync  # noqa: E402


def row(repo, num, title):
    return {"repository": {"nameWithOwner": repo}, "number": num, "title": title,
            "url": f"https://github.com/{repo}/pull/{num}", "updatedAt": "2026-11-18T09:00:00Z"}


REVIEWS = "search prs --review-requested @me --state open"
ISSUES = "search issues --assignee @me --state open"


class IntakeScan(tf.Base):
    def board(self):
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), "json"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_the_first_scan_seeds_then_new_work_becomes_a_blocked_plan_unit(self):
        self.gh({REVIEWS: [row("example-org/app", 1, "old ask")], ISSUES: []})
        _, out = self.foundry("intake", "scan")
        self.assertEqual((out["seeded"], out["proposed"]), (1, []))  # what was already there is not news
        self.assertEqual([u for u in self.snap()["units"] if u.startswith("github-")], [])
        self.gh({REVIEWS: [row("example-org/app", 1, "old ask"), row("example-org/app", 2, "new ask")],
                 ISSUES: [row("other-org/tool", 9, "fix the thing")]})
        _, out = self.foundry("intake", "scan")
        self.assertEqual(sorted(x["ref"] for x in out["proposed"]), ["example-org/app#2", "other-org/tool#9"])
        units = self.snap()["units"]
        for x in out["proposed"]:
            u = units[x["unit"]]
            self.assertEqual(u["mode"], "plan")
            self.assertTrue(u["blocked_by"], u)  # the owner's proceed/change/drop card holds it: never auto-started
        issue = units[next(x["unit"] for x in out["proposed"] if x["kind"] == "issue")]
        self.assertEqual(issue["keys"]["pr"], [])  # an issue is not tracked as a PR
        _, again = self.foundry("intake", "scan")
        self.assertEqual(again["proposed"], [])
        self.assertEqual(len([r for r in self.journal() if r["op"] == "intake.scan"]), 3)

    def test_refs_a_unit_holds_are_skipped_and_the_daily_cap_holds(self):
        self.gh({REVIEWS: [], ISSUES: []})
        self.foundry("intake", "scan")
        self.foundry("unit", "new", "--id", "mine", "--title", "Mine", "--pr", "example-org/app#3")
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"intake": {"scan_max_per_day": 1}}, f)
        self.gh({REVIEWS: [row("example-org/app", 3, "held"), row("example-org/app", 4, "a"), row("example-org/app", 5, "b")],
                 ISSUES: []})
        _, out = self.foundry("intake", "scan")
        self.assertEqual([x["ref"] for x in out["proposed"]], ["example-org/app#4"])
        self.assertEqual(out["capped"], ["example-org/app#5"])

    def test_an_unreadable_source_is_named(self):
        self.gh({REVIEWS: []})
        _, out = self.foundry("intake", "scan")
        self.assertEqual(out["unread"], ["issue"])


class Kaizen(tf.Base):
    def test_the_weekly_kaizen_lands_on_the_board(self):
        self.foundry("report", "kaizen")
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), "json"], env=self.env,
                           capture_output=True, text=True)
        k = json.loads(r.stdout)["kaizen"]
        self.assertTrue(k["at"])
        self.assertIn({"head": True, "text": "재작업률"}, k["lines"])
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), "text"], env=self.env,
                           capture_output=True, text=True)
        self.assertIn("## 카이젠 · ", r.stdout)


class InReview(tf.Base):
    def test_korean_status_names_count_as_in_review(self):
        for st in ("검토 중", "해결됨", "완료", "検討中", "In Review"):
            self.assertTrue(sync.IN_REVIEW_RE.search(st), st)
        self.assertFalse(sync.IN_REVIEW_RE.search("할 일"))

    def test_a_transition_batch_closes_only_with_the_ticket_status(self):
        self.foundry("unit", "new", "--id", "PROJ-9", "--title", "T", "--ticket", "PROJ-9")
        self.record("sync.drain", batches=[{"batch": "sb-1", "unit": "PROJ-9", "target": "jira", "keys": [],
                                             "transition": "in-review"},
                                            {"batch": "sb-2", "unit": "PROJ-9", "target": "jira", "keys": []}])
        rc, out = self.foundry("sync", "done", "sb-1", ok=(1,))
        self.assertIn("--ticket-status", out["error"])
        self.foundry("sync", "done", "sb-2")  # no transition: closes as before
        self.foundry("sync", "done", "sb-1", "--ticket-status", "검토 중")
        self.assertEqual(self.snap()["refs"]["ticket:PROJ-9"]["value"], "검토 중")


if __name__ == "__main__":
    import unittest
    unittest.main()
