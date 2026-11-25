"""0.1.104: (1) docs/recommended-automode.md has a "Foundry review-ask queue" rule for leads queuing review-ask /
review-remind items with `foundry outbox add`; (2) a classifier denial of that command triages straight to the
owner card naming the rule, never to a retry; (3) smoke fixes: a repo without its remote launches from its local
default branch, gh in smoke.sh runs with the real HOME, and an empty `foundry mcp agent` mapping passes."""

import json
import os
import subprocess
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import triage  # noqa: E402

ROOT = os.path.dirname(tf.SCRIPTS)
CMD = ("foundry outbox add u1 --kind review-ask --channel C0000000001 --thread 1700000000.000100 "
       "--text 'please review' --pr o/r#5")


class Rule(tf.Base):
    def rules(self):
        with open(os.path.join(ROOT, "docs", "recommended-automode.md")) as f:
            block = f.read().split("```json\n", 1)[1].split("```", 1)[0]
        return json.loads("{" + block.strip() + "}")["autoMode"]["allow"]

    def test_the_rule_and_its_limits(self):
        rule = next(r for r in self.rules() if r.startswith("Foundry review-ask queue:"))
        for words in ("--kind review-ask|review-remind", "dispatcher", "dedup and quiet hours",
                      "slack_send_message", "DMs", "configured review channels"):
            self.assertIn(words, rule)

    def test_a_classifier_denial_goes_to_the_owner_not_a_retry(self):
        d = {"unit": "u1", "tool": "Bash", "command": CMD, "reason": "Unsanctioned Communication", "source": "classifier"}
        out = triage.classify(d)
        self.assertEqual(out["class"], 3)
        self.assertIn("do not retry", out["action"])
        self.assertIn("Foundry review-ask queue", out["card"]["rule_draft"])
        other = dict(d, command="foundry outbox add u1 --kind message --channel C0000000001 --text hi")
        self.assertEqual(triage.classify(other)["class"], 2)  # only review asks skip the retry

    def test_the_lead_is_told_not_to_retry(self):
        with open(os.path.join(ROOT, "agents", "line-lead.md")) as f:
            text = f.read()
        self.assertIn("Never retry it in another form", text)
        self.assertIn("Foundry review-ask queue", text)


class LocalOnlyRepo(tf.Base):
    def test_a_repo_without_its_remote_launches_from_local_main(self):
        repo = os.path.join(self.dir, "local")
        subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q",
                        "--allow-empty", "-m", "init"], check=True)
        self.foundry("repo", "add", "local", "--path", repo)
        sys.path.insert(0, tf.SCRIPTS)
        code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import launcher; "
                "print(json.dumps(launcher.make_worktree({'repo': 'local', 'path': sys.argv[2], 'branch': 'foundry/u1'})))")
        wt = os.path.join(self.dir, "wt", "u1")
        r = subprocess.run([sys.executable, "-c", code, tf.SCRIPTS, wt], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(json.loads(r.stdout))
        head = subprocess.run(["git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
        self.assertEqual(head.stdout.strip(), "foundry/u1")


class SmokeFixes(tf.Base):
    def test_gh_runs_with_the_real_home_and_an_empty_mapping_passes(self):
        with open(os.path.join(tf.SCRIPTS, "smoke.sh")) as f:
            src = f.read()
        self.assertIn('HOME="$REAL_HOME" gh issue view', src)
        self.assertIn("no MCP mapped", src)
        self.assertNotIn("git remote add", src)  # the smoke repo stays local-only: it covers the launch fix
