"""0.1.122: a finished unit's waits end with it. Closing a unit (done or cancelled) clears its waits, and the watcher
never polls a closed unit's waits (snapshots from before this still carry some)."""

import datetime as dt
import sys
from unittest import mock

from test_foundry import SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import watch  # noqa: E402


class ClosedWaits(Base):
    def test_closing_clears_the_waits(self):
        self.foundry("unit", "new", "--id", "a", "--title", "A")
        self.record("wait.add", id="a", wid="w1", kind="pr-review", ref="example-org/app#1", note="review")
        self.assertEqual(len(self.snap()["units"]["a"]["waits"]), 1)
        self.record("unit.status", id="a", status="done")
        self.assertEqual(self.snap()["units"]["a"]["waits"], [])

    def test_the_watcher_skips_a_closed_units_waits(self):
        w = {"wid": "w1", "kind": "pr-review", "ref": "example-org/app#1", "fingerprint": None}
        snap = {"units": {"done": {"id": "done", "status": "done", "waits": [w]},
                          "open": {"id": "open", "status": "waiting_others", "waits": [dict(w, wid="w2")]}}}
        seen = []
        with mock.patch.object(watch, "observe", side_effect=lambda w, now, s: seen.append(w["wid"]) or ("x", "x")):
            watch.poll_waits(snap, dt.datetime.now(dt.timezone.utc))
        self.assertEqual(seen, ["w2"])
