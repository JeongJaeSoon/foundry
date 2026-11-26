"""0.1.119: stale foreman sessions are stopped. Every foreman SessionStart (a /clear included) records itself as the
current foreman; at that SessionStart, `foundry up` and `foundry foreman prune [--dry-run]`, the foreman sessions
foundry started (named `foreman`, recorded as the foreman) that are not the current one are stopped when idle and
left for the next check when busy. Nothing is stopped when the listing fails or the current foreman is not in it;
lead, boss and other sessions are never touched. The board says "stale foremen: N" until they are gone. Only fake
session lists here."""

import json
import os
import subprocess
import sys

from test_foundry import ORCH, ROOT, SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import launcher  # noqa: E402

CUR = "cur-0000"


def agent(sid, status=None, state="working", name="foreman"):
    return {"name": name, "sessionId": sid, "id": sid[:8], "kind": "background", "cwd": "/x",
            **({"status": status} if status else {}), **({"state": state} if state else {})}


def snap(current=CUR, sessions=("old-idle", "old-busy", "old-dead", "old-done", "cur-0000")):
    return {"foreman_current": {"session": current} if current else None,
            "foreman_sessions": {s: "t" for s in sessions},
            "foundry_sessions": {"hist-1": {"role": "foreman", "unit": None},
                                 "lead-like": {"role": "foreman", "unit": "PROJ-1"}}}


LISTING = [agent(CUR, "busy"), agent("old-idle", "idle"), agent("old-busy", "busy"), agent("old-dead"),
           agent("old-done", state="stopped"), agent("hist-1", "idle"), agent("lead-like", "idle"),
           agent("stranger", "idle"), agent("lead-1", "idle", name="lead-alpha")]


class Select(Base):
    CREW = False

    def test_only_recorded_stale_foremen_and_only_idle_ones_stop(self):
        got = {e["sessionId"]: act for e, act in launcher.stale_foremen(snap(), LISTING)}
        self.assertEqual(got, {"old-idle": "stop", "old-busy": "wait", "old-dead": "stop", "hist-1": "stop"})

    def test_fail_closed(self):
        self.assertIsNone(launcher.stale_foremen(snap(), None))
        self.assertIsNone(launcher.stale_foremen(snap(current=None), LISTING))
        self.assertIsNone(launcher.stale_foremen(snap(), [e for e in LISTING if e["sessionId"] != CUR]))


class Prune(Base):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.dir, "claude.jsonl")
        self.data = os.path.join(self.dir, "claude.json")
        self.env.update({"FOUNDRY_CLAUDE": f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_claude.py')}",
                         "FAKE_CLAUDE_DATA": self.data, "FAKE_CLAUDE_LOG": self.log})
        self.listing([agent("S1", "busy"), agent("S0", "idle"), agent("S2", "busy"),
                      agent("SL", "idle", name="lead-alpha")])
        self.record("foreman.current", session="S0")
        self.record("foreman.current", session="S2")
        self.record("foreman.current", session="S1")  # Base's SessionStart S1 ran before the fake claude was set

    def listing(self, agents):
        with open(self.data, "w") as f:
            json.dump({"agents": agents}, f)

    def stops(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(x)["argv"] for x in f if json.loads(x)["argv"][:1] == ["stop"]]

    def cli(self, *args):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_dry_run_lists_and_stops_nothing(self):
        out = self.cli("foreman", "prune", "--dry-run")
        self.assertEqual([(x["session"], x["action"]) for x in out["stale"]], [("S0", "stop"), ("S2", "wait")])
        self.assertEqual(self.stops(), [])
        self.assertIsNone(self.snap()["foreman_prune"])

    def test_prune_stops_the_idle_one_journals_it_and_the_board_counts_the_rest(self):
        out = self.cli("foreman", "prune")
        self.assertEqual(self.stops(), [["stop", "S0"]])
        self.assertEqual([x["session"] for x in out["stopped"]], ["S0"])
        rec = self.snap()["foreman_prune"]
        self.assertEqual(([x["session"] for x in rec["stopped"]], [x["session"] for x in rec["stale"]]), (["S0"], ["S2"]))
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["stale_foremen"], 1)
        self.listing([agent("S1", "busy"), agent("S0", state="stopped"), agent("S2", "idle")])  # S2 finished its turn
        self.cli("foreman", "prune")
        self.assertEqual(self.stops(), [["stop", "S0"], ["stop", "S2"]])
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["stale_foremen"], 0)

    def test_a_cleared_foreman_session_becomes_the_current_one_and_prunes(self):
        self.listing([agent("S1", "idle"), agent("S5", "busy")])
        self.hook("SessionStart", session_id="S5", source="clear", **ORCH)
        self.assertEqual(self.snap()["foreman_current"]["session"], "S5")
        self.assertEqual(self.stops(), [["stop", "S1"]])

    def test_a_lead_never_prunes(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "foreman", "prune"],
                           env={**self.env, "FOUNDRY_ROLE": "line-lead"}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.stops(), [])
