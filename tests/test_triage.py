"""Denial triage: journaling by source, near misses after a deny, false positives, classification, rule drafts."""

import sys

from test_foundry import LEAD, SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import triage  # noqa: E402

URL = "https://api.example.invalid/x"


class Triage(Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.start_lead("alpha")

    def bash(self, cmd):
        return self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **LEAD)

    def classifier_deny(self, cmd):
        self.hook("PermissionDenied", tool_name="Bash", tool_input={"command": cmd}, reason="classifier said no", **LEAD)

    def test_classifier_denial_then_similar_is_a_nearmiss(self):
        self.classifier_deny(f"curl -X POST {URL}")
        d = self.snap()["denials"][-1]
        self.assertEqual((d["unit"], d["source"], d["command"]), ("alpha", "classifier", f"curl -X POST {URL}"))
        self.assertIsNone(self.bash(f"curl -X POST {URL}"), "an identical retry is not a near miss")
        out = self.bash(f"curl -s -d a {URL}")
        self.assertIn("another form", out["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])
        nm = self.snap()["nearmiss"]
        self.assertEqual([(n["kind"], n["unit"]) for n in nm], [("deny-then-similar", "alpha")])
        _, model = self.foundry("board", "--format", "json")
        self.assertEqual(model["nearmiss_count"], 1)
        self.assertIn("nearmiss", [e["kind"] for e in model["escalations"]])
        self.assertIsNone(self.bash("ls -la"))

    def test_nearmiss_window_is_thirty_minutes(self):
        self.classifier_deny(f"curl -X POST {URL}")
        self.env["FOUNDRY_NOW"] = "2026-11-18T10:31:00+09:00"
        self.assertIsNone(self.bash(f"curl -s -d a {URL}"))

    def test_rule_denial_from_failure_and_outside_sessions_ignored(self):
        self.hook("PostToolUseFailure", tool_name="Bash", tool_input={"command": "rm -rf /x/y"},
                  error="Permission to use Bash with command rm -rf /x/y has been denied.", **LEAD)
        self.hook("PostToolUseFailure", tool_name="Bash", tool_input={"command": "false"}, error="exit 1", **LEAD)
        self.hook("PermissionDenied", session_id="OTHER", tool_name="Bash", tool_input={"command": "x"}, reason="r")
        ds = self.snap()["denials"]
        self.assertEqual([(d["source"], d["command"]) for d in ds], [("rule", "rm -rf /x/y")])

    def test_false_positive_cli(self):
        self.bash("git status; git diff")  # a hook deny
        _, out = self.foundry("triage", "false-positive", "--unit", "alpha", "--detail", "matched text in a doc")
        s = self.snap()
        self.assertEqual(s["denials"][-1]["source"], "false-positive")
        self.assertEqual(s["nearmiss"][-1]["kind"], "guard-false-positive")
        self.classifier_deny(f"curl -X POST {URL}")
        rc, _ = self.foundry("triage", "false-positive", "--unit", "alpha", "--detail", "x", ok=(2,))
        self.assertEqual(rc, 2)

    def test_classify_defaults(self):
        hook_deny = {"unit": "alpha", "tool": "Bash", "source": "hook", "command": "git status; git diff",
                     "reason": "one command per Bash call (no `;`, `&&`, `||`, `|`, redirects): run 2 separate Bash calls: "
                               "`git status`, then `git diff`"}
        self.assertEqual(triage.classify(hook_deny, ["git status"])["class"], 1)
        cls = {"unit": "alpha", "tool": "Bash", "source": "classifier", "command": f"curl -X POST {URL}",
               "reason": "no"}
        self.assertEqual(triage.classify(cls, [])["class"], 2)
        res = triage.classify(cls, [f"curl -X POST {URL}", f"curl -s -d a {URL}"])
        self.assertEqual(res["class"], 3)
        card = res["card"]
        self.assertEqual([o["label"] for o in card["options"]], ["this once", "add rule"])
        self.assertIn("Not covered:", card["options"][1]["preview"])

    def test_classify_cli_opens_a_card_on_the_board(self):
        self.classifier_deny(f"curl -X POST {URL}")
        _, out = self.foundry("triage", "classify", "--unit", "alpha", "--followup", f"curl -X POST {URL}",
                              "--followup", f"curl -d a {URL}", "--open-card")
        self.assertEqual(out["class"], 3)
        _, model = self.foundry("board", "--format", "json")
        self.assertIn(out["card_id"], [c["id"] for c in model["cards"]])

    def test_recurring_once_answers_become_rule_drafts(self):
        for _ in range(3):
            self.foundry("triage", "answer", "--pattern", "npm test <path>", "--answer", "once",
                         "--command", "npm test ./pkg")
        self.foundry("triage", "answer", "--pattern", "make deploy", "--answer", "once")
        _, out = self.foundry("triage", "rules")
        self.assertEqual([d["pattern"] for d in out["drafts"]], ["npm test <path>"])
        self.assertIn("Not covered:", out["drafts"][0]["draft"])
