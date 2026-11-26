"""0.1.77: review requests that reach the owner only through a team, and PRs a prefs review.ignore rule matches, are
not the owner's: intake proposes no card for them (and closes open ones on the next scan), the daily report counts
team requests on one line, and the board's PR tab hides ignored PRs behind a count."""

import json
import os
import sys

import test_foundry as tf
from test_lifecycle_0154 import ISSUES, REVIEWS, row

sys.path.insert(0, tf.SCRIPTS)
import intake  # noqa: E402

MINE = "search prs user-review-requested:@me --state open"
APP = "example-org/app"


def authored(num, title, login):
    return dict(row(APP, num, title), author={"login": login, "is_bot": True}, labels=[])


class ReviewIgnore(tf.Base):
    def prefs(self, data):
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump(data, f)

    def test_rules_match_every_field_they_name_with_literal_brackets(self):
        r = authored(1, "[Auto] Add Claude access", "app/securefix")
        self.assertEqual(intake.ignore_rule(r, [{"author": "app/*"}]), {"author": "app/*"})
        self.assertTrue(intake.ignore_rule(r, [{"title": "[Auto] *"}]))
        self.assertIsNone(intake.ignore_rule(dict(r, title="Auto fix"), [{"title": "[Auto] *"}]))
        self.assertIsNone(intake.ignore_rule(r, [{"author": "app/*", "repo": "other/*"}]))  # every field must match
        self.assertTrue(intake.ignore_rule(dict(r, labels=[{"name": "deps"}]), [{"label": "dep*"}]))

    def test_prefs_validate_the_rules(self):
        import prefs
        self.assertEqual([e for e in prefs.validate({"review": {"ignore": [{"author": "app/*"}]}})
                          if e.startswith("review.ignore")], [])
        for bad in ([{"who": "x"}], [{}], [{"title": ""}], "app/*"):
            self.assertTrue([e for e in prefs.validate({"review": {"ignore": bad}}) if e.startswith("review.ignore")], bad)

    def test_scan_proposes_only_personal_unignored_requests_and_closes_cards_that_no_longer_apply(self):
        self.gh({REVIEWS: [], MINE: [], ISSUES: []})
        self.foundry("intake", "scan")  # seed
        _, old = self.foundry("intake", "add", "--source", "github", "--ref", f"{APP}#2", "--title", "team ask",
                              "--kind", "review")
        self.assertTrue(old.get("card"))
        self.prefs({"review": {"ignore": [{"author": "app/*"}]}})
        team_only = row(APP, 2, "team ask")
        auto = authored(3, "[Auto] Add Claude access", "app/securefix")
        mine = row(APP, 1, "please review")
        self.gh({REVIEWS: [mine, team_only, auto], MINE: [mine, auto], ISSUES: []})
        _, out = self.foundry("intake", "scan")
        self.assertEqual([x["ref"] for x in out["proposed"]], [f"{APP}#1"])
        self.assertEqual((out["team"], out["ignored"]), (1, 1))
        self.assertEqual(out["withdrawn"], [old["card"]])
        snap = self.snap()
        self.assertIsNotNone(snap["cards"][old["card"]]["answer"])
        self.assertEqual(snap["units"][old["unit"]]["status"], "cancelled")
        self.assertEqual(snap["intake"]["ignored"][f"{APP}#3".lower()]["rule"], {"author": "app/*"})
        ops = [r for r in self.journal() if r["op"] == "intake.ignored"]
        self.assertEqual(len(ops), 1)
        self.foundry("intake", "scan")
        self.assertEqual(len([r for r in self.journal() if r["op"] == "intake.ignored"]), 1)  # once per ref

    def test_personal_search_failure_keeps_the_old_behaviour(self):
        self.gh({REVIEWS: [], ISSUES: []})
        self.foundry("intake", "scan")
        self.gh({REVIEWS: [row(APP, 5, "ask")], ISSUES: []})  # no answer for the personal search
        _, out = self.foundry("intake", "scan")
        self.assertEqual([x["ref"] for x in out["proposed"]], [f"{APP}#5"])


import test_report_0152 as r52  # noqa: E402


class ReportTeamLine(r52.Layout):
    def test_team_only_requests_are_a_count_and_ignored_ones_vanish(self):
        self.gh({**r52.GH, MINE: [row(APP, 31, "And this")],
                 REVIEWS: [row(APP, 30, "Please look"), row(APP, 31, "And this"),
                           authored(32, "[Auto] spend limit", "app/securefix")]})
        with open(os.path.join(self.dir, "prefs.json")) as f:
            p = json.load(f)
        p.setdefault("review", {})["ignore"] = [{"title": "[Auto] *"}]
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump(p, f)
        text = "\n".join(self.messages())
        self.assertIn("팀 리뷰 요청 1건", text)
        self.assertIn("내가 리뷰할 PR", text)
        self.assertNotIn("#30", text.split("팀 리뷰 요청")[0].split("내가 리뷰할 PR")[-1])
        self.assertNotIn("spend limit", text)


class PrTabHidesIgnored(tf.Base):
    def test_an_ignored_pr_is_hidden_and_counted(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "t", "--pr", f"{APP}#7")
        self.foundry("unit", "new", "--id", "u2", "--title", "t2", "--pr", f"{APP}#8")
        for n, u in ((7, "u1"), (8, "u2")):
            self.record("pr.state", unit=u, repo=APP, number=n, state="OPEN", title=f"pr {n}")
        self.record("intake.ignored", ref=f"{APP}#7", rule={"author": "app/*"})
        _, text = self.foundry("board", "--tab", "pr")
        self.assertNotIn("#7", text)
        self.assertIn("#8", text)
        self.assertRegex(text.splitlines()[0], r"(무시|無視|ignored) 1")
