"""0.1.80: a prod deploy PR approve is judged on the PR's facts, not on how the unit was made: bot author, a diff of
one-line image-tag value swaps only, the approve pinned to the head the unit recorded as reviewed, and an owner order
naming the PR. A release the owner orders now is made with `foundry release new`, never a hand-made unit."""

import json
import os
import unittest

import test_gitpolicy as tg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = "o/infra-ops#245757"
HEAD = "42a2e557" + "1" * 32
APP = "apps/x/environments/production/values_app_image.yaml"
LLM = "apps/x/environments/production/values_llm_proxy_image.yaml"


def patch(key="tag", old="v1.1.39", new="v1.1.40", image="app"):
    return f"@@ -2,4 +2,4 @@\n {image}:\n   image:\n-    {key}: {old}-{image}\n+    tag: {new}-{image}"


class FactsApprove(tg.GitPolicy):
    APPROVE = ('gh pr review 245757 -R o/infra-ops --approve --body '
               '"v1.1.40-app prod deploy at 42a2e557 (owner order: v1.1.40 prod 배포 go)"')

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.dir, "repos.json")) as f:
            repos = json.load(f)
        repos["infra-ops"] = {"path": os.path.join(self.dir, "co"), "slug": "o/infra-ops"}
        with open(os.path.join(self.dir, "repos.json"), "w") as f:
            json.dump(repos, f)
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"merge": {"deploy_pr_authors": ["deploy-bot"],
                                 "deploy_tag_files": ["apps/*/environments/production/values_*image.yaml"]},
                       "release": {"batch_repos": [{"repo": "webapp", "dev_app": "app-dev"}]}}, f)
        self.bot_pr()

    def bot_pr(self, author="deploy-bot", files=None, head=HEAD, changed=None):
        files = files if files is not None else [
            {"filename": APP, "status": "modified", "patch": patch()},
            {"filename": LLM, "status": "modified", "patch": patch(image="llm-proxy")}]
        self.gh_pr(REF, author={"login": author}, files=[{"path": f["filename"]} for f in files],
                   changedFiles=len(files) if changed is None else changed, headRefOid=head)
        p = os.path.join(self.dir, "gh.json")
        with open(p) as f:
            data = json.load(f)
        data["api repos/o/infra-ops/pulls/245757/files?per_page=100"] = files
        with open(p, "w") as f:
            json.dump(data, f)

    def order(self, pr=REF):
        self.env["FOUNDRY_ROLE"] = "foreman"
        try:
            self.foundry("unit", "order", "alpha", *(["--pr", pr] if pr else []), "--by", "foreman",
                         "--quote", "v1.1.40 prod 배포 go", ok=(0, 2))
        finally:
            self.env.pop("FOUNDRY_ROLE")

    def reviewed(self, sha=HEAD):
        self.foundry("unit", "reviewed", "alpha", "--pr", REF, "--sha", sha)

    def why(self, cmd=None):
        return self.reason(self.pre(cmd or self.APPROVE))

    def test_the_245757_shape_passes_on_a_hand_made_unit(self):
        self.assertIsNone(self.snap()["units"]["alpha"].get("release"))
        self.order()
        self.reviewed()
        self.assertIsNone(self.pre(self.APPROVE))

    def test_no_order_naming_the_pr_is_refused(self):
        self.reviewed()
        self.assertIn("[owner order]", self.why())
        self.order(pr=None)  # a unit-wide order is a release unit's only, refused here
        self.assertIn("[owner order]", self.why())
        self.order(pr="o/infra-ops#1")
        self.assertIn("[owner order]", self.why())

    def test_other_author_is_refused(self):
        self.order(), self.reviewed()
        self.bot_pr(author="someone")
        self.assertIn("[author]", self.why())

    def test_an_extra_non_tag_line_or_key_change_or_unpaired_line_is_refused(self):
        self.order(), self.reviewed()
        self.bot_pr(files=[{"filename": APP, "status": "modified", "patch": patch() + "\n+    replicas: 9"}])
        self.assertIn("non-tag line", self.why())
        self.bot_pr(files=[{"filename": APP, "status": "modified", "patch": patch(key="image_tag")}])
        self.assertIn("turns key", self.why())
        self.bot_pr(files=[{"filename": APP, "status": "modified", "patch": patch() + "\n+    sidecar_tag: v9"}])
        self.assertIn("not a one-line value swap", self.why())
        self.bot_pr(files=[{"filename": APP, "status": "added", "patch": "@@ -0,0 +1 @@\n+tag: v1.1.40-app"}])
        self.assertIn("instead of modifying", self.why())
        self.bot_pr(files=[{"filename": "apps/x/environments/production/values.yaml", "status": "modified",
                            "patch": patch()}])
        self.assertIn("not an image-tag file", self.why())

    def test_a_partial_file_list_is_refused(self):
        self.order(), self.reviewed()
        self.bot_pr(changed=31)
        self.assertIn("GitHub listed 2", self.why())

    def test_head_must_be_the_reviewed_commit_and_named_in_the_body(self):
        self.order()
        self.assertIn("[head pin]", self.why())  # nothing recorded yet
        self.assertIn(f"--sha {HEAD}", self.why())
        self.reviewed("9" * 40)
        self.assertIn("not the reviewed 999999999999", self.why())
        self.reviewed()
        bare = 'gh pr review 245757 -R o/infra-ops --approve --body "v1.1.40-app prod deploy (owner order: go)"'
        self.assertIn("at 42a2e5571111", self.why(bare))
        self.bot_pr(head="7" * 40)
        self.assertIn("[head pin]", self.why())

    def test_reviewed_needs_a_full_sha(self):
        self.assertEqual(self.foundry("unit", "reviewed", "alpha", "--pr", REF, "--sha", "42a2e557", ok=(2,))[0], 2)

    def test_hand_made_release_unit_is_refused_and_release_new_makes_one(self):
        rc, out = self.foundry("unit", "new", "--title", "Release webapp: #1", "--repo", "webapp", ok=(0, 2))[:2]
        self.assertEqual(rc, 2)
        self.assertIn("foundry release new", out["error"])
        rc, out = self.foundry("release", "new", "webapp", "--pr", "example-org/webapp#1")[:2]
        uid = out["unit"]
        u = self.snap()["units"][uid]
        self.assertEqual(u["release"]["prs"], ["example-org/webapp#1"])
        self.assertEqual(u["release"]["dev_app"], "app-dev")
        rc, out = self.foundry("release", "new", "webapp", "--pr", "example-org/webapp#1", ok=(2,))[:2]
        self.assertEqual(rc, 2)  # already covered: never batched twice
        self.assertEqual(self.foundry("release", "new", "other", "--pr", "a/other#1", ok=(2,))[0], 2)


class Text(unittest.TestCase):
    def test_docs_name_the_commands(self):
        with open(os.path.join(ROOT, "agents/foreman.md")) as f:
            self.assertIn("foundry release new", f.read())
        with open(os.path.join(ROOT, "agents/line-lead.md")) as f:
            self.assertIn("foundry unit reviewed", f.read())


if __name__ == "__main__":
    unittest.main()
