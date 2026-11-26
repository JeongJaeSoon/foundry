"""0.1.92: `foundry up --check` (no --crons) lists the streams and the events wait with no live process, each with
its re-arm command, leaves running ones out, and flags a heartbeat gap as a sleep; with --crons it adds them to the
cron check. foreman.md runs it every time the events wait returns."""

import json
import os
import subprocess
import sys

import test_foundry as tf

ROOT = os.path.dirname(tf.SCRIPTS)


class StreamHealth(tf.Base):
    def check(self, *extra):
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "up", "--check", *extra],
                           env=self.env, capture_output=True, text=True)
        return r.returncode, json.loads(r.stdout)

    def claim(self, kind):
        # a live process that owns `kind`: this test process itself
        d = os.path.join(self.dir, "streams")
        os.makedirs(d, exist_ok=True)
        rec = {"kind": kind, "pid": os.getpid(), "version": "x", "started": "2026-11-18T09:59:00+09:00"}
        for name in (f"{kind}.{os.getpid()}.json", f"{kind}.json"):
            with open(os.path.join(d, name), "w") as f:
                json.dump(rec, f)

    def test_dead_streams_are_listed_and_live_ones_left_alone(self):
        self.claim("github")
        self.claim("events-wait")
        rc, out = self.check()
        self.assertEqual(rc, 1)
        kinds = [x["kind"] for x in out["rearm"]]
        self.assertEqual(kinds, ["sessions", "pressure", "sync", "stalls"])
        self.assertEqual(out["rearm"][0]["line"], "re-arm: sessions")
        self.assertEqual(out["rearm"][0]["command"], "foundry watch stream --kind sessions")

    def test_all_running_is_quiet(self):
        for k in ("github", "sessions", "pressure", "sync", "stalls", "events-wait"):
            self.claim(k)
        rc, out = self.check()
        self.assertEqual((rc, out["rearm"]), (0, []))

    def test_a_heartbeat_gap_is_a_sleep(self):
        with open(os.path.join(self.dir, "journal.jsonl"), "a") as f:  # a heartbeat at 10:00
            f.write(json.dumps({"ts": "2026-11-18T10:00:00+09:00", "op": "foreman.state", "alive": True}) + "\n")
        self.env["FOUNDRY_NOW"] = "2026-11-18T10:45:00+09:00"
        _, out = self.check()
        self.assertEqual(out["slept_min"], 45)
        self.assertIn("slept", out["first"])
        self.env["FOUNDRY_NOW"] = "2026-11-18T10:03:00+09:00"
        self.assertIsNone(self.check()[1]["slept_min"])

    def test_foreman_runs_it_on_every_wake(self):
        with open(os.path.join(ROOT, "agents", "foreman.md")) as f:
            self.assertIn("Every time it returns, before you re-arm it, run `foundry up --check`", f.read())
