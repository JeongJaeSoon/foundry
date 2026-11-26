"""0.1.18: waiting leads never hold a slot; review policy and asks; sync lag read from the queue."""

import json
import os
import sys
import unittest

import test_foundry as tf
import test_launcher as tl
import test_sync as ts
from test_foundry import SCRIPTS


class ParkWaitingLeads(tl.Launcher):
    def stream(self):
        _, out = self.foundry("watch", "stream", "--kind", "sessions", "--once")
        return [x.split("] ", 1)[-1] for x in (out or "").splitlines() if x] if isinstance(out, str) else []

    def idle_waiting_alpha(self):
        self.claude({"bg_id": "a1", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-alpha", "status": "idle"}]})
        self.foundry("unit", "launch", "alpha")
        self.record("wait.add", id="alpha", wid="w1", kind="pr-review", ref="example-org/app#1")
        self.foundry("unit", "status", "--refresh")
        self.assertEqual(self.snap()["sessions"]["lead-alpha"]["state"], "idle")

    def slots(self):
        sys.path.insert(0, SCRIPTS)
        import foundrylib
        return foundrylib.slot_holders(self.snap())

    def test_idle_waiting_lead_holds_no_slot(self):
        self.idle_waiting_alpha()
        self.assertEqual(self.slots(), [])
        self.config(worktree_root=self.wt_root, max_concurrent=1)
        self.claude({"bg_id": "b1"})
        self.foundry("unit", "launch", "beta")  # the cap counts working leads only
        self.assertEqual(self.slots(), ["boss-beta"])

    def test_parked_on_wait_and_resumed_when_it_clears(self):
        self.idle_waiting_alpha()
        lines = self.stream()
        self.assertTrue(any(x.startswith("parked alpha (waits pr-review example-org/app#1)") for x in lines), lines)
        self.assertIn(["stop", "a1"], [c["argv"] for c in self.calls()])
        sess = self.snap()["sessions"]["lead-alpha"]
        self.assertEqual(sess["state"], "stopped")
        self.assertTrue(sess["note"].startswith("parked: "))
        self.assertIn("Parked by foundry", self.snap()["units"]["alpha"]["handoff"]["next"])
        self.assertFalse([x for x in self.stream() if "alpha" in x])  # parked stays parked while the wait is open
        self.record("wait.clear", id="alpha", wid="w1")
        self.claude({"bg_id": "a2", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-copy", "status": "busy"}]})
        lines = self.stream()
        self.assertTrue(any(x.startswith("resumed alpha (its waits cleared)") for x in lines), lines)
        self.assertEqual(self.bg_calls()[-1]["argv"][:3], ["--bg", "--resume", "S-alpha"])

    def test_decision_card_parks_and_an_answer_wakes(self):
        self.claude({"bg_id": "a1", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-alpha", "status": "idle"}]})
        self.foundry("unit", "launch", "alpha")
        self.record("decision.ask", key="alpha:merge", unit="alpha", question="Merge now?")
        lines = self.stream()
        self.assertTrue(any(x.startswith("parked alpha (decision alpha:merge)") for x in lines), lines)
        self.record("decision.answer", key="alpha:merge", answer="yes")
        self.claude({"bg_id": "a2", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-alpha", "status": "busy"}]})
        lines = self.stream()
        self.assertTrue(any(x.startswith("resumed alpha (status in_progress)") for x in lines), lines)

    def test_pending_inbox_keeps_the_lead_up_and_an_owner_stop_is_not_resumed(self):
        self.idle_waiting_alpha()
        self.record("inbox.add", id="alpha", iid="i1", text="owner note")
        self.assertFalse([x for x in self.stream() if x.startswith("parked")])
        self.record("inbox.delivered", id="alpha", iids=["i1"])
        self.foundry("unit", "stop", "alpha", "--reason", "owner asked")
        self.record("wait.clear", id="alpha", wid="w1")
        self.assertFalse([x for x in self.stream() if x.startswith("resumed")])


class SyncLagFromQueue(ts.SyncBase):
    def lags(self):
        sys.path.insert(0, SCRIPTS)
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        import watchers
        import foundrylib
        return watchers.sync_lags(self.snap(), foundrylib.parse_ts(self.env["FOUNDRY_NOW"]))

    def test_a_closed_batch_clears_the_lag(self):
        self.unit("u1")
        self.at("10:00")
        self.rec("unit.status", id="u1", status="in_progress")
        self.foundry("sync", "enqueue", "u1")
        self.at("10:40")
        self.assertEqual(self.lags()["u1"]["jira"], 40)
        for b in {i["batch"] for i in self.drain()}:
            self.foundry("sync", "done", b)
        self.assertEqual(self.lags(), {})
        # An event stamped after the batch closed (source clock ahead): synced, so no lag — the old
        # last_event_at > last_synced_at rule reported this as behind with nothing queued.
        self.rec("sync.needed", unit="u1", target="jira", key="late", event="unit.status", ts="2026-11-18T10:55:00+09:00")
        for b in {i["batch"] for i in self.drain()}:
            self.foundry("sync", "done", b)
        self.assertEqual(self.snap()["units"]["u1"]["sync"]["jira"]["pending"], [])
        self.assertEqual(self.lags(), {})
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["sync_lagging"], [])
        _, out = self.foundry("watch", "stream", "--kind", "sync", "--once")
        self.assertNotIn("behind", out or "")


POOL = ("alice-gh", "bob-gh", "carol-gh", "dave-gh")


class ReviewPolicy(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "docs", "--title", "Docs overhaul", "--repo", "example-org/gateway")

    def github(self, loads, writers=POOL, author="owner-gh", **pr):
        data = {"example-org/gateway#713": {"state": "OPEN", "isDraft": False, "author": {"login": author}, "reviewRequests": [],
                                     "latestReviews": [], "assignees": [], "title": "docs overhaul",
                                     "headRefOid": "abc1234def", "url": "https://github.com/example-org/gateway/pull/713",
                                     "statusCheckRollup": []} | pr}
        for login in POOL:
            data[f"api search/issues?q=is:pr+is:open+user-review-requested:{login}+-author:@me+*"] = {
                "items": [{"user": {"login": f"someone{i}", "type": "User"}} for i in range(loads[login])]}
            data[f"api repos/example-org/gateway/collaborators/{login}"] = {}
            data[f"api repos/example-org/gateway/collaborators/{login}/permission"] = {
                "permission": "write" if login in writers else "read"}
        self.gh(data)

    def test_assign_picks_the_least_loaded_approver_as_reviewer_and_assignee_and_files_the_ask(self):
        self.github({"alice-gh": 4, "bob-gh": 1, "carol-gh": 0, "dave-gh": 2},
                    writers=("alice-gh", "bob-gh", "dave-gh"))
        _, out = self.foundry("review", "assign", "example-org/gateway#713", "--unit", "docs")
        self.assertEqual((out["reviewer"], out["assignee"]), ("bob-gh", "bob-gh"))  # carol: no approve rights
        item = self.snap()["outbox"][out["outbox"]]
        self.assertEqual((item["kind"], item["channel"], item["to"], item["prs"]),
                         ("review-ask", "C0000000001", ["U0000000002"], ["example-org/gateway#713"]))
        self.assertIn("abc1234", item["text"])
        # 0.1.26: assign registers the unit's review wait itself; no lead types a free-text pr-review wait.
        waits = [w for w in self.snap()["units"]["docs"]["waits"] if w["kind"] == "pr-review"]
        self.assertEqual([(w["ref"], w["note"]) for w in waits], [("example-org/gateway#713", "review by bob-gh")])
        _, again = self.foundry("review", "assign", "example-org/gateway#713", "--unit", "docs")
        self.assertEqual(again["outbox_status"], "exists")  # one ask per head

    def test_the_author_is_never_picked_and_an_existing_reviewer_is_kept(self):
        self.github({"alice-gh": 0, "bob-gh": 3, "carol-gh": 3, "dave-gh": 3}, author="alice-gh")
        _, out = self.foundry("review", "assign", "example-org/gateway#713", "--unit", "docs", "--dry-run")
        self.assertNotEqual(out["reviewer"], "alice-gh")
        self.github({x: 0 for x in POOL}, reviewRequests=[{"login": "dave-gh"}])
        _, out = self.foundry("review", "assign", "example-org/gateway#713", "--unit", "docs")
        self.assertEqual((out["existing"], out["assignee_added"]), ("dave-gh", "dave-gh"))

    def test_reviewer_without_matching_assignee_is_denied(self):
        def bash(cmd):
            return self.hook("PreToolUse", session_id="S1", tool_name="Bash", tool_input={"command": cmd})
        out = bash("gh pr edit 713 -R example-org/gateway --add-reviewer alice-gh")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("--add-assignee alice-gh", out["hookSpecificOutput"]["permissionDecisionReason"])
        out = bash("gh pr edit 713 -R example-org/gateway --add-reviewer alice-gh --add-assignee alice-gh")
        self.assertFalse(out and out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny")

    def test_github_stream_says_once_when_a_ready_pr_has_no_reviewer(self):
        self.github({x: 0 for x in POOL})
        self.foundry("unit", "keys", "docs", "--pr", "example-org/gateway#713")
        _, out = self.foundry("watch", "stream", "--kind", "github", "--once")
        self.assertIn("foundry review assign example-org/gateway#713 --unit docs", out)
        _, out = self.foundry("watch", "stream", "--kind", "github", "--once")
        self.assertNotIn("review assign", out or "")

    def test_validate_review_alone_is_needs_approval_not_failure(self):
        sys.path.insert(0, SCRIPTS)
        import watch
        human = {"validate-review"}
        red = {"name": "validate-review", "conclusion": "FAILURE"}
        self.assertEqual(watch.ci_bucket([red, {"name": "test", "conclusion": "SUCCESS"}], human), "needs_approval")
        self.assertEqual(watch.ci_bucket([red, {"name": "test", "conclusion": "FAILURE"}], human), "failure")
        self.assertEqual(watch.ci_bucket([{"context": "validate-review", "state": "ERROR"}], human), "needs_approval")

    def test_thread_review_ask_may_not_broadcast(self):
        sys.path.insert(0, SCRIPTS)
        import slackguard as sg
        ask = {"channel_id": "C0000000001", "thread_ts": "1731900000.123456",
               "message": "example-org/gateway#713 のレビューをお願いします"}
        self.assertIn("reply_broadcast false", sg.broadcast_decision(ask | {"reply_broadcast": True}))
        self.assertIsNone(sg.broadcast_decision(ask | {"reply_broadcast": False}))
        self.assertIsNone(sg.broadcast_decision({"channel_id": "C1", "message": "リリースしました", "thread_ts": "1.2",
                                                 "reply_broadcast": True}))

    def test_review_asks_batch_per_reviewer_and_wait_for_morning(self):
        def ask(n, to, when):
            self.env["FOUNDRY_NOW"] = when
            self.foundry("outbox", "add", "docs", "--channel", "C0000000001", "--kind", "review-ask", "--to", to,
                         "--pr", f"example-org/gateway#{n}", "--text", f"example-org/gateway#{n} のレビューをお願いします")
        ask(1, "U0000000002", "2026-11-18T10:00:00+09:00")
        ask(2, "U0000000001", "2026-11-18T10:30:00+09:00")
        _, b = self.foundry("outbox", "batches")
        self.assertEqual([x["send"] for x in b], ["hold", "hold"])  # one ask each, fresh
        ask(3, "U0000000002", "2026-11-18T10:40:00+09:00")
        _, b = self.foundry("outbox", "batches")
        bob = next(x for x in b if x["to"] == ["U0000000002"])
        self.assertEqual((bob["count"], bob["send"]), (2, "now"))
        self.env["FOUNDRY_NOW"] = "2026-11-18T23:00:00+09:00"
        _, b = self.foundry("outbox", "batches")
        self.assertTrue(all(x["send"].startswith("schedule 2026-11-19T08:00") for x in b if x["due"]), b)
        self.assertTrue(all(x["due"] for x in b), b)  # by 23:00 every ask has waited past batch_wait_min


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (ParkWaitingLeads, SyncLagFromQueue, ReviewPolicy):
        suite.addTests(cls(n) for n in sorted(vars(cls)) if n.startswith("test_"))
    return suite
