"""0.1.120: review reminders and the release state. One review-stage predicate (factory.in_review: review and the
CI stages 0.1.100 split out of it) drives reminders; an ask posted to a channel with no one named starts the
channel's clock; the owner's own open PRs no unit tracks (`gh search prs --author @me`, pr.owner_open) are in the
inventory and age like a unit's; schedules missed while the foreman was down are listed by `foundry up` and the
foreman's SessionStart. One release-state function (factory.pr_release_state) keeps a merged PR out of 릴리스 대기
when its repo has no release step, a release unit recorded the tag that holds it, or it is past release.watch_days."""

import copy
import datetime as dt
import sys

from test_foundry import SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import aging  # noqa: E402
import factory  # noqa: E402
import prefs  # noqa: E402
import watchers  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 27, 15, 0, tzinfo=KST)  # a Friday
P = {"release": {"none": ["*ops"], "tag": "*", "watch_days": 14},
     "review": {"remind_business_days": 1, "escalate_business_days": 5}, "timezone": "Asia/Seoul"}


def merged(n, repo="example-org/app", days_ago=1, **kw):
    return {"repo": repo, "number": n, "state": "MERGED", "merged": (NOW - dt.timedelta(days=days_ago)).isoformat(), **kw}


class ReleaseState(Base):
    CREW = False

    def test_one_function_decides_release_wait(self):
        state = lambda pr, snap=None: factory.pr_release_state(pr, P, {}, snap, NOW)  # noqa: E731
        self.assertEqual(state(merged(1)), "release-wait")
        self.assertEqual(state(merged(2, repo="example-org/cluster-ops")), "no-release")
        self.assertEqual(state(merged(3, days_ago=20)), "unwatched")
        self.assertEqual(state(merged(4, released="v1.2.0")), "released")
        rel = {"units": {"release-app-1": {"release": {"released": {"example-org/app#5": "v1.3.0"}}}}}
        self.assertEqual(state(merged(5), rel), "released")  # a bump PR the release unit's tag holds
        self.assertEqual(state({"repo": "example-org/app", "number": 6, "state": "OPEN"}), "open")


class Reminders(Base):
    CREW = False

    def snap(self, to, stage="ci-running", unit="u1", owner_prs=None):
        ask = {"oid": "ob-1", "kind": "review-ask", "state": "sent", "prs": ["example-org/app#9"], "to": to,
               "channel": "C1", "thread": None, "sent_ts": "1794993628.067699",
               "closed": (NOW - dt.timedelta(days=3)).isoformat()}
        return {"units": {"u1": {"id": "u1", "status": "waiting_others"}} if unit else {},
                "outbox": {"ob-1": ask}, "owner_prs": owner_prs or {}, "cards": {}}

    def inv(self, stage, unit="u1", since_days=3):
        return {"repos": {"example-org/app": [{"kind": "pr", "ref": "example-org/app#9", "unit": unit, "stage": stage,
                                               "since": (NOW - dt.timedelta(days=since_days)).isoformat()}]}}

    def acts(self, snap, inv):
        p = copy.deepcopy(prefs.defaults())
        p["review"].update(P["review"])
        return aging.actions(snap, NOW, p, inv)

    def test_ci_stages_still_get_reminders(self):
        for stage in ("review", "ci-running", "ci-failing"):
            self.assertEqual(self.acts(self.snap(["U1"]), self.inv(stage)), {"example-org/app#9": "remind"}, stage)
        self.assertEqual(self.acts(self.snap(["U1"]), self.inv("approved")), {})

    def test_a_channel_ask_with_no_one_named_is_reminded(self):
        self.assertEqual(self.acts(self.snap([]), self.inv("review")), {"example-org/app#9": "remind"})
        self.assertEqual(aging.due_reviewers(self.snap([]), "example-org/app#9", NOW, KST, 1), [""])

    def test_an_owner_pr_with_no_unit_ages_like_a_units(self):
        snap = self.snap(["U1"], unit=None, owner_prs={"example-org/app#9": {"repo": "example-org/app", "number": 9}})
        self.assertEqual(self.acts(snap, self.inv("review", unit=None, since_days=10)),
                         {"example-org/app#9": "owner-decision"})
        self.assertEqual(self.acts(self.snap(["U1"], unit=None), self.inv("review", unit=None)),
                         {"example-org/app#9": "adopt-or-drop"})  # not the owner's: the orphan card as before

    def test_owner_prs_reach_the_inventory(self):
        snap = {"units": {}, "owner_prs": {"example-org/app#9": {"repo": "example-org/app", "number": 9,
                                                                 "title": "Fix", "createdAt": "2026-11-20T00:00:00Z"}}}
        prs = factory.collect_prs(snap)
        self.assertEqual((prs["example-org/app#9"]["unit"], prs["example-org/app#9"]["state"]), (None, "OPEN"))


class CatchUp(Base):
    CREW = False

    def test_schedules_missed_while_the_foreman_was_down(self):
        p = {"timezone": "Asia/Seoul", "schedules": [{"name": "handover", "cron": "0 8 * * *", "run": "foundry report handover"},
                                                     {"name": "kaizen", "cron": "0 9 * * 1", "run": "foundry report kaizen"}]}
        down = {"foreman": {"alive": True, "ts": (NOW - dt.timedelta(hours=10)).isoformat()}}
        got = watchers.missed_schedules(down, p, NOW)
        self.assertEqual([(m["name"], m["missed"][11:16], m["run"]) for m in got],
                         [("handover", "08:00", "Run `foundry report handover`")])
        alive = {"foreman": {"alive": True, "ts": (NOW - dt.timedelta(minutes=5)).isoformat()}}
        self.assertEqual(watchers.missed_schedules(alive, p, NOW), [])
        self.assertEqual(watchers.missed_schedules({}, p, NOW), [])
