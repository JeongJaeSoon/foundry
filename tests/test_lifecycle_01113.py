"""0.1.113: a stranded Argo lock and a lazily computed merge state no longer stall prod merges.

The merge hook names the lock it took; releasing the wrong name lists what the unit does hold; the owner has
`/foundry lock list|release`; a prod deploy record releases the unit's argo locks, and a lock held over 60 minutes by
a parked or waiting unit is flagged. mergeStateStatus UNKNOWN is re-read before the refusal, a `mergeable` wait clears
once GitHub settles, and a ci wait on finished checks is refused."""

import json
import os
import subprocess
import sys
import unittest

import test_gitpolicy as tg
from test_foundry import LEAD, SCRIPTS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MERGE = "gh pr merge 12 -R example-org/webapp --squash --match-head-commit abc"
PROD_LOCK = "argo:production-cluster-apps"

# Answers `gh pr view` with mergeStateStatus UNKNOWN for the first N calls, then the fake data.
SEQ_GH = '''import json, os, subprocess, sys
p = os.environ["SEQ_GH_COUNT"]
n = int(open(p).read()) if os.path.exists(p) else 0
open(p, "w").write(str(n + 1))
if sys.argv[1:3] == ["pr", "view"] and n < int(os.environ["SEQ_GH_UNKNOWN"]):
    print(json.dumps({"headRefOid": "abc", "reviewDecision": "APPROVED", "mergeStateStatus": "UNKNOWN",
                      "latestReviews": [], "files": []}))
    sys.exit(0)
sys.exit(subprocess.run([sys.executable, os.environ["SEQ_GH_FAKE"], *sys.argv[1:]]).returncode)
'''


class Gate(tg.GitPolicy):
    def cli(self, *args):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args], env=self.env,
                           capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def hold(self, name=PROD_LOCK, unit="alpha", ts=None):
        self.record("lock.take", name=name, unit=unit, **({"ts": ts} if ts else {}))

    def test_the_merge_hook_names_the_lock_it_took(self):
        self.gh_pr("example-org/webapp#12")
        out = self.hook("PostToolUse", tool_name="Bash", tool_input={"command": MERGE},
                        tool_response={"stdout": "", "stderr": ""}, cwd=self.wt, **LEAD)
        self.assertEqual(self.snap()["locks"]["argo:app1"]["unit"], "alpha")
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("took lock argo:app1 for alpha", text)
        self.assertIn("foundry lock release argo:app1 --unit alpha", text)

    def test_releasing_the_wrong_name_lists_the_held_lock(self):
        self.hold()
        rc, _, err = self.cli("lock", "release", "argo:app", "--unit", "alpha")
        self.assertEqual(rc, 2)
        self.assertNotIn("held by nobody", err)
        self.assertIn(f"alpha holds {PROD_LOCK}", err)
        self.assertIn(f"`foundry lock release {PROD_LOCK} --unit alpha`", err)
        self.assertEqual(self.cli("lock", "release", PROD_LOCK, "--unit", "alpha")[0], 0)
        self.assertNotIn(PROD_LOCK, self.snap()["locks"])

    def test_lock_is_an_owner_slash_verb_and_lists(self):
        with open(os.path.join(ROOT, "hooks", "slash.ts")) as f:
            self.assertIn("'lock'", f.read().split("SLASH_VERBS", 1)[1].split("\n", 1)[0])
        self.hold()
        rows = json.loads(self.cli("lock", "list")[1])
        self.assertEqual(rows[0]["name"], PROD_LOCK)
        self.assertEqual(rows[0]["unit"], "alpha")

    def test_a_prod_deploy_record_releases_the_units_argo_locks(self):
        self.hold()
        self.hold("argo:other", unit="beta-not-a-unit")
        rc, out, err = self.cli("unit", "deployed", "alpha", "--app", "app", "--env", "prod", "--tag", "v1.1.41",
                                "--evidence", "synced", "--by", "owner")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["released_locks"], [PROD_LOCK])
        self.assertNotIn(PROD_LOCK, self.snap()["locks"])
        rel = [r for r in self.journal() if r.get("op") == "lock.release"]
        self.assertEqual(rel[-1]["by"], "deploy-done")

    def test_a_dev_deploy_keeps_the_lock(self):
        self.hold()
        self.cli("unit", "deployed", "alpha", "--app", "app", "--env", "dev", "--tag", "v1", "--evidence", "x",
                 "--by", "owner")
        self.assertIn(PROD_LOCK, self.snap()["locks"])

    def test_a_lock_held_over_an_hour_by_a_parked_unit_is_flagged(self):
        self.hold(ts="2026-11-18T08:30:00+09:00")
        self.record("unit.set", id="alpha", status="parked", by="lead")
        rows = json.loads(self.cli("lock", "list")[1])
        self.assertEqual([r["stale"] for r in rows if "stale" in r],
                         [{"name": PROD_LOCK, "unit": "alpha", "minutes": 90}])
        sys.path.insert(0, SCRIPTS)
        import graph
        snap = self.snap()
        snap["units"]["alpha"]["status"] = "in_progress"
        self.assertEqual(graph.stale_locks(snap, graph.cl.parse_ts("2026-11-18T10:00:00+09:00")), [])


    def seq_gh(self, unknown):
        self.gh_pr("example-org/webapp#12")
        self.env.update(SEQ_GH_UNKNOWN=str(unknown), SEQ_GH_COUNT=os.path.join(self.dir, "gh-count"), SEQ_GH_FAKE=os.path.join(ROOT, "tests", "fake_gh.py"),
                        FOUNDRY_UNKNOWN_BACKOFF_SCALE="0.01")
        seq = os.path.join(self.dir, "seq_gh.py")
        with open(seq, "w") as f:
            f.write(SEQ_GH)
        self.env["FOUNDRY_GH"] = f"{sys.executable} {seq}"

    def test_unknown_is_re_read_before_refusing(self):
        self.seq_gh(2)
        self.assertIsNone(self.pre(MERGE))

    def test_unknown_that_never_settles_points_to_a_mergeable_wait(self):
        self.seq_gh(99)
        why = self.reason(self.pre(MERGE))
        self.assertIn("still UNKNOWN", why)
        self.assertIn("--kind mergeable --ref example-org/webapp#12", why)
        with open(self.env["SEQ_GH_COUNT"]) as f:
            self.assertEqual(int(f.read()), 6)  # the first read and five re-reads

    def test_a_mergeable_wait_clears_once_github_settles(self):
        self.gh_pr("example-org/webapp#12", state="OPEN", mergeStateStatus="UNKNOWN", statusCheckRollup=[])
        self.assertEqual(self.cli("wait", "add", "alpha", "--kind", "mergeable", "--ref", "example-org/webapp#12")[0], 0)
        self.script("watch.py", "poll")
        self.assertEqual([w["kind"] for w in self.snap()["units"]["alpha"]["waits"]], ["mergeable"])
        self.gh_pr("example-org/webapp#12", state="OPEN", mergeStateStatus="CLEAN", statusCheckRollup=[])
        self.script("watch.py", "poll")
        self.assertEqual(self.snap()["units"]["alpha"]["waits"], [])
        ev = [e for e in self.snap()["events"] if e.get("kind") == "mergeable"]
        self.assertIn("merge state is CLEAN", ev[-1]["summary"])

    def test_a_ci_wait_on_finished_checks_is_refused(self):
        self.gh_pr("example-org/webapp#12", state="OPEN", mergeStateStatus="UNKNOWN",
                   statusCheckRollup=[{"name": "test", "conclusion": "SUCCESS"}])
        rc, _, err = self.cli("wait", "add", "alpha", "--kind", "ci", "--ref", "example-org/webapp#12")
        self.assertEqual(rc, 2)
        self.assertIn("already finished (success)", err)
        self.assertIn("--kind mergeable", err)
        self.gh_pr("example-org/webapp#12", state="OPEN", statusCheckRollup=[{"name": "test", "status": "IN_PROGRESS"}])
        self.assertEqual(self.cli("wait", "add", "alpha", "--kind", "ci", "--ref", "example-org/webapp#12")[0], 0)


if __name__ == "__main__":
    unittest.main()
