"""0.1.58: one stream per kind and one events wait per machine. A new start takes the kind over and the older one
exits on its next tick; events wait has a hard lifetime; `foundry streams status` lists owners and duplicates."""

import json
import os
import subprocess
import sys
import time
from unittest import mock

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)


class Streams(tf.Base):
    def wait_proc(self, timeout=30):
        return subprocess.Popen([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "events", "wait",
                                 "--timeout", str(timeout), "--interval", "1"], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def owner(self, kind):
        with open(os.path.join(self.dir, "streams", f"{kind}.json")) as f:
            return json.load(f)["pid"]

    def until(self, cond, limit=10):
        end = time.time() + limit
        while time.time() < end and not cond():
            time.sleep(0.2)
        return cond()

    def test_a_second_events_wait_takes_over_and_the_first_exits(self):
        first = self.wait_proc()
        self.addCleanup(first.kill)
        self.assertTrue(self.until(lambda: os.path.exists(os.path.join(self.dir, "streams", "events-wait.json"))))
        self.assertTrue(self.until(lambda: self.owner("events-wait") == first.pid))
        second = self.wait_proc()
        self.addCleanup(second.kill)
        self.assertTrue(self.until(lambda: self.owner("events-wait") == second.pid))
        out, _ = first.communicate(timeout=10)
        self.assertIn("superseded", out)
        _, st = self.foundry("streams", "status")
        self.assertEqual(st["streams"]["events-wait"]["owner"], second.pid)
        self.assertEqual(st["duplicates"], [])
        second.kill()

    def test_events_wait_ends_by_itself(self):
        _, out = self.foundry("events", "wait", "--timeout", "1", "--interval", "1")
        self.assertEqual(out, "no events\n")
        import foundry
        self.assertEqual(foundry.EVENTS_WAIT_MAX_S, 3600)  # any --timeout is capped there

    def test_a_stream_exits_on_its_next_tick_once_taken_over(self):
        import foundrylib as cl
        import watchers
        with mock.patch.dict(os.environ, {"FOUNDRY_STATE_DIR": self.dir}):
            ticks = []

            def cycle(kind, p):
                ticks.append(kind)
                if len(ticks) == 1:  # another process starts the same kind
                    cl.write_atomic(os.path.join(self.dir, "streams", f"{kind}.json"),
                                    json.dumps({"kind": kind, "pid": os.getpid() + 1, "version": "9.9.9"}))
                return []
            with mock.patch.object(watchers, "run_cycle", cycle), mock.patch.object(watchers.time, "sleep"):
                self.assertEqual(watchers.stream("sync", interval=1, max_minutes=1), 0)
            self.assertEqual(ticks, ["sync"])
            self.assertFalse(os.path.exists(os.path.join(self.dir, "streams", f"sync.{os.getpid()}.json")))

    def test_status_flags_a_duplicate(self):
        import foundrylib as cl
        with mock.patch.dict(os.environ, {"FOUNDRY_STATE_DIR": self.dir}):
            cl.claim_instance("github")  # this test process, still alive
            d = os.path.join(self.dir, "streams")
            # an older instance of the same kind that has not noticed yet: alive (the parent pid), not the owner
            ppid = os.getppid()
            cl.write_atomic(os.path.join(d, f"github.{ppid}.json"),
                            json.dumps({"kind": "github", "pid": ppid, "version": "0.1.50", "started": cl.iso()}))
            rows = cl.instances()
            self.assertEqual(rows["github"]["duplicates"], [ppid])
            cl.release_instance("github")


if __name__ == "__main__":
    import unittest
    unittest.main()
