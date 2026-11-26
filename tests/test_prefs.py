"""prefs.json: deep merge over plugin defaults, validation that refuses bad files, the CLI, and windows."""

import datetime as dt
import json
import os
import sys

from test_foundry import SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import prefs  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))


class Prefs(Base):
    def write(self, obj):
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))

    def test_defaults_and_deep_merge(self):
        _, out = self.foundry("prefs", "show")
        self.assertEqual(out["prefs"]["night_window"], "22:00-08:00")
        self.assertEqual(out["prefs"]["slack_quiet"], "22:00-08:00")
        self.assertNotIn("max_open_prs_per_reviewer", out["prefs"])  # no reviewer cap: display only
        self.write({"intake": {"autostart_max_per_day": 3}})
        _, out = self.foundry("prefs", "show")
        self.assertEqual(out["prefs"]["intake"], {"autostart_max_per_day": 3, "promoted_kinds": [], "scan_max_per_day": 5})

    def test_invalid_file_is_refused_and_last_valid_is_used(self):
        self.write({"night_window": "23:00-07:00"})
        self.foundry("prefs", "show")  # records the valid merge
        self.write({"night_window": "late", "bogus": 1})
        rc, out = self.foundry("prefs", "validate", ok=(2,))
        self.assertFalse(out["valid"])
        self.assertTrue(any("night_window" in e for e in out["errors"]))
        self.assertTrue(any("bogus" in e for e in out["errors"]))
        _, out = self.foundry("prefs", "show")
        self.assertEqual(out["prefs"]["night_window"], "23:00-07:00")
        self.assertIn("refused", out["error"])

    def test_set_validates_writes_and_journals(self):
        self.foundry("prefs", "set", "reviewer_load_warn", "8")
        self.foundry("prefs", "set", "night_window", "nope", ok=(2,))
        with open(os.path.join(self.dir, "prefs.json")) as f:
            self.assertEqual(json.load(f), {"reviewer_load_warn": 8})
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            ops = [json.loads(x) for x in f]
        self.assertEqual([(o["op"], o["key"], o["new"]) for o in ops if o["op"] == "prefs.changed"],
                         [("prefs.changed", "reviewer_load_warn", 8)])

    def test_windows_wrap_midnight(self):
        z = JST
        w = "22:00-08:00"
        self.assertTrue(prefs.in_window(dt.datetime(2026, 11, 18, 22, 0, tzinfo=z), w, z))
        self.assertTrue(prefs.in_window(dt.datetime(2026, 11, 18, 7, 59, tzinfo=z), w, z))
        self.assertFalse(prefs.in_window(dt.datetime(2026, 11, 18, 8, 0, tzinfo=z), w, z))
        self.assertFalse(prefs.in_window(dt.datetime(2026, 11, 18, 21, 30, tzinfo=z), w, z))
        self.assertTrue(prefs.in_window(dt.datetime(2026, 11, 18, 13, 0, tzinfo=z), "12:00-14:00", z))
