"""0.1.64: the clock follows prefs `timezone` (any IANA zone): cl.now(), naive times, and the displays that used a
fixed JST. Also: the pane's built-in Korean table is the ko pane catalog."""

import datetime as dt
import json
import os
import re
import subprocess
import sys
import unittest

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import i18n  # noqa: E402

HOOKS = os.path.join(os.path.dirname(tf.SCRIPTS), "hooks")

PROBE = """
import sys
sys.path.insert(0, sys.argv[1])
import foundrylib as cl
print(cl.now().utcoffset().total_seconds() / 3600, cl.parse_ts("2026-07-01T12:00:00").utcoffset().total_seconds() / 3600)
"""


class Clock(tf.Base):
    CREW = False

    def probe(self, env=None):
        e = dict(self.env, **(env or {}))
        e.pop("FOUNDRY_NOW", None)
        r = subprocess.run([sys.executable, "-c", PROBE, tf.SCRIPTS], env=e, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [float(x) for x in r.stdout.split()]

    def test_default_zone_is_tokyo(self):
        self.assertEqual(self.probe(), [9.0, 9.0])

    def test_prefs_timezone_drives_now_and_naive_times(self):
        self.foundry("prefs", "set", "timezone", "Europe/Berlin", "--by", "owner")
        now_h, naive_h = self.probe()
        self.assertIn(now_h, (1.0, 2.0))
        self.assertEqual(naive_h, 2.0)  # July: CEST

    def test_handover_title_uses_the_zone(self):
        import factory
        import foundrylib as cl
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        self.addCleanup(lambda: os.environ.__setitem__("FOUNDRY_STATE_DIR", old) if old else
                        os.environ.pop("FOUNDRY_STATE_DIR", None))
        self.foundry("prefs", "set", "timezone", "UTC", "--by", "owner")
        cl._ZONE.clear()
        t = dt.datetime(2026, 11, 20, 0, 30, tzinfo=dt.timezone.utc)
        text = factory.handover(cl.load(), [], {"repos": {}}, t=t)
        self.assertIn("2026-11-20 00:30", text.split("\n")[0])
        cl._ZONE.clear()


class PaneFallback(unittest.TestCase):
    def test_the_pane_korean_table_is_the_ko_catalog(self):
        with open(os.path.join(HOOKS, "board-pane.tsx"), encoding="utf-8") as f:
            src = f.read()
        body = src.split("export const PANE_KO = {", 1)[1].split("} as const", 1)[0]
        table = {k: json.loads('"' + v.replace('\\\'', "'").replace('"', '\\"') + '"')
                 for k, v in re.findall(r"^\s+(\w+): '((?:[^'\\]|\\.)*)',?$", body, re.M)}
        # board.pane_words adds the status words to the pane catalog; PANE_KO keeps their ko values as its fallback.
        self.assertEqual(table, {**i18n._file("ko", "pane"),
                                 **{f"status_{s}": i18n.status_word(s, "ko") for s in i18n.STATUS_KEYS}})


if __name__ == "__main__":
    unittest.main()
