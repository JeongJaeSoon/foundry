"""0.1.26: the owner-facing board — Korean headlines, a PR tab, a unit detail that says what is next — and waits
that follow the PR (a registered review wait, a CI wait re-pinned to the head)."""

import os
import sys
import unittest

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import aging  # noqa: E402
import views  # noqa: E402

REF = "o/r#5"
HEAD = "6111840527aa00bb11cc22dd33ee44ff55667788"
OLD = "b42c0be17aa00bb11cc22dd33ee44ff556677889"


class _Clock(tf.Base):
    def setUp(self):
        super().setUp()
        self.at("2026-11-18T10:00:00+09:00")
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")

    def at(self, ts):
        self.env["FOUNDRY_NOW"] = ts
        os.environ["FOUNDRY_NOW"] = ts

    def tearDown(self):
        os.environ.pop("FOUNDRY_NOW", None)
        super().tearDown()

    def board(self):
        return self.foundry("board", "--format", "json")[1]

    def row(self, uid="alpha"):
        return next(r for s in self.board()["sections"] for r in s["rows"] if r["id"] == uid)

    def pr(self, created="2026-11-18T09:00:00+09:00", **f):
        self.record("pr.state", unit="alpha", repo="o/r", number=5, state="OPEN", title="Facets",
                    created=created, head=HEAD, **f)


class Headlines(_Clock):
    def test_a_decision_is_one_korean_line_and_decide_show_has_the_rest(self):
        self.foundry("decision", "ask", "--key", "alpha:merge", "--unit", "alpha",
                     "--question", "Merge now? The long context with gh pr merge 5 -R o/r and a Datadog query follows.", ok=(10,))
        m = self.board()
        hl = m["asks"][0]["headline"]
        self.assertEqual(hl, "Merge now? · 답 필요 · 막는 것: alpha 진행")
        self.assertEqual(m["asks"][0]["detail_cmd"], "foundry decide show alpha:merge")
        shown = self.foundry("decide", "show", "alpha:merge", "--format", "json")[1]
        self.assertIn("Datadog query", shown["question"])
        self.foundry("decide", "show", "nope", ok=(2,))

    def test_headline_names_the_recommendation(self):
        card = {"unit": "alpha", "question": "Merge now?\nMore lines.", "recommended": "B",
                "options": [{"key": "A", "label": "wait"}, {"key": "B", "label": "merge"}]}
        self.assertEqual(views.headline(card, {"units": {"alpha": {"status": "in_progress"}}}), "Merge now? · 추천 B (merge) · 막는 것: alpha")

class PrTab(_Clock):
    def test_rows_carry_state_reviewer_and_the_next_action_with_its_owner(self):
        self.pr(review="REVIEW_REQUIRED", reviewers=["alice"], auto_reviewers=[])
        rows = self.board()["pr_tab"]
        self.assertEqual([(r["ref"], r["state"], r["reviewer"], r["who"]) for r in rows], [(REF, "review", "alice", "reviewer")])
        self.assertEqual(rows[0]["action"], "리뷰")
        self.at("2026-11-19T12:00:00+09:00")
        r = self.board()["pr_tab"][0]  # a business day on, with no ask thread: foundry files the ask
        self.assertEqual((r["action"], r["who"]), ("리뷰 의뢰 올리기", "foundry"))
        self.pr(review="APPROVED", ci="success")
        r = self.board()["pr_tab"][0]
        self.assertEqual((r["state"], r["state_ko"], r["action"], r["who"]), ("merge-wait", "머지 대기", "머지", "foundry"))

    def test_board_tab_pr_prints_one_line_per_pr_and_the_main_board_only_a_count(self):
        self.pr(review="REVIEW_REQUIRED", reviewers=["alice"], auto_reviewers=[])
        import board
        m = self.board()
        tab = views.pr_table_text(m["pr_tab"])
        self.assertEqual(len(tab.splitlines()), 2)
        self.assertIn("alice", tab)
        self.assertIn("리뷰 (리뷰어)", tab)
        main = board.text_of(m)
        self.assertIn("PR 1 · 리뷰 대기 1  → foundry board --tab pr", main)
        self.assertIn("alice", board.pr_tab_text(m))  # reviewer load lives on the PR tab only
        self.assertNotIn("o/r#5 ", main)


class UnitDetail(_Clock):
    def test_tracker_falls_back_to_tickets_and_the_header_is_korean(self):
        self.foundry("unit", "keys", "alpha", "--ticket", "PROJ-787")
        v = self.row()["view"]
        self.assertEqual((v["status_line"], v["who_ko"], v["tracker"]), ("대기열", "foundry", "PROJ-787"))
        self.assertEqual(v["next"], "착수 대기")
        self.assertIsNone(v["chain"])  # no `chain: (this)` for a unit with nothing before or after it

    def test_a_merge_approval_is_not_ready_while_ci_is_pending_and_a_stale_wait_is_marked(self):
        self.pr(review="REVIEW_REQUIRED", ci="pending", merge_state="BLOCKED", reviewers=["alice"], auto_reviewers=[])
        self.foundry("wait", "add", "alpha", "--kind", "ci", "--ref", REF, "--note", f"head {OLD[:9]}")
        row = self.row()
        prs = (self.snap()["units"]["alpha"].get("prs") or [])
        a = {"rid": "r1", "tool": "Bash", "summary": f"Bash: Bash: gh pr merge 5 -R o/r --squash --match-head-commit {HEAD}"}
        ready, why = views.readiness(a, prs)
        self.assertFalse(ready)
        self.assertTrue(any("CI pending" in w for w in why) and any("리뷰" in w for w in why))
        self.assertEqual(views._cmd_of(a), f"gh pr merge 5 -R o/r --squash --match-head-commit {HEAD}")
        w = next(w for w in row["view"]["waits"] if w["kind"] == "ci")
        self.assertTrue(w["stale"])
        self.assertIn(f"{OLD[:7]} ≠ head {HEAD[:7]}", w["stale_text"])

    def test_owner_todos_and_dispatcher_requests_are_lifted_out_of_the_notes(self):
        for _ in range(2):  # the same report twice is one timeline line
            self.foundry("unit", "report", "alpha", "--text",
                         f"Pushed {HEAD}. Datadog facets need owner to create api_key_group_id. "
                         "Datadog query still to be posted on Jira via dispatcher.")
        v = self.row()["view"]
        self.assertTrue(any("api_key_group_id" in t for t in v["owner_todos"]))
        self.assertTrue(any("dispatcher" in t for t in v["dispatcher"]))
        self.assertEqual(len(v["timeline"]), 1)
        self.assertEqual(v["timeline"][0]["gist"], f"Pushed {HEAD[:7]}.")
        self.assertNotIn(HEAD, v["timeline"][0]["text"])

    def test_stopped_hands_are_a_count(self):
        for n in ("impl", "advisor"):
            self.record("hand.spawn", unit="alpha", name=n, role="hand", tool_use_id=f"t-{n}")
            self.record("hand.state", key=f"t-{n}", state="stopped")
        self.record("hand.spawn", unit="alpha", name="fixer", role="hand", tool_use_id="t-fixer")
        v = self.row()["view"]
        self.assertEqual(([h["name"] for h in v["hands_running"]], v["hands_stopped"]), (["fixer"], 2))


class WaitRepairs(_Clock):
    def viewed(self, **pr):
        base = {"state": "OPEN", "isDraft": False, "headRefOid": HEAD, "reviewDecision": "REVIEW_REQUIRED",
                "reviewRequests": [{"login": "carol-gh"}]}
        return {REF: ("alpha", base | pr)}

    def test_a_missing_review_wait_is_registered_and_a_stale_sha_is_repinned(self):
        self.foundry("wait", "add", "alpha", "--kind", "ci", "--ref", REF, "--note", f"head {OLD[:9]}")
        recs, lines = aging.wait_repairs(self.snap(), self.viewed())
        ops = [(op, f.get("kind") or f.get("note")) for op, f in recs]
        self.assertIn(("wait.add", "pr-review"), ops)
        self.assertIn(("wait.repin", f"head {HEAD[:7]}"), ops)
        for op, f in recs:
            self.record(op, **f)
        waits = {w["kind"]: w for w in self.snap()["units"]["alpha"]["waits"]}
        self.assertEqual(waits["pr-review"]["note"], "review by carol-gh")
        self.assertEqual(waits["ci"]["note"], f"head {HEAD[:7]}")
        self.assertEqual(aging.wait_repairs(self.snap(), self.viewed()), ([], []))  # idempotent

    def test_nothing_for_an_approved_or_draft_pr_or_a_unit_that_is_not_waiting(self):
        self.foundry("wait", "add", "alpha", "--kind", "ci", "--ref", REF, "--note", "ci")
        self.assertEqual(aging.wait_repairs(self.snap(), self.viewed(reviewDecision="APPROVED")), ([], []))
        self.assertEqual(aging.wait_repairs(self.snap(), self.viewed(isDraft=True)), ([], []))
        self.foundry("unit", "status", "alpha", "done", "--note", "shipped")
        self.assertEqual(aging.wait_repairs(self.snap(), self.viewed()), ([], []))


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (Headlines, PrTab, UnitDetail, WaitRepairs):
        suite.addTests(cls(n) for n in sorted(vars(cls)) if n.startswith("test_"))
    return suite
