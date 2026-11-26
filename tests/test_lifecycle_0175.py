"""0.1.75: GitHub Issues as a tracker: prefs trackers and repos.json take `github`, the sync drain hands the dispatcher
gh-only items (repo, issue, labels, open/closed), an assigned issue from intake is the unit's ticket, and Jira or Linear
items never receive an `owner/repo#n` key."""

import os
import subprocess

import test_foundry as tf


class GitHubTracker(tf.Base):
    CREW = False

    def setUp(self):
        super().setUp()
        self.env.pop("FOUNDRY_PREFS_OVERLAY", None)
        self.gh({"api user": {"login": "octo"}})
        self.clone = os.path.join(self.dir, "src", "app")
        subprocess.run(["git", "init", "-q", "-b", "main", self.clone], check=True)
        self.foundry("init", "--worktree-root", os.path.join(self.dir, "wt"), "--foreman-dir",
                     os.path.join(self.dir, "fm"), "--tracker", "github", "--repo", f"octo/app={self.clone}",
                     "--timezone", "UTC")

    def drain(self):
        _, out = self.foundry("sync", "drain", "--json")
        return [x for x in out["items"] if x["target"] == "github"]

    def test_init_enables_github_and_the_repo_takes_it(self):
        _, prefs = self.foundry("prefs", "show")
        self.assertEqual(prefs.get("trackers", (prefs.get("prefs") or {}).get("trackers")), ["github"])
        _, out = self.foundry("repo", "add", "octo/app", "--path", self.clone, "--tracker", "github")
        self.assertNotIn("error", out)

    def test_a_unit_without_an_issue_asks_for_one_in_its_repo(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "Fix login", "--repo", "octo/app", "--tracker", "github")
        self.foundry("sync", "enqueue", "u1", "--target", "github", "--text", "progress")
        [item] = self.drain()
        self.assertEqual((item["repo"], item["tickets"], item["state"], item["labels"]), ("octo/app", [], "open", []))
        self.assertTrue(item["needs_ticket"])
        self.assertIn("gh issue create -R", item["instructions"])
        self.assertIn("--body-file", item["instructions"])
        self.assertNotIn("footer", item)  # 0.1.89: the hook appends it to the body file

    def test_done_closes_labels_come_from_prefs_and_the_close_records_the_state(self):
        self.foundry("prefs", "set", "sync.github.labels", '["foundry"]', "--by", "owner")
        self.foundry("unit", "new", "--id", "u1", "--title", "Fix login", "--repo", "octo/app", "--tracker", "github",
                     "--ticket", "octo/app#5", "--ticket", "PROJ-1")
        self.foundry("unit", "status", "u1", "done", "--by", "owner")
        self.foundry("sync", "enqueue", "u1", "--target", "github", "--text", "shipped")
        [item] = self.drain()
        self.assertEqual((item["tickets"], item["state"], item["labels"]), (["octo/app#5"], "closed", ["foundry"]))
        self.assertNotIn("needs_ticket", item)
        _, out = self.foundry("sync", "done", item["batch"], "--ticket-status", "closed")
        self.assertEqual(out["ticket_states"], 1)  # PROJ-1 is not the GitHub issue

    def test_prefs_reject_a_bad_label_list(self):
        rc, _ = self.foundry("prefs", "set", "sync.github.labels", '"foundry"', "--by", "owner", ok=(0, 1, 2))
        _, prefs = self.foundry("prefs", "show")
        labels = ((prefs.get("sync") or (prefs.get("prefs") or {}).get("sync") or {}).get("github") or {}).get("labels")
        self.assertNotEqual(labels, "foundry")


    def test_jira_items_skip_github_issue_keys(self):
        self.foundry("prefs", "set", "trackers", '["jira", "github"]', "--by", "owner")
        self.foundry("prefs", "set", "sync.jira.project", "PROJ", "--by", "owner")
        self.foundry("unit", "new", "--id", "u1", "--title", "t", "--tracker", "jira", "--ticket", "PROJ-1",
                     "--ticket", "octo/app#5")
        self.foundry("sync", "enqueue", "u1", "--target", "jira", "--text", "x")
        _, out = self.foundry("sync", "drain", "--json")
        self.assertEqual([x["tickets"] for x in out["items"] if x["target"] == "jira"], [["PROJ-1"]])

    def test_an_assigned_issue_is_its_units_ticket(self):
        _, item = self.foundry("intake", "add", "--source", "github", "--ref", "octo/app#7", "--title", "Bug",
                               "--kind", "issue")
        uid = item.get("unit") or (item.get("item") or {}).get("unit")
        self.assertTrue(uid, item)
        self.assertEqual(self.snap()["units"][uid]["keys"]["ticket"], ["octo/app#7"])


    def test_github_intake_needs_no_github_tracker(self):
        self.foundry("prefs", "set", "trackers", "[]", "--by", "owner")
        _, item = self.foundry("intake", "add", "--source", "github", "--ref", "octo/app#8", "--title", "Review",
                               "--kind", "review")
        self.assertNotIn("refused", str(item))

class DispatcherGhCalls(tf.Base):
    """The dispatcher's gh issue forms, each one command per call, pass the hooks; inline multi-line bodies do not."""

    def test_issue_commands_pass_the_git_policy(self):
        self.spawn("foundry:dispatcher", **tf.ORCH)
        self.hook("SubagentStart", session_id="S1", agent_id="D1", agent_type="foundry:dispatcher")
        ctx = dict(tf.ORCH, agent_id="D1", agent_type="foundry:dispatcher")
        for cmd in ("gh issue view 5 -R octo/app --comments",
                    "gh issue comment 5 -R octo/app --body-file /tmp/foundry-sb-1.md",
                    "gh issue edit 5 -R octo/app --add-label foundry",
                    "gh issue close 5 -R octo/app", "gh issue reopen 5 -R octo/app",
                    "gh issue create -R octo/app --title Fix --body-file /tmp/foundry-sb-1.md"):
            out = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **ctx)
            self.assertFalse(self.denied(out), (cmd, out))
        out = self.hook("PreToolUse", tool_name="Bash",
                        tool_input={"command": "gh issue comment 5 -R octo/app --body 'a\nb'"}, **ctx)
        self.assertTrue(self.denied(out))

    def test_a_github_issue_key_links_to_the_issue(self):
        import sys
        sys.path.insert(0, tf.SCRIPTS)
        import views
        snap = {"units": {"u1": {"keys": {"ticket": ["octo/app#5"]}}}}
        self.assertEqual(views.unit_url(snap, "u1"), "https://github.com/octo/app/issues/5")
