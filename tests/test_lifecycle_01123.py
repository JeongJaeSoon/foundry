"""0.1.123: the sessions stream says once per unit and status when a waiting unit has nothing that will move it (no
wait a watcher resolves, no open owner question), so the foreman resumes its lead or sets the real status. Nothing
changes by itself."""

import os
import sys

from test_foundry import SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import watchers  # noqa: E402


class Stuck(Base):
    def lines(self):
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            return watchers.stuck_lines()
        finally:
            if old is None:
                os.environ.pop("FOUNDRY_STATE_DIR")
            else:
                os.environ["FOUNDRY_STATE_DIR"] = old

    def test_once_per_unit_and_status(self):
        for uid in ("idle", "watched"):
            self.foundry("unit", "new", "--id", uid, "--title", uid)
            self.record("unit.status", id=uid, status="waiting_others")
        self.record("wait.add", id="watched", wid="w1", kind="pr-review", ref="example-org/app#1", note="review")
        first = self.lines()
        self.assertEqual(len(first), 1)
        self.assertIn("unit idle waiting_others waits on nothing that will move it", first[0])
        self.assertIn("foundry unit resume idle", first[0])
        self.assertEqual(self.lines(), [])
        self.record("unit.status", id="idle", status="waiting_owner")
        self.assertEqual(len(self.lines()), 1)
        self.record("unit.status", id="idle", status="done")
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.snap()["units"]["idle"]["status"], "done")
