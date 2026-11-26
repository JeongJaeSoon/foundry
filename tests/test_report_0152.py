"""0.1.52: the daily report is a short parent message (counts, 오너 대기 still open at render time, the bare footer
after a blank line) plus one thread reply per project group; every PR appears once, by priority 오너 대기 > 릴리스
누락 > 리뷰 누락 > group; bot and bump PRs collapse into one count; review requests to the owner are a count with
links only when few."""

import re

import test_report_0150 as base
from test_report_0150 import DAY, FOOT, row

APP = "example-org/app"


def bot_row(num, login, typ="User"):
    r = row(APP, num, f"auto {num}")
    r["author"] = {"login": login, "type": typ, "is_bot": False}
    return r


GH = {
    **base.GH,
    f"search prs --author @me --updated >={DAY}": [row(APP, 6, "Facets API"), row(APP, 9, "Held by a card"),
                                                   row("other-org/tool", 3, "Tool fix")],
    f"search prs --author @me --merged-at >={DAY}": [row(APP, 4, "Old merge", "merged"), row(APP, 10, "Shipped", "merged")],
    f"search prs --involves @me --updated >={DAY}": [bot_row(20, "dependabot[bot]", "Bot"), bot_row(21, "release-bot"),
                                                     row(APP, 22, "Someone else's"), row(APP, 31, "And this"),
                                                     row("other-org/app", 6, "Same name and number")],
    "search prs --review-requested @me --state open": [row(APP, 30, "Please look"), row(APP, 31, "And this")],
    "search prs --author @me --state open": [row(APP, 6, "Facets API")],
    f"api graphql {APP}#6": base.pr(),
}


class Layout(base.Base):
    def setUp(self):
        super().setUp()
        self.gh(GH)
        tk = lambda t: ["--ticket", t]  # noqa: E731
        self.foundry("unit", "new", "--id", "u-a", "--title", "Facets", *tk("PROJ-1"))
        self.foundry("unit", "new", "--id", "u-b", "--title", "Collector", *tk("PROJ-1"), "--force")
        self.foundry("unit", "new", "--id", "u-c", "--title", "Loose")
        for uid, num in (("u-a", 4), ("u-a", 9), ("u-b", 6), ("u-b", 10)):
            self.rec(f"{DAY}T07:{num:02d}:00+09:00", "pr.state", unit=uid, repo=APP, number=num, state="OPEN", title="t")
        self.rec(f"{DAY}T08:04:00+09:00", "pr.merged", unit="u-a", repo=APP, number=4)
        self.rec(f"{DAY}T08:10:00+09:00", "pr.merged", unit="u-b", repo=APP, number=10)
        self.rec(f"{DAY}T08:30:00+09:00", "pr.released", unit="u-b", repo=APP, number=10, tag="v1.0.0")
        _, c = self.foundry("decide", "open", "--question", "오늘 머지할까요?", "--option", "A:오늘",
                            "--option", "B:내일", "--asked-by", "lead", "--key", "u-a:merge", "--unit", "u-a",
                            "--ref", f"pr:{APP}#9")
        self.card = c["card"]["id"]
        _, done = self.foundry("decide", "open", "--question", "이미 답한 질문", "--option", "A:예", "--option", "B:아니오",
                               "--asked-by", "lead", "--key", "u-c:old", "--unit", "u-c")
        self.foundry("decide", "answer", done["card"]["id"], "A", "--by", "owner")
        self.done_card = done["card"]["id"]

    def messages(self):
        _, out = self.foundry("report", "daily", "--format", "slack")
        return [m["text"] for m in out["messages"]]

    def test_the_parent_is_counts_owner_wait_and_the_bare_footer(self):
        parent = self.messages()[0].split("\n")
        self.assertEqual(parent[:4], [":kyouyuu-1:", "", "", f"**[foundry] tester 일보 ({DAY})**"])
        self.assertNotIn(FOOT, parent)  # 0.1.87: the Slack hook appends the footer at send
        self.assertTrue(any(ln.endswith("결정 1 · 오너 대기 0") for ln in parent), parent)  # 0.1.88
        self.assertIn("오늘 릴리스 1", parent)
        # The card's text never names the PR: the line names it from the card's refs, so it shows exactly once.
        self.assertIn(f"결정 · PROJ-1 · 오늘 머지할까요? · <https://github.com/{APP}/pull/9|{APP}#9> ({self.card})", parent)
        self.assertFalse(any(self.done_card in ln for ln in parent))  # answered: not waiting any more
        self.assertFalse(any(ln.lstrip().startswith("- ") for ln in parent))  # no list: it would swallow the appended footer

    def test_groups_by_ticket_then_unit_then_repo(self):
        replies = {m.split("\n")[0]: m for m in self.messages()[1:]}
        self.assertIn("**PROJ-1 · Facets**", replies)  # u-a and u-b share PROJ-1: one group, named by its first unit
        self.assertIn(f"{APP}#10> Shipped — 릴리스 v1.0.0", replies["**PROJ-1 · Facets**"])  # its latest state
        self.assertIn("**other-org/tool**", replies)  # no unit: its repo
        self.assertNotIn("**u-b · Collector**", replies)

    def test_every_pr_appears_once_by_priority(self):
        msgs = self.messages()
        text = "\n".join(msgs)
        for num in (4, 6, 10):  # each PR link exactly once across the parent and every reply
            self.assertEqual(len(re.findall(rf"\|{re.escape(APP)}#{num}>", text)), 1, num)
        replies = {m.split("\n")[0]: m for m in msgs[1:]}
        self.assertIn(f"{APP}#4", replies["**릴리스 누락**"])  # unreleased: there, not in its group
        self.assertNotIn(f"{APP}#9", "\n".join(msgs[1:]))  # the owner's open card holds it: nowhere else
        self.assertIn(f"{APP}#6", replies["**리뷰 누락**"])
        self.assertNotIn(f"{APP}#6", replies["**PROJ-1 · Facets**"])

    def test_bots_collapse_and_review_requests_are_a_count_with_few_links(self):
        text = "\n".join(self.messages())
        self.assertNotIn(f"{APP}#20", text)
        self.assertNotIn(f"{APP}#21", text)
        self.assertIn("자동 PR 2건 (봇·버전 bump)", text)
        self.assertIn("관여만 한 PR 2건", text)
        self.assertIn("내가 리뷰할 PR 2건", text)  # #31 is also an involved row: still a request, counted once
        self.assertIn(f"|{APP}#30>", text)

    def test_the_md_preview_is_the_same_split_and_the_draft_passes_the_lint(self):
        _, md = self.foundry("report", "daily", "--json")
        msgs = self.messages()
        self.assertEqual(md["text"].count("--- 스레드 답글 "), len(msgs) - 1)
        from report import slack_lint
        self.assertEqual(slack_lint([{"text": t, "reply": i > 0} for i, t in enumerate(msgs)]), [])

    def test_two_orgs_with_the_same_repo_name_and_number_stay_two_prs(self):
        text = "\n".join(self.messages())
        self.assertIn(f"|{APP}#6>", text)
        self.assertNotIn("other-org/app#6", text)  # involved only, no unit: counted, not merged into APP#6
        self.assertIn("관여만 한 PR 2건", text)

    def test_loose_slack_lines_are_one_per_channel_and_a_ticket_pulls_a_line_into_its_group(self):
        link = "https://example.slack.com/archives/C0TESTCHAN1/p17319000001234{:02d}"
        for n, (ch, text) in enumerate([("team", "PROJ-1 진행 공유"), ("misc", "잡담 하나"), ("misc", "잡담 둘")]):
            self.foundry("report", "slack-add", "--channel", ch, "--permalink", link.format(n), "--summary", text)
        replies = {m.split("\n")[0]: m for m in self.messages()[1:]}
        self.assertIn("PROJ-1 진행 공유", replies["**PROJ-1 · Facets**"])
        self.assertIn("|#misc> 2건 · 잡담 하나", replies["**その他**"])
        self.assertNotIn("잡담 둘", replies["**その他**"])

    def test_no_message_reads_as_a_review_ask_to_the_send_hook(self):
        import slackguard
        self.assertEqual([slackguard.kind_of(t) for t in self.messages() if slackguard.kind_of(t) == "review-ask"], [])
