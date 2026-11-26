"""0.1.67: Jira without a project key creates no ticket and fails the preflight tracker check; an unreadable
prefs.valid.json never crashes a prefs load."""

import json
import os
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)


class JiraWithoutProject(tf.Base):
    def test_no_create_ticket_rule_and_a_preflight_failure(self):
        import factory
        import sync
        p = {"trackers": ["jira"], "sync": {"jira": {"project": "", "scope": "tracked work", "site": "x"}}}
        self.assertNotIn("createJiraIssue", sync.NO_PROJECT_RULE)
        self.assertEqual(sync._jira_project(p), "")
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            with open(os.path.join(self.dir, "config.json"), "w") as f:
                json.dump({"jira_base": "https://x.atlassian.net"}, f)
            ok, detail = factory._check_trackers(p)
            self.assertFalse(ok)
            self.assertIn("sync.jira.project is empty", detail)
            p["sync"]["jira"]["project"] = "PROJ"
            self.assertTrue(factory._check_trackers(p)[0])
        finally:
            if old is None:
                os.environ.pop("FOUNDRY_STATE_DIR", None)
            else:
                os.environ["FOUNDRY_STATE_DIR"] = old

    def test_the_drained_batch_carries_the_no_project_rule(self):
        self.foundry("prefs", "set", "sync.jira", json.dumps({"project": "", "scope": "x", "site": "y"}), "--by", "owner")
        self.foundry("unit", "new", "--id", "u1", "--title", "U1", "--tracker", "jira")
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--event", "manual", "--text", "progress")
        _, out = self.foundry("sync", "drain", "--json")
        jira = [x for x in out["items"] if x["target"] == "jira"]
        self.assertTrue(jira, out)
        self.assertIn("sync.jira.project is empty", jira[0]["instructions"])
        self.assertNotIn("createJiraIssue", jira[0]["instructions"])


class CorruptValid(tf.Base):
    CREW = False

    def test_an_unreadable_valid_file_without_marker_still_loads(self):
        with open(os.path.join(self.dir, "prefs.valid.json"), "w") as f:
            f.write("{not json")
        _, out = self.foundry("prefs", "show")
        self.assertTrue(out)


if __name__ == "__main__":
    import unittest
    unittest.main()
