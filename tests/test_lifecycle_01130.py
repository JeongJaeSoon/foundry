"""0.1.130: a `claude stop` that fails for a stale foreman is journaled once (foreman.prune stop_failed) and not
tried again for 24 h; the board says "stale foremen: N (stop failed)" meanwhile. A fake `claude` whose stop fails."""

import json
import os
import subprocess
import sys
from unittest import mock

from test_foundry import ROOT, SCRIPTS, Base
from test_lifecycle_01119 import agent

sys.path.insert(0, SCRIPTS)
import board  # noqa: E402
import i18n  # noqa: E402


class StopFailed(Base):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.dir, "claude.jsonl")
        self.data = os.path.join(self.dir, "claude.json")
        self.env.update({"FOUNDRY_CLAUDE": f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_claude.py')}",
                         "FAKE_CLAUDE_DATA": self.data, "FAKE_CLAUDE_LOG": self.log})
        self.fake(stop_rc=1)
        self.record("foreman.current", session="S0")
        self.record("foreman.current", session="S1")

    def fake(self, stop_rc):
        with open(self.data, "w") as f:
            json.dump({"agents": [agent("S1", "busy"), agent("S0", "idle")], "stop_rc": stop_rc}, f)

    def stops(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(x)["argv"] for x in f if json.loads(x)["argv"][:1] == ["stop"]]

    def prune(self, now):
        self.env["FOUNDRY_NOW"] = now
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "foreman", "prune"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def prunes(self):
        return [r for r in self.journal() if r.get("op") == "foreman.prune"]

    def test_a_failed_stop_backs_off_for_a_day_and_the_board_says_so(self):
        out = self.prune("2026-11-18T10:00:00+09:00")
        self.assertEqual(self.stops(), [["stop", "S0"]])
        self.assertEqual([(x["session"], x["action"]) for x in out["stale"]], [("S0", "stop-failed")])
        self.assertEqual(self.snap()["foreman_prune"]["stop_failed"], {"S0": "2026-11-18T10:00:00+09:00"})
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual((m["stale_foremen"], m["stale_foremen_failed"]), (1, 1))
        with mock.patch.object(i18n, "lang", return_value="en"):
            self.assertIn("stale foremen: 1 (stop failed)", board.band(m))
        n = len(self.prunes())
        self.prune("2026-11-18T22:00:00+09:00")  # within the day: not tried, nothing new journaled
        self.assertEqual(self.stops(), [["stop", "S0"]])
        self.assertEqual(len(self.prunes()), n)
        self.fake(stop_rc=0)
        out = self.prune("2026-11-19T10:30:00+09:00")  # a day later: tried once more, and it works now
        self.assertEqual(self.stops(), [["stop", "S0"], ["stop", "S0"]])
        self.assertEqual([x["session"] for x in out["stopped"]], ["S0"])
        self.assertEqual(self.snap()["foreman_prune"]["stop_failed"], {})
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual((m["stale_foremen"], m["stale_foremen_failed"]), (0, 0))
