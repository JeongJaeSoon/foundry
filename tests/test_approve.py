"""Tests for the tiered PermissionRequest approver (scripts/approve.py)."""

import json
import os
import subprocess
import sys
import time
import unittest

from test_foundry import ORCH, ROOT, SCRIPTS, Base

LEAD = {"agent_type": "foundry:line-lead", "session_id": "SL"}


class Approver(Base):
    def setUp(self):
        super().setUp()
        self.wt = os.path.join(self.dir, "wt-alpha")
        os.makedirs(self.wt)
        self.config(approval_wait_seconds=6)
        # The notifier gets the message as its last argument; keep it inside the test dir.
        self.env["FOUNDRY_NOTIFY_CMD"] = (f"{sys.executable} -c \"import sys; open(sys.argv[1], 'a').write(sys.argv[2])\" "
                                          f"{os.path.join(self.dir, 'notified')}")
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.record("unit.worktree", id="alpha", repo="r", path=self.wt, branch="b")
        self.start_lead("alpha")

    def config(self, **cfg):
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump(cfg, f)

    def perm(self, tool, ti, who=LEAD):
        return self.hook("PermissionRequest", tool_name=tool, tool_input=ti, cwd=self.wt, **who)

    def start_perm(self, tool, ti, who=LEAD):
        inp = {"tool_name": tool, "tool_input": ti, "cwd": self.wt, "hook_event_name": "PermissionRequest", **who}
        p = subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "hook.py"), "PermissionRequest"], env=self.env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        p.stdin.write(json.dumps(inp))
        p.stdin.close()
        return p

    def finish(self, p):
        p.wait(timeout=30)
        with p.stdout, p.stderr:
            return json.loads(p.stdout.read())

    def open_rid(self, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            asks = [a for a in self.snap()["approvals"].values() if a["answer"] is None]
            if asks:
                return asks[0]
            time.sleep(0.2)
        self.fail("no approval request recorded")

    @staticmethod
    def decision(out):
        return out["hookSpecificOutput"]["decision"]

    def log_lines(self):
        with open(os.path.join(self.dir, "enforce.log")) as f:
            return [json.loads(x) for x in f if '"PermissionRequest"' in x]

    def test_tier0_allows_edit_inside_worktree_and_reads(self):
        out = self.perm("Write", {"file_path": os.path.join(self.wt, "a.py"), "content": "x"})
        self.assertEqual(self.decision(out), {"behavior": "allow"})
        self.assertEqual(self.decision(self.perm("Bash", {"command": "git status", "description": "d"}))["behavior"], "allow")
        self.assertEqual(self.log_lines()[-1]["tier"], 0)
        self.assertEqual(self.snap()["approvals"], {})

    def test_tier0_owner_only_goes_tier3_and_times_out(self):
        self.config(approval_wait_seconds=1)
        out = self.perm("Bash", {"command": "kubectl apply -f x.yaml --context prod"})
        d = self.decision(out)
        self.assertEqual(d["behavior"], "deny")
        # 0.1.33: a lead session never blocks on tier 3; it is queued at once and the unit waits on it.
        self.assertIn("queued as", d["message"])
        (a,) = self.snap()["approvals"].values()
        self.assertEqual((a["tier"], a["unit"], a["answer"]), (3, "alpha", None))
        self.assertTrue(a["gave_up"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "notified")))
        self.assertTrue(any(e["kind"] == "approval" and e["ref"] == a["rid"] for e in self.snap()["events"]))
        # The foreman may not answer tier 3; the owner may.
        _, msg = self.foundry("approve", "allow", a["rid"], ok=(2,))
        self.foundry("approve", "allow", a["rid"], "--by", "owner")
        self.assertEqual(self.snap()["approvals"][a["rid"]]["by"], "owner")

    def test_owner_patterns(self):
        sys.path.insert(0, SCRIPTS)
        import approve
        ctx = {"worktree": self.wt, "cfg": {}}
        owner = [("Bash", {"command": "gh auth token"}), ("Bash", {"command": "cat .env"}),
                 ("Write", {"file_path": os.path.expanduser("~/.claude/settings.json")}),
                 ("Edit", {"file_path": os.path.join(self.wt, ".claude/settings.local.json")}),
                 ("Bash", {"command": "sudo rm x"}), ("Bash", {"command": "chmod 600 /etc/hosts"}),
                 ("Bash", {"command": "npm publish"}), ("Bash", {"command": "git push --force origin main"}),
                 ("Bash", {"command": "gh pr merge 3 -R other-org/repo"}), ("Bash", {"command": "stripe charges"}),
                 ("mcp__x__slack_send_message", {"text": "hi"}), ("Bash", {"command": "docker login ghcr.io"}),
                 # Review 12: a relative path's base is unknown, so chmod on it is the owner's call.
                 ("Bash", {"command": "chmod +x run.sh"}),
                 # Review 14: credential stores and credential-named files, reads included.
                 ("Read", {"file_path": os.path.expanduser("~/.ssh/id_ed25519")}),
                 ("Bash", {"command": "cat ~/.aws/credentials"}), ("Bash", {"command": "cat ~/.netrc"}),
                 ("Read", {"file_path": os.path.join(self.wt, "config/api_token.txt")})]
        for tool, ti in owner:
            self.assertEqual(approve.classify(tool, ti, ctx)[0], "owner", (tool, ti))
        other = [("Bash", {"command": f"chmod +x {self.wt}/run.sh"}),
                 ("Read", {"file_path": os.path.join(self.wt, "src/tokenizer.py")}), ("Bash", {"command": "git push origin feat"}),
                 ("Bash", {"command": "grep login src/app.py"}), ("Write", {"file_path": "/elsewhere/a.py"})]
        for tool, ti in other:
            self.assertNotEqual(approve.classify(tool, ti, ctx)[0], "owner", (tool, ti))
        ctx["cfg"] = {"approval_rules": [{"tool": "Bash", "pattern": "kubectl", "action": "deny"}],
                      "approval_mcp_allow": ["slack_send"]}
        self.assertEqual(approve.classify("Bash", {"command": "kubectl -n prd get po"}, ctx)[0], "deny")
        self.assertIsNone(approve.classify("mcp__x__slack_send_message", {"text": "hi"}, ctx)[0])

    def test_tier2_answered_while_waiting_then_tier1_ledger(self):
        self.config(approval_wait_seconds=20)
        ti = {"command": "make build", "description": "one"}
        p = self.start_perm("Bash", ti)
        a = self.open_rid()
        self.assertEqual(a["tier"], 2)
        self.assertTrue(any(e["kind"] == "approval" for e in self.snap()["events"]))
        _, res = self.foundry("approve", "allow", a["rid"], "--remember", "--reason", "build is fine")
        self.assertEqual(res["behavior"], "allow")
        self.assertEqual(self.decision(self.finish(p)), {"behavior": "allow"})
        _, res = self.foundry("approve", "deny", a["rid"])
        self.assertIn("noop", res)
        # Same input (description ignored): tier 1, no new request.
        out = self.perm("Bash", {"command": "make build", "description": "two"})
        self.assertEqual(self.decision(out), {"behavior": "allow"})
        self.assertEqual(len(self.snap()["approvals"]), 1)
        self.assertEqual(self.log_lines()[-1]["tier"], 1)

    def test_timeout_denies_and_queues_then_late_answer_applies_once(self):
        self.config(approval_wait_seconds=1)
        out = self.perm("Bash", {"command": "make deploy-docs"})
        d = self.decision(out)
        self.assertEqual(d["behavior"], "deny")
        rid = d["message"].split("queued as ")[1].split(".")[0]
        _, rows = self.foundry("approve", "list")
        self.assertEqual([r["rid"] for r in rows], [rid])
        _, res = self.foundry("approve", "allow", rid)
        self.assertTrue(res["queued"])
        self.assertIn("SendMessage", res["next"])
        self.assertEqual(self.decision(self.perm("Bash", {"command": "make deploy-docs"})), {"behavior": "allow"})
        self.assertEqual(self.decision(self.perm("Bash", {"command": "make deploy-docs"}))["behavior"], "deny")
        _, rows = self.foundry("approve", "list", "--all")
        self.assertEqual(len(rows), 2)

    def test_outside_foundry_prints_nothing(self):
        self.assertIsNone(self.hook("PermissionRequest", session_id="OTHER", tool_name="Bash",
                                    tool_input={"command": "make"}))

    def test_exception_denies(self):
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            f.write("{")
        d = self.decision(self.perm("Bash", {"command": "make"}))
        self.assertEqual(d["behavior"], "deny")
        self.assertIn("Approver error", d["message"])
        self.assertEqual(self.log_lines()[-1]["decision"], "error")

    def test_bash_decision_who_may_answer(self):
        self.record("approval.request", rid="ap-2", key="k2", unit="alpha", tool="Bash", summary="s", tier=2)
        self.record("approval.request", rid="ap-3", key="k3", unit="alpha", tool="Bash", summary="s", tier=3)

        def run(cmd, **who):
            return self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **who)

        self.assertTrue(self.denied(run("foundry approve allow ap-2", **LEAD)))
        self.assertTrue(self.denied(run("foundry approve deny ap-2", agent_type="foundry:hand", agent_id="H1")))
        out = run("foundry approve allow ap-3 --by owner", **ORCH)  # the verb form: a by-path call would be judged on the cwd
        self.assertTrue(self.denied(out))
        self.assertIn("owner", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(run("foundry approve allow ap-2 --remember", **ORCH))
        self.assertIsNone(run("foundry approve list", **LEAD))

    def test_review_read_only_rule_is_really_read_only(self):
        sys.path.insert(0, SCRIPTS)
        import approve
        ctx = {"worktree": self.wt, "cfg": {}}
        for cmd in ("git branch -D main", "git remote remove origin", "git diff --output=/x/y", "rg --pre sh x",
                    "tree -o out.txt", "git branch -m a b", "git remote add x y"):
            self.assertNotEqual(approve.classify("Bash", {"command": cmd}, ctx)[0], "allow", cmd)
        for cmd in ("git status", "git branch", "git branch -a", "git remote -v", "git log --oneline -3", "rg foo src"):
            self.assertEqual(approve.classify("Bash", {"command": cmd}, ctx)[0], "allow", cmd)

    def test_review_no_unit_means_no_worktree_fallback_to_cwd(self):
        home_file = os.path.join(self.dir, ".zshrc")
        p = self.start_perm("Write", {"file_path": home_file, "content": "x"},
                            who={"agent_type": "foundry:foreman", "session_id": "SF"})
        a = self.open_rid()
        self.assertEqual(a["tier"], 3)  # the foreman's own request: never tier 0 just because cwd contains it
        p.kill()
        p.wait()
        with p.stdout, p.stderr:
            pass

    def test_review_owner_answers_cannot_be_hidden(self):
        self.record("approval.request", rid="ap-2", key="k2", unit="alpha", tool="Bash", summary="s", tier=2)
        orch = {"agent_type": "foundry:foreman", "session_id": "SF"}
        for cmd in ("foundry 'approve' allow ap-2 --by owner", "foundry approve allow ap-2 --by=owner",
                    "foundry approve allow ap-2 --by $WHO", "echo x >> ~/.local/state/foundry/journal.jsonl",
                    "python3 -c 'open(\"journal.jsonl\",\"a\")'"):
            out = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **orch)
            self.assertTrue(self.denied(out), cmd)
        self.assertIsNone(self.hook("PreToolUse", tool_name="Bash",
                                    tool_input={"command": "foundry approve allow ap-2 --by foreman"}, **orch))

    def test_review_remembered_answer_is_per_unit(self):
        self.config(approval_wait_seconds=20)
        ti = {"command": "make build"}
        p = self.start_perm("Bash", ti)
        a = self.open_rid()
        self.foundry("approve", "allow", a["rid"], "--remember")
        self.finish(p)
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta")
        self.config(approval_wait_seconds=1)
        self.start_lead("beta", sid="SL-beta")
        self.assertEqual(self.decision(self.perm("Bash", ti))["behavior"], "allow")  # alpha: tier 1
        out = self.perm("Bash", ti, who={"agent_type": "foundry:line-lead", "session_id": "SL-beta"})
        self.assertEqual(self.decision(out)["behavior"], "deny")  # beta was never answered: tier 2 timeout
        self.assertEqual(self.log_lines()[-1]["unit"], "beta")

    def test_hooks_json_entry(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as f:
            (entry,) = json.load(f)["hooks"]["PermissionRequest"]
        self.assertEqual(entry["matcher"], "*")
        self.assertEqual(entry["hooks"][0]["timeout"], 600)
        self.assertIn("hook.py\" PermissionRequest", entry["hooks"][0]["command"])

    def test_rules_cli(self):
        self.config(approval_wait_seconds=1, approval_rules=[{"tool": "Bash", "pattern": '"command": "make ', "action": "allow"}])
        _, rows = self.foundry("approve", "rules")
        # Review fable 5: a config "allow" never precedes a built-in owner rule.
        sources = [(r["source"], r["action"]) for r in rows]
        self.assertLess(sources.index(("builtin", "owner")), sources.index(("config", "allow")))
        self.assertTrue(any(r["reason"] == "production" for r in rows))
        self.assertEqual(self.decision(self.perm("Bash", {"command": "make x"})), {"behavior": "allow"})


if __name__ == "__main__":
    unittest.main()
