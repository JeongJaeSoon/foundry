"""0.1.16: fixes from the foreman design audit (2026-11-18)."""

import json
import os
import subprocess
import sys
import unittest

import test_foundry as tf
from test_foundry import ORCH, SCRIPTS
from test_sync import SyncBase

FOREMAN = {"FOUNDRY_ROLE": "foreman"}
LEAD = {"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "alpha"}


class Base(tf.Base):
    def run_f(self, *args, env=None):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args],
                           env={**self.env, **(env or {})}, capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def bash(self, cmd, **inp):
        return self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **{**ORCH, **inp})


class ForemanRelaysAnAnswer(Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.foundry("decision", "ask", "--key", "alpha:window", "--unit", "alpha", "--question", "Merge today?", ok=(10,))

    def test_foreman_needs_a_quote_and_it_is_journaled(self):
        rc, _, err = self.run_f("decision", "answer", "--key", "alpha:window", "--answer", "yes", env=FOREMAN)
        self.assertEqual(rc, 2)
        self.assertIn("--quote", err)
        rc, out, err = self.run_f("decision", "answer", "--key", "alpha:window", "--answer", "yes",
                                  "--quote", "응 오늘 머지해", env=FOREMAN)
        self.assertEqual(rc, 0, err)
        rec = [r for r in self.journal() if r["op"] == "decision.answer"][-1]
        self.assertEqual((rec["by"], rec["quote"], rec["answer"]), ("foreman", "응 오늘 머지해", "yes"))
        self.assertIn("relayed by the foreman", rec["note"])
        u = self.snap()["units"]["alpha"]
        self.assertIn("Owner decision alpha:window: yes", u["inbox"][-1]["text"])
        self.assertEqual(self.snap()["decisions"]["alpha:window"]["quote"], "응 오늘 머지해")

    def test_other_roles_stay_refused(self):
        rc, _, err = self.run_f("decision", "answer", "--key", "alpha:window", "--answer", "yes", "--quote", "x", env=LEAD)
        self.assertEqual(rc, 2, err)
        self.assertIsNone(self.snap()["decisions"]["alpha:window"]["answer"])

    def test_owner_needs_no_quote(self):
        rc, _, err = self.run_f("decision", "answer", "--key", "alpha:window", "--answer", "yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.snap()["decisions"]["alpha:window"]["by"], "owner")

    def test_decide_answer_by_foreman_needs_a_quote_too(self):
        rc, _, _ = self.run_f("decide", "answer", "alpha:window", "yes", "--by", "foreman", env=FOREMAN)
        self.assertEqual(rc, 2)
        rc, _, err = self.run_f("decide", "answer", "alpha:window", "yes", "--by", "foreman", "--quote", "yes", env=FOREMAN)
        self.assertEqual(rc, 0, err)


class NoPluginScriptByPath(Base):
    def test_raw_scripts_refused_for_foundry_sessions(self):
        for cmd in (f"python3 {SCRIPTS}/foundry.py unit list",
                    f"cd /tmp && python3 -u {SCRIPTS}/watch.py poll",
                    f"LANG=C python3 {SCRIPTS}/sync.py",
                    'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry.py" board',
                    f"{SCRIPTS}/foundry.py unit list"):
            out = self.bash(cmd)
            self.assertTrue(self.denied(out), cmd)
            self.assertIn("installed `foundry", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_reading_or_the_installed_verb_is_fine(self):
        for cmd in ("foundry unit list", f"cat {SCRIPTS}/foundry.py", f"grep -n def {SCRIPTS}/sync.py",
                    "python3 -m unittest discover -s tests", "python3 tools/other.py"):
            self.assertFalse(self.denied(self.bash(cmd)), cmd)

    def test_a_relative_path_from_the_plugin_dir_is_caught(self):
        out = self.bash("python3 scripts/foundry.py unit list", cwd=os.path.dirname(SCRIPTS))
        self.assertTrue(self.denied(out))


class JiraBatchWithoutTicket(SyncBase):
    def test_unit_with_no_ticket_gets_find_or_create(self):
        self.foundry("unit", "new", "--id", "u9", "--title", "No ticket", "--tracker", "jira")
        self.foundry("sync", "enqueue", "u9", "--target", "jira", "--text", "merged")
        item = [i for i in self.drain() if i["target"] == "jira"][0]
        self.assertTrue(item["needs_ticket"])
        self.assertEqual(item["tickets"], [])
        for s in ("searchJiraIssuesUsingJql", "createJiraIssue", "project PROJ", "assignee = the owner",
                  "foundry unit keys"):
            self.assertIn(s, item["instructions"])

    def test_unit_with_a_ticket_has_no_extra_step(self):
        self.unit("u1")
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--text", "merged")
        item = [i for i in self.drain() if i["target"] == "jira"][0]
        self.assertNotIn("needs_ticket", item)
        self.assertNotIn("createJiraIssue", item["instructions"])


class NearMissAgeOut(Base):
    def test_only_the_last_day_is_escalated_but_all_are_counted(self):
        self.env["FOUNDRY_NOW"] = "2026-11-16T10:00:00+09:00"
        os.environ["FOUNDRY_NOW"] = self.env["FOUNDRY_NOW"]
        try:
            self.record("nearmiss", kind="old", unit="u1", detail="two days ago")
        finally:
            os.environ.pop("FOUNDRY_NOW")
        self.env["FOUNDRY_NOW"] = "2026-11-18T10:00:00+09:00"
        os.environ["FOUNDRY_NOW"] = self.env["FOUNDRY_NOW"]
        try:
            self.record("nearmiss", kind="new", unit="u1", detail="now")
        finally:
            os.environ.pop("FOUNDRY_NOW")
        _, m = self.foundry("board", "--format", "json")
        near = [e for e in m["escalations"] if e["kind"] == "nearmiss"]
        self.assertEqual([e["text"] for e in near], ["new:other x1: now"])  # 0.1.25: one row per pattern
        self.assertEqual(m["nearmiss_count"], 2)


class FinishedSubagentNotReannounced(Base):
    def test_a_stopped_subagent_marked_done_is_no_candidate(self):
        self.record("agent.start", agent_id="ag1", type="foundry:advisor")
        self.record("agent.stop", agent_id="ag1")
        hids = {c["hid"] for c in self.script("housekeep.py", "scan")}
        self.assertIn("subagent:ag1", hids)
        self.script("housekeep.py", "plan", "--hid", "subagent:ag1", "--verdict", "safe", "--reason", "finished")
        self.script("housekeep.py", "subagent-done", "ag1")
        hids = {c["hid"] for c in self.script("housekeep.py", "scan")}
        self.assertNotIn("subagent:ag1", hids)


class WaitRefs(Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        repo = os.path.join(self.dir, "webapp")
        subprocess.run(["git", "init", "-q", repo], check=True)
        self.foundry("repo", "add", "webapp", "--path", repo)

    def test_unresolvable_refs_are_refused(self):
        for args in (("--kind", "ci", "--ref", "example-org/webapp@051fb42"),
                     ("--kind", "pr-review", "--ref", "PR 710 on gateway"),
                     ("--kind", "deploy-window", "--ref", "deploy:app@production"),
                     ("--kind", "deploy-window", "--ref", "webapp prod"),
                     ("--kind", "team", "--ref", "gateway-dev-release"),
                     ("--kind", "time", "--ref", "later")):
            rc, _, err = self.run_f("wait", "add", "alpha", *args)
            self.assertEqual(rc, 2, args)
            self.assertIn("refused", err)
        self.assertEqual(self.snap()["units"]["alpha"]["waits"], [])

    def test_resolvable_refs_are_kept(self):
        for args in (("--kind", "ci", "--ref", "example-org/webapp#12"),
                     ("--kind", "deploy-window", "--ref", "deploy:webapp@production"),
                     ("--kind", "team", "--ref", "slack thread", "--until", "2026-11-19T09:00:00+09:00")):
            self.foundry("wait", "add", "alpha", *args)
        self.assertEqual(len(self.snap()["units"]["alpha"]["waits"]), 3)

    def test_a_recorded_deploy_clears_the_deploy_wait(self):
        self.foundry("wait", "add", "alpha", "--kind", "deploy-window", "--ref", "deploy:webapp@production")
        self.assertEqual(self.script("watch.py", "poll")["events"], [])
        self.foundry("unit", "deployed", "alpha", "--app", "webapp", "--env", "production", "--tag", "v1",
                     "--evidence", "argo synced")
        out = self.script("watch.py", "poll")
        self.assertEqual([e["kind"] for e in out["events"]], ["deploy-window"])
        self.assertEqual(self.snap()["units"]["alpha"]["waits"], [])

    def test_waiting_units_with_nothing_to_wait_on_are_flagged(self):
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta")
        self.record("unit.status", id="alpha", status="waiting_others")
        self.record("unit.status", id="beta", status="waiting_owner")
        # stored before 0.1.16 validated refs: nothing resolves it
        self.record("wait.add", id="alpha", wid="team:x", kind="team", ref="x", until=None, note=None, fingerprint=None)
        _, m = self.foundry("board", "--format", "json")
        rows = {r["id"]: r for s in m["sections"] for r in s["rows"]}
        self.assertEqual(rows["alpha"]["unwatched"], "감시 안 되는 대기")
        self.assertEqual(rows["beta"]["unwatched"], "결정 대상 없음")
        brief = self.hook("SessionStart", source="resume", **ORCH)
        text = json.dumps(brief, ensure_ascii=False)
        self.assertIn("Waiting on nothing a watcher resolves", text)


class CronKeys(Base):
    def test_two_preflight_crons_are_two_entries(self):
        for cid, cron in (("c1", "25 21 * * *"), ("c2", "25 7 * * *")):
            self.hook("PostToolUse", tool_name="CronCreate", tool_response={"id": cid},
                      tool_input={"cron": cron, "prompt": "Run `foundry preflight`", "recurring": True}, **ORCH)
        crons = self.snap()["crons"]
        self.assertEqual(sorted(crons), ["preflight-morning", "preflight-night"])
        self.hook("PostToolUse", tool_name="CronCreate", tool_response={"id": "c3"},
                  tool_input={"cron": "25 21 * * *", "prompt": "Run `foundry preflight`", "recurring": True}, **ORCH)
        crons = self.snap()["crons"]
        self.assertEqual((len(crons), crons["preflight-night"]["id"]), (2, "c3"))


class RepoDedupe(Base):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.dir, "gateway")
        subprocess.run(["git", "init", "-q", self.repo], check=True)

    def write(self, data):
        with open(os.path.join(self.dir, "repos.json"), "w") as f:
            json.dump(data, f)

    def test_slug_named_twin_folds_into_the_short_name(self):
        e = {"path": self.repo, "default_branch": "main", "remote": "origin", "tracker": "jira"}
        self.write({"gateway": dict(e), "example-org/gateway": dict(e)})
        _, rs = self.foundry("repo", "list")
        self.assertEqual(list(rs), ["gateway"])
        self.assertEqual(rs["gateway"]["slug"], "example-org/gateway")

    def test_policy_disagreement_keeps_both(self):
        e = {"path": self.repo, "default_branch": "main", "remote": "origin", "tracker": "jira"}
        self.write({"gateway": dict(e, merge="self"), "example-org/gateway": dict(e, merge="owner")})
        _, rs = self.foundry("repo", "list")
        self.assertEqual(sorted(rs), ["example-org/gateway", "gateway"])
        self.assertEqual((rs["gateway"]["merge"], rs["example-org/gateway"]["merge"]), ("self", "owner"))

    def test_adding_the_slug_name_reuses_the_entry_and_keeps_its_policy(self):
        self.write({"gateway": {"path": self.repo, "default_branch": "main", "remote": "origin", "tracker": "jira",
                               "merge": "self"}})
        self.foundry("repo", "add", "example-org/gateway", "--path", self.repo)
        with open(os.path.join(self.dir, "repos.json")) as f:
            rs = json.load(f)
        self.assertEqual(list(rs), ["gateway"])
        self.assertEqual((rs["gateway"]["slug"], rs["gateway"]["merge"]), ("example-org/gateway", "self"))


class AutoLaunch(Base):
    """Owner decision 2026-11-18 17:55: queued units start in `foundry next` order when a slot is free."""

    def stream(self):
        _, out = self.foundry("watch", "stream", "--kind", "sessions", "--once")
        return [x.split("] ", 1)[-1] for x in (out or "").splitlines() if x] if isinstance(out, str) else []

    def test_sessions_stream_says_launch_once_per_ready_unit(self):
        for uid in ("a1", "a2"):
            self.foundry("unit", "new", "--id", uid, "--title", uid, "--repo", "r1")
        lines = [x for x in self.stream() if x.startswith("launch ")]
        self.assertEqual([x.split()[1] for x in lines], ["a1", "a2"])
        self.assertIn("foundry unit launch a1", lines[0])
        self.assertFalse([x for x in self.stream() if x.startswith("launch ")])  # said once
        _, plan = self.foundry("up")
        self.assertEqual([x["unit"] for x in plan["next"]], ["a1", "a2"])
        self.assertTrue(any("auto_launch" in s and "foundry unit launch" in s for s in plan["steps"]))

    def test_off_means_no_launch_lines(self):
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"auto_launch": False}, f)
        self.foundry("unit", "new", "--id", "a1", "--title", "a1", "--repo", "r1")
        self.assertFalse([x for x in self.stream() if x.startswith("launch ")])
        _, plan = self.foundry("up")
        self.assertEqual(plan["next"], [])


from test_launcher import Launcher  # noqa: E402


class McpAtLaunch(Launcher):
    """A unit whose stages need an MCP server is refused at launch when nothing provides it (PROJ-779)."""

    def prefs(self, fake):
        self.env["FOUNDRY_MCP_FAKE"] = json.dumps(fake)
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"required_mcp": [{"stage": "deploy-verify", "repos": ["app"], "servers": ["*datadog*"]}]}, f)

    def test_missing_server_refuses_and_files_an_owner_todo(self):
        self.prefs({"connected": ["plugin_acme_acme-atlassian"]})
        rc, out = self.foundry("unit", "launch", "alpha", ok=(8,))
        self.assertEqual(self.bg_calls(), [])
        todo = self.snap()["owner_todos"]["mcp:alpha"]
        self.assertIn("deploy-verify needs *datadog*", todo["text"])

    def test_connected_server_launches_and_says_which(self):
        self.prefs({"connected": ["plugin_acme_acme-datadog"]})
        self.claude({"bg_id": "a1b2", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S", "status": "busy"}]})
        _, out = self.foundry("unit", "launch", "alpha")
        self.assertEqual(out["mcp"]["required"], ["deploy-verify: plugin_acme_acme-datadog"])
        self.assertNotIn("--mcp-config", self.bg_calls()[-1]["argv"])

    def test_project_scoped_server_is_passed_with_mcp_config(self):
        self.prefs({"scoped": {"datadog": {"type": "http", "url": "https://example.invalid/mcp"}}})
        self.claude({"bg_id": "a1b2", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S", "status": "busy"}]})
        _, out = self.foundry("unit", "launch", "alpha")
        self.assertEqual(out["mcp"]["added"], ["datadog"])
        argv = self.bg_calls()[-1]["argv"]
        self.assertEqual(json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"], {
            "datadog": {"type": "http", "url": "https://example.invalid/mcp"}})


del Launcher  # run its tests once, in test_launcher


if __name__ == "__main__":
    unittest.main()
