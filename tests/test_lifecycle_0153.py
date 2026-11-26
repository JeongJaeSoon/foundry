"""0.1.53: deploy environments are normalized (dev/development -> dev, prod/production -> prod) for deploy refs,
including journal records written before; open release units show as 릴리스 진행 with their stage; a merged PR on
the PR tab names the release carrying it; the handover is refreshed through the day by a prefs schedule."""

import json
import os
import subprocess
import sys
import unittest

import test_graph


class EnvNames(test_graph.GraphBase):
    assert_ready = test_graph.Readiness.assert_ready

    def test_a_production_deploy_resolves_a_prod_ref_and_the_reverse(self):
        self.new("a")
        self.new("b")
        self.foundry("unit", "block", "a", "--on", "deploy:app@prod")
        self.foundry("unit", "block", "b", "--on", "deploy:api@development")
        self.record("deploy.done", app="app", env="production")  # an old record, written before 0.1.53
        self.record("deploy.done", app="api", env="dev")
        self.foundry("graph", "resolve")
        self.assert_ready("a")
        self.assert_ready("b")


class Releases(test_graph.GraphBase):
    def board(self):
        r = subprocess.run([sys.executable, os.path.join(self.scripts(), "board.py"), "json"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def scripts(self):
        import test_foundry
        return test_foundry.SCRIPTS

    def test_stages_from_tag_to_prod(self):
        self.new("release-app-1")
        self.record("release.unit", id="release-app-1", repo="example-org/app", prs=["example-org/app#5", "example-org/app#6"],
                    dev_app="app")
        for n in (5, 6):
            self.record("pr.state", unit="release-app-1", repo="example-org/app", number=n, state="MERGED", title=f"t{n}")
        [x] = self.board()["releases"]
        self.assertEqual((x["stage"], x["stage_ko"], x["released"]), ("tag", "태그 대기 (0/2)", 0))
        self.record("pr.released", unit="release-app-1", repo="example-org/app", number=5, tag="v1.2.0")
        self.record("pr.released", unit="release-app-1", repo="example-org/app", number=6, tag="v1.2.0")
        self.assertEqual(self.board()["releases"][0]["stage"], "dev")
        self.record("deploy.done", unit="release-app-1", app="app", env="development", tag="v1.2.0")
        x = self.board()["releases"][0]
        self.assertEqual((x["stage"], x["dev"], x["tag"]), ("prod", "v1.2.0", "v1.2.0"))
        self.record("deploy.done", unit="release-app-1", app="app", env="production", tag="v1.2.0")
        self.assertEqual(self.board()["releases"][0]["stage_ko"], "prod 배포 완료")
        self.record("unit.set", id="release-app-1", status="done", by="lead")
        self.assertEqual(self.board()["releases"], [])  # in progress only

    def test_the_pr_tab_names_the_release_carrying_a_merged_pr(self):
        self.new("u1")
        for n in (5, 8):
            self.record("pr.state", unit="u1", repo="example-org/app", number=n, state="MERGED", title=f"t{n}")
            self.record("pr.merged", unit="u1", repo="example-org/app", number=n)
        self.new("release-app-3")
        self.record("release.unit", id="release-app-3", repo="example-org/app", prs=["example-org/app#5"])
        rows = {r["ref"]: r for r in self.board()["pr_tab"] if r["state"] == "release-wait"}
        self.assertEqual(set(rows), {"example-org/app#5", "example-org/app#8"}, rows)
        self.assertEqual(rows["example-org/app#5"]["release"], "태그 대기 (0/1) · release-app-3")
        self.assertEqual(rows["example-org/app#5"]["action"], "릴리스: 태그 대기 (0/1) · release-app-3")
        self.assertEqual(rows["example-org/app#8"]["release"], "릴리스 unit 없음")

    def test_text_board_lists_releases(self):
        self.new("release-app-2")
        self.record("release.unit", id="release-app-2", repo="example-org/app", prs=["example-org/app#7"])
        r = subprocess.run([sys.executable, os.path.join(self.scripts(), "board.py"), "text"], env=self.env,
                           capture_output=True, text=True)
        self.assertIn("## 릴리스 진행 (1)", r.stdout)
        self.assertIn("release-app-2  태그 대기 (0/1)  PR 1", r.stdout)


class Schedules(unittest.TestCase):
    def test_the_handover_is_refreshed_through_the_working_day(self):
        import test_foundry
        sys.path.insert(0, test_foundry.SCRIPTS)
        import prefs
        s = {x["name"]: x for x in prefs.defaults()["schedules"]}
        self.assertEqual(s["handover-refresh"]["cron"], "0 10,12,14,16,18,20 * * 1-5")
        self.assertTrue(s["handover-refresh"]["run"].startswith("foundry report handover"))


if __name__ == "__main__":
    unittest.main()
