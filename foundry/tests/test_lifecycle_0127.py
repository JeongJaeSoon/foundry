"""0.1.27: the foreman carries out the owner's "정리해줘 / 종료해줘" itself on the owner's quoted words; a session's
own temp dir is scratch; leads read external systems with their own read-only tools; a corrected retry after a
shape deny is not a near-miss."""

import json
import os
import re
import sys
import unittest

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import approve  # noqa: E402
import gitpolicy  # noqa: E402
import triage  # noqa: E402

SAID = "응 정리해줘, 다 종료해줘"
ORCH = {"agent_type": "foundry:foreman", "session_id": "S1"}


class _Base(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")

    def journal(self, op):
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            return [r for r in map(json.loads, filter(str.strip, f)) if r.get("op") == op]

    def record(self, op, **fields):
        sys.path.insert(0, tf.SCRIPTS)
        import foundrylib
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            foundrylib.append(op, **fields)
        finally:
            if old is None:
                os.environ.pop("FOUNDRY_STATE_DIR")
            else:
                os.environ["FOUNDRY_STATE_DIR"] = old


class UnitOnTheOwnersWord(_Base):
    def test_done_and_cancel_need_the_quote_and_journal_it(self):
        _, err = self.foundry("unit", "set", "alpha", "--status", "done", "--by", "foreman", ok=(2,))
        self.foundry("unit", "set", "alpha", "--status", "done", "--by", "foreman", "--quote", "  ", ok=(2,))
        out = self.foundry("unit", "set", "alpha", "--status", "done", "--by", "foreman", "--quote", SAID)[1]
        self.assertEqual((out["status"], out["by"], out["quote"]), ("done", "foreman", SAID))
        (rec,) = [r for r in self.journal("unit.set") if r.get("status") == "done"]
        self.assertEqual((rec["by"], rec["quote"]), ("foreman", SAID))
        self.assertIn("relayed by the foreman", rec["note"])
        (ov,) = self.journal("owner.override")
        self.assertEqual((ov["by"], ov["quote"]), ("foreman", SAID))

    def test_cancel_a_running_unit_on_the_owners_word(self):
        self.foundry("unit", "set", "alpha", "--status", "cancelled", "--by", "foreman", ok=(2,))
        self.foundry("unit", "set", "alpha", "--status", "cancelled", "--by", "foreman", "--quote", SAID)
        self.assertEqual(self.snap()["units"]["alpha"]["status"], "cancelled")

    def test_a_lead_cannot_borrow_the_quote(self):
        env = dict(self.env, FOUNDRY_ROLE="line-lead", FOUNDRY_UNIT="alpha")
        old, self.env = self.env, env
        try:
            self.foundry("unit", "set", "alpha", "--status", "cancelled", "--by", "foreman", "--quote", SAID, ok=(2,))
        finally:
            self.env = old


class ApprovalsOnTheOwnersWord(_Base):
    def setUp(self):
        super().setUp()
        self.record("approval.request", rid="ap-3", key="k3", unit="alpha", tool="Write", summary="s", tier=3)

    def test_tier3_deny_with_quote_allow_never(self):
        self.foundry("approve", "deny", "ap-3", ok=(2,))
        _, err = self.foundry("approve", "allow", "ap-3", "--quote", SAID, ok=(2,))
        self.foundry("approve", "deny", "ap-3", "--quote", SAID)
        a = self.snap()["approvals"]["ap-3"]
        self.assertEqual((a["answer"], a["by"], a["quote"]), ("deny", "foreman", SAID))

    def test_hook_lets_the_foreman_type_a_quoted_tier3_deny_only(self):
        def run(cmd):
            return self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **ORCH)

        self.assertIsNone(run(f"foundry approve deny ap-3 --quote '{SAID}'"))
        out = run(f"foundry approve allow ap-3 --quote '{SAID}'")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("owner's alone", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(run("foundry approve deny ap-3")["hookSpecificOutput"]["permissionDecision"], "deny")


class CardsAndPrefsOnTheOwnersWord(_Base):
    def test_close_a_stale_card(self):
        card = self.foundry("decide", "open", "--unit", "alpha", "--question", "Keep it?", "--option", "A:yes",
                            "--option", "B:no", "--asked-by", "foreman", "--key", "alpha:keep")[1]["card"]
        cid = card["id"]
        self.foundry("decide", "withdraw", cid, "--by", "foreman", "--reason", "stale", ok=(2,))
        out = self.foundry("decide", "withdraw", cid, "--by", "foreman", "--reason", "stale", "--quote", SAID)[1]
        self.assertEqual((out["withdrawn"], out["quote"]), (cid, SAID))
        c = self.snap()["cards"][cid]
        self.assertEqual((c["answer"], c["answered_by"]), ("withdrawn", "foreman"))

    def test_prefs_on_the_owners_word_except_merge_and_release(self):
        self.foundry("prefs", "set", "handover_time", "09:00", "--by", "foreman", "--quote", SAID)
        (rec,) = self.journal("prefs.changed")
        self.assertEqual((rec["by"], rec["quote"]), ("foreman", SAID))
        for key in ("merge.default", "release.tag"):
            self.foundry("prefs", "set", key, "x", "--by", "foreman", "--quote", SAID, ok=(2,))


class SessionScratch(unittest.TestCase):
    CTX = {"cfg": {}, "worktree": "/nonexistent/wt", "cwd": "/nonexistent/wt", "exempt": []}

    def cls(self, tool, ti):
        return approve.classify(tool, ti, self.CTX)[0]

    def test_jobs_tmp_is_scratch_settings_stay_owner(self):
        jobs = os.path.expanduser("~/.claude/jobs/9c1333de/tmp/")
        self.assertEqual(self.cls("Write", {"file_path": jobs + "pr-body.md"}), "allow")
        self.assertEqual(self.cls("Edit", {"file_path": jobs + "astcmp.py"}), "allow")
        self.assertEqual(self.cls("Write", {"file_path": jobs + "../../../settings.json"}), "owner")
        self.assertEqual(self.cls("Write", {"file_path": os.path.expanduser("~/.claude/settings.json")}), "owner")
        self.assertEqual(self.cls("Write", {"file_path": os.path.expanduser("~/.claude/jobs/x/notes.md")}), "owner")
        self.assertNotEqual(self.cls("Bash", {"command": "git commit -F ~/.claude/jobs/9c1333de/tmp/msg.txt"}), "owner")
        self.assertEqual(self.cls("Bash", {"command": "cp ~/.claude/jobs/a/tmp/x ~/.claude/settings.json"}), "owner")
        self.assertEqual(self.cls("Bash", {"command": "cp ~/.claude/jobs/a/tmp/x .claude/settings.json"}), "owner")


class ExternalReads(unittest.TestCase):
    WRITE = re.compile(r"(?i)send|post|create|edit|update|delete|upsert|transition|add|schedule|apply|patch|exec")

    def tools(self, agent):
        with open(os.path.join(tf.SCRIPTS, "..", "agents", agent)) as f:
            return [t.strip() for t in f.read().split("\ntools:", 1)[1].split("\n", 1)[0].split(",")]

    def test_leads_hold_read_only_external_tools(self):
        for agent in ("line-lead.md", "shop-boss.md"):
            tools = self.tools(agent)
            for need in ("datadog__search_datadog_logs", "slack__slack_read_thread", "atlassian__getConfluenceContent",
                         "kubernetes-prd__kubectl_get", "kubernetes-stg__kubectl_describe"):
                self.assertTrue(any(t.endswith(need) for t in tools), (agent, need))
            mcp = [t.split("__", 2)[2] for t in tools if t.startswith("mcp__")]
            self.assertEqual([t for t in mcp if self.WRITE.search(t)], [], agent)

    def test_prompts_never_send_a_missing_mcp_to_the_owner(self):
        for agent in ("line-lead.md", "shop-boss.md", "foreman.md"):
            with open(os.path.join(tf.SCRIPTS, "..", "agents", agent)) as f:
                self.assertIn("external read blocked", f.read(), agent)


class CorrectedRetry(unittest.TestCase):
    def test_a_truncated_denied_command_still_parses(self):
        self.assertEqual(triage._acts('gh pr create -R o/r --title "fix: a cut-off ti'), {("gh", "pr create")})

    def test_the_split_form_after_a_chain_deny_is_not_a_near_miss(self):
        denial = {"command": 'gh pr create -R o/r --title "t" --label "2.Bugfix & Maintenance" && echo "done for ',
                  "reason": gitpolicy.ONE_PER_CALL + ": run it alone"}
        self.assertTrue(triage.fixes_shape("gh pr create -R o/r --title t --label '2.Bugfix & Maintenance' "
                                           "--body-file b.md", denial))
        legacy = {"command": "gh pr view 5 -R o/r --json state -q .state; gh pr checks 5 -R o/r",
                  "reason": "one git/gh command per call, no chaining or pipes: split into: ..."}
        self.assertTrue(triage.fixes_shape("gh pr view 5 -R o/r --json state,headRefOid", legacy))


if __name__ == "__main__":
    unittest.main()
