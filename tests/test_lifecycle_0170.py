"""0.1.70: every foundry cron prompt starts with `[foundry-cron <name> <sha8>]` (hash over the spec prompt), and
`up --check` compares only that tag, so a prompt CronList cut short still checks; an untagged cron is recreated."""

import json
import os
import subprocess
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)


class CronTag(tf.Base):
    def check(self, live):
        p = os.path.join(self.dir, "crons.json")
        with open(p, "w") as f:
            json.dump(live, f)
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "up", "--check", "--crons", p],
                           env=self.env, capture_output=True, text=True)
        return r.returncode, json.loads(r.stdout)

    def live(self):
        import prefs
        import watchers
        self.scheds = prefs.load()["schedules"]
        return [{"id": f"c{i}", "cron": watchers.cron_lead(s["cron"]), "prompt": watchers.cron_prompt(s)}
                for i, s in enumerate(self.scheds)]

    def test_up_emits_the_tag(self):
        _, plan = self.foundry("up")
        for c in plan["crons"]:
            self.assertRegex(c["prompt"], rf"^\[foundry-cron {c['name']} [0-9a-f]{{8}}\] ")

    def test_truncated_prompts_still_match(self):
        live = self.live()
        for x in live:
            x["prompt"] = x["prompt"][:x["prompt"].index("]") + 4]  # CronList cuts long prompts right after the tag
        rc, out = self.check(live)
        self.assertEqual((rc, out["recreate"], out["missing"]), (0, [], []), out)

    def test_changed_spec_and_untagged_legacy_are_recreated(self):
        import watchers
        live = self.live()
        live[0]["prompt"] = live[0]["prompt"].replace(live[0]["prompt"][len(self.scheds[0]["name"]) + 15:
                                                                         len(self.scheds[0]["name"]) + 23], "deadbeef")
        live[1]["prompt"] = watchers.cron_base_prompt(self.scheds[1])  # created before 0.1.70
        rc, out = self.check(live)
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(x["delete"] for x in out["recreate"]), ["c0", "c1"])

    def test_tagged_and_legacy_crons_journal_under_their_schedule(self):
        import watchers
        live = self.live()
        s = self.scheds[0]
        self.assertEqual(watchers.cron_key({"cron": live[0]["cron"], "prompt": live[0]["prompt"][:live[0]["prompt"].index("]") + 3]}, "x"), s["name"])
        self.assertEqual(watchers.cron_key({"cron": live[0]["cron"], "prompt": watchers.cron_base_prompt(s)}, "x"),
                         s["name"])


if __name__ == "__main__":
    import unittest
    unittest.main()
