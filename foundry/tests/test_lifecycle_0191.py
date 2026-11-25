"""0.1.91: (1) CI is judged on each check's newest run: a run cancelled because a later run of the same check replaced
it is not a failure (example-org/app#1672: three metadata checks cancelled and rerun green), and a newest run that is
itself cancelled waits (pending) rather than failing. (2) Every finished subagent gets a scripted verdict, so a
finished hand or judge never raises a housekeeping event (the judge -> event -> judge loop)."""

import json
import os
import sys
import unittest

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import watch  # noqa: E402


def run(name, conclusion, start, wf="Check PR metadata"):
    return {"__typename": "CheckRun", "name": name, "workflowName": wf, "status": "COMPLETED",
            "conclusion": conclusion, "startedAt": f"2026-11-24T02:{start}Z", "completedAt": f"2026-11-24T02:{start}Z"}


ROLLUP_1672 = [run("CodeRabbit", "SUCCESS", "48:59", ""), run("Pytest app", "SUCCESS", "48:43", "Pytest")]
for n in ("check-label", "check-ai-level-label", "check-milestone"):
    ROLLUP_1672 += [run(n, "CANCELLED", "48:43"), run(n, "CANCELLED", "48:43"), run(n, "SUCCESS", "48:44"),
                    run(n, "SUCCESS", "49:00")]


class LatestRun(unittest.TestCase):
    def test_superseded_cancelled_runs_are_not_failures(self):
        self.assertEqual(watch.ci_bucket(ROLLUP_1672, human=set()), "success")

    def test_a_newest_run_that_failed_still_fails(self):
        self.assertEqual(watch.ci_bucket(ROLLUP_1672 + [run("check-label", "FAILURE", "50:00")], human=set()), "failure")

    def test_a_newest_run_that_was_cancelled_waits(self):
        self.assertEqual(watch.ci_bucket(ROLLUP_1672 + [run("lint", "CANCELLED", "50:00", "Lint")], human=set()),
                         "pending")

    def test_status_contexts_keep_their_own_keys(self):
        ctx = [{"__typename": "StatusContext", "context": "ci/a", "state": "SUCCESS"},
               {"__typename": "StatusContext", "context": "ci/b", "state": "FAILURE"}]
        self.assertEqual(watch.ci_bucket(ctx, human=set()), "failure")


class NoJudgeLoop(tf.Base):
    def announce(self):
        out = self.script("housekeep.py", "scan")
        import subprocess
        r = subprocess.run([sys.executable, "-c", "import sys, json; sys.path.insert(0, sys.argv[1]); import sweep, "
                            "housekeep; print(json.dumps(sweep.announce(housekeep.scan())))", tf.SCRIPTS],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out, json.loads(r.stdout)

    def test_finished_hands_and_judges_raise_no_event(self):
        self.env["FOUNDRY_DOCKER"] = "false"
        self.env["FOUNDRY_REPO_ROOTS"] = ""
        self.env["FOUNDRY_WORKTREE_DIRS"] = ""
        self.foundry("unit", "new", "--id", "u1", "--title", "U1")
        self.record("agent.start", agent_id="ahand1", type="foundry:hand", session="S1", unit="u1")
        self.record("agent.stop", agent_id="ahand1")
        self.record("agent.start", agent_id="ajudge1", type="foundry:sweeper-judge", session="S1")
        self.record("agent.stop", agent_id="ajudge1")
        _, ev = self.announce()
        self.assertIsNone(ev)
        plans = {p["hid"]: p["verdict"] for p in self.snap()["housekeeping"]["plans"]}
        self.assertEqual((plans.get("subagent:ahand1"), plans.get("subagent:ajudge1")), ("safe", "safe"))
        self.script("housekeep.py", "execute")
        _, ev = self.announce()
        self.assertIsNone(ev)  # closed: never a candidate again
