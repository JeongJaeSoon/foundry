"""0.1.90: a lead launched without the MCP-mapped `--agents` (before 0.1.81) is resumed as a copy with the launch
flags, which carry it, instead of in place (an in-place resume re-applies the original flags; passing flags starts a
copy, measured 2.1.329). A lead launched with it still resumes in place."""

import unittest

import test_launcher as tl


class ResumeMapped(tl.Launcher):
    def started(self, old=False):
        self.claude({"bg_id": "a1", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-alpha", "status": "busy"}]})
        self.foundry("unit", "launch", "alpha")
        if old:  # what a pre-0.1.81 launch recorded: no `agents`
            self.record("session.launch", name="lead-alpha", unit="alpha", role="line-lead", bg_id="a1",
                        cwd=self.snap()["sessions"]["lead-alpha"]["cwd"], model="opus", resume=True,
                        session_id="S-alpha")
        self.foundry("unit", "stop", "alpha")
        # an in-place resume comes back under the old id (a1); a copy gets a new one (a2)
        self.claude({"bg_id": "a2" if old else "a1",
                     "agents": [{"id": "a1", "name": "lead-alpha", "sessionId": "S-alpha", "state": "done"}]})

    def test_an_old_lead_resumes_as_a_copy_with_the_mapped_agent(self):
        self.started(old=True)
        _, out = self.foundry("unit", "resume", "alpha")
        self.assertNotIn("in_place", out)
        argv = self.bg_calls()[-1]["argv"]
        self.assertEqual(argv[:3], ["--bg", "--resume", "S-alpha"])
        self.assertIn("--agents", argv)
        self.assertTrue(self.snap()["sessions"]["lead-alpha"]["agents"])

    def test_a_lead_launched_with_it_resumes_in_place(self):
        self.started()
        self.assertTrue(self.snap()["sessions"]["lead-alpha"]["agents"])
        _, out = self.foundry("unit", "resume", "alpha")
        self.assertTrue(out["in_place"])
        self.assertEqual(self.bg_calls()[-1]["argv"][:3], ["--bg", "--resume", "S-alpha"])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ResumeMapped(n) for n in sorted(vars(ResumeMapped)) if n.startswith("test_"))
