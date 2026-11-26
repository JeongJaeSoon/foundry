"""0.1.65/0.1.66: org and owner values become user prefs. `prefs materialize` (the first prefs load, `foundry up`
and preflight) copies every ORG_KEYS value the user's prefs.json lacks from the last effective prefs, once; with
the shipped defaults now org-neutral, the effective prefs of an existing install do not change. Jira wording, the bump bot and desktop
notifications come from prefs/config instead of code."""

import json
import os
import re
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)


class Materialize(tf.Base):
    """An install that ran an earlier version has prefs.valid.json (its last effective prefs) and no marker."""
    CREW = False

    def setUp(self):
        super().setUp()
        self._old = {k: os.environ.get(k) for k in ("FOUNDRY_STATE_DIR", "FOUNDRY_PREFS_OVERLAY")}
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        import foundrylib as cl
        import prefs
        cl._ZONE.clear()
        # what 0.1.65 left behind: the old org defaults as the last effective prefs
        self.old_effective = prefs.defaults()
        with open(os.path.join(self.dir, "prefs.valid.json"), "w") as f:
            json.dump(self.old_effective, f)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def user(self):
        with open(os.path.join(self.dir, "prefs.json")) as f:
            return json.load(f)

    def test_copies_absent_keys_once_and_keeps_the_users_own(self):
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"review": {"channel": "C0OWN"}}, f)
        _, out = self.foundry("prefs", "materialize")
        self.assertTrue(out["materialized"])
        self.assertIn("merge.owner_paths", out["keys"])
        self.assertNotIn("review.channel", out["keys"])
        self.assertEqual(self.user()["review"]["channel"], "C0OWN")
        _, again = self.foundry("prefs", "materialize")
        self.assertEqual(again, {"materialized": False, "keys": []})
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            self.assertEqual(sum('"prefs.materialized"' in x for x in f), 1)

    def test_an_upgrade_keeps_the_effective_prefs_with_neutral_defaults(self):
        import prefs
        os.environ.pop("FOUNDRY_PREFS_OVERLAY", None)  # the shipped, org-neutral defaults
        self.assertEqual(prefs.defaults()["merge"]["owner_paths"], [])
        self.assertEqual(prefs.load(), self.old_effective)  # the first load materializes before anything else
        self.assertTrue(os.path.exists(os.path.join(self.dir, "prefs.materialized")))
        self.assertEqual(self.user()["merge"]["owner_paths"], self.old_effective["merge"]["owner_paths"])
        self.assertEqual(prefs.load(), self.old_effective)

    def test_a_fresh_install_gets_only_the_marker(self):
        import prefs
        os.remove(os.path.join(self.dir, "prefs.valid.json"))
        os.environ.pop("FOUNDRY_PREFS_OVERLAY", None)
        self.assertEqual(prefs.load()["review"]["people"], [])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "prefs.json")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "prefs.materialized")))

    def test_up_and_preflight_materialize(self):
        self.foundry("preflight", ok=(0, 1, 2))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "prefs.materialized")))

    def test_inside_a_session_it_is_refused(self):
        import subprocess
        env = dict(self.env, FOUNDRY_ROLE="line-lead")
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "prefs", "materialize"],
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class JiraWording(tf.Base):
    def test_project_scope_and_site_come_from_prefs(self):
        import sync
        p = {"sync": {"jira": {"project": "ZZZ", "scope": "Acme work", "site": "the acme site"}}}
        self.assertIn("use the acme site's id", sync.instructions("jira", p))
        rule = sync.NO_TICKET_RULE.format(**sync._jira(p))
        self.assertIn("project = ZZZ AND", rule)
        self.assertIn("not Acme work gets no ticket", rule)
        self.assertEqual(re.findall(r"\bproject (?:= )?(\S+)", rule), ["ZZZ", "ZZZ"])  # no project but the prefs one


class Bump(tf.Base):
    def test_no_bump_bot_unless_prefs_names_one(self):
        import gitpolicy
        self.assertEqual(gitpolicy._bump_prefs({})["bump_pr_authors"], [])


class Notify(tf.Base):
    def test_notify_command_override(self):
        import foundrylib as cl
        out = os.path.join(self.dir, "notified")
        os.environ["FOUNDRY_NOTIFY_CMD"] = f"sh -c 'printf %s \"$0\" > {out}'"
        try:
            cl.desktop_notify("foundry", "hello")
        finally:
            os.environ.pop("FOUNDRY_NOTIFY_CMD")
        with open(out) as f:
            self.assertEqual(f.read(), "hello")


if __name__ == "__main__":
    import unittest
    unittest.main()
