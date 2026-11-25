"""Tests for the sweep domains: docker cache prune, lead sessions, PC pressure. Fakes only: nothing real is pruned or stopped."""

import json
import os
import sys

import test_foundry as tf

FAKE_CLI = os.path.join(tf.ROOT, "tests", "fake_cli.py")
PRUNES = [["docker", "image", "prune", "-f", "--filter", "dangling=true", "--filter", "until=168h"],
          ["docker", "builder", "prune", "-f", "--filter", "until=168h"]]
HEALTHY = {"disk_free_gb": {"/": 500}, "mem_free_pct": 50, "load_per_cpu": 0.1}


class SweepBase(tf.Base):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.dir, "cli.log")
        self.env.update({"FAKE_CLI_LOG": self.log,
                         "FOUNDRY_DOCKER": f"{sys.executable} {FAKE_CLI} docker",
                         "FOUNDRY_CLAUDE": f"{sys.executable} {FAKE_CLI} claude"})

    def calls(self, tool=None):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            rows = [json.loads(line) for line in f]
        return [r for r in rows if tool is None or r[0] == tool]

    def rec(self, op, **fields):
        """A journal record stamped with the test clock (FOUNDRY_NOW), as the launcher would write it."""
        old = os.environ.get("FOUNDRY_NOW")
        os.environ["FOUNDRY_NOW"] = self.env["FOUNDRY_NOW"]
        try:
            self.record(op, **fields)
        finally:
            if old is None:
                os.environ.pop("FOUNDRY_NOW")
            else:
                os.environ["FOUNDRY_NOW"] = old

    def unit(self, uid, status):
        self.rec("unit.new", id=uid, title=uid)
        self.rec("unit.status", id=uid, status=status)

    def lead(self, uid, state="running"):
        self.rec("session.launch", name="lead-" + uid, unit=uid, role="line-lead", bg_id="bg-" + uid)
        self.rec("session.state", name="lead-" + uid, state=state)

    def journal_ops(self):
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            return [json.loads(line) for line in f if line.strip()]


class DockerCache(SweepBase):
    def test_prune_runs_exactly_the_two_argv_lists(self):
        self.foundry("sweep", "run")
        prunes = [c for c in self.calls("docker") if "prune" in c]
        self.assertEqual(prunes, PRUNES)
        self.assertFalse(any(a in c for c in self.calls("docker") for a in ("-a", "--all", "volume", "network", "container")))

    def test_unreachable_docker_prunes_nothing(self):
        self.env["FAKE_DOCKER_DOWN"] = "1"
        _, out = self.foundry("sweep", "run")
        self.assertEqual([c for c in self.calls("docker") if "prune" in c], [])
        self.assertIn("docker info failed", out["docker_cache"][0]["result"])

    def test_dry_run_prunes_nothing(self):
        self.foundry("sweep", "run", "--dry-run")
        self.assertEqual([c for c in self.calls("docker") if "prune" in c], [])


class ComposeProjects(SweepBase):
    def test_only_a_done_units_stack_comes_down(self):
        self.env["FAKE_COMPOSE"] = json.dumps([{"Name": n} for n in
                                               ("foundry-alpha", "foundry-beta-db", "foundry-zeta", "other")])
        self.unit("alpha", "done")
        self.unit("beta", "in_progress")
        _, out = self.foundry("sweep", "scan")
        safe = {c["target"]: c["safe"] for c in out if c["cls"] == "docker"}
        self.assertEqual(safe, {"foundry-alpha": True, "foundry-beta-db": False, "foundry-zeta": False, "other": False})
        for name in safe:  # a careless judge says safe for all
            self.script("housekeep.py", "plan", "--hid", "docker:" + name, "--verdict", "safe", "--reason", "judge")
        self.script("housekeep.py", "execute")
        downs = [c for c in self.calls("docker") if c[1:2] == ["compose"] and "down" in c]
        self.assertEqual(downs, [["docker", "compose", "-p", "foundry-alpha", "down", "--remove-orphans"]])


class Sessions(SweepBase):
    def test_failed_stop_is_not_recorded_and_is_retried(self):
        self.unit("alpha", "done")
        self.lead("alpha")
        self.script("housekeep.py", "plan", "--hid", "session:lead-alpha", "--verdict", "safe", "--reason", "judge")
        self.env["FAKE_CLAUDE_STOP_FAIL"] = "1"
        self.assertTrue(self.script("housekeep.py", "execute")["results"][0]["result"].startswith("failed"))
        self.assertNotEqual(self.snap()["sessions"]["lead-alpha"]["state"], "stopped")
        del self.env["FAKE_CLAUDE_STOP_FAIL"]
        self.assertEqual(self.script("housekeep.py", "execute")["results"][0]["result"], "stopped")

    def cands(self):
        _, out = self.foundry("sweep", "scan")
        return {c["target"]: c for c in out if c["cls"] == "session"}

    def test_done_unit_session_is_stopped_and_a_live_unit_never_is(self):
        self.unit("alpha", "done")
        self.unit("beta", "in_progress")
        self.lead("alpha")
        self.lead("beta", "idle")
        c = self.cands()
        self.assertTrue(c["lead-alpha"]["safe"])
        self.assertNotIn("lead-beta", c)
        for name in ("lead-alpha", "lead-beta"):  # a careless judge says safe for both
            self.script("housekeep.py", "plan", "--hid", "session:" + name, "--verdict", "safe", "--reason", "judge")
        res = {r["hid"]: r["result"] for r in self.script("housekeep.py", "execute")["results"]}
        self.assertEqual(res["session:lead-alpha"], "stopped")
        self.assertTrue(res["session:lead-beta"].startswith("skipped"))
        self.assertEqual([c for c in self.calls("claude") if c[1] == "stop"], [["claude", "stop", "bg-alpha"]])
        s = self.snap()["sessions"]
        self.assertEqual((s["lead-alpha"]["state"], s["lead-beta"]["state"]), ("stopped", "idle"))
        self.script("housekeep.py", "execute")  # already acted on: not stopped twice
        self.assertEqual(len([c for c in self.calls("claude") if c[1] == "stop"]), 1)

    def test_new_verdict_in_the_same_second_as_the_last_cleanup_is_acted_on(self):
        # Review 13: order by journal position; the clock is frozen, so every record shares one second.
        self.unit("alpha", "done")
        self.lead("alpha")
        self.script("housekeep.py", "plan", "--hid", "session:lead-alpha", "--verdict", "safe", "--reason", "judge")
        self.script("housekeep.py", "execute")
        self.lead("alpha")  # relaunched, done again
        self.script("housekeep.py", "plan", "--hid", "session:lead-alpha", "--verdict", "safe", "--reason", "judge")
        self.assertEqual(self.script("housekeep.py", "execute")["results"][0]["result"], "stopped")
        self.assertEqual(len([c for c in self.calls("claude") if c[1] == "stop"]), 2)

    def test_unit_reopened_before_execute_is_not_stopped(self):
        self.unit("alpha", "done")
        self.lead("alpha")
        self.script("housekeep.py", "plan", "--hid", "session:lead-alpha", "--verdict", "safe", "--reason", "judge")
        self.rec("unit.status", id="alpha", status="in_progress")
        self.assertTrue(self.script("housekeep.py", "execute")["results"][0]["result"].startswith("skipped"))
        self.assertEqual([c for c in self.calls("claude") if c[1] == "stop"], [])

    def test_stuck_orphaned_and_lost_sessions_are_proposals(self):
        self.unit("gamma", "in_progress")
        self.lead("gamma")
        self.rec("session.launch", name="lead-ghost", unit="ghost", role="line-lead", bg_id="bg-ghost")
        self.unit("delta", "in_progress")
        self.lead("delta", "lost")
        self.env["FAKE_CLAUDE_AGENTS"] = json.dumps([{"name": "lead-zzz", "sessionId": "Z1", "status": "running"},
                                                     {"name": "other", "sessionId": "O1"}])
        self.env["FOUNDRY_NOW"] = "2026-11-18T17:00:00+09:00"  # 7h without a journal update
        c = self.cands()
        self.assertEqual(sorted(c), ["lead-delta", "lead-gamma", "lead-ghost", "lead-zzz"])
        self.assertTrue(all(not x["safe"] and x["suggest"] == "propose" for x in c.values()))
        self.assertIn("stuck", c["lead-gamma"]["reason"])
        self.assertIn("foundry unit resume delta", c["lead-delta"]["reason"])
        self.assertIn("claude stop Z1", c["lead-zzz"]["reason"])
        for name in c:
            self.script("housekeep.py", "plan", "--hid", "session:" + name, "--verdict", "safe", "--reason", "judge")
        self.script("housekeep.py", "execute")
        self.assertEqual([x for x in self.calls("claude") if x[1] == "stop"], [])

    def test_not_stuck_within_the_window(self):
        self.unit("gamma", "in_progress")
        self.lead("gamma")
        self.assertEqual(self.cands(), {})


class RepoBase(SweepBase, tf.Housekeeping):
    """Housekeeping's repo (merged-clean / merged-dirty / open-clean worktrees) with the fake docker and claude."""
    test_only_the_safe_class_is_removed_even_if_the_judge_is_wrong = None  # runs in test_foundry already


class Pressure(RepoBase):
    def setUp(self):
        super().setUp()
        self.unit("delta", "in_progress")
        self.lead("delta", "lost")  # a proposal, announced after the cleanup

    def pressure(self, **m):
        self.env["FOUNDRY_PRESSURE_FAKE"] = json.dumps(HEALTHY | m)

    def test_high_pressure_records_once_holds_launches_cleans_first_then_recovers(self):
        self.pressure(disk_free_gb={"/": 5, "/Users/x": 300})
        out = self.script("watch.py", "poll")
        snap = self.snap()
        self.assertEqual(snap["pressure"]["level"], "danger")  # the launcher holds every launch on this
        self.assertEqual(snap["pressure"]["reasons"], ["disk: 5 GB free"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "merged-clean")))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "merged-dirty")))
        self.assertEqual([c for c in self.calls("docker") if "prune" in c], PRUNES)
        ops = [r["op"] for r in self.journal_ops() if r["op"] in ("pressure", "hk.done", "event")]
        self.assertEqual(ops, ["pressure", "hk.done", "event"])
        self.assertEqual([e["kind"] for e in out["events"]], ["housekeeping"])

        out = self.script("watch.py", "poll")  # still danger: no second pressure record, no repeat announcement
        self.assertEqual([r["op"] for r in self.journal_ops()].count("pressure"), 1)
        self.assertEqual(out["events"], [])

        self.pressure()
        self.script("watch.py", "poll")
        self.assertEqual(self.snap()["pressure"]["level"], "ok")
        self.assertEqual([r["op"] for r in self.journal_ops()].count("pressure"), 2)

    def test_each_threshold_and_prefs(self):
        # Review 16: the same band and prefs thresholds as the foreman's pressure stream.
        self.pressure(mem_free_pct=5, load_per_cpu=3.0)
        _, out = self.foundry("sweep", "pressure")
        self.assertEqual((out["level"], out["reasons"]), ("danger", ["load: 3.0 per CPU", "memory: 5% reclaimable"]))
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"pressure": {"mem_reclaimable_pct_warn": 4, "mem_reclaimable_pct_danger": 3,
                                    "load_per_cpu_warn": 4.0, "load_per_cpu_danger": 5.0}}, f)
        _, out = self.foundry("sweep", "pressure")
        self.assertEqual(out["level"], "ok")

    def test_ok_pressure_cleans_nothing_without_a_verdict(self):
        self.script("watch.py", "poll")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "merged-clean")))
        self.assertEqual([c for c in self.calls("docker") if "prune" in c], [])
        self.assertEqual(self.snap()["pressure"]["level"], "ok")


class WorktreeDirs(RepoBase):
    def setUp(self):
        super().setUp()
        self.env["FOUNDRY_REPO_ROOTS"] = ""

    def test_worktrees_under_the_worktree_root_are_scanned_once(self):
        self.env["FOUNDRY_WORKTREE_DIRS"] = f"{self.dir}:{self.dir}"
        _, out = self.foundry("sweep", "scan")
        wts = {c["branch"]: c for c in out if c["cls"] == "worktree"}
        self.assertEqual(sorted(wts), ["merged-clean", "merged-dirty", "open-clean"])  # never the main checkout
        self.assertTrue(wts["merged-clean"]["safe"])
        self.assertFalse(wts["open-clean"]["safe"])


HK = tf.HK


class JudgeSurface(tf.Base):
    JUDGE = {"agent_type": "foundry:sweeper-judge", "agent_id": "J1"}

    def test_judge_whitelist_still_holds(self):
        bash = lambda cmd: self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **self.JUDGE)  # noqa: E731
        self.assertIsNone(bash(f"python3 {HK} scan"))
        self.assertIsNone(bash(f'python3 {HK} plan --hid "session:lead-a" --verdict safe --reason "unit done"'))
        # Review 11: the documented form is the bin wrapper; `${CLAUDE_PLUGIN_ROOT}` text would be refused.
        self.assertIsNone(bash("foundry-housekeep scan"))
        self.assertIsNone(bash('foundry-housekeep plan --hid "session:lead-a" --verdict skip --reason "open PR"'))
        self.assertTrue(self.denied(bash("foundry-housekeep execute")))
        for bad in ("python3 /x/foundry/scripts/sweep.py scan", "python3 /x/foundry/scripts/foundry.py sweep run",
                    f"python3 {HK} execute", "docker image prune -f",
                    "docker builder prune -f", "claude stop bg-a"):
            self.assertTrue(self.denied(bash(bad)), bad)
