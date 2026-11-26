"""0.1.112: one batch release ships one tag per app at the same commit (core-rel-…, slack-mcp-rel-…). The release
unit records the other app tags with `foundry release tag`, each verified on GitHub against the commit of a tag the
unit already recorded, and the deploy PR approve accepts any recorded tag. A tag is never accepted for its suffix."""

import json
import os
import unittest

import test_gitpolicy as tg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD = "apps/x/environments/production/values_app_image.yaml"
HEAD = "42a2e557" + "0" * 32
SUFFIX = "rel-20261125175749"
SHA = "c0ffee11" + "2" * 32


def patch(new, old="slack-mcp-rel-20261120090000"):
    return f"@@ -2,4 +2,4 @@\n app:\n   image:\n-    tag: {old}\n+    tag: {new}"


class BatchTags(tg.GitPolicy):
    APPROVE = (f'gh pr review 247698 -R o/infra-ops --approve --body "slack-mcp-{SUFFIX} prod deploy at 42a2e557 '
               '(owner order: prod 배포해)"')

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
        snap["units"]["alpha"]["release"] = {"repo": "example-org/app", "prs": ["example-org/app#12"],
                                             "dev_deployed": f"core-{SUFFIX}",
                                             "released": {"example-org/app#12": f"core-{SUFFIX}"}}
        self.record("snapshot", state=snap)
        self.record("pr.reviewed", unit="alpha", pr="o/infra-ops#247698", sha=HEAD)
        for app in ("core", "slack-mcp", "flapps-mcp"):
            self.gh_api(f"repos/example-org/app/commits/{app}-{SUFFIX}", {"sha": SHA})
        self.gh_api(f"repos/example-org/app/commits/designdoc-mcp-{SUFFIX}", {"sha": "d" * 40})
        self.bot_pr(f"slack-mcp-{SUFFIX}")
        self.foundry("decide", "open", "--id", "d-0903", "--unit", "alpha", "--key", "alpha:prod-deploy",
                     "--option", "go:prod 배포", "--option", "hold:보류", "--question", "prod?", "--asked-by", "line-lead")
        self.env["FOUNDRY_ROLE"] = "foreman"
        try:
            self.foundry("decide", "answer", "d-0903", "go", "--by", "foreman", "--quote", "prod 배포해")
        finally:
            self.env.pop("FOUNDRY_ROLE")

    def gh_api(self, path, value):
        p = os.path.join(self.dir, "gh.json")
        data = {}
        if os.path.exists(p):
            with open(p) as f:
                data = json.load(f)
        data[f"api {path}"] = value
        with open(p, "w") as f:
            json.dump(data, f)

    def bot_pr(self, new):
        self.gh_pr("o/infra-ops#247698", author={"login": "deploy-bot"}, files=[{"path": PROD}], changedFiles=1,
                   headRefOid=HEAD)
        self.gh_api("repos/o/infra-ops/pulls/247698/files?per_page=100",
                    [{"filename": PROD, "status": "modified", "patch": patch(new)}])

    def tag(self, *tags, ok=(0,)):
        args = [x for t in tags for x in ("--tag", t)]
        return self.foundry("release", "tag", "alpha", *args, "--by", "owner", ok=ok)

    def test_an_unrecorded_same_suffix_tag_is_refused(self):
        self.assertIn("not alpha's released tag", self.reason(self.pre(self.APPROVE)))

    def test_every_recorded_batch_tag_is_accepted(self):
        self.tag(f"slack-mcp-{SUFFIX}", f"flapps-mcp-{SUFFIX}")
        self.assertEqual(self.snap()["units"]["alpha"]["release"]["tags"],
                         [f"flapps-mcp-{SUFFIX}", f"slack-mcp-{SUFFIX}"])
        self.assertIsNone(self.pre(self.APPROVE))
        self.bot_pr(f"flapps-mcp-{SUFFIX}-api")  # the `<tag>-<image>` form still holds
        self.assertIsNone(self.pre(self.APPROVE))
        self.bot_pr(f"bigquery-mcp-{SUFFIX}")  # same suffix, never recorded
        self.assertIn("not alpha's released tag", self.reason(self.pre(self.APPROVE)))

    def test_a_tag_at_another_commit_or_missing_is_refused_at_record_time(self):
        self.assertEqual(self.tag(f"designdoc-mcp-{SUFFIX}", ok=(2,))[0], 2)
        self.assertEqual(self.tag(f"bigquery-mcp-{SUFFIX}", ok=(2,))[0], 2)
        self.assertNotIn("tags", self.snap()["units"]["alpha"]["release"])

    def test_github_unreadable_fails_closed(self):
        p = os.path.join(self.dir, "gh.json")
        with open(p) as f:
            data = json.load(f)
        data.pop(f"api repos/example-org/app/commits/core-{SUFFIX}")
        with open(p, "w") as f:
            json.dump(data, f)
        self.assertEqual(self.tag(f"slack-mcp-{SUFFIX}", ok=(2,))[0], 2)


class Doc(unittest.TestCase):
    def test_the_lead_is_told_how_to_record_batch_tags(self):
        with open(os.path.join(ROOT, "agents", "line-lead.md")) as f:
            self.assertIn("foundry release tag <unit> --tag", f.read())


if __name__ == "__main__":
    unittest.main()
