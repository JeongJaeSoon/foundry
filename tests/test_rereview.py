"""Regressions for the focused re-review: the triggers that still broke findings 2, 3, 5 and 9 after the fixes."""

import json
import os
import sys
import unittest

import test_review_fable_b as rb
from test_foundry import LEAD, SCRIPTS
from test_review_fable import Fable, env

sys.path.insert(0, SCRIPTS)
import foundrylib as cl  # noqa: E402


class CleanEnvironment(rb.OwnerOnly):
    """3: `env -i` (or any script that drops the environment) does not turn a lead into the owner."""

    def test_a_cleared_environment_is_still_the_foundry_session(self):
        proc = [4242, "Wed Nov 18 06:00:00 2026"]
        self.record("session.foundry", session="SL", role="line-lead", unit="a", proc=proc)
        # Nothing in the environment says foundry, but the lead's claude process is an ancestor.
        self.refused({"FOUNDRY_TEST_ANCESTORS": json.dumps([proc])}, "send", "allow-now", "--channel", "C1", "--reason", "x")
        self.refused({"FOUNDRY_TEST_ANCESTORS": json.dumps([proc])}, "prefs", "set", "slack_quiet", "12:00-12:01")
        self.assertEqual(self.ops("send.override") + self.ops("prefs.changed"), [])
        # A reused pid with another start time is some other claude: the owner's terminal keeps working.
        rc, out, err = self.run_f("prefs", "set", "slack_quiet", "22:00-08:00",
                                  env={"FOUNDRY_TEST_ANCESTORS": json.dumps([[4242, "Thu Nov 19 06:00:00 2026"]])})
        self.assertEqual(rc, 0, err + out)

    def test_session_start_records_the_claude_process(self):
        proc = [777, "Wed Nov 18 06:00:00 2026"]
        self.env["FOUNDRY_TEST_ANCESTORS"] = json.dumps([proc])
        self.start_lead("a")
        self.assertIn(proc, cl.load()["foundry_sessions"]["SL"]["procs"])


class Guards(Fable):
    def test_env_clearing_and_interpreters_cannot_hide_foundry(self):
        f = os.path.join(SCRIPTS, "foundry.py")
        for cmd in (f"env -i python3 {f} send allow-now --channel C1 --reason x",
                    "env - foundry prefs set slack_quiet 12:00-12:01",
                    "env --ignore-environment PATH=/usr/bin foundry unit set alpha --status done",
                    f"env -u HOME python3 {f} send allow-now --channel C1 --reason x",
                    "/usr/bin/env -i HOME=/x foundry intake promote alpha"):
            self.assertTrue(self.denied(self.bash(cmd)), cmd)

    def test_literal_dollar_in_git_and_gh_args_passes(self):
        for cmd in (f"git -C {self.wt} commit -m 'fix: cost is $5'", f"git -C {self.wt} grep 'foo$'",
                    f"git -C {self.wt} log -S'$x'", "gh pr create --title t --body 'uses $HOME literally'",
                    f"git -C {self.wt} commit -m \"price \\$5\""):
            self.assertFalse(self.denied(self.bash(cmd)), cmd)
        for cmd in (f'git -C {self.wt} commit -m "$MSG"', f"git -C {self.wt} push origin $BR",
                    "gh pr merge ${N} --match-head-commit abc"):
            self.assertTrue(self.denied(self.bash(cmd)), cmd)

    def test_gh_api_ref_moves_and_merges_are_denied(self):
        for cmd in ("gh api -X PATCH repos/o/r/git/refs/heads/main -F force=true",
                    "gh api -X POST repos/o/r/merges -f base=main -f head=x",
                    "gh api graphql -F query=@merge.graphql",
                    "gh api --method=DELETE repos/o/r/git/refs/heads/main"):
            self.assertTrue(self.denied(self.bash(cmd)), cmd)
        self.assertFalse(self.denied(self.bash("gh api repos/o/r/git/refs/heads/main")))  # a read

    def test_state_dir_named_in_any_form(self):
        sd = self.dir
        home = os.path.expanduser("~")
        rel_home = sd[len(home) + 1:] if sd.startswith(home + "/") else None
        cmds = [f"python3 -c \"open('{sd}/repos.json','w').write('{{}}')\"",
                f"echo x > {sd.upper()}/prefs.json"]
        if rel_home:
            cmds.append(f"python3 -c \"import os;open(os.path.expanduser('~/{rel_home}/prefs.json'),'w')\"")
        for cmd in cmds:
            self.assertTrue(self.denied(self.bash(cmd)), cmd)
        # Relative to a cwd inside the state dir.
        os.makedirs(os.path.join(sd, "handoffs"), exist_ok=True)
        out = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": "echo '{}' > ../prefs.json"},
                        cwd=os.path.join(sd, "handoffs"), **LEAD)
        self.assertTrue(self.denied(out))
        # A sibling directory that only starts with the same name is not the state dir.
        with env(FOUNDRY_STATE_DIR=self.dir):
            import approve
            self.assertFalse(approve.touches_state_dir("Bash", {"command": f"ls {sd}-tests/x"}, None))

class Lows(rb.OwnerOnly):
    def test_lock_take_for_another_unit_is_the_owners(self):
        lead_a = {"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "a"}
        self.refused(lead_a, "lock", "take", "db", "--unit", "b")  # a lead takes locks for its own unit only
        self.assertEqual(self.ops("lock.take"), [])
        self.assertEqual(self.run_f("lock", "take", "db", "--unit", "a", env=lead_a)[0], 0)
        rc, out, err = self.run_f("lock", "take", "db", "--unit", "b")  # the owner, while a holds it
        self.assertNotEqual(rc, 0)
        self.assertIn("held by a", err + out)
        self.assertEqual(self.run_f("lock", "release", "db", "--unit", "a", env=lead_a)[0], 0)
        self.assertEqual(self.run_f("lock", "take", "db", "--unit", "b")[0], 0)  # free again

    def test_a_stored_unreadable_until_is_reported_once_not_raised(self):
        # Written before `wait add` validated --until.
        self.record("wait.add", id="a", wid="w-bad", kind="time", ref="deploy", until="tomorrow 9am", note=None,
                    fingerprint=None)
        out = self.script("watch.py", "poll")
        self.assertEqual(out.get("errors") or [], [])
        self.assertEqual([e["kind"] for e in out["events"]], ["time"])
        self.assertIn("unreadable until", json.dumps(out["events"], ensure_ascii=False))
        self.assertEqual(self.script("watch.py", "poll")["events"], [])  # said once


def load_tests(loader, tests, pattern):
    """Only this module's own tests: the imported base classes carry their own, run in their modules."""
    return unittest.TestSuite(cls(n) for cls in (CleanEnvironment, Guards, Lows) for n in vars(cls) if n.startswith("test_"))


if __name__ == "__main__":
    unittest.main()
