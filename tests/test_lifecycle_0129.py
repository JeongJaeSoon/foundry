"""0.1.29: the release done gate reads the tag itself when the journal never saw it; one reviewer-load function
feeds the pane, board.html and the assignment; the assignment weighs project relevance before load."""

import os
import subprocess
import sys
import unittest

import test_foundry as tf
from test_foundry import SCRIPTS

SHA = "4b1d0e6c0ffee0000000000000000000000000aa"


class ReleaseGateTagBeforeUnit(tf.Base):
    UID = "release-app-20261118-1949"

    def run_f(self, *args, env=None):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args],
                           env={**self.env, **(env or {})}, capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def setUp(self):
        super().setUp()
        self.lead = {"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": self.UID}

    def release_unit(self, prs):
        self.record("unit.new", id=self.UID, kind="project", title="Release app", repo="example-org/app",
                    lead="foundry:line-lead", pr=[], ticket=[], topic=["release"])
        self.record("release.unit", id=self.UID, repo="example-org/app", prs=prs, procedure=None, dev_app=None)
        self.record("unit.status", id=self.UID, status="in_progress")
        self.assertEqual(self.run_f("unit", "verify", self.UID, "--evidence", "tag checked",
                                    env={"FOUNDRY_ROLE": "foreman"})[0], 0)

    def github(self, contains=True):
        self.gh({"example-org/app#710": {"mergeCommit": {"oid": SHA}},
                 "api repos/example-org/app/tags?per_page=100": [{"name": "app-rel-20261118162533"},
                                                                  {"name": "app-rel-20261117090000"}],
                 f"api repos/example-org/app/compare/{SHA}...app-rel-20261118162533":
                     {"status": "ahead" if contains else "diverged"},
                 f"api repos/example-org/app/compare/{SHA}...app-rel-20261117090000": {"status": "behind"}})

    def test_a_tag_cut_before_the_unit_existed_lets_it_close_and_is_backfilled(self):
        self.github()  # the tag exists first; no pr.released was ever written
        self.release_unit(["example-org/app#710"])
        rc, _, err = self.run_f("unit", "set", self.UID, "--status", "done", env=self.lead)
        self.assertEqual(rc, 0, err)
        rel = self.snap()["units"][self.UID]["release"]
        self.assertEqual(rel["released"], {"example-org/app#710": "app-rel-20261118162533"})
        self.assertEqual(self.snap()["units"][self.UID]["prs"], [])  # no phantom OPEN PR on the release unit

    def test_the_backfill_lands_on_the_unit_that_holds_the_pr(self):
        self.foundry("unit", "new", "--id", "feat", "--title", "Feat", "--repo", "example-org/app")
        self.record("pr.state", unit="feat", repo="example-org/app", number=710, state="MERGED")
        self.github()
        self.release_unit(["example-org/app#710"])
        rc, _, err = self.run_f("unit", "set", self.UID, "--status", "done", env=self.lead)
        self.assertEqual(rc, 0, err)
        (pr,) = self.snap()["units"]["feat"]["prs"]
        self.assertEqual(pr["released"], "app-rel-20261118162533")

    def test_no_tag_holds_it_still_refuses(self):
        self.github(contains=False)
        self.release_unit(["example-org/app#710"])
        rc, _, err = self.run_f("unit", "set", self.UID, "--status", "done", env=self.lead)
        self.assertEqual(rc, 2, err)
        self.assertIn("no release tag contains example-org/app#710", err)


POOL = ["alice", "bob", "carol"]


class _Review(tf.Base):
    REF = "example-org/app#5"

    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "docs", "--title", "Docs", "--repo", "example-org/app")

    def prefs(self, cap=0):
        import json
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"review": {"people": [{"login": x, "slack": f"U00000000{i}"} for i, x in enumerate(POOL)],
                                  "channel": "C000000001", "load_cap": cap,
                                  "repos": [{"repo": "*", "pool": POOL}]}}, f)

    def github(self, others, owners="", reviews=None, commits=None, bots=0):
        import base64
        data = {self.REF: {"state": "OPEN", "isDraft": False, "author": {"login": "owner"}, "reviewRequests": [],
                           "latestReviews": [], "assignees": [], "title": "api handler", "headRefOid": "abc1234def",
                           "url": "https://github.com/example-org/app/pull/5", "files": [{"path": "api/handler.go"}]}}
        if owners:
            data["api repos/example-org/app/contents/.github/CODEOWNERS"] = {
                "content": base64.b64encode(owners.encode()).decode()}
        for login in POOL:
            data[f"api repos/example-org/app/collaborators/{login}"] = {}
            data[f"api repos/example-org/app/collaborators/{login}/permission"] = {"permission": "write"}
            items = [{"user": {"login": f"dev{i}", "type": "User"}} for i in range(others.get(login, 0))]
            items += [{"user": {"login": "renovate[bot]", "type": "Bot"}} for _ in range(bots)]
            data[f"api search/issues?q=is:pr+is:open+user-review-requested:{login}+-author:@me+*"] = {"items": items}
            data[f"api search/issues?q=is:pr+repo:example-org/app+reviewed-by:{login}+*"] = {
                "total_count": (reviews or {}).get(login, 0)}
            data[f"api repos/example-org/app/commits?author={login}*"] = [{}] * (commits or {}).get(login, 0)
        self.gh(data)

    def assign(self, dry=True):
        return self.foundry("review", "assign", self.REF, "--unit", "docs", *(["--dry-run"] if dry else []),
                            ok=(0, 3))[1]


class ReviewerChoice(_Review):
    def test_relevance_beats_a_lower_load(self):
        self.prefs()
        self.github({"alice": 0, "bob": 1, "carol": 6}, owners="/api/ @carol\n", reviews={"bob": 2})
        out = self.assign()
        self.assertEqual(out["reviewer"], "carol")
        self.assertTrue(out["reason"].startswith("선정: CODEOWNERS"), out["reason"])

    def test_recent_reviews_beat_load_when_nobody_owns_the_path(self):
        self.prefs()
        self.github({"alice": 0, "bob": 3}, reviews={"bob": 4}, commits={"alice": 0})
        out = self.assign()
        self.assertEqual((out["reviewer"], out["reason"]), ("bob", "선정: 최근 리뷰 4건 · 내 의뢰 0"))

    def test_the_load_cap_skips_a_busy_reviewer_unless_they_are_the_only_codeowner(self):
        self.prefs(cap=3)
        self.github({"alice": 0, "bob": 1, "carol": 6}, owners="/api/ @carol @bob\n")
        self.assertEqual(self.assign()["reviewer"], "bob")  # carol is over the cap and bob owns the path too
        self.github({"alice": 0, "bob": 1, "carol": 6}, owners="/api/ @carol\n")
        out = self.assign()
        self.assertEqual(out["reviewer"], "carol")
        self.assertIn("상한 초과지만 유일한 CODEOWNER", out["reason"])
        self.github({"alice": 4, "bob": 5, "carol": 6})
        self.assertIsNone(self.assign().get("reviewer"))  # everyone over the cap, no CODEOWNER: nobody

    def test_the_reason_is_recorded_and_shown_on_the_pr_tab(self):
        self.prefs()
        self.github({"alice": 2, "bob": 0, "carol": 1}, owners="/api/ @carol\n", reviews={"carol": 4})
        out = self.assign(dry=False)
        self.assertEqual(out["reason"], "선정: CODEOWNERS + 최근 리뷰 4건 · 내 의뢰 0")
        pick = self.snap()["units"]["docs"]["review_picks"][self.REF]
        self.assertEqual((pick["reviewer"], pick["reason"]), ("carol", out["reason"]))
        self.record("pr.state", unit="docs", repo="example-org/app", number=5, state="OPEN", title="api handler",
                    review="REVIEW_REQUIRED", reviewers=["carol"])
        (row,) = self.foundry("board", "--format", "json")[1]["pr_tab"]
        self.assertEqual(row["pick"], out["reason"])


class OneLoadDefinition(_Review):
    def test_pane_board_and_assign_read_the_same_numbers(self):
        self.prefs()
        self.github({"alice": 3, "bob": 0, "carol": 0}, bots=5)  # bot authors never count
        self.record("pr.state", unit="docs", repo="example-org/app", number=5, state="OPEN", title="api handler",
                    review="REVIEW_REQUIRED", reviewers=["alice", "some-team-slug"])
        self.record("pr.state", unit="docs", repo="example-org/app", number=6, state="OPEN", title="other",
                    review="REVIEW_REQUIRED", reviewers=["alice"])
        loads = {r["login"]: r for r in self.foundry("review", "load", "--refresh")[1]["reviewers"]}
        self.assertEqual((loads["alice"]["mine"], loads["alice"]["others"], loads["alice"]["text"]),
                         (2, 3, "내 의뢰 2 · 참고 3"))
        inv = {r["login"]: r for r in self.foundry("board", "--format", "json")[1]["inventory"]["reviewers"]}
        self.assertEqual((inv["alice"]["mine"], inv["alice"]["others"], inv["alice"]["text"]),
                         (2, 3, "내 의뢰 2 · 참고 3"))
        out = self.assign()
        self.assertEqual({c["login"]: (c["mine"], c["others"]) for c in out["candidates"]}["alice"], (2, 3))

    def test_the_inventory_and_the_assignment_call_one_function(self):
        from unittest import mock
        sys.path.insert(0, SCRIPTS)
        import factory
        import foundrylib as cl
        import review
        keep = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            with mock.patch.object(review, "load_rows", wraps=review.load_rows) as spy, \
                    mock.patch.object(review, "refresh_others", return_value={}), \
                    mock.patch.object(review, "can_approve", return_value=True), \
                    mock.patch.object(review, "_gh", return_value=None):
                snap = cl.load()
                factory.inventory(snap, live={}, rs={})
                review.choose("example-org/app", "owner", p={"review": {"repos": [{"repo": "*", "pool": POOL}]}},
                              snap=snap)
            self.assertEqual(spy.call_count, 2)
        finally:
            os.environ["FOUNDRY_STATE_DIR"] = keep or ""
            if keep is None:
                os.environ.pop("FOUNDRY_STATE_DIR")


if __name__ == "__main__":
    unittest.main()
