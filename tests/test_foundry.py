"""Tests for foundry's hooks and scripts. Run: python3 -m unittest discover -s tests (from the plugin root)."""

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
# The shipped defaults are org-neutral; the suite's fixtures (reviewers, repo globs, owner paths) come from here.
os.environ["FOUNDRY_PREFS_OVERLAY"] = os.path.join(ROOT, "tests", "fixtures", "prefs-overlay.json")
os.environ["FOUNDRY_TEST_ANCESTORS"] = "[]"  # the suite itself runs under a claude session; tests that need ancestry set it
HK = os.path.join(SCRIPTS, "housekeep.py")  # the judge may run only this plugin's script
# Never /tmp: a reboot wipes it, and the owner asked for nothing there.
SCRATCH = os.path.expanduser("~/.local/state/foundry-tests")
# The run never touches the user's own install: code that falls back to ~/.local/state/foundry (whose prefs.json
# picks the UI language), the Claude config or ~/.local/bin/foundry (the shim) gets a throwaway home instead.
REAL_HOME = os.path.expanduser("~")
REAL_GUARDED = [os.path.join(REAL_HOME, ".local", "bin", "foundry"),
                os.path.join(REAL_HOME, ".local", "state", "foundry", "prefs.json"),
                os.path.join(REAL_HOME, ".local", "state", "foundry", "config.json")]


def _stamps():
    return {p: (os.stat(p).st_mtime_ns if os.path.exists(p) else None) for p in REAL_GUARDED}


REAL_STAMPS = _stamps()
os.makedirs(SCRATCH, exist_ok=True)
SESSION_DIR = tempfile.mkdtemp(dir=SCRATCH, prefix="session-")
os.environ.update(HOME=os.path.join(SESSION_DIR, "home"), FOUNDRY_STATE_DIR=os.path.join(SESSION_DIR, "state"),
                  CLAUDE_CONFIG_DIR=os.path.join(SESSION_DIR, "claude"), GIT_AUTHOR_NAME="t",
                  GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
os.makedirs(os.environ["HOME"], exist_ok=True)


def _check_real_home():
    shutil.rmtree(SESSION_DIR, True)
    changed = [p for p, v in _stamps().items() if v != REAL_STAMPS[p]]
    if changed:  # unittest has already reported; make the run fail loudly anyway
        sys.stderr.write(f"FAILED: the suite wrote the user's real files: {changed}\n")
        sys.stderr.flush()
        os._exit(3)


atexit.register(_check_real_home)
FAKE_GH = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_gh.py')}"


class Base(unittest.TestCase):
    CREW = True

    def setUp(self):
        os.makedirs(SCRATCH, exist_ok=True)
        self.dir = tempfile.mkdtemp(dir=SCRATCH)
        self.env = {**os.environ, "FOUNDRY_STATE_DIR": self.dir, "FOUNDRY_NOW": "2026-11-18T10:00:00+09:00",
                    "FOUNDRY_GH": FAKE_GH, "FAKE_GH_DATA": os.path.join(self.dir, "gh.json"),
                    # The poll's sweep must never reach the real machine: no worktree dirs, docker, claude.
                    "FOUNDRY_WORKTREE_DIRS": "", "FOUNDRY_DOCKER": "false", "FOUNDRY_CLAUDE": "false",
                    "FOUNDRY_MCP_FAKE": json.dumps({"connected": ["plugin_acme_acme-atlassian",
                                                                  "plugin_acme_acme-datadog"]}),
                    # This install's MCP servers, so the generic agent names map without reading the real config.
                    "FOUNDRY_MCP_SERVERS": json.dumps(["plugin_acme_acme-slack", "plugin_acme_acme-atlassian",
                                                       "plugin_acme_acme-datadog", "obsidian"]),
                    "FOUNDRY_PRESSURE_FAKE": json.dumps({"disk_free_gb": {"/": 500}, "mem_free_pct": 50,
                                                         "load_per_cpu": 0.1})}
        self.env.pop("FOUNDRY_SESSION", None)
        self.gh({})
        if self.CREW:
            # Foundry rules follow the session (L1): session S1 is the foreman's, as SessionStart records it.
            self.hook("SessionStart", session_id="S1", source="startup", **ORCH)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def gh(self, data):
        with open(self.env["FAKE_GH_DATA"], "w") as f:
            json.dump(data, f)

    def foundry(self, *args, ok=(0,)):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args], env=self.env,
                           capture_output=True, text=True)
        self.assertIn(r.returncode, ok, r.stderr + r.stdout)
        try:
            return r.returncode, json.loads(r.stdout) if r.stdout.strip() else None
        except json.JSONDecodeError:
            return r.returncode, r.stdout

    def hook(self, event, raw=False, **inp):
        inp.setdefault("session_id", "S1")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "hook.py"), event], env=self.env,
                           input=json.dumps(inp), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout) if r.stdout.strip() else None
        hso = (out or {}).get("hookSpecificOutput") or {}
        if not raw and set(hso) == {"hookEventName", "updatedInput"} and set(out) == {"hookSpecificOutput"}:
            return None  # 0.1.87: only the appended Slack footer, an allowed call (test_lifecycle_0187 reads it raw)
        return out

    def script(self, name, *args):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, name), *args], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def snap(self):
        sys.path.insert(0, SCRIPTS)
        import foundrylib
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            return foundrylib.replay(foundrylib.read_journal())
        finally:
            if old is None:
                os.environ.pop("FOUNDRY_STATE_DIR")
            else:
                os.environ["FOUNDRY_STATE_DIR"] = old

    def journal(self):
        sys.path.insert(0, SCRIPTS)
        import foundrylib
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            return foundrylib.read_journal()
        finally:
            if old is None:
                os.environ.pop("FOUNDRY_STATE_DIR")
            else:
                os.environ["FOUNDRY_STATE_DIR"] = old

    def denied(self, out):
        return bool(out) and (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"

    def record(self, op, **fields):
        """Write a journal record directly, as the launcher or the sweeper would."""
        sys.path.insert(0, SCRIPTS)
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

    def start_lead(self, unit, sid="SL", role="line-lead", source="startup"):
        """What `foundry unit launch` sets up: the session record, then the lead session's SessionStart
        (FOUNDRY_ROLE/FOUNDRY_UNIT arrive through `--settings env`)."""
        agent = "foundry:line-lead" if role == "line-lead" else "foundry:shop-boss"
        name = ("lead-" if role == "line-lead" else "boss-") + unit
        if source == "startup":
            self.record("session.launch", name=name, unit=unit, role=role, bg_id="bg" + sid, cwd="/wt/" + unit)
        env = dict(self.env)
        self.env.update({"FOUNDRY_ROLE": role, "FOUNDRY_UNIT": unit})
        try:
            return self.hook("SessionStart", session_id=sid, source=source, agent_type=agent)
        finally:
            self.env = env

    def spawn(self, target, prompt="do it", **caller):
        return self.hook("PreToolUse", tool_name="Agent", tool_use_id="t1",
                         tool_input={"subagent_type": target, "prompt": prompt, "description": "x"}, **caller)


# In-process tests too: the MCP mapping never reads the real Claude config.
os.environ.setdefault("FOUNDRY_MCP_SERVERS", json.dumps(["plugin_acme_acme-slack", "plugin_acme_acme-atlassian"]))

ORCH = {"agent_type": "foundry:foreman"}               # main thread of `claude --agent foundry:foreman`
LEAD = {"agent_type": "foundry:line-lead", "session_id": "SL"}   # main thread of a background lead session
MGR = {"agent_type": "foundry:shop-boss", "session_id": "SM"}
SPEC = {"agent_type": "foundry:hand", "agent_id": "P1"}
EXPLORE = {"agent_type": "Explore", "agent_id": "E1"}


class ChainEnforcement(Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--ticket", "PROJ-1")

    def test_foreman_cannot_spawn_hands_or_builtins(self):
        self.assertTrue(self.denied(self.spawn("foundry:hand", **ORCH)))
        self.assertTrue(self.denied(self.spawn("foundry:hand-heavy", **ORCH)))
        self.assertTrue(self.denied(self.spawn("general-purpose", **ORCH)))

    def test_foreman_never_spawns_leads_as_subagents(self):
        out = self.spawn("foundry:line-lead", "UNIT: alpha\nwork", **ORCH)
        self.assertTrue(self.denied(out))
        self.assertIn("foundry unit launch", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertTrue(self.denied(self.spawn("foundry:shop-boss", "UNIT: alpha\nwork", **ORCH)))
        self.assertIsNone(self.spawn("foundry:dispatcher", **ORCH))
        self.assertIsNone(self.spawn("foundry:advisor", **ORCH))

    def test_only_the_foreman_launches_sessions(self):
        cli = "cla" + "ude"
        for cmd in ("foundry unit launch alpha", 'python3 "/p/scripts/foundry.py" unit resume alpha',
                    f"{cli} --bg -n x 'hi' < /dev/null", f"cd /w && {cli} -p hi", f"{cli} stop abc"):
            self.assertTrue(self.denied(self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **LEAD)), cmd)
            self.assertTrue(self.denied(self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **SPEC)), cmd)
        self.assertIsNone(self.hook("PreToolUse", tool_name="Bash", tool_input={"command": "foundry unit launch alpha"}, **ORCH))
        self.assertIsNone(self.hook("PreToolUse", tool_name="Bash", tool_input={"command": f"{cli} agents --json"}, **LEAD))
        raw = f"cd /w && {cli} --bg -n lead-alpha --agent foundry:line-lead '/goal x' < /dev/null"
        out = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": raw}, **ORCH)
        self.assertIn("foundry unit launch", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_foundry_session_marker_covers_plain_main_thread(self):
        self.env["FOUNDRY_SESSION"] = "1"
        self.assertTrue(self.denied(self.spawn("foundry:hand")))

    def test_lead_tier_spawns_hands_not_leads(self):
        self.assertIsNone(self.spawn("foundry:hand", **LEAD))
        self.assertIsNone(self.spawn("Explore", **MGR))
        self.assertTrue(self.denied(self.spawn("foundry:line-lead", **LEAD)))
        self.assertTrue(self.denied(self.spawn("foundry:shop-boss", **MGR)))
        self.assertTrue(self.denied(self.spawn("foundry:dispatcher", **LEAD)))
        self.assertIsNone(self.spawn("foundry:advisor", **LEAD))

    def test_hands_and_staff_are_leaves(self):
        self.assertTrue(self.denied(self.spawn("foundry:hand", **SPEC)))
        self.assertTrue(self.denied(self.spawn("Explore", **SPEC)))
        self.assertTrue(self.denied(self.spawn("general-purpose", **{"agent_type": "foundry:dispatcher", "agent_id": "C1"})))

    def test_builtin_worker_under_a_foundry_lead_is_a_leaf(self):
        self.env["FOUNDRY_SESSION"] = "1"
        self.assertTrue(self.denied(self.spawn("general-purpose", **EXPLORE)))

    def test_non_foundry_sessions_are_left_alone(self):
        self.assertIsNone(self.spawn("foundry:hand", session_id="OTHER"))
        self.assertIsNone(self.spawn("general-purpose", session_id="OTHER", **EXPLORE))

    def test_housekeeping_judge_cannot_act(self):
        judge = {"agent_type": "foundry:sweeper-judge", "agent_id": "J1"}
        ok = f'python3 "{HK}" plan --hid "worktree:/a" --verdict safe --reason "merged"'
        self.assertIsNone(self.hook("PreToolUse", tool_name="Bash", tool_input={"command": ok}, **judge))
        for bad in ("git worktree remove /a", 'python3 /x/scripts/housekeep.py execute',
                    'python3 /x/scripts/housekeep.py scan; rm -rf /a'):
            self.assertTrue(self.denied(self.hook("PreToolUse", tool_name="Bash", tool_input={"command": bad}, **judge)), bad)

    def test_foreman_does_not_message_hands(self):
        self.start_lead("alpha")
        self.spawn("foundry:hand", **LEAD)
        self.hook("SubagentStart", session_id="SL", agent_id="P1", agent_type="foundry:hand")
        self.assertTrue(self.denied(self.hook("PreToolUse", tool_name="SendMessage", tool_input={"to": "P1", "message": "hi"}, **ORCH)))
        self.assertIsNone(self.hook("PreToolUse", tool_name="SendMessage", tool_input={"to": "lead-alpha", "message": "hi"}, **ORCH))

    def test_a_finished_unit_is_never_woken_by_message(self):
        self.start_lead("alpha")
        self.foundry("unit", "status", "alpha", "done", "--note", "merged")
        for who in (ORCH, {"session_id": "OTHER"}):
            out = self.hook("PreToolUse", tool_name="SendMessage", tool_input={"to": "lead-alpha", "message": "one more"}, **who)
            self.assertTrue(self.denied(out))
            self.assertIn("foundry inbox route --unit alpha", out["hookSpecificOutput"]["permissionDecisionReason"])
        _, out = self.foundry("inbox", "route", "--unit", "alpha", "--text", "one more thing")
        self.assertFalse(out["lead_running"])
        self.assertIn("next `foundry unit launch|resume alpha`", out["next"])

    def test_a_stopped_session_is_not_messaged_either(self):
        self.start_lead("alpha")
        self.record("session.state", name="lead-alpha", state="stopped")
        self.assertTrue(self.denied(self.hook("PreToolUse", tool_name="SendMessage",
                                              tool_input={"to": "lead-alpha [3fa9c1]", "message": "x"}, **ORCH)))

    def test_topology_records_parent_and_unit(self):
        self.start_lead("alpha")
        self.spawn("foundry:hand", **LEAD)
        self.hook("SubagentStart", session_id="SL", agent_id="P1", agent_type="foundry:hand")
        s = self.snap()
        self.assertEqual(s["agents"]["P1"]["parent"], "main")
        self.assertEqual(s["agents"]["P1"]["unit"], "alpha")
        self.assertEqual(s["agents"]["P1"]["session"], "SL")
        self.assertEqual(s["sessions"]["lead-alpha"]["session_id"], "SL")
        self.assertEqual(s["sessions"]["lead-alpha"]["state"], "running")
        self.assertEqual(s["foundry_sessions"]["SL"]["unit"], "alpha")
        self.assertEqual(s["units"]["alpha"]["status"], "in_progress")

    def test_every_decision_is_logged(self):
        self.spawn("foundry:hand", **ORCH)
        self.spawn("foundry:hand", **LEAD)
        with open(os.path.join(self.dir, "enforce.log")) as f:
            lines = [json.loads(x) for x in f]
        self.assertEqual([x["decision"] for x in lines], ["deny", "allow"])
        self.assertEqual(lines[0]["caller"], "foreman")


class SlackDedup(Base):
    TOOL = "mcp__plugin_acme_acme-slack__slack_send_message"
    MSG = {"channel_id": "C1", "thread_ts": "1.2",
           "message": "<@U1> レビューお願いします https://github.com/example-org/webapp/pull/12"}

    def send(self, msg=None, tool=None):
        msg = msg or self.MSG
        out = self.hook("PreToolUse", tool_name=tool or self.TOOL, tool_input=msg)
        if not (out and self.denied(out)):
            self.hook("PostToolUse", tool_name=tool or self.TOOL, tool_input=msg, tool_response={"ok": True})
        return out

    def test_second_identical_send_is_blocked(self):
        self.assertIsNone(self.send())
        out = self.send()
        self.assertTrue(self.denied(out))
        self.assertIn("Duplicate", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_same_pr_reworded_is_still_a_duplicate(self):
        self.send()
        reworded = dict(self.MSG, message="<@U1> 再掲: レビューをお願いします example-org/webapp#12")
        self.assertTrue(self.denied(self.send(reworded)))

    def test_different_recipient_or_pr_is_not(self):
        self.send()
        self.assertIsNone(self.send(dict(self.MSG, message=self.MSG["message"].replace("U1", "U2"))))
        self.assertIsNone(self.send(dict(self.MSG, message=self.MSG["message"].replace("/12", "/13"))))

    def test_in_flight_duplicate_is_blocked(self):
        self.hook("PreToolUse", tool_name=self.TOOL, tool_input=self.MSG)
        self.assertTrue(self.denied(self.hook("PreToolUse", tool_name=self.TOOL, tool_input=self.MSG)))

    def test_failed_send_can_be_retried(self):
        self.hook("PreToolUse", tool_name=self.TOOL, tool_input=self.MSG)
        self.hook("PostToolUse", tool_name=self.TOOL, tool_input=self.MSG, tool_response={"is_error": True})
        self.assertIsNone(self.hook("PreToolUse", tool_name=self.TOOL, tool_input=self.MSG))

    def test_quiet_hours(self):
        self.env["FOUNDRY_NOW"] = "2026-11-18T22:30:00+09:00"
        out = self.send()
        self.assertTrue(self.denied(out))
        self.assertIn("quiet", out["hookSpecificOutput"]["permissionDecisionReason"])
        sched = "mcp__plugin_acme_acme-slack__slack_schedule_message"
        late = 1795440600   # 2026-11-23 22:30 JST
        morning = 1795480200  # 2026-11-24 09:30 JST
        self.assertTrue(self.denied(self.send(dict(self.MSG, post_at=late), sched)))
        self.assertIsNone(self.send(dict(self.MSG, post_at=morning), sched))

    def test_owner_named_override(self):
        self.env["FOUNDRY_NOW"] = "2026-11-18T22:30:00+09:00"
        self.foundry("send", "allow-now", "--channel", "C1", "--reason", "owner: send it now")
        self.assertIsNone(self.send())


class DecisionDedup(Base):
    def test_ask_once_then_reuse_answer(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        rc, out = self.foundry("decision", "ask", "--key", "alpha:merge-window", "--unit", "alpha",
                            "--question", "Merge today?", ok=(10,))
        self.assertEqual(out["status"], "escalate")
        rc, out = self.foundry("decision", "ask", "--key", "alpha:merge-window", "--question", "Merge today?", ok=(3,))
        self.assertEqual(out["status"], "pending")
        self.assertEqual(self.snap()["units"]["alpha"]["status"], "waiting_owner")
        self.foundry("decision", "answer", "--key", "alpha:merge-window", "--answer", "yes, after 15:00")
        rc, out = self.foundry("decision", "ask", "--key", "alpha:merge-window", "--question", "Can we merge?", ok=(0,))
        self.assertEqual(out["answer"], "yes, after 15:00")
        u = self.snap()["units"]["alpha"]
        self.assertEqual(u["status"], "in_progress")
        self.assertIn("yes, after 15:00", u["inbox"][-1]["text"])


class WithdrawAsk(Base):
    def test_the_asker_withdraws_a_moot_question(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.foundry("decision", "ask", "--key", "alpha:reviewer", "--unit", "alpha", "--question", "Who reviews?", ok=(10,))
        self.foundry("decide", "withdraw", "alpha:reviewer", "--by", "lead", ok=(2,))  # --reason is required
        _, out = self.foundry("decide", "withdraw", "alpha:reviewer", "--by", "lead", "--reason", "PR merged")
        self.assertEqual(out["withdrawn"], "alpha:reviewer")
        d = self.snap()["decisions"]["alpha:reviewer"]
        self.assertEqual((d["withdrawn"], d["answer"]), ("PR merged (lead)", "withdrawn: PR merged (lead)"))
        self.assertEqual(self.snap()["units"]["alpha"]["status"], "in_progress")
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["asks"], [])
        self.foundry("decide", "withdraw", "nope", "--by", "lead", "--reason", "x", ok=(2,))


class LeadAsksReachTheOwner(Base):
    def test_a_lead_ask_is_listed_counted_and_answered_into_the_inbox(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.foundry("decision", "ask", "--key", "alpha:data-security-check", "--unit", "alpha",
                     "--question", "Is the data check OK?", ok=(10,))
        _, rows = self.foundry("decide", "list")
        self.assertEqual([(r["kind"], r["id"], r["unit"]) for r in rows], [("ask", "alpha:data-security-check", "alpha")])
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["summary"]["decisions"], 1)
        self.assertTrue(m["summary"]["attention"])
        # `/foundry decide answer <key> "<text>"` runs exactly this argv (plus --by owner).
        _, out = self.foundry("decide", "answer", "alpha:data-security-check", "yes, go ahead", "--by", "owner")
        self.assertEqual((out["answered"], out["delivered_to"]), ("alpha:data-security-check", "alpha"))
        s = self.snap()
        self.assertEqual(s["decisions"]["alpha:data-security-check"]["answer"], "yes, go ahead")
        self.assertIn("Owner decision alpha:data-security-check: yes, go ahead", s["units"]["alpha"]["inbox"][-1]["text"])
        self.assertEqual(s["units"]["alpha"]["status"], "in_progress")
        self.assertEqual(self.foundry("decide", "list")[1], [])
        # Answering again the same way is a no-op; differently is refused.
        self.foundry("decide", "answer", "alpha:data-security-check", "yes, go ahead", "--by", "owner")
        self.foundry("decide", "answer", "alpha:data-security-check", "no", "--by", "owner", ok=(2,))
        self.foundry("decide", "answer", "nope:unknown", "x", "--by", "owner", ok=(2,))


class Outbox(Base):
    def test_a_lead_files_an_outbox_item_with_ids(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        rc, _ = self.foundry("outbox", "add", "alpha", "--channel", "#team", "--kind", "review-ask", "--text", "x", ok=(2,))
        self.foundry("outbox", "add", "alpha", "--channel", "C0123ABCD", "--thread", "yesterday", "--text", "x", ok=(2,))
        _, out = self.foundry("outbox", "add", "alpha", "--channel", "C0123ABCD", "--thread", "1731900000.123456",
                              "--kind", "review-ask", "--text", "<@U01ABCDEF> example-org/x#5 のレビューをお願いします")
        self.assertEqual(out["status"], "queued")
        _, again = self.foundry("outbox", "add", "alpha", "--channel", "C0123ABCD", "--thread", "1731900000.123456",
                                "--kind", "review-ask", "--text", "<@U01ABCDEF>  example-org/x#5 のレビューをお願いします")
        self.assertEqual((again["status"], again["oid"]), ("exists", out["oid"]))
        _, items = self.foundry("outbox", "list")
        self.assertEqual([(i["channel"], i["thread"], i["prs"]) for i in items],
                         [("C0123ABCD", "1731900000.123456", ["example-org/x#5"])])
        ev = [e for e in self.snap()["events"] if e["kind"] == "outbox"]
        self.assertEqual([e["ref"] for e in ev], [out["oid"]])  # wakes the foreman's waiter
        self.foundry("outbox", "done", out["oid"], "--ts", "1731900100.000100")
        self.assertEqual(self.foundry("outbox", "list")[1], [])
        self.foundry("outbox", "done", out["oid"], ok=(2,))


class FeedbackRouting(Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha API", "--ticket", "PROJ-1",
                  "--pr", "example-org/webapp#12", "--topic", "apikey")
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta", "--ticket", "PROJ-2")

    def test_attaches_by_ticket_pr_topic_or_id(self):
        _, out = self.foundry("inbox", "route", "--text", "PROJ-1 also cover the 4xx path")
        self.assertEqual(out["attached"], "alpha")
        _, out = self.foundry("inbox", "route", "--text", "on https://github.com/example-org/webapp/pull/12 rename the flag")
        self.assertEqual(out["attached"], "alpha")
        _, out = self.foundry("inbox", "route", "--text", "expiry should be 90 days", "--topic", "apikey")
        self.assertEqual(out["attached"], "alpha")
        _, out = self.foundry("inbox", "route", "--text", "ship it", "--unit", "beta")
        self.assertEqual(out["attached"], "beta")
        self.assertEqual(len(self.snap()["units"]["alpha"]["inbox"]), 3)
        self.assertEqual(len(self.snap()["units"]), 2)

    def test_no_match_creates_nothing_unless_new(self):
        rc, out = self.foundry("inbox", "route", "--text", "look at the dashboard", ok=(5,))
        self.assertTrue(out["no_match"])
        self.assertEqual(len(self.snap()["units"]), 2)
        self.foundry("inbox", "route", "--text", "look at the dashboard", "--new", "dash")
        self.assertIn("dash", self.snap()["units"])

    def test_ambiguous_asks_for_unit(self):
        self.foundry("inbox", "route", "--text", "PROJ-1 and PROJ-2 share a fix", ok=(4,))

    def test_new_unit_for_existing_ticket_is_refused(self):
        rc, out = self.foundry("unit", "new", "--title", "Alpha again", "--ticket", "PROJ-1", ok=(1,))
        self.assertEqual(out["units"], ["alpha"])

    def test_inbox_is_injected_into_the_next_lead_run(self):
        self.foundry("inbox", "route", "--text", "PROJ-1 use the new endpoint")
        out = self.start_lead("alpha")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("UNIT: alpha", ctx)  # the handoff brief
        self.assertIn("use the new endpoint", ctx)
        self.assertTrue(all(i["delivered"] for i in self.snap()["units"]["alpha"]["inbox"]))
        ctx = self.start_lead("alpha", source="resume")["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("use the new endpoint", ctx)  # delivered once

    def test_running_lead_is_reported_for_direct_message(self):
        self.start_lead("alpha")
        _, out = self.foundry("inbox", "route", "--text", "PROJ-1 hurry")
        self.assertTrue(out["lead_running"])
        self.assertEqual(out["session"], "lead-alpha")


class StateRecovery(Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.start_lead("alpha")

    def test_snapshot_rebuilds_from_journal(self):
        with open(os.path.join(self.dir, "snapshot.json")) as f:
            before = json.load(f)
        os.remove(os.path.join(self.dir, "snapshot.json"))
        with open(os.path.join(self.dir, "journal.jsonl"), "a") as f:
            f.write('{"ts": "2026-11-18T10:00:00+09:00", "op": "unit.st')  # torn write from a crash
        self.foundry("rebuild")
        with open(os.path.join(self.dir, "snapshot.json")) as f:
            after = json.load(f)
        self.assertEqual(before["units"], after["units"])

    def test_session_start_after_reboot_flags_lost_leads(self):
        self.env["FOUNDRY_TEST_ANCESTORS"] = json.dumps([[99999999, "Mon Jan  1 00:00:00 2001"]])
        self.hook("SessionStart", session_id="S1", source="startup", **ORCH)  # the old foreman's process, now gone
        self.env["FOUNDRY_TEST_ANCESTORS"] = "[]"
        self.spawn("foundry:dispatcher", **ORCH)
        self.hook("SubagentStart", session_id="S1", agent_id="D1", agent_type="foundry:dispatcher")
        self.spawn("foundry:hand", **LEAD)
        self.hook("SubagentStart", session_id="SL", agent_id="P1", agent_type="foundry:hand")
        out = self.hook("SessionStart", session_id="S2", source="startup", **ORCH)
        self.assertNotIn("alpha (Alpha)", out["hookSpecificOutput"]["additionalContext"])  # its session is live
        self.record("session.state", name="lead-alpha", state="lost")  # the sweeper saw it gone after a reboot
        out = self.hook("SessionStart", session_id="S2", source="startup", **ORCH)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("alpha (Alpha)", ctx)
        self.assertIn("foundry unit resume", ctx)
        s = self.snap()
        self.assertEqual(s["agents"]["D1"]["status"], "lost")      # the old foreman's subagent
        self.assertEqual(s["agents"]["P1"]["status"], "running")   # another session's hand is not the foreman's call
        self.assertIn("S2", s["foundry_sessions"])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "board.html")))

    def test_compaction_keeps_running_subagents(self):
        self.spawn("foundry:advisor", **ORCH)
        self.hook("SubagentStart", session_id="S1", agent_id="A1", agent_type="foundry:advisor")
        out = self.hook("SessionStart", session_id="S1", source="compact", **ORCH)
        self.assertEqual(self.snap()["agents"]["A1"]["status"], "running")
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Subagents still running", ctx)
        self.assertIn("A1", ctx)
        # Another foreman session with no evidence the first one's process ended: nothing is marked either.
        self.hook("SessionStart", session_id="S2", source="startup", **ORCH)
        self.assertEqual(self.snap()["agents"]["A1"]["status"], "running")
        self.assertFalse([e for e in self.snap()["events"] if e["kind"] == "agent.lost"])

    def test_real_loss_reports_what_the_agent_was_asked(self):
        self.hook("PreToolUse", tool_name="Agent", tool_use_id="t9",
                  tool_input={"subagent_type": "foundry:hand", "prompt": "unit alpha: long prompt",
                              "description": "collect the BPaaS facts"}, **LEAD)
        self.hook("SubagentStart", session_id="SL", agent_id="P9", agent_type="foundry:hand")
        self.start_lead("alpha", source="resume")  # the lead session restarted: its old subagents died with it
        s = self.snap()
        self.assertEqual(s["agents"]["P9"]["status"], "lost")
        inbox = [i["text"] for i in s["units"]["alpha"]["inbox"] if i["iid"] == "lost-P9"]
        self.assertEqual(len(inbox), 1)
        self.assertIn("collect the BPaaS facts", inbox[0])
        ev = [e for e in s["events"] if e["kind"] == "agent.lost"]
        self.assertEqual([(e["unit"], e["ref"]) for e in ev], [("alpha", "P9")])

    def test_named_hand_keeps_its_role_and_unit(self):
        self.hook("PreToolUse", tool_name="Agent", tool_use_id="t7",
                  tool_input={"subagent_type": "foundry:hand-light", "name": "hand-bpaas-facts",
                              "prompt": "unit alpha: facts", "description": "facts"}, **LEAD)
        # A named spawn starts with agent_type = its name, not its foundry type.
        self.hook("SubagentStart", session_id="SL", agent_id="ahand-bpaas-facts-a87e", agent_type="hand-bpaas-facts")
        a = self.snap()["agents"]["ahand-bpaas-facts-a87e"]
        self.assertEqual((a["type"], a["unit"], a["name"]), ("foundry:hand-light", "alpha", "hand-bpaas-facts"))
        named = {"session_id": "SL", "agent_id": "ahand-bpaas-facts-a87e", "agent_type": "hand-bpaas-facts"}
        out = self.hook("PreToolUse", tool_name="Bash", tool_use_id="t8",
                        tool_input={"command": "git push --force origin main"}, **named)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        with open(os.path.join(self.dir, "enforce.log")) as f:
            rows = [json.loads(line) for line in f if '"t8"' in line or "ahand-bpaas" in line]
        rows = [r for r in rows if r.get("agent_id") == "ahand-bpaas-facts-a87e"]
        self.assertTrue(rows)
        self.assertEqual({r["caller"] for r in rows}, {"hand"})
        denied = [d for d in self.snap()["denials"] if "push --force" in (d.get("command") or "")]
        self.assertEqual([d["unit"] for d in denied], ["alpha"])

    def test_resumed_agent_is_one_hand_and_a_resume_event(self):
        self.hook("PreToolUse", tool_name="Agent", tool_use_id="t7",
                  tool_input={"subagent_type": "foundry:hand-light", "name": "hand-bpaas-facts",
                              "prompt": "unit alpha: facts", "description": "facts"}, **LEAD)
        req = {"subagentType": "foundry:hand-light", "name": "hand-bpaas-facts", "tool_use_id": "t7",
               "session_id": "SL", "prompt": "unit alpha: facts"}
        self.foundry("spawn-check", "--json", json.dumps(req))
        aid = "ahand-bpaas-facts-a87e"
        self.hook("SubagentStart", session_id="SL", agent_id=aid, agent_type="hand-bpaas-facts")
        self.hook("SubagentStop", session_id="SL", agent_id=aid)
        self.assertEqual(self.snap()["agents"][aid]["status"], "stopped")
        # SendMessage wakes it: the mod's agent.spawn carries resume, with no tool_use_id of its own.
        self.foundry("spawn-check", "--json", json.dumps({**req, "tool_use_id": None, "resume": aid}))
        self.hook("SubagentStart", session_id="SL", agent_id=aid, agent_type="hand-bpaas-facts")
        s = self.snap()
        self.assertEqual(len(s["hands"]), 1)
        self.assertEqual([a for a in s["agents"] if a == aid], [aid])
        a = s["agents"][aid]
        self.assertEqual((a["status"], a["unit"], a["type"], a["resumes"]), ("running", "alpha", "foundry:hand-light", 1))
        ops = [r["op"] for r in self.journal() if r.get("agent_id") == aid]
        self.assertEqual(ops, ["agent.start", "agent.stop", "agent.resume"])

    def test_non_foundry_session_start_is_silent(self):
        self.env["FOUNDRY_ROLE"] = "foreman"
        self.foundry("watch", "stream", "--kind", "sync", "--once")  # a foreman stream's heartbeat: the foreman is up
        del self.env["FOUNDRY_ROLE"]
        self.assertIsNone(self.hook("SessionStart", session_id="S3", source="startup"))

    def test_task_steps_mirror_to_the_unit(self):
        self.hook("PostToolUse", tool_name="TaskCreate", tool_input={"subject": "write tests", "description": "x"},
                  tool_response={"task": {"id": "1"}}, **LEAD)
        self.hook("PostToolUse", tool_name="TaskUpdate", tool_input={"taskId": "1", "status": "in_progress"}, **LEAD)
        todos = self.snap()["units"]["alpha"]["todos"]
        self.assertEqual(todos["main:1"], {"subject": "write tests", "status": "in_progress"})
        _, text = self.foundry("unit", "brief", "alpha")
        self.assertIn("[in_progress] write tests", text)

    def test_lead_cannot_end_without_recording_state(self):
        out = self.hook("Stop", stop_hook_active=False, **LEAD)
        self.assertEqual(out["decision"], "block")
        self.assertIsNone(self.hook("Stop", stop_hook_active=True, **LEAD))  # once only
        self.assertIsNone(self.hook("Stop", stop_hook_active=False, **ORCH))
        self.foundry("wait", "add", "alpha", "--kind", "pr-review", "--ref", "example-org/webapp#12")
        self.assertIsNone(self.hook("Stop", stop_hook_active=False, **LEAD))

    def test_lead_report_reaches_the_journal_and_wakes_the_foreman(self):
        self.foundry("unit", "report", "alpha", "--text", "PR #12 merged; unit done")
        _, pending = self.foundry("events", "pending")
        self.assertEqual([(e["kind"], e["unit"]) for e in pending], [("report", "alpha")])
        self.assertIn("[report] PR #12 merged", self.snap()["units"]["alpha"]["notes"][-1]["text"])

    def test_brief_carries_everything_needed_to_resume(self):
        self.foundry("unit", "checkpoint", "alpha", "--note", "PR opened, waiting review")
        self.foundry("wait", "add", "alpha", "--kind", "ci", "--ref", "example-org/webapp#12")
        _, text = self.foundry("unit", "brief", "alpha")
        self.assertTrue(text.startswith("UNIT: alpha"))
        self.assertIn("PR opened", text)
        self.assertIn("ci example-org/webapp#12", text)


class Handoff(Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--ticket", "PROJ-1", "--pr", "example-org/webapp#12")
        self.start_lead("alpha")
        for tid, subj in (("1", "add migration"), ("2", "middleware 401")):
            self.hook("PostToolUse", tool_name="TaskCreate", tool_input={"subject": subj},
                      tool_response={"task": {"id": tid}}, **LEAD)
        self.hook("PostToolUse", tool_name="TaskUpdate", tool_input={"taskId": "1", "status": "completed"}, **LEAD)
        self.hook("PostToolUse", tool_name="TaskUpdate", tool_input={"taskId": "2", "status": "in_progress"}, **LEAD)
        self.foundry("wait", "add", "alpha", "--kind", "ci", "--ref", "example-org/webapp#12")
        self.foundry("decision", "ask", "--key", "alpha:window", "--unit", "alpha", "--question", "Deploy today?", ok=(10,))
        self.foundry("handoff", "write", "alpha", "--goal", "Expire API keys after 90 days",
                  "--next", "finish the 401 path, then open the PR", "--file", "app/middleware/auth.py")

    def path(self, uid):
        return os.path.join(self.dir, "handoffs", f"{uid}.md")

    def test_handoff_holds_every_section_and_no_transcript(self):
        _, text = self.foundry("handoff", "show", "alpha")
        order = ["UNIT: alpha", "## Goal", "Expire API keys", "## Done", "add migration", "## In progress",
                 "[in_progress] middleware 401", "## Waits", "ci example-org/webapp#12", "## Decisions",
                 "OPEN", "do not re-ask", "## Next step", "finish the 401 path", "## Pointers",
                 "ticket: PROJ-1", "pr: example-org/webapp#12", "file: app/middleware/auth.py"]
        at = 0
        for x in order:  # each item appears after the previous one
            at = text.index(x, at) + len(x)

    def test_handoff_stays_compact(self):
        for i in range(60):
            self.foundry("unit", "checkpoint", "alpha", "--note", f"step {i} " + "x" * 600)
        _, text = self.foundry("handoff", "show", "alpha")
        self.assertLess(len(text), 4000)
        self.assertNotIn("step 0 ", text)
        self.assertIn("step 59", text)

    def test_respawn_brief_is_the_handoff(self):
        _, brief = self.foundry("unit", "brief", "alpha")
        _, shown = self.foundry("handoff", "show", "alpha")
        self.assertEqual(brief, shown)

    def test_phase_end_respawn_goes_through_the_event_path(self):
        _, out = self.foundry("handoff", "write", "alpha", "--summary", "phase 1 done", "--respawn")
        self.assertTrue(os.path.exists(out["handoff"]))
        _, pending = self.foundry("events", "pending")
        self.assertEqual([e["kind"] for e in pending], ["respawn"])
        self.assertIsNone(self.hook("Stop", stop_hook_active=False, **LEAD))

    def test_lead_session_end_leaves_a_handoff_file(self):
        self.hook("SessionEnd", reason="other", **LEAD)
        self.assertEqual(self.snap()["sessions"]["lead-alpha"]["state"], "stopped")
        with open(self.path("alpha")) as f:
            self.assertIn("finish the 401 path", f.read())

    def test_compaction_saves_and_second_one_asks_for_clear(self):
        self.hook("SessionStart", session_id="S1", source="startup", **ORCH)
        self.hook("PreCompact", session_id="S1", trigger="auto", **ORCH)
        self.assertTrue(os.path.exists(self.path("_foreman")))
        out = self.hook("SessionStart", session_id="S1", source="compact", **ORCH)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("# Handoff: foundry foreman", ctx)
        self.assertNotIn("/clear", ctx)
        self.hook("PreCompact", session_id="S1", trigger="auto", **ORCH)
        ctx = self.hook("SessionStart", session_id="S1", source="compact", **ORCH)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("/clear", ctx)
        ctx = self.hook("SessionStart", session_id="S1", source="clear", **ORCH)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("alpha:window", ctx)  # open decision carried over the clear
        self.assertIn("alpha (Alpha)", ctx)
        self.assertEqual(self.snap()["foundry_sessions"]["S1"]["compactions"], 0)

    def test_compaction_inside_a_lead_saves_its_unit(self):
        self.hook("PreCompact", trigger="auto", **LEAD)
        self.assertTrue(os.path.exists(self.path("alpha")))

    def test_session_end_saves_foreman_handoff_only_for_foundry(self):
        self.hook("SessionEnd", session_id="OTHER", reason="clear")
        self.assertFalse(os.path.exists(self.path("_foreman")))
        self.hook("SessionEnd", session_id="S1", reason="clear", **ORCH)
        self.assertTrue(os.path.exists(self.path("_foreman")))
        self.assertEqual(self.snap()["units"]["_foreman"]["handoff"]["last_trigger"], "session_end:clear")


class Import(Base):
    def test_import_is_idempotent_and_skips_held_tickets(self):
        self.foundry("unit", "new", "--id", "held", "--title", "Held", "--ticket", "PROJ-5")
        src = os.path.join(self.dir, "export.json")
        with open(src, "w") as f:
            json.dump([{"id": "u1", "title": "One", "status": "active", "ticket": ["PROJ-1"], "notes": ["PR open"]},
                       {"id": "u2", "title": "Two", "ticket": ["PROJ-5"]}], f)
        out = self.script("import_units.py", src, "--source", "sangsoe")
        self.assertEqual(out["imported"], ["u1"])
        self.assertEqual(out["skipped"][0]["existing"], ["held"])
        self.assertEqual(self.script("import_units.py", src)["imported"], [])
        u = self.snap()["units"]["u1"]
        self.assertEqual(u["status"], "in_progress")
        self.assertEqual(u["notes"][-1]["text"], "PR open")

    def test_unit_import_verb_dry_run_then_real(self):
        src = os.path.join(self.dir, "units.json")
        with open(src, "w") as f:
            json.dump([{"id": "u1", "title": "One", "status": "blocked", "kind": "repo", "repo": "o/r",
                        "notes": ["waiting on dave", "PR o/r#3 open"]},
                       {"id": "u1", "title": "dup in file"}, {"id": "../x", "title": "bad id"}], f)
        _, dry = self.foundry("unit", "import", src, "--dry-run", "--source", "export")
        self.assertEqual((dry["imported"], dry["dry_run"]), (["u1"], True))
        self.assertEqual([s.get("existing") or "error" for s in dry["skipped"]], [["u1"], "error"])
        self.assertNotIn("u1", self.snap()["units"])
        _, real = self.foundry("unit", "import", src, "--source", "export")
        self.assertEqual(real["imported"], dry["imported"])
        u = self.snap()["units"]["u1"]
        self.assertEqual((u["status"], u["kind"]), ("waiting_owner", "repo"))
        self.assertEqual([n["text"] for n in u["notes"]][-2:], ["waiting on dave", "PR o/r#3 open"])
        self.assertEqual(self.foundry("unit", "import", os.path.join(self.dir, "nope.json"), ok=(2,))[0], 2)

    def test_foreman_hook_allows_the_import_verb_not_the_raw_script(self):
        src = os.path.join(self.dir, "units.json")
        ok = self.hook("PreToolUse", tool_name="Bash",
                       tool_input={"command": f"foundry unit import {src} --dry-run --source sangsoe"}, **ORCH)
        self.assertIsNone(ok)
        raw = f"python3 {os.path.join(SCRIPTS, 'import_units.py')} {src}"
        self.assertTrue(self.denied(self.hook("PreToolUse", tool_name="Bash", tool_input={"command": raw}, **ORCH)))

    def test_unit_new_can_start_in_a_given_status(self):
        self.foundry("unit", "new", "--id", "later", "--title", "Later", "--status", "waiting_owner",
                     "--note", "owner decides scope")
        u = self.snap()["units"]["later"]
        self.assertEqual(u["status"], "waiting_owner")
        self.foundry("unit", "new", "--id", "bad", "--title", "B", "--status", "nope", ok=(2,))


class Watcher(Base):
    def test_review_and_ci_changes_become_events_once(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.foundry("wait", "add", "alpha", "--kind", "pr-review", "--ref", "example-org/webapp#12")
        self.foundry("wait", "add", "alpha", "--kind", "ci", "--ref", "example-org/webapp#12")
        self.foundry("wait", "add", "alpha", "--kind", "time", "--ref", "deploy", "--until", "2026-11-18T09:00:00+09:00")
        pr = {"state": "OPEN", "reviewDecision": "REVIEW_REQUIRED", "headRefOid": "a" * 40, "latestReviews": [],
              "statusCheckRollup": [{"status": "IN_PROGRESS"}]}
        self.gh({"example-org/webapp#12": pr})
        out = self.script("watch.py", "poll")
        self.assertEqual([e["kind"] for e in out["events"]], ["time"])  # review baseline taken; CI pending
        pr.update(reviewDecision="APPROVED", latestReviews=[{"author": {"login": "alice"}, "state": "APPROVED"}],
                  statusCheckRollup=[{"conclusion": "SUCCESS"}])
        self.gh({"example-org/webapp#12": pr})
        out = self.script("watch.py", "poll")
        self.assertEqual(sorted(e["kind"] for e in out["events"]), ["ci", "pr-review"])
        self.assertEqual(self.script("watch.py", "poll")["events"], [])
        _, pending = self.foundry("events", "pending")
        self.assertEqual(len(pending), 3)
        self.foundry("events", "consume", *[e["eid"] for e in pending])
        _, pending = self.foundry("events", "pending")
        self.assertEqual(pending, [])

    def test_a_merged_pr_clears_its_waits_and_withdraws_the_reviewer_ask(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        for kind in ("pr-review", "ci", "pr-merge"):
            self.foundry("wait", "add", "alpha", "--kind", kind, "--ref", "example-org/gateway#710")
        self.foundry("wait", "add", "alpha", "--kind", "team", "--ref", "gateway-dev-release",
                     "--until", "2026-11-19T09:00:00+09:00")
        self.foundry("decision", "ask", "--key", "alpha:reviewer", "--unit", "alpha",
                     "--question", "Who should review example-org/gateway#710 (OL-2)?", ok=(10,))
        self.foundry("decision", "ask", "--key", "alpha:other", "--unit", "alpha", "--question", "Ship #710 today?", ok=(10,))
        pr = {"state": "OPEN", "reviewDecision": "REVIEW_REQUIRED", "headRefOid": "a" * 40, "latestReviews": [],
              "statusCheckRollup": [{"conclusion": "SUCCESS"}]}
        self.gh({"example-org/gateway#710": pr})
        self.script("watch.py", "poll")
        self.assertEqual(len(self.snap()["units"]["alpha"]["waits"]), 4)
        self.gh({"example-org/gateway#710": {**pr, "state": "MERGED"}})
        out = self.script("watch.py", "poll")
        self.assertIn("pr-merge", [e["kind"] for e in out["events"]])  # the merge is still an event
        s = self.snap()
        self.assertEqual([w["kind"] for w in s["units"]["alpha"]["waits"]], ["team"])
        self.assertEqual(s["decisions"]["alpha:reviewer"]["withdrawn"], "example-org/gateway#710 merged")
        self.assertIsNone(s["decisions"]["alpha:other"]["answer"])  # only the reviewer ask is moot
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual([a["key"] for a in m["asks"]], ["alpha:other"])
        self.assertEqual(self.script("watch.py", "poll")["events"], [])

    def test_gh_failure_is_reported_not_fatal(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.foundry("wait", "add", "alpha", "--kind", "pr-merge", "--ref", "example-org/webapp#99")
        out = self.script("watch.py", "poll")
        self.assertEqual(len(out["errors"]), 1)


class Housekeeping(Base):
    def git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                       env={**self.env, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})

    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.dir, "repo")
        self.git("init", "-q", "-b", "main", self.repo)
        self.git("commit", "-q", "--allow-empty", "-m", "init", cwd=self.repo)
        origin = os.path.join(self.dir, "origin.git")
        self.git("init", "-q", "--bare", origin)
        self.git("remote", "add", "origin", origin, cwd=self.repo)
        self.git("push", "-q", "origin", "main", cwd=self.repo)
        for b in ("merged-clean", "merged-dirty", "open-clean"):
            self.git("worktree", "add", "-q", "-b", b, os.path.join(self.dir, b), cwd=self.repo)
        with open(os.path.join(self.dir, "merged-dirty", "wip.txt"), "w") as f:
            f.write("x")
        sha = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.gh({"merged-clean": {"state": "MERGED", "number": 1, "url": "u1", "headRefOid": sha},
                 "merged-dirty": {"state": "MERGED", "number": 2, "url": "u2", "headRefOid": sha},
                 "open-clean": {"state": "OPEN", "number": 3, "url": "u3", "headRefOid": sha}})
        self.env["FOUNDRY_LSOF"] = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_lsof.py')}"
        self.env["FOUNDRY_REPO_ROOTS"] = self.repo
        self.env["FOUNDRY_DOCKER"] = "false"

    def test_only_the_safe_class_is_removed_even_if_the_judge_is_wrong(self):
        cands = {c["branch"]: c for c in self.script("housekeep.py", "scan") if c["cls"] == "worktree"}
        self.assertTrue(cands["merged-clean"]["safe"])
        self.assertFalse(cands["merged-dirty"]["safe"])
        self.assertFalse(cands["open-clean"]["safe"])
        for c in cands.values():  # a careless judge marks everything safe
            self.script("housekeep.py", "plan", "--hid", c["hid"], "--verdict", "safe", "--reason", "judge")
        res = {r["hid"].rsplit("/", 1)[1]: r["result"] for r in self.script("housekeep.py", "execute")["results"]}
        self.assertEqual(res["merged-clean"], "removed")
        self.assertTrue(res["merged-dirty"].startswith("skipped"))
        self.assertTrue(res["open-clean"].startswith("skipped"))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "merged-clean")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "merged-dirty", "wip.txt")))


# ---------------------------------------------------------------------------
# Review findings (H1-H4, M1-M7, L1-L4): each test reproduces one finding.
# ---------------------------------------------------------------------------

SLACK = "mcp__plugin_acme_acme-slack__slack_send_message"
SCHED = "mcp__plugin_acme_acme-slack__slack_schedule_message"


class SlackDedupFindings(Base):
    """H1, H2, M7, L3."""

    def pre(self, msg, tool=SLACK):
        return self.hook("PreToolUse", tool_name=tool, tool_input=msg)

    def send(self, msg, resp=None, tool=SLACK):
        out = self.pre(msg, tool)
        if not self.denied(out):
            self.hook("PostToolUse", tool_name=tool, tool_input=msg,
                      tool_response=resp if resp is not None else {"ok": True, "ts": "1795440600.000100"})
        return out

    def test_h1_dev_then_prod_deploy_report_in_one_thread(self):
        base = {"channel_id": "C1", "thread_ts": "1.2"}
        self.assertIsNone(self.send(dict(base, message="example-org/webapp#12 を dev にデプロイしました")))
        self.assertIsNone(self.send(dict(base, message="example-org/webapp#12 を prod にデプロイしました")))

    def test_h1_re_review_after_new_commit_with_sha_in_japanese(self):
        base = {"channel_id": "C1", "thread_ts": "1.2"}
        self.assertIsNone(self.send(dict(base, message="<@U1> example-org/webapp#12 のレビューお願いします（head 1a2b3c4）")))
        self.assertTrue(self.denied(self.pre(dict(base, message="<@U1> example-org/webapp#12 再度レビューお願いします（1a2b3c4）"))))
        self.assertIsNone(self.send(dict(base, message="<@U1> 修正コミット9f8e7d6で直したので、example-org/webapp#12 再レビューお願いします")))

    def test_h1_review_ask_rule_is_24h_per_pr_reviewer_head(self):
        a = {"channel_id": "C1", "thread_ts": "1.2", "message": "<@U1> example-org/webapp#12 レビューお願いします 1a2b3c4"}
        self.assertIsNone(self.send(a))
        elsewhere = {"channel_id": "C9", "message": "<@U1> 別スレッドからも example-org/webapp#12 のレビュー依頼です 1a2b3c4"}
        self.assertTrue(self.denied(self.pre(elsewhere)))
        self.assertIsNone(self.send(dict(elsewhere, message=elsewhere["message"].replace("U1", "U2"))))
        self.env["FOUNDRY_NOW"] = "2026-11-19T10:00:01+09:00"
        self.assertIsNone(self.send(elsewhere))

    def test_h1_sent_expires_after_ttl_configurable_per_kind(self):
        msg = {"channel_id": "C1", "message": "週次の報告です"}
        self.assertIsNone(self.send(msg))
        self.env["FOUNDRY_NOW"] = "2026-11-18T20:00:00+09:00"
        self.assertTrue(self.denied(self.pre(msg)))
        self.env["FOUNDRY_NOW"] = "2026-11-19T10:00:01+09:00"
        self.assertIsNone(self.send(msg))
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump({"send_ttl_hours": {"report": 1}}, f)
        self.env["FOUNDRY_NOW"] = "2026-11-19T11:00:02+09:00"
        self.assertIsNone(self.pre(msg))

    def test_h2_mcp_error_inside_content_list_is_failed(self):
        msg = {"channel_id": "C1", "message": "hello list"}
        self.send(msg, resp=[{"type": "text", "text": '{"ok": false, "error": "channel_not_found"}'}])
        self.assertEqual({v["state"] for v in self.snap()["sends"].values()}, {"failed"})
        self.assertIsNone(self.pre(msg))

    def test_h2_mcp_error_string_is_failed(self):
        msg = {"channel_id": "C1", "message": "hello string"}
        self.send(msg, resp='Error: {"ok":false,"error":"not_in_channel"}')
        self.assertIsNone(self.pre(msg))

    def test_h2_unparseable_result_is_unknown_and_retryable_soon(self):
        msg = {"channel_id": "C1", "message": "hello unknown"}
        self.send(msg, resp="")
        self.assertEqual({v["state"] for v in self.snap()["sends"].values()}, {"unknown"})
        self.assertTrue(self.denied(self.pre(msg)))
        self.env["FOUNDRY_NOW"] = "2026-11-18T10:15:00+09:00"
        self.assertIsNone(self.pre(msg))

    def test_h2_success_in_content_list_is_sent(self):
        msg = {"channel_id": "C1", "message": "hello ok"}
        self.send(msg, resp=[{"type": "text", "text": "Message sent. ts=1795440600.000100 "
                                                     "https://x.slack.com/archives/C1/p1795440600000100"}])
        self.assertEqual({v["state"] for v in self.snap()["sends"].values()}, {"sent"})

    def test_m7_check_and_pending_record_share_one_lock(self):
        msg = {"channel_id": "C1", "message": "race me"}
        procs = [subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "hook.py"), "PreToolUse"], env=self.env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) for _ in range(6)]
        payload = json.dumps({"session_id": "S1", "tool_name": SLACK, "tool_input": msg})
        for p in procs:
            p.stdin.write(payload)
            p.stdin.close()
        outs = [p.stdout.read() for p in procs]
        for p in procs:
            p.wait()
        allowed = [o for o in outs if '"deny"' not in o]  # an allowed send may carry the footer updatedInput
        self.assertEqual(len(allowed), 1, outs)

    def test_l3_naive_or_malformed_override_never_opens_the_gate(self):
        self.env["FOUNDRY_NOW"] = "2026-11-18T22:30:00+09:00"
        with open(os.path.join(self.dir, "journal.jsonl"), "a") as f:
            f.write(json.dumps({"ts": "2026-11-18T22:29:00+09:00", "op": "send.override", "channel": "C1",
                                "until": "not a time"}) + "\n")
        out = self.pre({"channel_id": "C1", "message": "late"})
        self.assertTrue(self.denied(out))
        self.assertIn("quiet", out["hookSpecificOutput"]["permissionDecisionReason"])
        with open(os.path.join(self.dir, "enforce.log")) as f:
            self.assertIn("malformed override", f.read())

    def test_l3_naive_until_is_read_as_jst(self):
        self.env["FOUNDRY_NOW"] = "2026-11-18T22:30:00+09:00"
        with open(os.path.join(self.dir, "journal.jsonl"), "a") as f:
            f.write(json.dumps({"ts": "2026-11-18T22:29:00+09:00", "op": "send.override", "channel": "C1",
                                "until": "2026-11-18T22:40:00"}) + "\n")
        self.assertIsNone(self.pre({"channel_id": "C1", "message": "late but allowed"}))


class OwnerDmAndOverride(Base):
    """H3, H4."""

    def setUp(self):
        super().setUp()
        self.env["FOUNDRY_NOW"] = "2026-11-18T22:30:00+09:00"
        self.script("install_config.py", "--owner-user", "UOWNER1", "--owner-dm", "DOWNER1")

    def test_h3_install_writes_owner_ids_privately(self):
        p = os.path.join(self.dir, "config.json")
        with open(p) as f:
            cfg = json.load(f)
        self.assertEqual(cfg["owner_slack_user_id"], "UOWNER1")
        self.assertEqual(cfg["owner_dm_channels"], ["DOWNER1"])
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)

    def test_h3_install_keeps_other_keys(self):
        p = os.path.join(self.dir, "config.json")
        with open(p, "w") as f:
            json.dump({"repo_roots": ["~/r"], "owner_dm_channels": ["DOLD"]}, f)
        self.script("install_config.py", "--owner-user", "UOWNER1", "--owner-dm", "DOWNER1")
        with open(p) as f:
            cfg = json.load(f)
        self.assertEqual(cfg["repo_roots"], ["~/r"])
        self.assertEqual(cfg["owner_dm_channels"], ["DOLD", "DOWNER1"])

    def test_h3_owner_dm_allowed_in_quiet_hours(self):
        self.assertIsNone(self.hook("PreToolUse", tool_name=SLACK, tool_input={"channel_id": "DOWNER1", "message": "done"}))
        self.assertIsNone(self.hook("PreToolUse", tool_name=SLACK, tool_input={"channel_id": "UOWNER1", "message": "done 2"}))
        self.assertTrue(self.denied(self.hook("PreToolUse", tool_name=SLACK, tool_input={"channel_id": "UOTHER", "message": "x"})))

    def test_h4_allow_now_requires_channel(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "send", "allow-now", "--reason", "owner"],
                           env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.snap()["send_overrides"], [])

    def test_h4_settings_draft_does_not_auto_allow_allow_now(self):
        with open(os.path.join(ROOT, "docs", "recommended-automode.md")) as f:
            allow = [ln for ln in f if ln.startswith('  "Foundry')]
        self.assertFalse([ln for ln in allow if "allow-now" in ln.split("Not covered", 1)[0]])
        foundry_rule = next(ln for ln in allow if "Foundry state commands" in ln)
        self.assertIn("Not covered", foundry_rule)
        self.assertIn("send allow-now", foundry_rule.split("Not covered", 1)[1])

    def test_h4_quiet_deny_tells_model_to_ask_owner(self):
        out = self.hook("PreToolUse", tool_name=SLACK, tool_input={"channel_id": "C1", "message": "late"})
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("owner", reason)
        self.assertNotIn("run `foundry send allow-now", reason)


class HookHardening(Base):
    """M1, M5, M6, L1, L4."""

    JUDGE = {"agent_type": "foundry:sweeper-judge", "agent_id": "J1"}

    def bash(self, cmd, **who):
        return self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **who)

    def test_m1_judge_bypasses_are_denied(self):
        good = [f'python3 {HK} scan',
                f'python3 "{HK}" plan --hid "worktree:/a b" --verdict propose --reason "open PR, idle"']
        for cmd in good:
            self.assertIsNone(self.bash(cmd, **self.JUDGE), cmd)
        bad = ['python3 /x/scripts/housekeep.py scan\nrm -rf /a',
               'python3 /x/scripts/housekeep.py scan > /x/state/journal.jsonl',
               'python3 /x/scripts/housekeep.py plan --hid "$(rm -rf /a)" --verdict safe --reason x',
               'python3 /x/scripts/housekeep.py plan --hid `id` --verdict safe --reason x',
               'python3 /x/scripts/housekeep.py scan && rm -rf /a',
               'python3 /x/scripts/housekeep.py scan || true',
               'FOUNDRY_STATE_DIR=/elsewhere python3 /x/scripts/housekeep.py scan',
               'python3 /x/scripts/housekeep.py execute',
               'python3 /x/scripts/housekeep.py plan --hid a --verdict safe --reason x --extra y',
               'python3 /x/scripts/housekeep.py scan < /etc/hosts',
               'python3 -c "import os" /x/scripts/housekeep.py scan']
        for cmd in bad:
            self.assertTrue(self.denied(self.bash(cmd, **self.JUDGE)), cmd)

    def test_m5_torn_last_line_does_not_swallow_the_next_record(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        with open(os.path.join(self.dir, "journal.jsonl"), "a") as f:
            f.write('{"ts": "2026-11-18T10:00:00+09:00", "op": "unit.st')
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta")
        self.assertIn("beta", self.snap()["units"])

    def test_m6_non_foundry_spawns_are_not_logged(self):
        before = len(self.snap()["pending_spawns"])
        log = os.path.join(self.dir, "enforce.log")
        if os.path.exists(log):
            os.remove(log)
        self.spawn("general-purpose", session_id="OTHER")
        self.assertFalse(os.path.exists(log))
        self.assertEqual(len(self.snap()["pending_spawns"]), before)

    def test_m6_enforce_log_rotates(self):
        self.env["FOUNDRY_LOG_MAX_BYTES"] = "2000"
        for _ in range(30):
            self.spawn("foundry:hand", **ORCH)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "enforce.log.1")))
        self.assertLess(os.path.getsize(os.path.join(self.dir, "enforce.log")), 2600)

    def test_m6_matchers_are_anchored(self):
        import re
        with open(os.path.join(ROOT, "hooks", "hooks.json")) as f:
            hooks = json.load(f)["hooks"]
        pre = hooks["PreToolUse"][0]["matcher"]
        post = hooks["PostToolUse"][0]["matcher"]
        for tool in ("Agent", "Task", "SendMessage", "Bash", SLACK, SCHED, SLACK + "_draft"):  # 0.1.82: drafts lint
            self.assertTrue(re.search(pre, tool), tool)
        for tool in ("TaskCreate", "TaskUpdate", "TaskStop", "TaskList", "BashOutput", "AgentX", SLACK + "_drafts"):
            self.assertFalse(re.search(pre, tool), tool)
        for tool in ("TaskCreate", "TaskUpdate", "TodoWrite", SLACK):
            self.assertTrue(re.search(post, tool), tool)
        for tool in ("TaskCreateX", "TaskList", SLACK + "_drafts"):
            self.assertFalse(re.search(post, tool), tool)

    def test_m6_compaction_keeps_state_and_drops_expired(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--ticket", "PROJ-1")
        self.foundry("decision", "ask", "--key", "alpha:k", "--unit", "alpha", "--question", "q?", ok=(10,))
        self.hook("PreToolUse", tool_name=SLACK, tool_input={"channel_id": "C1", "message": "old"})
        self.env["FOUNDRY_NOW"] = "2026-11-25T10:00:00+09:00"
        self.hook("PreToolUse", tool_name=SLACK, tool_input={"channel_id": "C1", "message": "new"})
        before = self.snap()
        self.foundry("compact")
        after = self.snap()
        self.assertEqual(before["units"], after["units"])
        self.assertEqual(before["decisions"], after["decisions"])
        self.assertEqual(len(after["sends"]), 1)
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            self.assertLess(len(f.readlines()), 4)
        self.foundry("unit", "checkpoint", "alpha", "--note", "after compaction")
        self.assertEqual(self.snap()["units"]["alpha"]["notes"][-1]["text"], "after compaction")

    def test_m6_compaction_runs_by_itself_past_the_size_limit(self):
        self.env["FOUNDRY_COMPACT_BYTES"] = "4000"
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        for i in range(30):
            self.foundry("unit", "checkpoint", "alpha", "--note", f"n{i} " + "x" * 100)
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            self.assertEqual(json.loads(f.readline())["op"], "snapshot")
        self.assertLess(os.path.getsize(os.path.join(self.dir, "journal.jsonl")), 12000)
        self.assertEqual(len(self.snap()["units"]["alpha"]["notes"]), 30)

    def test_l1_foundry_agents_in_a_non_foundry_session_are_outside(self):
        other = {"session_id": "OTHER", "agent_type": "foundry:hand", "agent_id": "X1"}
        self.assertIsNone(self.spawn("foundry:hand", **other))
        self.assertIsNone(self.hook("SubagentStart", session_id="OTHER", agent_id="X2", agent_type="foundry:line-lead"))
        self.assertNotIn("X2", self.snap()["agents"])

    def test_l4_board_files_are_written_atomically(self):
        import ast
        with open(os.path.join(SCRIPTS, "board.py")) as f:
            tree = ast.parse(f.read())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "write_all")
        opens = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "open"]
        self.assertEqual(opens, [], "write_all must go through foundrylib's tmp+rename writer")


class HousekeepingFindings(Housekeeping):
    """M2, M3, M4."""

    def cands(self):
        return {c["branch"]: c for c in self.script("housekeep.py", "scan") if c["cls"] == "worktree"}

    def test_m2_failed_git_status_is_not_clean(self):
        self.env["FOUNDRY_GIT"] = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_git.py')}"
        self.env["FAKE_GIT_FAIL"] = "status"
        self.assertFalse(self.cands()["merged-clean"]["safe"])

    def test_m2_failed_rev_list_is_not_clean(self):
        self.env["FOUNDRY_GIT"] = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_git.py')}"
        self.env["FAKE_GIT_FAIL"] = "rev-list"
        self.assertFalse(self.cands()["merged-clean"]["safe"])

    def test_m3_pr_head_must_match_worktree_head(self):
        # 0.1.84: a moved head with nothing unpushed loses nothing (test_lifecycle_0184); a local commit found
        # nowhere else still keeps it
        self.git("commit", "-q", "--allow-empty", "-m", "local only", cwd=os.path.join(self.dir, "merged-clean"))
        self.gh({"merged-clean": {"state": "MERGED", "number": 1, "url": "u1", "headRefOid": "0" * 40}})
        self.assertFalse(self.cands()["merged-clean"]["safe"])

    def test_m3_worktree_in_use_is_skipped(self):
        self.env["FAKE_LSOF_CWDS"] = os.path.join(self.dir, "merged-clean", "sub")
        self.assertFalse(self.cands()["merged-clean"]["safe"])
        self.env["FAKE_LSOF_CWDS"] = os.path.join(self.dir, "merged-clean-other")
        self.assertTrue(self.cands()["merged-clean"]["safe"])
        self.env["FAKE_LSOF_FAIL"] = "1"
        self.assertFalse(self.cands()["merged-clean"]["safe"])

    def test_m3_gitignored_files_downgrade_to_proposal(self):
        common = subprocess.run(["git", "-C", self.repo, "rev-parse", "--git-common-dir"], capture_output=True,
                                text=True).stdout.strip()
        with open(os.path.join(self.repo, common, "info", "exclude"), "a") as f:
            f.write("*.secret\n")
        with open(os.path.join(self.dir, "merged-clean", "local.secret"), "w") as f:
            f.write("TOKEN=1")
        c = self.cands()["merged-clean"]
        self.assertFalse(c["safe"])
        self.assertEqual(c["suggest"], "propose")
        self.script("housekeep.py", "plan", "--hid", c["hid"], "--verdict", "safe", "--reason", "judge")
        res = self.script("housekeep.py", "execute")["results"]
        self.assertTrue(res[0]["result"].startswith("skipped"))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "merged-clean", "local.secret")))

    def test_m4_short_docker_prefix_is_refused(self):
        fake = os.path.join(self.dir, "fake_docker.sh")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\necho \'[{"Name": "c-db"}, {"Name": "prod-db"}]\'\n')
        os.chmod(fake, 0o755)
        self.env["FOUNDRY_DOCKER"] = fake
        for prefix in ("", "c"):
            with open(os.path.join(self.dir, "config.json"), "w") as f:
                json.dump({"docker_project_prefix": prefix}, f)
            docker = [c for c in self.script("housekeep.py", "scan") if c["cls"] == "docker"]
            self.assertTrue(docker and not any(c["safe"] for c in docker), prefix)


class ReviewFailClosed(Base):
    def run_hook(self, stdin, **env):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "hook.py"), "PreToolUse"], input=stdin,
                           env={**self.env, **env}, capture_output=True, text=True)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def test_crash_in_a_foundry_session_or_a_slack_send_denies(self):
        out = self.run_hook("not json", FOUNDRY_ROLE="line-lead")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        # An unreadable journal (a bad record is skipped since review 11, so it no longer crashes the hook).
        for name in ("journal.jsonl", "snapshot.json"):
            if os.path.exists(os.path.join(self.dir, name)):
                os.remove(os.path.join(self.dir, name))
        os.mkdir(os.path.join(self.dir, "journal.jsonl"))
        slack = json.dumps({"tool_name": "mcp__x-slack__slack_send_message", "session_id": "S",
                            "tool_input": {"channel_id": "C1", "message": "hi"}})
        out = self.run_hook(slack)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Hook error", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIsNone(self.run_hook("not json"))  # any other session fails open

    def test_compaction_never_leaves_the_journal_missing(self):
        sys.path.insert(0, SCRIPTS)
        import foundrylib
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
            seen = []
            real = foundrylib.write_atomic

            def spy(path, data):
                seen.append(os.path.exists(os.path.join(self.dir, "journal.jsonl")))
                return real(path, data)
            foundrylib.write_atomic = spy
            try:
                foundrylib.compact()
            finally:
                foundrylib.write_atomic = real
            self.assertTrue(seen and all(seen))
            self.assertTrue(os.path.exists(os.path.join(self.dir, "journal.jsonl.1")))
            self.assertIn("alpha", self.snap()["units"])
        finally:
            if old is None:
                os.environ.pop("FOUNDRY_STATE_DIR")
            else:
                os.environ["FOUNDRY_STATE_DIR"] = old


class Ancestry(unittest.TestCase):
    """The real process walk, without FOUNDRY_TEST_ANCESTORS (which hid a missing import)."""

    def test_claude_ancestors_runs_for_real(self):
        code = ("import sys; sys.path.insert(0, %r); import foundrylib as cl; "
                "a = cl.claude_ancestors(); assert isinstance(a, list) and all(len(x) == 2 for x in a), a; print(len(a))"
                % SCRIPTS)
        env = {k: v for k, v in os.environ.items() if k != "FOUNDRY_TEST_ANCESTORS"}
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_an_identity_lookup_crash_fails_closed(self):
        sys.path.insert(0, SCRIPTS)
        import foundrylib as cl
        snap = {"foundry_sessions": {"s": {"procs": [[1, "x"]]}}, "sessions": {}}
        saved, env = cl.claude_ancestors, {k: os.environ.pop(k) for k in ("FOUNDRY_ROLE", "FOUNDRY_SESSION") if k in os.environ}
        def boom():
            raise NameError("subprocess")
        cl.claude_ancestors = boom
        try:
            self.assertTrue(cl.in_foundry_session(snap))
            self.assertIsNotNone(cl.require_owner("x", snap))
        finally:
            cl.claude_ancestors = saved
            os.environ.update(env)


class PromptsAndNames(unittest.TestCase):
    def test_one_command_rule_reaches_every_agent(self):
        names = sorted(n[:-3] for n in os.listdir(os.path.join(ROOT, "agents")) if n.endswith(".md"))
        self.assertTrue({"line-lead", "hand", "dispatcher", "foreman", "shop-boss"} <= set(names))
        for name in names:
            with open(os.path.join(ROOT, "agents", name + ".md")) as f:
                body = f.read().split("---", 2)[2]
            self.assertIn("**One command per Bash call.**", body, name)
            self.assertLess(body.index("One command per Bash call"), 2500, name)  # up front, not buried

    def test_only_the_pane_toggle_is_named_foundry_board(self):
        names = []
        for d in os.listdir(os.path.join(ROOT, "skills")):
            with open(os.path.join(ROOT, "skills", d, "SKILL.md")) as f:
                names += re.findall(r"^name:\s*(\S+)", f.read(), re.M) + [d]
        for d in os.listdir(os.path.join(ROOT, "agents")):
            names.append(d[:-3])
        hooks = ""
        for d in os.listdir(os.path.join(ROOT, "hooks")):
            if d.endswith((".ts", ".tsx")) and ".test." not in d:
                with open(os.path.join(ROOT, "hooks", d)) as f:
                    hooks += f.read()
        commands = re.findall(r"command\.register\(\{\s*name:\s*'([^']+)'", hooks)
        typed = "foundry-board"
        completes = [n for n in names + commands if n.startswith(typed) or typed in n]
        self.assertEqual(completes, ["foundry-board"])
        self.assertIn("foundry-board", commands)
        with open(os.path.join(ROOT, "skills", "foundry-status", "SKILL.md")) as f:
            self.assertIn("pane: /foundry-board", f.read())


class LaunchLines(unittest.TestCase):
    """Effort is passed on every launch line: the CLI ignores a user-settings effortLevel for models outside its
    legacy list (Opus 5.5 among them), so neither it nor the agent frontmatter can be relied on."""

    def test_foreman_start_line_pins_model_and_effort(self):
        sys.path.insert(0, SCRIPTS)
        import firstrun
        import foundrylib as cl
        with open(os.path.join(ROOT, "README.md")) as f:
            lines = [l for l in f.read().splitlines() if "--agent foundry:foreman" in l and "--bg" in l and "cd " in l]
        lines.append(firstrun.foreman_command(cl.load_config()))  # install.sh prints it (`foundry foreman line`)
        for l in lines:
            self.assertRegex(l, r"--model opus --effort (medium|\$FOREMAN_EFFORT) ")
        with open(os.path.join(ROOT, "install.sh")) as f:
            self.assertIn("foundry.py\" foreman line", f.read())

    def test_effort_defaults_are_medium(self):
        sys.path.insert(0, SCRIPTS)
        import foundrylib as cl
        d = tempfile.mkdtemp()
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = d
        try:
            cfg = cl.load_config()
        finally:
            shutil.rmtree(d, ignore_errors=True)
            if old is None:
                os.environ.pop("FOUNDRY_STATE_DIR")
            else:
                os.environ["FOUNDRY_STATE_DIR"] = old
        self.assertEqual((cfg["foreman_effort"], cfg["lead_effort"], cfg["boss_effort"]), ("medium", "medium", "medium"))

    def test_lead_launch_and_resume_pass_model_and_effort(self):
        sys.path.insert(0, SCRIPTS)
        import foundrylib as cl
        import launcher
        cfg = cl.load_config()
        for lead_type, prefix in (("foundry:line-lead", "lead"), ("foundry:shop-boss", "boss")):
            argv = launcher.launch_argv(cfg, "x", lead_type, cfg[f"{prefix}_model"], cfg[f"{prefix}_effort"], "u", 5)
            self.assertEqual(argv[argv.index("--model") + 1], cfg[f"{prefix}_model"])
            self.assertIn(argv[argv.index("--effort") + 1], ("low", "medium", "high", "xhigh", "max"))
        with open(os.path.join(ROOT, "scripts", "launcher.py")) as f:
            src = f.read()
        self.assertIn('argv[argv.index("--bg") + 1:argv.index("--bg") + 1] = ["--resume"', src)  # resume reuses launch argv


class InstallDocs(Base):
    """L2 and the install acceptance: no launchd; the foreman's Monitor streams replace it."""

    def test_l2_install_section_has_every_step(self):
        with open(os.path.join(ROOT, "README.md")) as f:
            doc = f.read()
        sec = doc.split("## Quickstart", 1)[1].split("\n## ", 1)[0]
        steps = ["install.sh", "uninstall.sh", "foundry init", "foundry foreman start", "in the foreman: foundry up",
                 "claude --bg -n foreman --agents", "watch stream"]
        pos = [sec.index(s) for s in steps]
        self.assertEqual(pos, sorted(pos))
        self.assertNotIn("cd ~/work/wt && claude", sec)  # worktree trust is automatic now

    def test_no_launch_agents_anywhere(self):
        """Install never writes ~/Library/LaunchAgents; only an uninstall line may name it."""
        self.assertFalse(os.path.exists(os.path.join(ROOT, "launchd")))
        allowed = re.compile(r"rm -f (~|\"\$HOME\")/Library/LaunchAgents/com\.\*\.foundry\*\.plist")
        hits = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "tests")]
            for name in files:
                if not name.endswith((".md", ".sh", ".py", ".json", ".plist", ".diff", ".patch")) and "bin" not in base:
                    continue
                with open(os.path.join(base, name), errors="replace") as f:
                    for line in f:
                        if re.search(r"LaunchAgents|launchctl", line) and not allowed.search(line):
                            hits.append(f"{name}: {line.strip()[:120]}")
        self.assertEqual(hits, [])

if __name__ == "__main__":
    unittest.main()
