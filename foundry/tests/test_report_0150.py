"""0.1.50: the daily report (공장 일일 가동현황), release gaps and review gaps. Collection is the whole GitHub account
(not prefs repos) plus the owner's own Slack messages, an unread source is named, the Slack form is linted, and
nothing reaches the outbox without the owner's answer on the draft card."""

import json
import os
import sys
import unittest
from unittest import mock

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import report  # noqa: E402

DAY = "2026-11-18"
CH = "C0TESTCHAN1"
ALICE = "U0AAAAAAA1"
FOOT = "🤖 Claude Code が作成"


def row(repo, num, title="t", state="open"):
    return {"repository": {"nameWithOwner": repo, "name": repo.split("/")[1]}, "number": num, "title": title,
            "state": state, "url": f"https://github.com/{repo}/pull/{num}", "isDraft": False,
            "createdAt": "2026-11-15T10:00:00+09:00", "updatedAt": "2026-11-18T09:00:00+09:00", "closedAt": None}


def pr(requests=(), reviews=(), pushed="2026-11-16T09:00:00+09:00", threads=(), asked=None):
    return {"data": {"repository": {"pullRequest": {
        "url": None, "title": None, "isDraft": False, "createdAt": "2026-11-15T10:00:00+09:00",
        "reviewRequests": {"nodes": [{"requestedReviewer": {"__typename": "User", "login": x}} for x in requests]},
        "reviews": {"nodes": list(reviews)},
        "timelineItems": {"nodes": [{"createdAt": asked, "requestedReviewer": {"__typename": "User", "login": x}}
                                    for x in requests] if asked else []},
        "commits": {"nodes": [{"commit": {"oid": "abc1234def5678", "committedDate": pushed}}]},
        "reviewThreads": {"nodes": list(threads)}}}}}


GH = {
    f"search prs --author @me --updated >={DAY}": [row("other-org/tool", 3, "Tool fix"), row("example-org/secret-x", 1)],
    f"search prs --author @me --merged-at >={DAY}": [row("example-org/app", 5, "Merged one", "merged")],
    f"search prs --reviewed-by @me --updated >={DAY}": [],
    f"search prs --involves @me --updated >={DAY}": [],
    "search prs --review-requested @me --state open": [],
    "api user": {"login": "me"},
    "search prs --author @me --state open": [row("example-org/app", 7), row("example-org/app", 8), row("example-org/app", 9)],
    "api graphql example-org/app#7": pr(requests=["alice"], asked="2026-11-16T10:00:00+09:00"),
    "api graphql example-org/app#8": pr(),
    "api graphql example-org/app#9": pr(
        reviews=[{"author": {"__typename": "User", "login": "bob"}, "state": "APPROVED",
                  "submittedAt": "2026-11-17T09:00:00+09:00"}],
        pushed="2026-11-17T12:00:00+09:00",
        threads=[{"isResolved": False, "comments": {"nodes": [{"author": {"__typename": "Bot", "login": "rabbit"}}]}}]),
}

PREFS = {"review": {"channel": CH, "people": [{"login": "alice", "slack": ALICE, "alias": "a"}]},
         "report": {"user": "tester", "exclude": {"repos": ["example-org/secret*"], "channels": ["random"]},
                    "collapse_lines": 0},  # every group its own reply here; 0.1.56 tests the collapse
         "release": {"batch_repos": [{"repo": "*app", "dev_app": "app"}]}}


class Base(tf.Base):
    def setUp(self):
        super().setUp()
        self.env.pop("FOUNDRY_MODEL", None)
        self.env.pop("ANTHROPIC_MODEL", None)
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump(PREFS, f)
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump({"owner_slack_user_id": "U0OWNER0001"}, f)
        self.gh(GH)

    def at(self, when, *args, ok=(0,)):
        old = self.env["FOUNDRY_NOW"]
        self.env["FOUNDRY_NOW"] = when
        try:
            return self.foundry(*args, ok=ok)
        finally:
            self.env["FOUNDRY_NOW"] = old

    def rec(self, when, op, **fields):
        with mock.patch.dict(os.environ, {"FOUNDRY_NOW": when}):
            self.record(op, **fields)

    def outbox(self):
        return self.foundry("outbox", "list")[1]


class SlackLint(unittest.TestCase):
    def ok(self, *texts):
        return report.slack_lint([{"text": t} for t in texts])

    def main(self, body):
        return f":kyouyuu-1:\n\n\n{body}\n\n{FOOT}"

    def test_bold_is_double_asterisk(self):
        self.assertEqual(self.ok(self.main("**제목**")), [])
        self.assertIn("single-asterisk", self.ok(self.main("*제목*"))[0])
        self.assertEqual(self.ok(self.main("`*literal*` 와 3 * 4 * 5")), [])

    def test_the_lint_has_no_footer_rule(self):  # 0.1.87: the Slack hook appends prefs slack.footer at send
        self.assertEqual(self.ok(":kyouyuu-1:\n\n\nx"), [])

    def test_a_share_without_a_mention_starts_with_the_emoji_and_two_blank_lines(self):
        self.assertIn("kind emoji", " ".join(self.ok(f"제목\n\n{FOOT}")))
        self.assertIn("kind emoji", " ".join(self.ok(f":kyouyuu-1:\n\n제목\n\n{FOOT}")))
        self.assertEqual(self.ok(f"<@{ALICE}>\n\n용건\n\n{FOOT}"), [])  # a mention takes line 1

    def test_nothing_follows_a_bullet_list(self):
        errs = self.ok(self.main("- a\n- b\n\n**다음 섹션**"))
        self.assertIn("follows a bullet list", errs[0])
        self.assertIn("follows a bullet list", " ".join(self.ok(":kyouyuu-1:\n\n\n- a\n\n" + FOOT)))  # the footer too
        self.assertEqual(report.slack_lint([{"text": self.main("x")}, {"text": "**섹션**\n- a\n  - b", "reply": True}]), [])

    def test_no_tables_or_h3(self):
        self.assertIn("###", " ".join(self.ok(self.main("### 제목"))))
        self.assertIn("tables", " ".join(self.ok(self.main("| a | b |"))))


class Daily(Base):
    def test_every_org_shows_excluded_repos_do_not_and_unread_sources_are_named(self):
        _, out = self.foundry("report", "daily", "--json")
        text = out["text"]
        self.assertIn("**other-org/tool**\n- [other-org/tool#3](https://github.com/other-org/tool/pull/3) Tool fix — 리뷰 중",
                      text)  # a repo prefs never listed: its own group
        self.assertIn("example-org/app#5", text)
        self.assertNotIn("secret-x", text)
        self.assertIn("**[foundry] tester 일보 (2026-11-18)**", text)
        self.assertEqual(out["gaps"], ["Slack (수집 기록 없음)"])
        self.gh({k: v for k, v in GH.items() if "involves" not in k})
        _, out = self.foundry("report", "daily", "--json")
        self.assertIn("읽지 못한 곳: GitHub (involved), Slack (수집 기록 없음)", out["text"])

    def test_owner_slack_messages_one_line_each_excluded_channels_refused(self):
        _, q = self.foundry("report", "slack-query")
        self.assertEqual(q["query"], f"from:<@U0OWNER0001> on:{DAY}")
        link = "https://example.slack.com/archives/C0TESTCHAN1/p1731900000123456"
        _, ex = self.foundry("report", "slack-add", "--channel", "#random", "--permalink", link, "--summary", "잡담")
        self.assertEqual(ex, {"excluded": "random"})
        self.foundry("report", "slack-add", "--channel", "team", "--permalink", link, "--summary", "a\nb" * 80, ok=(2,))
        self.foundry("report", "slack-add", "--channel", "team", "--permalink", "https://x.test/1", "--summary", "s", ok=(2,))
        self.foundry("report", "slack-add", "--channel", "team", "--permalink", link, "--summary", "배포 일정 공유")
        self.foundry("report", "slack-done", "--count", "1")
        _, out = self.foundry("report", "daily", "--json")
        self.assertIn(f"**その他**\n- [#team]({link}) 배포 일정 공유", out["text"])  # no PR or ticket: その他
        self.assertNotIn("Slack", " ".join(out["gaps"]))

    def test_slack_form_passes_the_lint_with_the_bare_footer(self):
        _, out = self.foundry("report", "daily", "--format", "slack", "--model", "claude-sonnet-5-5")  # --model: ignored
        main = out["messages"][0]["text"].split("\n")
        self.assertEqual(main[:4], [":kyouyuu-1:", "", "", f"**[foundry] tester 일보 ({DAY})**"])
        self.assertNotIn(FOOT, main)
        self.assertTrue(all(m.get("reply") for m in out["messages"][1:]))
        self.assertTrue(all(m["text"].startswith("**") for m in out["messages"][1:]))
        self.assertEqual(report.slack_lint(out["messages"]), [])
        self.assertIn("<https://github.com/other-org/tool/pull/3|other-org/tool#3>", json.dumps(out, ensure_ascii=False))


class Approval(Base):
    def draft(self, when="2026-11-18T10:00:00+09:00"):
        return self.at(when, "report", "daily", "--draft")[1]

    def test_a_draft_sends_nothing_until_the_owner_answers(self):
        d = self.draft()
        self.assertEqual(d["status"], "opened")
        self.assertEqual(self.outbox(), [])
        with mock.patch.dict(os.environ, {"FOUNDRY_STATE_DIR": self.dir}):
            import outbox
            with self.assertRaisesRegex(ValueError, "no owner approval"):
                outbox.add(None, CH, None, "report", "x", rid=d["draft"])
        self.foundry("outbox", "add", "x", "--channel", CH, "--kind", "report", "--text", "x", ok=(2,))
        self.foundry("decide", "answer", d["card"], "post", "--by", "foreman", ok=(2,))  # a relay needs the owner's words
        self.assertEqual(self.outbox(), [])
        _, a = self.foundry("decide", "answer", d["card"], "post", "--by", "owner")
        self.assertEqual((a["state"], a["send_at"]), ("approved", "now"))
        [o] = self.outbox()
        self.assertEqual((o["kind"], o["channel"], o["rid"]), ("report", CH, d["draft"]))
        self.assertEqual(o["text"], d["messages"][0]["text"])
        self.assertEqual(o["replies"], [m["text"] for m in d["messages"][1:]])
        self.assertIsNone(o["send_at"])
        self.foundry("decide", "answer", d["card"], "post", "--by", "owner")  # idempotent
        self.assertEqual(len(self.outbox()), 1)

    def test_an_answer_inside_quiet_hours_sends_at_eight(self):
        d = self.draft()
        _, a = self.at("2026-11-18T23:00:00+09:00", "decide", "answer", d["card"], "post", "--by", "foreman",
                       "--quote", "일보 보내줘")
        self.assertEqual(a["send_at"], "2026-11-19T08:00+09:00")
        [o] = self.foundry("outbox", "list")[1]
        self.assertFalse(o["due"])

    def test_hold_files_nothing_and_a_newer_draft_supersedes_the_pending_one(self):
        first = self.draft()
        self.foundry("report", "slack-add", "--channel", "team", "--permalink",
                     "https://example.slack.com/archives/C0TESTCHAN1/p1731900000123456", "--summary", "공유")
        second = self.draft()
        self.assertNotEqual(first["card"], second["card"])
        snap = self.snap()
        self.assertEqual(snap["cards"][first["card"]]["answer"], "moot")
        self.assertEqual(snap["reports"][first["draft"]]["state"], "superseded")
        self.assertEqual(self.draft()["card"], second["card"])  # same content: the same card
        _, a = self.foundry("decide", "answer", second["card"], "hold", "--by", "owner")
        self.assertEqual(a["state"], "held")
        self.assertEqual(self.outbox(), [])


class ReviewGaps(Base):
    def test_gaps_per_reviewer(self):
        _, out = self.foundry("report", "review-gaps", "--json")
        kinds = {who: [(g["ref"], g["kind"]) for g in xs] for who, xs in out["reviewers"].items()}
        self.assertEqual(kinds, {"alice": [("example-org/app#7", "stale_request")],
                                 "(미지정)": [("example-org/app#8", "no_reviewer")],
                                 "bob": [("example-org/app#9", "dismissed")],
                                 "(오너 대응)": [("example-org/app#9", "bot_unresolved")]})

    def test_remind_replaces_queued_asks_sends_at_nine_and_once_per_head(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        _, ask = self.foundry("outbox", "add", "alpha", "--channel", CH, "--kind", "review-ask", "--text",
                              f"<@{ALICE}> example-org/app#7 のレビューをお願いします")
        _, d = self.foundry("report", "review-gaps", "--remind", "--draft")
        self.assertEqual([i["login"] for i in d["reminders"]], ["alice"])
        self.assertIn("bob: example-org/app#9 (Slack id 없음)", d["skipped"])
        text = d["reminders"][0]["text"]
        self.assertTrue(text.startswith(f"<@{ALICE}>\n\n"))
        self.assertNotIn(FOOT, text)
        self.assertEqual(report.slack_lint([text]), [])
        self.assertEqual([o["oid"] for o in self.outbox()], [ask["oid"]])  # nothing replaced before the answer
        _, a = self.foundry("decide", "answer", d["card"], "post", "--by", "owner")
        self.assertEqual(a["send_at"], "2026-11-19T09:00+09:00")
        self.assertEqual(a["superseded"], [ask["oid"]])
        [o] = self.outbox()
        self.assertEqual((o["kind"], o["to"], o["prs"], o["send_at"]), ("review-remind", [ALICE], ["example-org/app#7"],
                                                                         "2026-11-19T09:00+09:00"))
        _, again = self.foundry("report", "review-gaps", "--remind", "--json")
        self.assertEqual(again["reminders"], [])
        self.assertIn("alice: example-org/app#7 (이미 리마인드)", again["skipped"])


class ReleaseGaps(Base):
    def test_unreleased_and_dev_only_with_age(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.rec("2026-11-17T10:00:00+09:00", "pr.merged", unit="alpha", repo="example-org/app", number=5)
        self.rec("2026-11-16T10:00:00+09:00", "pr.merged", unit="alpha", repo="example-org/app", number=4)
        self.rec("2026-11-16T12:00:00+09:00", "pr.released", unit="alpha", repo="example-org/app", number=4, tag="v1.2.0")
        self.rec("2026-11-16T14:00:00+09:00", "deploy.done", unit="alpha", app="app", env="development", tag="v1.2.0")
        _, out = self.foundry("report", "release-gaps", "--json")
        got = [(r["ref"], r["gap"], r["age_h"]) for r in out["gaps"]]
        self.assertEqual(got, [("example-org/app#4", "dev_only", 44), ("example-org/app#5", "unreleased", 24)])
        self.assertEqual(out["propose"], ["example-org/app"])
        self.rec("2026-11-17T14:00:00+09:00", "deploy.done", unit="alpha", app="app", env="production", tag="v1.2.1")
        _, out = self.foundry("report", "release-gaps", "--json")
        self.assertEqual([r["ref"] for r in out["gaps"]], ["example-org/app#5"])  # a later prod tag covers v1.2.0


class Prefs(unittest.TestCase):
    def test_defaults_carry_the_report_block_and_both_schedules(self):
        import prefs
        p = prefs.defaults()
        self.assertEqual(p["report"]["title"], "[foundry] {user} 일보")
        names = {s["name"]: s["cron"] for s in p["schedules"]}
        self.assertEqual((names["daily-report"], names["review-remind"]), ("30 20 * * 1-5", "0 8 * * 1-5"))
        self.assertEqual(prefs.validate(prefs.merge(p, {"report": {"remind_time": "9시"}})),
                         ["report.remind_time: must look like 09:00"])
        self.assertTrue(prefs.validate(prefs.merge(p, {"report": {"sections": ["nope"]}})))
        self.assertEqual(p["slack"]["footer"], "🤖 Claude Code が作成")
        self.assertTrue(prefs.validate(prefs.merge(p, {"slack": {"footer": " "}})))
        self.assertTrue(all("--model" not in s["run"] for s in p["schedules"]))


if __name__ == "__main__":
    unittest.main()
