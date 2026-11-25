"""0.1.19: personal review load and collaborators only; repo-less units never auto-launched."""

import json
import os
import subprocess
import sys
import unittest

import test_foundry as tf
import test_launcher as tl
from test_foundry import ROOT, SCRIPTS

POOL = ("alice-gh", "bob-gh", "carol-gh", "dave-gh")


def stream_lines(t):
    _, out = t.foundry("watch", "stream", "--kind", "sessions", "--once")
    return [x.split("] ", 1)[-1] for x in (out or "").splitlines() if x] if isinstance(out, str) else []


class ReviewLoad(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "docs", "--title", "Docs", "--repo", "example-org/gateway")

    def github(self, personal, team=0, collaborators=POOL):
        data = {"example-org/gateway#713": {"state": "OPEN", "isDraft": False, "author": {"login": "owner-gh"},
                                     "reviewRequests": [], "latestReviews": [], "assignees": [], "title": "docs",
                                     "headRefOid": "abc1234def", "url": "https://github.com/example-org/gateway/pull/713",
                                     "statusCheckRollup": []}}
        for login in POOL:
            # the load reads direct requests only; the team-wide query must never be what is counted
            data[f"api search/issues?q=is:pr+is:open+user-review-requested:{login}+-author:@me+*"] = {
                "items": [{"user": {"login": f"dev{i}", "type": "User"}} for i in range(personal[login])]}
            data[f"api search/issues?q=is:pr+is:open+review-requested:{login}*"] = {
                "items": [{"user": {"login": "dev", "type": "User"}}] * (personal[login] + team)}
            data[f"api repos/example-org/gateway/collaborators/{login}/permission"] = {"permission": "write"}
            if login in collaborators:
                data[f"api repos/example-org/gateway/collaborators/{login}"] = {}
        self.gh(data)

    def test_load_counts_personal_requests_not_team_ones(self):
        self.github({"alice-gh": 3, "bob-gh": 1, "carol-gh": 10, "dave-gh": 2}, team=1000)
        _, out = self.foundry("review", "pick", "example-org/gateway#713", "--author", "owner-gh")
        self.assertEqual(out["reviewer"], "bob-gh")
        loads = {c["login"]: c["load"] for c in out["candidates"]}
        self.assertEqual(loads["bob-gh"], 1)  # not 1001: team requests are not this person's queue

    def test_non_collaborators_are_never_picked_even_when_least_loaded(self):
        self.github({"alice-gh": 0, "bob-gh": 5, "carol-gh": 4, "dave-gh": 6},
                    collaborators=("bob-gh", "carol-gh", "dave-gh"))
        _, out = self.foundry("review", "pick", "example-org/gateway#713", "--author", "owner-gh")
        self.assertEqual(out["reviewer"], "carol-gh")
        self.assertNotIn("alice-gh", [c["login"] for c in out["candidates"] if c["approve"]])


class RepolessUnits(tl.Launcher):
    def test_launch_refuses_a_unit_with_no_repo_and_says_how_to_fix_it(self):
        self.foundry("unit", "new", "--id", "card-watch", "--title", "Card watch")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "unit", "launch", "card-watch"],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("has no repo", r.stderr)
        self.assertIn("foundry unit set card-watch --repo", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_unit_set_repo_records_a_registered_repo_and_refuses_an_unknown_one(self):
        self.foundry("unit", "new", "--id", "card-watch", "--title", "Card watch")
        self.foundry("unit", "set", "card-watch", "--repo", "nowhere", ok=(2,))
        self.assertFalse(self.snap()["units"]["card-watch"].get("repo"))
        self.foundry("unit", "set", "card-watch", "--repo", "app")
        self.assertEqual(self.snap()["units"]["card-watch"]["repo"], "app")

    def test_sessions_stream_never_proposes_a_repoless_unit_and_escalates_it_once(self):
        self.foundry("unit", "new", "--id", "card-watch", "--title", "Card watch")
        _, nxt = self.foundry("next", "--json")
        self.assertNotIn("card-watch", [r["unit"] for r in nxt])
        lines = stream_lines(self)
        self.assertFalse([x for x in lines if x.startswith("launch card-watch")])
        bare = [x for x in lines if x.startswith("unit card-watch is queued with no repo")]
        self.assertEqual(len(bare), 1)
        self.assertIn("foundry unit set card-watch --repo", bare[0])
        self.assertFalse([x for x in stream_lines(self) if "card-watch" in x])  # said once
        self.foundry("unit", "set", "card-watch", "--repo", "app")
        self.assertTrue([x for x in stream_lines(self) if x.startswith("launch card-watch")])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (ReviewLoad, RepolessUnits):
        suite.addTests(cls(n) for n in sorted(vars(cls)) if n.startswith("test_"))
    return suite
