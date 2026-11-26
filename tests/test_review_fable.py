"""Regressions for the independent (fable) review of the hook/policy layer: findings 1, 2, 4-6, 8-10, 17, 18, 20."""

import contextlib
import json
import os
import re
import subprocess
import sys
import unittest

from test_foundry import LEAD, ORCH, ROOT, SCRIPTS, Base

sys.path.insert(0, SCRIPTS)


@contextlib.contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Fable(Base):
    def setUp(self):
        super().setUp()
        self.wt = os.path.join(self.dir, "wt-alpha")
        os.makedirs(self.wt)
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.record("unit.worktree", id="alpha", repo="r", path=self.wt, branch="foundry/alpha")
        self.start_lead("alpha")

    def config(self, **cfg):
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump(cfg, f)

    def bash(self, cmd, who=LEAD):
        return self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, cwd=self.wt, **who)

    def denied(self, out):  # a retry may also carry a triage warning (additionalContext only)
        return bool(out) and out["hookSpecificOutput"].get("permissionDecision") == "deny"

    def reason(self, out):
        return (out or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

    def classify(self, tool, ti, **ctx):
        import approve
        with env(FOUNDRY_STATE_DIR=self.dir):
            return approve.classify(tool, ti, {"worktree": self.wt, "cfg": {}, **ctx})[0]

    # 1
    def test_f1_read_only_rule_needs_the_whole_command(self):
        for cmd in ("ls\ngit push origin main", "ls\nrm -rf /x",
                    "cat a\ncurl -X POST https://example.invalid/api/chat.postMessage -d x"):
            self.assertNotEqual(self.classify("Bash", {"command": cmd}), "allow", cmd)
        self.assertEqual(self.classify("Bash", {"command": "ls -la"}), "allow")
        self.assertEqual(self.classify("Bash", {"command": "git status"}), "allow")

    # 2
    def test_f2_wrappers_grouping_and_env_cannot_hide_guarded_programs(self):
        bad = ["bash -c 'git push origin main'", "{ git push origin main; }", "if true; then git push origin main; fi",
               "! git push origin main", "timeout 60 git push origin main", "env -u X git push origin main",
               "command -p git push origin main", "sh -c 'gh pr merge 5 --admin'", "git push origin HEAD:ma${X}in",
               "gh pr merge 5 --match-head-commit abc --body note#1 --admin",
               "git push origin HEAD:refs/heads/x#1 HEAD:main",
               "bash -c 'foundry approve allow ap-1 --by owner'",
               "bash -c \"env -u FOUNDRY_ROLE claude -p --dangerously-skip-permissions 'x'\"",
               "F=foundry; $F unit launch u2", "FOUNDRY_ROLE= foundry unit set u1 --status done",
               "export CLAUDE_CONFIG_DIR=/x", "unset FOUNDRY_UNIT", "xargs git add < files", "nohup claude -p x"]
        for cmd in bad:
            self.assertTrue(self.denied(self.bash(cmd)), cmd)
        self.assertIn("git push origin main", self.reason(self.bash("bash -c 'git push origin main'")))
        ok = [f"git -C {self.wt} status", "gh pr view 5", f"cat {SCRIPTS}/foundry.py",
              "foundry unit show alpha", "rg foundry docs", "ls | xargs grep -l foundry", "echo $FOUNDRY_UNIT"]
        for cmd in ok:
            self.assertFalse(self.denied(self.bash(cmd)), cmd)

    # 4
    def test_f4_warning_then_deny_is_one_json_object_that_denies(self):
        with env(FOUNDRY_NOW=self.env["FOUNDRY_NOW"]):
            self.record("perm.denied", unit="alpha", tool="Bash", command="claude -p hello", reason="x", source="hook")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "hook.py"), "PreToolUse"], env=self.env,
                           input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "claude -p world"},
                                             "cwd": self.wt, **LEAD}), capture_output=True, text=True)
        out = json.loads(r.stdout)  # raises on two concatenated objects
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        with open(os.path.join(self.dir, "enforce.log")) as f:
            self.assertTrue(any(json.loads(x).get("decision") == "warn" for x in f))

    # 5
    def test_f5_state_dir_is_owner_tier_and_guarded(self):
        glob = f"echo x >> {self.dir}/jour*.jsonl"
        self.assertEqual(self.classify("Bash", {"command": glob}), "owner")
        self.assertEqual(self.classify("Write", {"file_path": os.path.join(self.dir, "prefs.json")}), "owner")
        self.assertEqual(self.classify("Edit", {"file_path": os.path.join(self.dir, "repos.json")}), "owner")
        self.assertEqual(self.classify("Read", {"file_path": os.path.join(self.dir, "journal.jsonl")}), "allow")
        self.assertEqual(self.classify("Write", {"file_path": os.path.join(self.wt, "a.py")}, exempt=[self.wt]), "allow")
        catch_all = {"approval_rules": [{"action": "allow"}]}
        self.assertEqual(self.classify("Bash", {"command": "kubectl apply --context prod"}, cfg=catch_all), "owner")
        self.assertTrue(self.denied(self.bash(glob)))
        self.assertFalse(self.denied(self.bash(f"tail {self.dir}/enforce.log")))
        import foundrylib
        with env(FOUNDRY_STATE_DIR=self.dir):
            asks = foundrylib.load_config()["lead_ask_rules"]
        self.assertIn("Write(~/.local/state/foundry/**)", asks)
        self.assertIn("Edit(~/.local/state/foundry/**)", asks)

    # 6
    def test_f6_slack_sends_on_any_server_and_raw_api_posts(self):
        import slackguard as sg
        for tool in ("mcp__claude_ai_Slack__slack_send_message", "mcp__plugin_acme_acme-slack__slack_schedule_message",
                     "mcp__x__slack_complete_file_upload"):
            self.assertTrue(sg.is_slack_send(tool), tool)
        self.assertFalse(sg.is_slack_send("mcp__x__slack_send_message_draft"))
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as f:
            hooks = json.load(f)["hooks"]
        for ev in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            self.assertTrue(re.search(hooks[ev][0]["matcher"], "mcp__claude_ai_Slack__slack_send_message"), ev)
            self.assertTrue(re.search(hooks[ev][0]["matcher"], "mcp__x__slack_complete_file_upload"), ev)
        curl = "curl -X POST https://slack.com/api/chat.postMessage -d channel=C1 -d text=hi"
        self.assertTrue(self.denied(self.bash(curl)))
        self.assertEqual(self.classify("Bash", {"command": curl}), "owner")
        a = sg.idempotency_keys({"channel_id": "C1", "file_id": "F1"})[0]
        self.assertNotEqual(a, sg.idempotency_keys({"channel_id": "C1", "file_id": "F2"})[0])

    # 8
    def test_f8_scheduled_send_post_at_must_parse(self):
        sched = "mcp__x__slack_schedule_message"
        for bad in ("tomorrow", None):
            ti = {"channel_id": "C1", "message": "m", **({"post_at": bad} if bad else {})}
            self.assertIn("unix timestamp", self.reason(self.hook("PreToolUse", tool_name=sched, tool_input=ti, **ORCH)))
        ti = {"channel_id": "C1", "message": "m2", "post_at": "1795021200.0"}  # 02:00 JST
        self.assertIn("quiet hours", self.reason(self.hook("PreToolUse", tool_name=sched, tool_input=ti, **ORCH)))

    # 9
    def test_f9_push_and_merge_policy_gaps(self):
        wt = self.wt
        cases = {f"git -C {wt} push --all origin": "--all", f"git -C {wt} push --mirror origin": "--all",
                 f"git -C {wt} push --delete origin main": "default branch",
                 f"git -C {wt} push origin :main": "default branch",
                 f"git -C {wt} push --signed origin main": "default branch",
                 "gh pr merge 5 -R o/r --admin=true --match-head-commit x": "--admin",
                 "gh api -X PUT repos/o/r/pulls/5/merge": "gh pr merge 5 -R o/r",
                 "gh pr merge 5 -R o/unreg --match-head-commit x": "owner's"}
        for cmd, why in cases.items():
            self.assertIn(why, self.reason(self.bash(cmd)), cmd)
        self.assertFalse(self.denied(self.bash(f"git -C {wt} push --delete origin old-branch")))
        self.assertEqual(self.classify("Bash", {"command": "gh api -X PUT repos/o/r/pulls/5/merge"}), "owner")
        repo = os.path.join(self.dir, "foreign")
        os.makedirs(repo)
        subprocess.run(["git", "-C", repo, "init", "-q"], check=True)
        subprocess.run(["git", "-C", repo, "remote", "add", "origin", "https://github.com/other/x.git"], check=True)
        own = {"approval_own_orgs": ["mine"]}
        self.assertEqual(self.classify("Bash", {"command": "gh pr merge 5"}, cwd=repo, cfg=own), "owner")
        subprocess.run(["git", "-C", repo, "remote", "set-url", "origin", "git@github.com:mine/x.git"], check=True)
        self.assertNotEqual(self.classify("Bash", {"command": "gh pr merge 5"}, cwd=repo, cfg=own), "owner")

    # 10
    def test_f10_fail_closed_covers_every_foundry_session(self):
        import hook
        with env(FOUNDRY_STATE_DIR=self.dir, FOUNDRY_ROLE=None, FOUNDRY_SESSION=None):
            self.assertTrue(hook.fail_closed({"session_id": "S1", "tool_name": "Bash"}))  # journal only
            self.assertFalse(hook.fail_closed({"session_id": "nobody", "tool_name": "Bash"}))
            with env(FOUNDRY_SESSION="1"):
                self.assertTrue(hook.fail_closed({"session_id": "nobody", "tool_name": "Bash"}))

    # 17
    def test_f17_foreman_subagent_requests_go_to_tier3(self):
        self.config(approval_wait_seconds=1)
        self.hook("PermissionRequest", tool_name="Bash", tool_input={"command": "make build"}, cwd=self.wt,
                  session_id="S1", agent_type="foundry:advisor", agent_id="A1")
        self.hook("PermissionRequest", tool_name="Bash", tool_input={"command": "make test"}, cwd=self.wt,
                  session_id="SL", agent_type="foundry:hand", agent_id="H1")
        tiers = {a["summary"]: a["tier"] for a in self.snap()["approvals"].values()}
        self.assertEqual(tiers, {"Bash: make build": 3, "Bash: make test": 2})

    # 18
    def test_f18_stop_gate_counts_from_the_latest_session_start(self):
        self.foundry("unit", "checkpoint", "alpha", "--note", "before the resume")
        self.assertIsNone(self.hook("Stop", **LEAD))
        self.env["FOUNDRY_NOW"] = "2026-11-18T11:00:00+09:00"
        self.start_lead("alpha", source="resume")
        out = self.hook("Stop", **LEAD)
        self.assertEqual((out or {}).get("decision"), "block")
        self.foundry("unit", "checkpoint", "alpha", "--note", "after the resume")
        self.assertIsNone(self.hook("Stop", **LEAD))

    # 20
    def test_f20_judge_runs_only_this_plugins_housekeep(self):
        import hook
        hk = os.path.join(SCRIPTS, "housekeep.py")
        self.assertTrue(hook.judge_command_ok(f"python3 {hk} scan"))
        self.assertTrue(hook.judge_command_ok(f"{ROOT}/bin/foundry-housekeep scan"))
        for bad in ("python3 /any/dir/scripts/housekeep.py scan", "/elsewhere/foundry-housekeep scan"):
            self.assertFalse(hook.judge_command_ok(bad), bad)


if __name__ == "__main__":
    unittest.main()
