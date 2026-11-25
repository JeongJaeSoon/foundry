"""0.1.22: the housekeeping judge never wakes itself; finished subagents with nothing attached get a scripted
verdict, so the housekeeping event fires only when something needs judgment."""

import unittest

import test_foundry as tf


class JudgeSelfLoop(tf.Base):
    def finished(self, aid, typ, unit=None):
        if unit:
            self.record("agent.spawn", session="S1", caller="foreman", caller_type="foundry:foreman",
                        target_type=typ, unit=unit, tool_use_id=f"tu-{aid}")
        self.record("agent.start", agent_id=aid, type=typ, session="S1")
        self.record("agent.stop", agent_id=aid)

    def housekeeping_lines(self):
        _, out = self.foundry("watch", "stream", "--kind", "sessions", "--once")
        rows = [x.split("] ", 1)[-1] for x in (out or "").splitlines() if x] if isinstance(out, str) else []
        return [x for x in rows if "housekeeping" in x]

    def plans(self):
        return {p["hid"]: p for p in self.snap()["housekeeping"]["plans"]}

    def test_a_finished_judge_is_no_candidate_and_wakes_no_judge(self):
        self.finished("j1", "foundry:sweeper-judge")
        hids = {c["hid"] for c in self.script("housekeep.py", "scan")}
        self.assertNotIn("subagent:j1", hids)
        self.assertEqual(self.housekeeping_lines(), [])
        self.assertEqual(self.plans()["subagent:j1"]["verdict"], "safe")
        _, run = self.foundry("sweep", "run")
        self.assertNotIn("j1", run["foreman_taskstop"])  # 0.1.45: already stopped, closed by the script
        self.finished("j2", "foundry:sweeper-judge")  # the next judge finishing: still silent
        self.assertEqual(self.housekeeping_lines(), [])

    def test_a_finished_subagent_with_nothing_attached_gets_a_scripted_verdict(self):
        self.finished("a1", "foundry:advisor")
        self.assertEqual(self.housekeeping_lines(), [])
        p = self.plans()["subagent:a1"]
        self.assertEqual(p["verdict"], "safe")
        self.assertTrue(p["reason"].startswith("scripted"))
        _, run = self.foundry("sweep", "run")
        self.assertEqual(run["foreman_taskstop"], [])  # 0.1.45: a finished subagent needs no TaskStop
        self.assertNotIn("subagent:a1", {c["hid"] for c in self.script("housekeep.py", "scan")})

    def test_a_subagent_bound_to_a_unit_is_scripted_too(self):  # 0.1.91: each finished hand woke the judge
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")
        self.finished("d1", "foundry:dispatcher", unit="alpha")
        self.assertEqual(self.snap()["agents"]["d1"]["unit"], "alpha")
        self.assertEqual(self.housekeeping_lines(), [])
        self.assertEqual(self.plans()["subagent:d1"]["verdict"], "safe")


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(JudgeSelfLoop(n) for n in sorted(vars(JudgeSelfLoop)) if n.startswith("test_"))
    return suite
