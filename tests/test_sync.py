"""Tests for the tracker/Obsidian sync queue: coalescing, lag, failure to-dos, the Stop hook enqueue."""

import io
import unittest
import json
import os
import sys
from contextlib import redirect_stdout

import test_foundry as tf


class SyncBase(tf.Base):
    def at(self, when):
        self.env["FOUNDRY_NOW"] = f"2026-11-18T{when}:00+09:00"

    def rec(self, op, **fields):
        old = os.environ.get("FOUNDRY_NOW")
        os.environ["FOUNDRY_NOW"] = self.env["FOUNDRY_NOW"]
        try:
            self.record(op, **fields)
        finally:
            if old is None:
                os.environ.pop("FOUNDRY_NOW")
            else:
                os.environ["FOUNDRY_NOW"] = old

    def unit(self, uid="u1", tracker="jira"):
        self.foundry("unit", "new", "--id", uid, "--title", "Unit " + uid, "--tracker", tracker,
                     "--ticket", f"PROJ-{uid[1:]}")

    def drain(self):
        return self.foundry("sync", "drain", "--json")[1]["items"]


class LongReports(SyncBase):
    def test_a_2000_char_report_reaches_events_and_sync_whole(self):
        self.unit("u1")
        text = ("결과 " + "x" * 60 + " ") * 32
        text = text[:1990] + " END-MARK"
        self.assertGreaterEqual(len(text), 1999)
        self.foundry("unit", "report", "u1", "--text", text)
        _, ev = self.foundry("events", "pending")
        e = [x for x in ev if x["kind"] == "report"][0]
        self.assertEqual(e["text"], text)
        self.assertIn("foundry unit show u1", e["summary"])
        self.assertLessEqual(len(e["summary"]), 400)
        items = {i["target"]: i for i in self.drain()}
        self.assertIn("jira", items)
        for target in ("jira", "obs"):
            if target not in items:
                continue
            line = [x for x in items[target]["lines"] if "보고:" in x][0]
            self.assertTrue(line.endswith("END-MARK"), line[-40:])
            self.assertIn(" ".join(text.split()), line)
            self.assertIn("Never paste a report verbatim", items[target]["instructions"])

    def test_report_rule_rides_only_on_items_with_a_report(self):
        self.unit("u1")
        self.rec("unit.status", id="u1", status="in_progress", note="started")
        for i in self.drain():
            self.assertNotIn("보고:", i["instructions"])

    def test_dispatcher_prompt_carries_the_rewrite_and_cloudid_rules(self):
        with open(os.path.join(tf.ROOT, "agents", "dispatcher.md")) as f:
            body = f.read()
        self.assertIn("Never paste a report verbatim, to Jira, Linear or Obsidian alike", body)
        self.assertIn("getAccessibleAtlassianResources", body.split("---")[1])  # in the tools list
        self.assertIn("call `getAccessibleAtlassianResources` and pass the `cloudId` of the site in prefs `sync.jira.site`", body)


class ReviewAskMovesTicket(SyncBase):
    SEND = "mcp__plugin_acme_acme-slack__slack_send_message"

    def ask(self, text="<@U01ABCDEF> example-org/x#5 のレビューをお願いします", ok=True):
        self.hook("PostToolUse", tool_name=self.SEND, tool_input={"channel_id": "C0123ABCD", "thread_ts": "1731900000.123456",
                                                                    "message": text}, tool_response={"ok": ok})

    def jira(self):
        return next(i for i in self.drain() if i["target"] == "jira")

    def test_sent_review_ask_asks_for_the_in_review_transition(self):
        self.unit("u1")
        self.foundry("unit", "keys", "u1", "--pr", "example-org/x#5")
        self.ask()
        item = self.jira()
        self.assertEqual(item["transition"], "in-review")
        self.assertIn("listJiraIssueTransitions", item["instructions"])
        self.assertIn("never edit", item["instructions"])
        self.assertTrue(any("리뷰 요청 발송: example-org/x#5" in x for x in item["lines"]))
        # A failed transition becomes one unit report; the batch still closes (no retry loop).
        _, out = self.foundry("sync", "done", item["batch"], "--ticket-status", "進行中",
                              "--transition-error", "no transition to 検討中")
        self.assertEqual((out["ticket_states"], out["reported"]), (1, True))
        s = self.snap()
        self.assertIn("no transition to 検討中", s["units"]["u1"]["notes"][-1]["text"])
        self.assertEqual(s["sync_batches"][item["batch"]]["state"], "done")
        self.assertNotIn("transition", self.jira())  # the report syncs as a plain comment

    def test_no_transition_when_already_in_review_or_unsent(self):
        self.unit("u1")
        self.foundry("unit", "keys", "u1", "--pr", "example-org/x#5")
        self.ask(ok=False)  # failed send: nothing to move
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--text", "progress")
        item = self.jira()
        self.assertNotIn("transition", item)
        self.foundry("sync", "done", item["batch"], "--ticket-status", "検討中")
        self.ask()
        self.assertNotIn("transition", self.jira())

    def test_an_ask_naming_no_unit_moves_nothing(self):
        self.unit("u1")
        self.ask("<@U01ABCDEF> example-org/other#9 のレビューをお願いします")
        self.assertEqual([i for i in self.drain() if i.get("transition")], [])


class TicketState(SyncBase):
    def test_sync_done_with_ticket_status_resolves_ticket_ref(self):
        self.unit("u1")
        self.foundry("unit", "new", "--id", "u2", "--title", "Unit u2")
        self.foundry("unit", "block", "u2", "--on", "ticket:PROJ-1:Done")
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--text", "merged")
        item = next(i for i in self.drain() if i["target"] == "jira")
        self.assertIn("--ticket-status", item["instructions"])
        _, out = self.foundry("sync", "done", item["batch"], "--ticket-status", "Done")
        self.assertEqual(out["ticket_states"], 1)
        self.assertEqual(self.snap()["refs"]["ticket:PROJ-1"]["value"], "Done")
        self.assertEqual(self.snap()["units"]["u2"]["blocked_by"], [])
        self.foundry("sync", "enqueue", "u1", "--target", "obs", "--text", "x")
        obs = next(i for i in self.drain() if i["target"] == "obs")
        self.foundry("sync", "done", obs["batch"], "--ticket-status", "Done", ok=(1,))


class Trackers(SyncBase):
    """prefs `trackers` (default ["jira"]) decides which trackers exist at all."""

    def set_trackers(self, *names, **preflight):
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"trackers": list(names), "jira_base": "https://example.atlassian.net",
                       "preflight": {"mcp_list_cmd": [], **preflight}}, f)

    def check(self):
        sys.path.insert(0, tf.SCRIPTS)
        import factory
        import prefs
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            return factory._check_trackers(prefs.load())
        finally:
            os.environ.pop("FOUNDRY_STATE_DIR") if old is None else os.environ.update(FOUNDRY_STATE_DIR=old)

    def test_jira_only_never_produces_a_linear_sync_attempt(self):
        self.unit("u1")
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--text", "merged")
        rc, _ = self.foundry("unit", "new", "--id", "u2", "--title", "U2", "--tracker", "linear", ok=(2,))
        self.foundry("unit", "new", "--id", "u3", "--title", "U3")
        self.foundry("unit", "tracker", "u3", "linear", ok=(2,))
        # A unit left on linear from before, with a queued linear line: still never handed out.
        self.record("unit.tracker", id="u3", tracker="linear")
        self.record("sync.needed", unit="u3", target="linear", event="manual", text="old", ts="2026-11-18T09:00:00+09:00",
                    key="k-old")
        self.foundry("unit", "set", "u3", "--status", "in_progress", "--by", "owner")
        self.foundry("sync", "enqueue", "u3", "--target", "linear", "--text", "x", ok=(2,))
        self.foundry("intake", "add", "--source", "linear", "--ref", "ENG-1", "--title", "t", "--kind", "chore", ok=(2,))
        items = self.drain()
        self.assertTrue(items)
        self.assertNotIn("linear", {i["target"] for i in items})
        self.assertIn("jira", {i["target"] for i in items})
        self.set_trackers("jira")
        self.assertEqual(self.check(), (True, "jira"))  # no Linear MCP complaint when Linear is off

    def test_jira_without_jira_base_names_the_missing_key(self):
        # 0.1.33: Jira links come from `jira_base`; preflight names it instead of drawing broken links.
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"trackers": ["jira"], "preflight": {"mcp_list_cmd": []}}, f)
        ok, detail = self.check()
        self.assertFalse(ok)
        self.assertIn("`jira_base` is unset", detail)

    def test_repo_with_disabled_tracker_is_refused(self):
        repo = os.path.join(self.dir, "r")
        os.makedirs(repo)
        tf.subprocess.run(["git", "init", "-q", repo], check=True)
        r = tf.subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "repo", "add", "r", "--path", repo,
                               "--tracker", "linear"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("tracker linear is not enabled", r.stderr)
        self.foundry("repo", "add", "r", "--path", repo, "--tracker", "jira")
        with open(os.path.join(self.dir, "repos.json")) as f:
            repos = json.load(f)
        repos["old"] = {"path": repo, "tracker": "linear"}
        with open(os.path.join(self.dir, "repos.json"), "w") as f:
            json.dump(repos, f)
        ok, detail = self.check()
        self.assertFalse(ok)
        self.assertIn("old=linear", detail)

    def test_linear_enabled_keeps_the_linear_path_and_preflight_checks_its_tools(self):
        self.set_trackers("jira", "linear")
        self.foundry("unit", "new", "--id", "u2", "--title", "U2", "--tracker", "linear", "--ticket", "ENG-2")
        self.foundry("sync", "enqueue", "u2", "--target", "linear", "--text", "x")
        self.assertIn("linear", {i["target"] for i in self.drain()})
        ok, detail = self.check()
        self.assertFalse(ok)  # enabled, but no Linear MCP server configured
        self.assertIn("linear enabled, but no Linear MCP server is configured or connected", detail)
        cfg = os.path.join(self.dir, "claude.json")
        with open(cfg, "w") as f:
            json.dump({"projects": {"/x": {"mcpServers": {"linear": {"type": "http"}}}}}, f)
        self.set_trackers("jira", "linear", mcp_config_paths=[cfg])
        self.assertEqual(self.check(), (True, "jira, linear; linear via linear (configured)"))
        self.set_trackers("bogus")
        _, out = self.foundry("prefs", "show")
        self.assertIn("trackers", out["error"])


    def fake_mcp_list(self, *lines):
        out = os.path.join(self.dir, "mcp-list.txt")
        with open(out, "w") as f:
            f.write("Checking MCP server health…\n\n" + "\n".join(lines) + "\n")
        return ["cat", out]

    def test_a_connected_claude_ai_linear_connector_satisfies_preflight(self):
        self.set_trackers("linear", mcp_config_paths=[], mcp_list_cmd=self.fake_mcp_list(
            "claude.ai Linear: https://mcp.linear.app/mcp - \u2714 Connected",
            "other: x (HTTP) - \u2714 Connected"))
        self.assertEqual(self.check(), (True, "linear; linear via claude_ai_Linear (connected)"))
        self.set_trackers("linear", mcp_config_paths=[], mcp_list_cmd=self.fake_mcp_list(
            "claude.ai Linear: https://mcp.linear.app/mcp - ! Needs authentication",
            "linear-mcp: npx x - \u2714 Connected"))
        ok, detail = self.check()
        self.assertFalse(ok)
        self.assertIn("found linear-mcp, rename it to `linear`", detail)

    def test_linear_only_config_end_to_end(self):
        """Switching is settings-only: prefs trackers ["linear"], a repo on linear, a connected server."""
        self.set_trackers("linear", mcp_config_paths=[], mcp_list_cmd=self.fake_mcp_list(
            "linear: https://mcp.linear.app/mcp (HTTP) - \u2714 Connected"))
        self.foundry("unit", "new", "--id", "l1", "--title", "L1", "--tracker", "linear", "--ticket", "ENG-1")
        self.foundry("sync", "enqueue", "l1", "--target", "linear", "--text", "x")
        self.assertEqual({i["target"] for i in self.drain()}, {"linear"})
        self.foundry("unit", "new", "--id", "j1", "--title", "J1", "--tracker", "jira", ok=(2,))
        self.foundry("sync", "enqueue", "l1", "--target", "jira", "--text", "x", ok=(2,))
        self.foundry("intake", "add", "--source", "jira", "--ref", "PROJ-1", "--title", "t", "--kind", "chore", ok=(2,))
        self.assertEqual(self.check(), (True, "linear; linear via linear (connected)"))


class LinearOptIn(tf.unittest.TestCase):
    def test_dispatcher_keeps_linear_server_wildcards(self):
        with open(os.path.join(tf.ROOT, "agents", "dispatcher.md")) as f:
            tools = [t.strip() for t in f.read().split("\ntools:", 1)[1].split("\n", 1)[0].split(",")]
        self.assertIn("mcp__linear__*", tools)
        self.assertIn("mcp__claude_ai_Linear__*", tools)

    def test_plugin_and_marketplace_versions_agree(self):
        with open(os.path.join(tf.ROOT, ".claude-plugin", "plugin.json")) as f:
            version = json.load(f)["version"]
        with open(os.path.join(os.path.dirname(tf.ROOT), ".claude-plugin", "marketplace.json")) as f:
            entry = next(x for x in json.load(f)["plugins"] if x["name"] == "foundry")
        self.assertEqual(entry["version"], version)


class Coalescing(SyncBase):
    def test_one_item_per_unit_per_target_with_lines_merged(self):
        self.unit("u1")
        self.unit("u2", tracker="none")
        self.rec("unit.status", id="u1", status="in_progress")
        self.rec("pr.opened", unit="u1", repo="o/r", number=5, url="https://github.com/o/r/pull/5")
        self.rec("unit.report", id="u1", text="tests pass", eid="e1")
        self.rec("unit.status", id="u2", status="done", note="shipped")
        items = self.drain()
        got = sorted((i["unit"], i["target"]) for i in items)
        self.assertEqual(got, [("u1", "jira"), ("u1", "obs"), ("u2", "obs")])
        jira = next(i for i in items if i["target"] == "jira")
        self.assertEqual(len(jira["lines"]), 3)
        self.assertIn("PR 오픈: https://github.com/o/r/pull/5", "\n".join(jira["lines"]))
        self.assertIn("진행 중", jira["status_line"])
        self.assertEqual(jira["tickets"], ["PROJ-1"])
        self.assertIn("never edit the description", jira["instructions"])
        self.assertIn("vault_append", next(i for i in items if i["target"] == "obs")["instructions"])
        # In flight: a second drain offers nothing until the batch closes or goes stale.
        self.assertEqual(self.drain(), [])
        for i in items:
            self.foundry("sync", "done", i["batch"])
        self.rec("pr.merged", unit="u1", repo="o/r", number=5)
        items = self.drain()
        self.assertEqual(sorted(i["target"] for i in items), ["jira", "obs"])
        self.assertTrue(all(len(i["lines"]) == 1 and "PR 머지" in i["lines"][0] for i in items))

    def test_failed_batch_is_offered_again(self):
        self.unit("u1")
        self.rec("unit.status", id="u1", status="in_progress")
        b = next(i for i in self.drain() if i["target"] == "jira")
        self.foundry("sync", "failed", b["batch"], "--error", "timeout")
        again = next(i for i in self.drain() if i["target"] == "jira")
        self.assertEqual(again["keys"], b["keys"])


class Lag(SyncBase):
    def test_lag_flag_at_thirty_minutes(self):
        self.unit("u1")
        self.at("10:00")
        self.rec("unit.status", id="u1", status="in_progress")
        self.foundry("sync", "enqueue", "u1")
        self.at("10:29")
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["sync_lagging"], [])
        self.at("10:30")
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["sync_lagging"], ["u1"])
        row = next(r for s in m["sections"] for r in s["rows"] if r["id"] == "u1")
        self.assertEqual(row["sync_lag_min"], 30)
        self.assertIn("동기화 지연", row["sync_text"])
        _, st = self.foundry("sync", "status")
        self.assertTrue(st["due"])


class Failures(SyncBase):
    def test_argument_errors_never_ask_for_a_reconnect(self):
        self.unit("u1")
        for n in range(4):
            self.rec("unit.status", id="u1", status="in_progress", note=f"step {n}")
            b = next(i for i in self.drain() if i["target"] == "jira")
            _, out = self.foundry("sync", "failed", b["batch"], "--error", "cloud id not granted: 1234")
            self.assertEqual((out["shape"], out["todo"]), ("argument", False))
            self.assertIn("getAccessibleAtlassianResources", out["hint"])
        self.assertEqual([td for td in self.snap()["owner_todos"].values() if not td["closed"]], [])
        self.assertIn("getAccessibleAtlassianResources", b["instructions"])

    def test_failure_shapes(self):
        import sync
        for err in ("MCP server not connected", "connection refused", "401 Unauthorized", "token expired",
                    "request timed out", "fetch failed"):
            self.assertEqual(sync.failure_shape(err), "connection", err)
        for err in ("cloud id not granted", "Field 'x' cannot be set", "Issue does not exist", "400 Bad Request"):
            self.assertEqual(sync.failure_shape(err), "argument", err)

    def test_three_failures_open_one_reconnect_todo(self):
        self.unit("u1")
        for n in range(5):
            self.rec("unit.checkpoint", id="u1", note=f"n{n}")
            self.rec("unit.status", id="u1", status="in_progress", note=f"step {n}")
            b = next(i for i in self.drain() if i["target"] == "jira")
            self.foundry("sync", "failed", b["batch"], "--error", "mcp down")
        todos = [td for td in self.snap()["owner_todos"].values() if not td["closed"]]
        self.assertEqual(len(todos), 1)
        self.assertEqual((todos[0]["kind"], todos[0]["target"]), ("mcp-reconnect", "jira"))
        self.assertNotIn("re-auth", todos[0]["text"].replace("재인증 아님", ""))
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            ops = [json.loads(x) for x in f if x.strip()]
        self.assertEqual(sum(1 for r in ops if r["op"] == "owner.todo"), 1)
        b = next(i for i in self.drain() if i["target"] == "jira")
        self.foundry("sync", "done", b["batch"])
        self.assertTrue(all(td["closed"] for td in self.snap()["owner_todos"].values()))


class StopHook(SyncBase):
    def test_stop_enqueues(self):
        self.unit("u1")
        self.start_lead("u1")
        self.rec("unit.status", id="u1", status="done", note="ok")
        env = dict(self.env)
        self.env.update({"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "u1"})
        try:
            out = self.hook("Stop", session_id="SL")
        finally:
            self.env = env
        self.assertIsNone(out)
        pend = self.snap()["units"]["u1"]["sync"]
        self.assertTrue(pend["jira"]["pending"] and pend["obs"]["pending"])

    def test_stop_never_blocks_when_enqueue_raises(self):
        self.unit("u1")
        self.start_lead("u1")
        self.rec("unit.status", id="u1", status="done", note="ok")
        sys.path.insert(0, tf.SCRIPTS)
        import hook
        import sync
        keep = {k: os.environ.get(k) for k in ("FOUNDRY_STATE_DIR", "FOUNDRY_ROLE", "FOUNDRY_UNIT", "FOUNDRY_NOW")}
        os.environ.update({"FOUNDRY_STATE_DIR": self.dir, "FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "u1",
                           "FOUNDRY_NOW": self.env["FOUNDRY_NOW"]})
        orig = sync.enqueue_for

        def boom(*a):
            raise RuntimeError("tracker down")
        sync.enqueue_for = boom
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                hook.stop({"session_id": "SL"})
        finally:
            sync.enqueue_for = orig
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(buf.getvalue(), "")
        with open(os.path.join(self.dir, "enforce.log")) as f:
            self.assertIn("sync-enqueue-error", f.read())


class DecisionLine(unittest.TestCase):
    def test_answer_text_comes_from_the_recorded_key(self):
        import sync
        snap = {"cards": {"d-0001": {"unit": "a", "question": "Which notes format?"}}, "decisions": {}}
        _, _, text = sync.describe({"op": "decide.answer", "id": "d-0001", "key": "A", "by": "owner"}, snap)
        self.assertEqual(text, "결정: Which notes format? → A")
