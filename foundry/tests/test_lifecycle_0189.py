"""0.1.89: Jira and GitHub tracker comments get prefs sync.comment_footer from the hook too: a new Jira comment through
updatedInput, a `gh issue|pr comment --body-file /tmp/foundry-<batch>.md` by appending to that file. The sync items and
the dispatcher no longer mention it for them (Linear keeps the item footer: no hook sees its tool)."""

import json
import os

import test_foundry as tf

ROOT = os.path.dirname(tf.SCRIPTS)
JIRA = "mcp__plugin_acme_acme-atlassian__addOrEditJiraIssueComment"
FOOT = "🤖 test tracker footer"


class TrackerFooter(tf.Base):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"sync": {"comment_footer": FOOT}}, f)

    def pre(self, tool, ti, **kw):
        return self.hook("PreToolUse", raw=True, tool_name=tool, tool_input=ti, **kw)

    def test_a_new_jira_comment_gets_the_footer_once(self):
        out = self.pre(JIRA, {"cloudId": "c", "issueIdOrKey": "PROJ-1", "commentBody": "- done"})
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["commentBody"], f"- done\n\n{FOOT}")
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])
        self.assertIsNone(self.pre(JIRA, {"cloudId": "c", "issueIdOrKey": "PROJ-1", "commentBody": f"x\n\n{FOOT}"}))
        self.assertIsNone(self.pre(JIRA, {"cloudId": "c", "issueIdOrKey": "PROJ-1", "commentId": "9",
                                          "commentBody": "edit"}))  # an edit is never re-signed

    def test_a_gh_comment_body_file_is_signed_before_gh_reads_it(self):
        path = "/tmp/foundry-sb-test0189.md"
        try:
            with open(path, "w") as f:
                f.write("- step one\n")
            cmd = f"gh issue comment 7 -R o/r --body-file {path}"
            self.env["FOUNDRY_ROLE"] = "foreman"
            for _ in range(2):  # twice: still one footer
                out = self.pre("Bash", {"command": cmd})
                self.assertFalse(self.denied(out), out)
            with open(path) as f:
                self.assertEqual(f.read(), f"- step one\n\n{FOOT}\n")
        finally:
            os.remove(path)

    def test_sync_items_carry_no_footer_for_jira_and_github(self):
        import sys
        sys.path.insert(0, tf.SCRIPTS)
        import sync
        self.assertNotIn("footer", sync.POSTED_RULE.lower())
        with open(os.path.join(ROOT, "agents", "dispatcher.md")) as f:
            t = f.read()
        self.assertEqual(t.count("footer"), 1)  # the linear line only
