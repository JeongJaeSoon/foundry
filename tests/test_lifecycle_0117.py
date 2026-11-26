"""0.1.17: lifecycle gaps closed in the plugin (batch release, dev deploy stage, Argo app lock, cards)."""

import json
import os
import subprocess
import sys
import unittest

import test_foundry as tf
from test_foundry import ORCH, SCRIPTS
import test_gitpolicy as tg


class Base(tf.Base):
    def run_f(self, *args, env=None):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args],
                           env={**self.env, **(env or {})}, capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def at(self, when, op, **fields):
        os.environ["FOUNDRY_NOW"] = when
        try:
            self.record(op, **fields)
        finally:
            os.environ.pop("FOUNDRY_NOW")


class BatchRelease(Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "example-org/webapp")
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta", "--repo", "example-org/notes-repo")
        self.at("2026-11-18T09:00:00+09:00", "pr.state", unit="alpha", repo="example-org/webapp", number=12, state="MERGED")
        self.at("2026-11-18T09:00:00+09:00", "pr.state", unit="beta", repo="example-org/notes-repo", number=3, state="MERGED")

    def check(self, now):
        self.env["FOUNDRY_NOW"] = now
        return self.foundry("release", "check")[1]

    def test_queued_after_the_quiet_period_once_and_never_at_night(self):
        self.assertEqual(self.check("2026-11-18T09:30:00+09:00")["queued"], [])  # newest merge only 30 min old
        self.assertEqual(self.check("2026-11-18T23:30:00+09:00")["queued"], [])  # night window
        out = self.check("2026-11-18T10:30:00+09:00")
        self.assertEqual(out["queued"], ["release-webapp-20261118-1030"])  # notes-repo is no batch repo
        u = self.snap()["units"]["release-webapp-20261118-1030"]
        self.assertEqual((u["status"], u["kind"], u["repo"]), ("queue", "project", "example-org/webapp"))
        self.assertEqual(u["release"]["prs"], ["example-org/webapp#12"])
        self.assertEqual(u["release"]["dev_app"], "webapp")
        for s in ("deploy-team-a", "D1-D3", "--env dev", "foundry unit order"):
            self.assertIn(s, u["handoff"]["goal"])
        self.assertEqual(self.check("2026-11-18T11:30:00+09:00")["queued"], [])  # one open release per repo
        # The queued release unit heads `foundry next`, so auto_launch starts it with no owner order.
        _, nxt = self.foundry("next")
        self.assertIn("release-webapp-20261118-1030", json.dumps(nxt))

    def test_done_needs_the_release_tag_and_the_dev_deploy(self):
        self.check("2026-11-18T10:30:00+09:00")
        uid = "release-webapp-20261118-1030"
        lead = {"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": uid}
        self.assertEqual(self.run_f("unit", "verify", uid, "--evidence", "x", env={"FOUNDRY_ROLE": "foreman"})[0], 0)
        rc, _, err = self.run_f("unit", "set", uid, "--status", "done", env=lead)
        self.assertEqual(rc, 2, err)
        self.assertIn("no release tag contains example-org/webapp#12", err)
        self.record("pr.released", unit="alpha", repo="example-org/webapp", number=12, tag="v1.2.0")
        rc, _, err = self.run_f("unit", "set", uid, "--status", "done", env=lead)
        self.assertEqual(rc, 2, err)
        self.assertIn(f"foundry unit deployed {uid} --app webapp --env dev", err)
        rc, _, err = self.run_f("unit", "deployed", uid, "--app", "webapp", "--env", "dev", "--tag", "v1.2.0",
                                "--evidence", "sync rev, pod tag, D1-D3", env=lead)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.snap()["units"][uid]["release"]["dev_deployed"], "v1.2.0")
        rc, _, err = self.run_f("unit", "set", uid, "--status", "done", env=lead)
        self.assertEqual(rc, 0, err)
        # Released PRs are covered: no second release unit for them.
        self.assertEqual(self.check("2026-11-18T12:30:00+09:00")["queued"], [])

    def test_the_github_stream_queues_it(self):
        self.env["FOUNDRY_NOW"] = "2026-11-18T10:30:00+09:00"
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "watch", "stream", "--kind", "github",
                            "--once"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("release release-webapp-20261118-1030", r.stdout)
        self.assertIn("release-webapp-20261118-1030", self.snap()["units"])


class ArgoAppLock(tg.GitPolicy):
    def setUp(self):
        super().setUp()
        repos = json.load(open(os.path.join(self.dir, "repos.json")))
        repos["infra-ops"] = {"path": os.path.join(self.dir, "co"), "slug": "o/infra-ops"}
        with open(os.path.join(self.dir, "repos.json"), "w") as f:
            json.dump(repos, f)
        self.foundry("unit", "order", "alpha", "--pr", "o/infra-ops#8", "--quote", "8번 prod 머지해")
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta")
        self.gh_pr("o/infra-ops#8", files=[
            {"path": "services/team-a/apps/01-platform-inhouse/environments/production/values_app_image.yaml"}])
        self.gh_pr("o/infra-ops#9", files=[{"path": "services/development-team-a/apps/50-webapp/environments/x.yaml"}])

    def test_prod_merge_takes_the_app_lock_and_blocks_the_next(self):
        cmd = "gh pr merge 8 -R o/infra-ops --squash --match-head-commit abc"
        self.run_allowed(cmd, stdout="merged")
        locks = self.snap()["locks"]
        self.assertEqual(locks["argo:production-team-a-apps"]["unit"], "alpha")
        # Another unit's prod merge into the same app waits for the release.
        self.foundry("unit", "order", "beta", "--pr", "o/infra-ops#8", "--quote", "x")
        self.start_lead("beta", sid="SB")
        why = self.reason(self.pre(cmd, **{"agent_type": "foundry:line-lead", "session_id": "SB"}))
        self.assertIn("locked by unit alpha", why)
        self.assertIn("foundry lock release argo:production-team-a-apps --unit alpha", why)

    def test_dev_paths_take_no_lock(self):
        self.run_allowed("gh pr merge 9 -R o/infra-ops --squash --match-head-commit abc", stdout="merged")
        self.assertEqual(self.snap()["locks"], {})

    def test_a_closed_holder_frees_the_app(self):
        snap = self.snap()
        snap["locks"] = {"argo:production-team-a-apps": {"unit": "beta"}}
        snap["units"]["beta"]["status"] = "done"
        self.record("snapshot", state=snap)
        self.assertIsNone(self.pre("gh pr merge 8 -R o/infra-ops --squash --match-head-commit abc"))


class OwnerQuestionNeedsCard(Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")

    def stop(self, text, **who):
        return self.hook("Stop", stop_hook_active=False, last_assistant_message=text, **(who or ORCH))

    def test_foreman_question_without_card_is_blocked_once(self):
        out = self.stop("webapp#12 는 리뷰 대기입니다.\n지금 prod 에 배포할까요?")
        self.assertEqual(out["decision"], "block")
        self.assertIn("foundry decide open", out["reason"])
        self.assertIsNone(self.hook("Stop", stop_hook_active=True, last_assistant_message="배포할까요?", **ORCH))

    def test_statements_code_and_carded_questions_pass(self):
        self.assertIsNone(self.stop("webapp#12 merged; release queued."))
        self.assertIsNone(self.stop("```\nwhat?\n```\n> quoted?\nDone."))
        self.foundry("decision", "ask", "--key", "alpha:prod", "--unit", "alpha", "--question", "Prod now?", ok=(10,))
        self.assertIsNone(self.stop("Prod 배포할까요? (card alpha:prod)"))

    def test_lead_report_with_question_is_flagged_but_kept(self):
        rc, out, err = self.run_f("unit", "report", "alpha", "--text", "merge 할까요?",
                                  env={"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "alpha"})
        self.assertEqual(rc, 0, err)
        self.assertIn("no decision card is open for alpha", json.loads(out)["flag"])
        self.assertTrue(any(r["op"] == "unit.report" for r in self.journal()))


def load_tests(loader, tests, pattern):
    """Only this file's tests: ArgoAppLock reuses GitPolicy's fixture, not its tests."""
    suite = unittest.TestSuite()
    for cls in (BatchRelease, OwnerQuestionNeedsCard):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    suite.addTests(ArgoAppLock(n) for n in sorted(vars(ArgoAppLock)) if n.startswith("test_"))
    return suite


if __name__ == "__main__":
    unittest.main()
