"""0.1.28: the PR tab names reviewers from the PR alone and keeps the next action a fixed phrase; a merged PR is
never an adopt-or-drop; a merge request for a PR that already merged is withdrawn by the github cycle; the cards
foundry opens itself are Korean; a finished unit's worktree is reclaimed by the sweep, or says why it stays."""

import json
import os
import subprocess
import sys
import unittest

import test_foundry as tf
import test_sweep as ts

sys.path.insert(0, tf.SCRIPTS)
import views  # noqa: E402

REF = "o/r#5"
HEAD = "6111840527aa00bb11cc22dd33ee44ff55667788"
NOTE = "Dev-only PR. CI is green; waiting for carol to look before the window"


class _Clock(tf.Base):
    def setUp(self):
        super().setUp()
        self.at("2026-11-18T10:00:00+09:00")
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")

    def at(self, ts_):
        self.env["FOUNDRY_NOW"] = ts_
        os.environ["FOUNDRY_NOW"] = ts_

    def tearDown(self):
        os.environ.pop("FOUNDRY_NOW", None)
        super().tearDown()

    def pr(self, **f):
        self.record("pr.state", unit="alpha", repo="o/r", number=5, title="Facets",
                    created="2026-11-18T09:00:00+09:00", head=HEAD, **{"state": "OPEN", **f})

    def tab(self):
        return self.foundry("board", "--format", "json")[1]["pr_tab"]

    def cards(self, prefix):
        return {c["key"]: c for c in (self.snap().get("cards") or {}).values() if (c.get("key") or "").startswith(prefix)}


class PrTabRows(_Clock):
    def test_a_wait_note_never_becomes_the_reviewer_or_the_action(self):
        self.foundry("wait", "add", "alpha", "--kind", "pr-review", "--ref", REF, "--note", NOTE)
        self.pr(review="REVIEW_REQUIRED", reviewers=[])
        (r,) = self.tab()
        self.assertEqual((r["reviewer"], r["action"], r["who_ko"]), ("-", "리뷰", "리뷰어"))
        text = views.pr_table_text([r])
        self.assertNotIn("Dev-only", text)
        self.assertEqual(len(text.splitlines()), 2)

    def test_requested_reviewers_then_approvers(self):
        self.pr(review="REVIEW_REQUIRED", reviewers=["dave"], auto_reviewers=[])
        self.assertEqual(self.tab()[0]["reviewer"], "dave")
        # 0.1.31: a reviewer nobody can show the owner asked (no timeline, not in the pool) rides along, dim
        self.pr(review="REVIEW_REQUIRED", reviewers=["cloud-infra-reviewer"], auto_reviewers=["cloud-infra-reviewer"])
        self.assertEqual((self.tab()[0]["reviewer"], self.tab()[0]["reviewer_auto"]), ("-", ["cloud-infra-reviewer"]))
        self.pr(review="APPROVED", reviewers=[], approvers=["bob"], ci="pending")
        r = self.tab()[0]
        self.assertEqual((r["state_ko"], r["reviewer"], r["action"]), ("승인 · CI 대기", "✓bob", "CI 통과 대기"))

    def test_a_merged_pr_of_a_done_unit_is_not_adopt_or_drop(self):
        self.pr(review="APPROVED", reviewers=[])
        self.record("pr.merged", unit="alpha", repo="o/r", number=5)
        self.foundry("unit", "status", "alpha", "done", "--note", "shipped")
        self.at("2026-11-19T12:00:00+09:00")
        self.assertNotIn(REF, self.foundry("pr", "age", "--dry-run")[1]["actions"])
        self.foundry("pr", "age")
        self.assertEqual(self.cards("pr-orphan:"), {})
        for r in self.tab():
            self.assertNotIn("편입", r["action"])

    def test_the_github_stream_records_approvers_and_a_reviewer_change_is_seen(self):
        from unittest import mock
        import foundrylib as cl
        import prefs
        import watch
        import watchers
        self.foundry("unit", "keys", "alpha", "--pr", REF)
        pr = {"state": "OPEN", "reviewDecision": "APPROVED", "statusCheckRollup": [], "headRefOid": HEAD,
              "headRefName": "b", "title": "Facets", "reviewRequests": [],
              "latestReviews": [{"author": {"login": "bob"}, "state": "APPROVED"},
                                {"author": {"login": "alice"}, "state": "COMMENTED"}]}
        keep = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            import review
            with mock.patch.object(watch, "poll_waits", return_value=([], [])), \
                    mock.patch.object(review, "refresh_others", return_value={}), \
                    mock.patch.object(review, "read_auto", return_value=[]):
                with mock.patch.object(watch, "gh_pr", return_value=pr):
                    watchers.run_cycle("github", prefs.load())
                with mock.patch.object(watch, "gh_pr", return_value={**pr, "reviewRequests": [{"login": "dave"}]}):
                    watchers.run_cycle("github", prefs.load())
            states = [r for r in cl.read_journal() if r["op"] == "pr.state"]
        finally:
            os.environ["FOUNDRY_STATE_DIR"] = keep or ""
            if keep is None:
                os.environ.pop("FOUNDRY_STATE_DIR")
        self.assertEqual([(r["approvers"], r["reviewers"]) for r in states], [(["bob"], []), (["bob"], ["dave"])])
        self.assertEqual(self.tab()[0]["reviewer"], "dave")


class MootMergeRequests(_Clock):
    def ask(self, rid, cmd):
        self.record("approval.request", rid=rid, key=rid, unit="alpha", tool="Bash", summary=f"Bash: {cmd}", tier=3)

    def test_a_merge_request_for_a_merged_or_closed_pr_is_withdrawn_once(self):
        self.ask("ap-1", f"gh pr merge 5 -R o/r --squash --match-head-commit {HEAD}")
        self.ask("ap-2", "gh pr merge 6 -R o/r --squash")
        self.ask("ap-3", "gh pr merge -R o/r 7 --merge")
        self.pr(review="APPROVED")
        self.record("pr.state", unit="alpha", repo="o/r", number=7, state="CLOSED", title="x")
        self.foundry("pr", "age")
        self.assertEqual(self.snap()["approvals"]["ap-1"]["answer"], None)  # still open: nothing to withdraw
        self.record("pr.merged", unit="alpha", repo="o/r", number=5)
        self.foundry("pr", "age")
        self.foundry("pr", "age")
        aps = self.snap()["approvals"]
        self.assertEqual((aps["ap-1"]["answer"], aps["ap-1"]["by"]), ("deny", "github"))
        self.assertIn("이미 머지됨", aps["ap-1"]["reason"])
        self.assertIn("닫힘", aps["ap-3"]["reason"])
        self.assertIsNone(aps["ap-2"]["answer"])  # #6 was never seen merged
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            answers = [r for r in map(json.loads, filter(str.strip, f)) if r.get("op") == "approval.answer"]
        self.assertEqual(sorted(r["rid"] for r in answers), ["ap-1", "ap-3"])


class KoreanCards(_Clock):
    def test_orphan_and_near_miss_cards_are_korean(self):
        self.pr(review="REVIEW_REQUIRED", reviewers=["alice"])
        self.foundry("unit", "status", "alpha", "done", "--note", "shipped")
        self.at("2026-11-19T12:00:00+09:00")
        self.foundry("pr", "age")
        c = self.cards("pr-orphan:")[f"pr-orphan:{REF}"]
        self.assertIn("맡은 unit 이 없습니다", c["question"])
        self.assertEqual([o["label"] for o in c["options"]][1], "보드에서 제외")
        for u in ("u1", "u2", "u3"):
            self.record("nearmiss", kind="weird", unit=u, detail="odd thing")
        self.foundry("nearmiss", "triage")
        n = self.cards("nearmiss:")["nearmiss:weird:other"]
        self.assertIn("3번 반복", n["question"])
        self.assertEqual([o["key"] for o in n["options"]], ["fixed", "guard-fp", "ack"])
        self.assertTrue(all(any("가" <= ch <= "힣" for ch in o["label"]) for o in n["options"]))


class Reclaim(ts.RepoBase):
    def setUp(self):
        super().setUp()
        for uid, b, status in (("a", "merged-clean", "done"), ("b", "merged-dirty", "done"),
                               ("c", "open-clean", "cancelled")):
            self.unit(uid, status)
            self.rec("unit.worktree", id=uid, repo="o/r", path=os.path.join(self.dir, b), branch=b)
        # c: a commit on no remote, its PR open
        self.git("commit", "-q", "--allow-empty", "-m", "wip", cwd=os.path.join(self.dir, "open-clean"))

    def branches(self):
        out = subprocess.run(["git", "-C", self.repo, "branch", "--format=%(refname:short)"], capture_output=True, text=True)
        return set(out.stdout.split())

    def view(self, uid):
        m = self.foundry("board", "--format", "json")[1]
        return next(r for s in m["sections"] for r in s["rows"] if r["id"] == uid)["view"]

    def test_finished_units_worktree_goes_with_its_branch_and_the_rest_say_why(self):
        self.script("watch.py", "poll")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "merged-clean")))
        self.assertNotIn("merged-clean", self.branches())
        self.assertTrue(os.path.exists(os.path.join(self.dir, "merged-dirty", "wip.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "open-clean")))
        self.assertEqual(self.view("a")["worktree_note"], "회수됨")
        self.assertEqual(self.view("b")["worktree_note"], "남김: 커밋하지 않은 변경 / untracked 파일 있음")
        self.assertEqual(self.view("c")["worktree_note"], "남김: 원격에 없는 커밋 1개 (PR OPEN)")
        done = [r for r in self.journal_ops() if r["op"] == "hk.done"]
        self.assertEqual(len(done), 1)
        self.assertIn("branch deleted", done[0]["result"])

    def test_a_live_lead_or_a_cwd_keeps_it(self):
        self.lead("a", "running")
        self.script("watch.py", "poll")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "merged-clean")))
        self.assertEqual(self.view("a")["worktree_note"], "남김: lead 세션이 아직 살아 있음")
        self.rec("session.state", name="lead-a", state="stopped")
        self.env["FAKE_LSOF_CWDS"] = os.path.join(self.dir, "merged-clean")
        self.script("watch.py", "poll")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "merged-clean")))
        self.assertEqual(self.view("a")["worktree_note"], "남김: 프로세스가 이 worktree 를 쓰는 중")

    def test_a_running_unit_is_never_touched(self):
        self.rec("unit.status", id="a", status="in_progress")
        self.script("watch.py", "poll")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "merged-clean")))
        self.assertIsNone(self.view("a").get("worktree_note"))


if __name__ == "__main__":
    unittest.main()
