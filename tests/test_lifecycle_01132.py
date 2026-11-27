"""0.1.132: `claude agents` drains. The sweep `claude rm`s every finished foundry row: older duplicates of an open
unit's lead (any cwd, failed rows too), every row of a closed unit, and stopped foreman rows that are not the current
one (also one `foundry foreman prune` stopped that is still listed as working with no process). Other names, live rows
and the current foreman stay. A failed rm is journaled once and backed off for a day; the board counts it meanwhile."""

import datetime
import os
import sys
import unittest
from unittest import mock

import test_foundry as tf
import test_launcher as tl

sys.path.insert(0, tf.SCRIPTS)
import board  # noqa: E402
import i18n  # noqa: E402

NOW = datetime.datetime.fromisoformat("2026-11-18T10:00:00+09:00").timestamp()


def ms(minutes_ago):
    return int((NOW - minutes_ago * 60) * 1000)


class DrainAgents(tl.Launcher):
    def setUp(self):
        super().setUp()
        self.claude({"bg_id": "a1", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-alpha", "status": "busy"}]})
        self.foundry("unit", "launch", "alpha")
        self.claude({"bg_id": "b1", "agents": [{"name": "boss-beta", "kind": "bg", "sessionId": "S-beta", "status": "busy"}]})
        self.foundry("unit", "launch", "beta")
        self.foundry("unit", "status", "beta", "done")
        for sid in ("F-old", "F-ghost", "F-live", "F-cur"):
            self.record("foreman.current", session=sid)
        self.record("foreman.prune", stopped=[{"session": "F-ghost", "id": "fghost"}, {"session": "F-live", "id": "flive"}],
                    stale=[], by="up", stop_failed={})

    def rows(self):
        return [
            {"id": "a0", "name": "lead-alpha", "state": "stopped", "cwd": "/moved/alpha", "startedAt": ms(500)},
            {"id": "a2", "name": "lead-alpha", "state": "failed", "cwd": "/x", "startedAt": ms(400)},
            {"id": "a3", "name": "lead-alpha", "state": "working", "cwd": "/x", "startedAt": ms(350), "pid": os.getpid()},
            {"id": "a1", "name": "lead-alpha", "state": "done", "cwd": "/x", "startedAt": ms(300), "sessionId": "S-alpha"},
            {"id": "a9", "name": "lead-alpha", "state": "stopped", "cwd": "/x", "startedAt": ms(100)},
            {"id": "b0", "name": "boss-beta", "state": "done", "cwd": "/y", "startedAt": ms(200)},
            {"id": "b1", "name": "boss-beta", "state": "stopped", "cwd": "/y", "startedAt": ms(90)},
            {"id": "g1", "name": "gapfix-probe", "state": "done", "cwd": "/x", "startedAt": ms(900)},
            {"id": "u1", "name": "lead-unknown", "state": "done", "cwd": "/x", "startedAt": ms(900)},
            {"id": "fold", "name": "foreman", "sessionId": "F-old", "state": "stopped", "startedAt": ms(900)},
            {"id": "fghost", "name": "foreman", "sessionId": "F-ghost", "state": "working", "startedAt": ms(800)},
            {"id": "flive", "name": "foreman", "sessionId": "F-live", "state": "working", "pid": os.getpid(),
             "startedAt": ms(700)},
            {"id": "fcur", "name": "foreman", "sessionId": "F-cur", "state": "stopped", "startedAt": ms(600)},
            {"id": "fx", "name": "foreman", "sessionId": "F-other", "state": "stopped", "startedAt": ms(600)},
        ]

    def rm_calls(self):
        return [c["argv"][1:] for c in self.calls() if c["argv"][:1] == ["rm"]]

    def test_only_finished_foundry_rows_go_and_the_open_units_newest_stays(self):
        self.claude({"agents": self.rows()})
        _, out = self.foundry("sweep", "agents", "--dry-run")
        want = ["a0", "a2", "b0", "b1", "fghost", "fold"]
        self.assertEqual(sorted(r["id"] for r in out["would_remove"]), want)
        self.assertEqual(self.rm_calls(), [])
        _, out = self.foundry("sweep", "agents")
        self.assertEqual(sorted(self.rm_calls()), [[x] for x in want])  # plain `claude rm <id>`, no flags
        reasons = {r["id"]: r["reason"] for r in out["removed"]}
        self.assertEqual(reasons["b0"], "unit done")
        self.assertEqual(reasons["fghost"], "foreman session stopped, still listed")
        pruned = sorted(r["ids"][0] for r in self.journal() if r["op"] == "session.pruned")
        self.assertEqual(pruned, want)

    def test_a_failed_rm_is_journaled_once_backed_off_and_shown(self):
        self.claude({"agents": self.rows(), "rm_rc": 1})
        self.foundry("sweep", "agents")
        n = len(self.rm_calls())
        self.assertEqual(n, 6)
        self.foundry("sweep", "agents")
        self.assertEqual(len(self.rm_calls()), n)  # backing off: not tried again
        self.assertEqual(len([r for r in self.journal() if r["op"] == "session.rm_failed"]), 6)
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["agent_rm_failed"], 6)
        with mock.patch.object(i18n, "lang", return_value="en"):
            self.assertIn("claude rm failed: 6", board.band(m))
        self.env["FOUNDRY_NOW"] = "2026-11-19T10:30:00+09:00"
        self.claude({"agents": self.rows()})
        self.foundry("sweep", "agents")
        self.assertEqual(len(self.rm_calls()), 2 * n)  # a day later: tried again, and it works now
        self.assertEqual(self.snap()["agent_rm_failed"], {})
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["agent_rm_failed"], 0)

    def test_no_current_foreman_recorded_removes_no_foreman_row(self):
        import sweep
        snap = self.snap()
        snap["foreman_current"] = None
        self.assertEqual(sweep._foreman_rows(snap, self.rows()), [])


for _name in dir(tl.Launcher):  # the launcher fixture, not its tests
    if _name.startswith("test_"):
        setattr(DrainAgents, _name, None)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(DrainAgents))
    return suite


if __name__ == "__main__":
    unittest.main()
