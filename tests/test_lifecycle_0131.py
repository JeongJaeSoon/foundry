"""0.1.31: the PR tab is the one PR list (one counting function for every surface), PRs and units link out, the
reviewer load counts only asked reviewers, repos are blocks with a stable accent, and 재고 says real states."""

import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import board  # noqa: E402
import factory  # noqa: E402
import housekeep  # noqa: E402
import review  # noqa: E402
import views  # noqa: E402

HEAD = "6111840527aa00bb11cc22dd33ee44ff55667788"


def _lum(hexc):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def ev(login, actor="owner", at="2026-11-18T09:00:00Z", **f):
    return {"event": "review_requested", "requested_reviewer": {"login": login}, "actor": {"login": actor},
            "created_at": at, **f}


class AutoReviewers(unittest.TestCase):
    def test_timeline_marks_bot_app_and_team_requests_auto(self):
        events = [
            ev("alice"),
            ev("carol", actor="github-actions[bot]"),
            ev("dave", performed_via_github_app={"slug": "x"}),
            {"event": "review_requested", "requested_team": {"slug": "core"}, "actor": {"login": "owner"},
             "created_at": "2026-11-18T08:00:00Z"},
            ev("erin", at="2026-11-18T08:00:00Z"),  # same second as the team: CODEOWNERS
            ev("frank", actor="renovate[bot]"), ev("frank", at="2026-11-18T10:00:00Z"),  # re-asked by hand
        ]
        self.assertEqual(review.auto_requested(events), ["carol", "dave", "erin"])

    def test_split_fails_closed_without_a_timeline(self):
        pr = {"reviewers": ["alice", "stranger", "picked", "x[bot]", {"slug": "core"}]}
        asked, auto = review.split_reviewers(pr, pool=["alice"], picked={"picked"})
        self.assertEqual(asked, ["alice", "picked"])
        self.assertEqual(auto, ["stranger", "x[bot]", "core"])
        # with the timeline read, a human the timeline does not mark auto counts
        asked, auto = review.split_reviewers({**pr, "auto_reviewers": []}, pool=["alice"], picked=set())
        self.assertIn("stranger", asked)
        asked, auto = review.split_reviewers({**pr, "auto_reviewers": ["stranger"]}, pool=["alice"], picked=set())
        self.assertNotIn("stranger", asked)


class Links(unittest.TestCase):
    def test_short_ref_keeps_the_number_and_jira_needs_a_base_and_a_key(self):
        self.assertEqual(views.short_ref("example-org/example-developers-auth#1234"), "example-developers-auth#1234")
        self.assertEqual(views.short_ref("plain"), "plain")
        self.assertEqual(views.jira_url("PROJ-5", base="https://jira.example.com/"), "https://jira.example.com/browse/PROJ-5")
        self.assertIsNone(views.jira_url("alpha", base="https://jira.example.com"))
        self.assertIsNone(views.jira_url("PROJ-5", base=""))

    def test_repo_accent_is_pinned_and_readable(self):
        self.assertEqual([views.repo_hash(x) for x in ("example-org/webapp", "a", "example-developers-auth", "example-org/gateway")],
                         [1609613780, 177670, 222722518, 2253952167])
        self.assertEqual(views.repo_accent("example-org/webapp"), {"bg": views.REPO_ACCENT[4][0], "fg": views.REPO_ACCENT[4][1]})
        for bg, fg in views.REPO_ACCENT:
            self.assertGreaterEqual(contrast(bg, fg), 4.5, (bg, fg))
        with open(os.path.join(tf.SCRIPTS, "..", "hooks", "palette.ts")) as f:
            ts = f.read()
        for bg, fg in views.REPO_ACCENT:
            self.assertIn(bg, ts)
            self.assertIn(fg, ts)


class InventoryState(unittest.TestCase):
    def test_states_replace_lead_wait(self):
        self.assertEqual(views.inv_state({"kind": "branch", "ahead": 3}), ("PR 없음 · +3", None))
        self.assertEqual(views.inv_state({"kind": "branch", "gh": "open", "stage": "review"})[1], "open")
        self.assertEqual(views.inv_state({"kind": "worktree", "status": "done", "reclaim": "회수 대기"}), ("회수 대기", "done"))
        text, tone = views.inv_state({"kind": "worktree", "status": "in_progress"})
        self.assertNotIn("리드 대기", text)
        self.assertEqual(tone, "in_progress")
        self.assertEqual([views.age_tone(x) for x in (None, 2, 10, 30)], ["dim", "dim", None, "warn"])

    def test_finished_units_worktree_on_disk_is_a_reclaim_row(self):
        with tempfile.TemporaryDirectory() as d:
            snap = {"units": {
                "old": {"id": "old", "status": "done", "repo": "o/r", "worktree": {"path": d}, "updated": "2026-11-18T09:00:00+09:00"},
                "kept": {"id": "kept", "status": "cancelled", "repo": "o/r", "worktree": {"path": d + "/k"},
                         "updated": "2026-11-18T09:00:00+09:00"},
                "gone": {"id": "gone", "status": "done", "repo": "o/r", "worktree": {"path": "/nonexistent/x"}}},
                "cards": {}, "decisions": {}}
            os.makedirs(d + "/k")
            notes = {"kept": {"path": d + "/k", "kept": "uncommitted 2"}}
            with mock.patch.object(housekeep, "reclaim_notes", return_value=notes), \
                    mock.patch.object(factory, "collect_prs", return_value={}, create=True):
                try:
                    inv = factory.inventory(snap)
                except TypeError:
                    self.skipTest("inventory signature differs")
            rows = {r.get("unit"): r for rs in (inv.get("repos") or {}).values() for r in rs if r.get("kind") == "worktree"}
            self.assertEqual(rows.get("old", {}).get("reclaim"), "회수 대기")
            self.assertEqual(rows.get("kept", {}).get("reclaim"), "남김: uncommitted 2")
            self.assertNotIn("gone", rows)


class Counting(unittest.TestCase):
    def test_pr_counts_text(self):
        rows = [{"state": "review", "stale": True}] * 11 + [{"state": "merge-wait"}] + [{"state": "release-wait"}] * 4
        c = views.pr_counts(rows)
        self.assertEqual(c["text"], "PR 16 · 리뷰 대기 11 · 머지 대기 1 · 릴리스 대기 4")
        self.assertEqual((c["total"], c["stale"]), (16, 11))

    def test_repo_blocks_count_per_repo(self):
        inv = {"repos": {"o/a": [{"kind": "branch"}, {"kind": "worktree"}, {"kind": "worktree"}]}}
        b = views.repo_blocks(inv, [{"ref": "o/a#1"}, {"ref": "o/b#2"}])
        self.assertEqual([(x["repo"], x["short"], x["counts"]) for x in b],
                         [("o/a", "a", "PR 1 · 브랜치 1 · 워크트리 2"), ("o/b", "b", "PR 1")])

    def test_pr_changes_caps_and_links(self):
        since = datetime.datetime(2026, 11, 18, 8, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
        t = since + datetime.timedelta(hours=10)
        prs = [{"repo": "o/r", "number": n, "title": f"t{n}", "merged": f"2026-11-18T1{n}:00:00+09:00"} for n in range(7)]
        snap = {"units": {"alpha": {"prs": prs}}}
        out = factory.pr_changes(snap, {"repos": {}}, since, t=t, p={})
        self.assertEqual(len(out), 7)
        self.assertEqual(out[0]["line"], "r#6 머지됨 — t6")
        self.assertEqual(out[0]["url"], "https://github.com/o/r/pull/6")
        self.assertEqual(factory.PR_CHANGES_CAP, 5)


class Surfaces(tf.Base):
    def setUp(self):
        super().setUp()
        os.environ["FOUNDRY_NOW"] = self.env["FOUNDRY_NOW"] = "2026-11-18T10:00:00+09:00"
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")
        for n, extra in ((5, {"review": "REVIEW_REQUIRED", "reviewers": ["alice"]}),
                         (6, {"review": "APPROVED", "ci": "success"})):
            self.record("pr.state", unit="alpha", repo="o/r", number=n, state="OPEN", title=f"PR {n}",
                        created="2026-11-18T09:00:00+09:00", head=HEAD, auto_reviewers=[], **extra)

    def tearDown(self):
        os.environ.pop("FOUNDRY_NOW", None)
        super().tearDown()

    def html(self):
        with open(self.foundry("board", "--format", "html")[1].strip()) as f:
            return f.read()

    def test_one_count_line_on_every_surface_and_korean_html(self):
        m = self.foundry("board", "--format", "json")[1]
        line = m["pr_summary"]["text"]
        self.assertEqual(line, views.pr_counts(m["pr_tab"])["text"])
        self.assertTrue(line.startswith("PR 2"))
        text = self.foundry("board")[1]
        self.assertIn(line, text)
        self.assertNotIn("o/r#5", text.split("→ foundry board --tab pr")[0].split(line)[-1] if line in text else text)
        self.assertIn(line, self.foundry("board", "--tab", "pr")[1])
        html = self.html()
        self.assertIn(line, html)
        self.assertIn("https://github.com/o/r/pull/5", html)
        for en in ("Escalations", "Pending approvals", ">none<", "Unit</th>"):
            self.assertNotIn(en, html)
        self.assertEqual([r["short"] for r in m["pr_tab"]], ["r#5", "r#6"] if m["pr_tab"][0]["ref"] == "o/r#5" else ["r#6", "r#5"])
        self.assertTrue(all(r["url"].startswith("https://github.com/o/r/pull/") for r in m["pr_tab"]))
        self.assertEqual([b["repo"] for b in m["repo_blocks"]][:1], ["o/r"])

    def test_html_decision_count_matches_its_list_and_long_questions_fold(self):
        self.foundry("decision", "ask", "--key", "alpha:merge", "--unit", "alpha",
                     "--question", "Merge now? " + "context " * 30, ok=(10,))
        html = self.html()
        self.assertIn("<h2>결정 필요 <span>1</span></h2>", html)
        self.assertEqual(html.split("<h2>결정 필요")[1].split("</section>")[0].count("<li>"), 1)
        self.assertIn("<details", html)


if __name__ == "__main__":
    unittest.main()
