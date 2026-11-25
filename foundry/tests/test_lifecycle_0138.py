"""0.1.38: with the owner's prod go recorded, a release unit's lead approves and merges the deploy bot's tag-only
prod PR itself; human PRs, non-tag diffs and non-release units stay the owner's."""

import json
import os
import unittest

import test_gitpolicy as tg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD = "apps/x/environments/production/values_app_image.yaml"
HEAD = "42a2e557" + "0" * 32
PATCH = "@@ -2,4 +2,4 @@\n app:\n   image:\n-    tag: v1.1.36-app\n+    tag: v1.1.37-app"


class DeployApprove(tg.GitPolicy):
    APPROVE = 'gh pr review 8 -R o/infra-ops --approve --body "v1.1.37 prod deploy at 42a2e557 (owner order: prod 배포해)"'
    MERGE = "gh pr merge 8 -R o/infra-ops --squash --match-head-commit 42a2e557"

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.dir, "repos.json")) as f:
            repos = json.load(f)
        repos["infra-ops"] = {"path": os.path.join(self.dir, "co"), "slug": "o/infra-ops"}
        with open(os.path.join(self.dir, "repos.json"), "w") as f:
            json.dump(repos, f)
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"merge": {"deploy_pr_authors": ["deploy-bot"],
                                 "deploy_tag_files": ["apps/*/environments/production/values_*image.yaml"]}}, f)
        snap = self.snap()
        snap["units"]["alpha"]["release"] = {"prs": ["example-org/app#12"], "dev_deployed": "v1.1.37",
                                             "released": {"example-org/app#12": "v1.1.37"}}
        self.record("snapshot", state=snap)
        self.record("pr.reviewed", unit="alpha", pr="o/infra-ops#8", sha=HEAD)
        self.bot_pr()

    def bot_pr(self, author="deploy-bot", path=PROD, patch=PATCH, n=8):
        self.gh_pr(f"o/infra-ops#{n}", author={"login": author}, files=[{"path": path}], changedFiles=1, headRefOid=HEAD)
        self.gh_api(f"repos/o/infra-ops/pulls/{n}/files?per_page=100",
                    [{"filename": path, "status": "modified", "patch": patch}])

    def gh_api(self, path, value):
        p = os.path.join(self.dir, "gh.json")
        with open(p) as f:
            data = json.load(f)
        data[f"api {path}"] = value
        with open(p, "w") as f:
            json.dump(data, f)

    def go(self):
        self.foundry("decide", "open", "--id", "d-0902", "--unit", "alpha", "--key", "alpha:prod-deploy",
                     "--option", "go:prod 배포", "--option", "hold:보류", "--question", "prod?",
                     "--asked-by", "line-lead")
        self.env["FOUNDRY_ROLE"] = "foreman"
        try:
            self.foundry("decide", "answer", "d-0902", "go", "--by", "foreman", "--quote", "prod 배포해")
        finally:
            self.env.pop("FOUNDRY_ROLE")

    def test_without_the_go_it_is_a_wait(self):
        why = self.reason(self.pre(self.APPROVE))
        self.assertIn("not recorded yet", why)
        self.assertIn("never ask the owner", why)
        self.assertIn("production paths", self.reason(self.pre(self.MERGE)))

    def test_go_lets_the_lead_approve_and_merge_the_bot_pr(self):
        self.go()
        self.assertIsNone(self.pre(self.APPROVE))
        self.assertIn('--body "v1.1.37 prod deploy at 42a2e5570000 (owner order: prod 배포해)"',
                      self.reason(self.pre("gh pr review 8 -R o/infra-ops --approve")))
        self.assertIsNone(self.pre(self.MERGE))  # not opened by the unit, yet ordered: a verified bot deploy PR

    def test_unit_order_works_too(self):
        self.env["FOUNDRY_ROLE"] = "foreman"
        try:
            self.foundry("unit", "order", "alpha", "--by", "foreman", "--quote", "prod 배포해")
        finally:
            self.env.pop("FOUNDRY_ROLE")
        self.assertIsNone(self.pre(self.APPROVE))

    def test_human_pr_stays_the_owners(self):
        self.go()
        self.bot_pr(author="someone")
        self.assertIn("not a deploy bot", self.reason(self.pre(self.APPROVE)))
        self.assertIn("production paths", self.reason(self.pre(self.MERGE)))

    def test_non_tag_diff_or_other_tag_is_a_card(self):
        self.go()
        self.bot_pr(patch=PATCH + "\n+    replicas: 9")
        self.assertIn("non-tag line", self.reason(self.pre(self.APPROVE)))
        self.bot_pr(patch=PATCH.replace("v1.1.37-app", "v1.1.38-app"))
        self.assertIn("not alpha's released tag", self.reason(self.pre(self.APPROVE)))
        self.bot_pr(path="apps/x/environments/production/values.yaml")
        self.assertIn("not an image-tag file", self.reason(self.pre(self.APPROVE)))

    def test_non_release_unit_cannot_approve(self):
        self.go()
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta")
        self.start_lead("beta", sid="SB")
        why = self.reason(self.pre(self.APPROVE, **{"agent_type": "foundry:line-lead", "session_id": "SB"}))
        self.assertIn("[owner order]", why)  # 0.1.80: judged on the PR's facts; no order names #8

    def test_dev_paths_and_other_repos_are_not_gated(self):
        self.gh_pr("o/infra-ops#9", author={"login": "someone"}, files=[{"path": "apps/x/environments/dev/v.yaml"}])
        self.assertIsNone(self.pre('gh pr review 9 -R o/infra-ops --approve --body ok'))

    def test_api_approve_goes_through_the_gate(self):
        why = self.reason(self.pre("gh api repos/o/infra-ops/pulls/8/reviews -f event=APPROVE"))
        self.assertIn("gh pr review 8 -R o/infra-ops --approve", why)


class Text(unittest.TestCase):
    def test_no_slack_ask_or_auto_merge_choice(self):
        for rel in ("agents/foreman.md", "agents/line-lead.md"):
            with open(os.path.join(ROOT, rel)) as f:
                t = f.read()
            self.assertIn('Never offer the owner a "Slack review ask or auto-merge" choice', t, rel)
            self.assertIn("merge.deploy_pr_authors", t, rel)


if __name__ == "__main__":
    unittest.main()
