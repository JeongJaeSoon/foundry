"""0.1.47: a release unit's release bump PR (bump bot, release/bump-v*, version lines only) is approved and merged by
the lead under the owner's standing rule; no reviewer pick, no pr-review wait and no Slack review ask can reach it."""

import json
import os
import sys
import unittest

import test_gitpolicy as tg
from test_foundry import SCRIPTS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = "example-org/app#1657"
APPROVE = 'gh pr review 1657 -R example-org/app --approve --body "1.1.38 release bump (owner standing rule)"'


def py_patch(old="1.1.37", new="1.1.38"):
    return f'@@ -1,6 +1,6 @@\n [project]\n name = "app"\n-version = "{old}"\n+version = "{new}"\n description = "x"'


LOCK = ('@@ -410,7 +410,7 @@ wheels = [\n ]\n \n [[package]]\n name = "app"\n-version = "1.1.37"\n+version = "1.1.38"\n'
        ' source = { editable = "." }\n dependencies = [\n     { name = "fastapi" },\n'
        '@@ -900,7 +900,7 @@\n [[package]]\n name = "app-core"\n-version = "1.1.37"\n+version = "1.1.38"\n'
        ' source = { virtual = "packages/core" }\n')
THIRD_PARTY_LOCK = ('@@ -10,7 +10,7 @@\n [[package]]\n name = "fastapi"\n-version = "0.110.0"\n+version = "1.1.38"\n'
                    ' source = { registry = "https://pypi.org/simple" }\n')


def files_1657(**override):
    paths = ["pyproject.toml", "packages/core/pyproject.toml", "packages/api/pyproject.toml",
             "packages/cli/pyproject.toml", "packages/sdk/pyproject.toml", "packages/web/pyproject.toml",
             "tools/pyproject.toml", "uv.lock"]
    return [{"filename": p, "patch": override.get(p, LOCK if p == "uv.lock" else py_patch())} for p in paths]


class ReleaseBump(tg.GitPolicy):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.dir, "repos.json")) as f:
            repos = json.load(f)
        repos["webapp"]["slug"] = "example-org/app"
        with open(os.path.join(self.dir, "repos.json"), "w") as f:
            json.dump(repos, f)
        snap = self.snap()
        snap["units"]["alpha"]["release"] = {"repo": "webapp", "prs": ["example-org/app#12"]}
        self.record("snapshot", state=snap)
        self.bump_pr()

    def bump_pr(self, author="release-bot", branch="release/bump-v1.1.38", files=None):
        self.gh_pr(REF, author={"login": author}, headRefName=branch, state="OPEN", isDraft=False,
                   reviewRequests=[], assignees=[], title="chore: bump version to 1.1.38",
                   url="https://github.com/example-org/app/pull/1657", files=[])
        p = os.path.join(self.dir, "gh.json")
        with open(p) as f:
            data = json.load(f)
        data["api repos/example-org/app/pulls/1657/files*"] = files if files is not None else files_1657()
        with open(p, "w") as f:
            json.dump(data, f)

    def outbox_ask(self, unit="alpha"):
        return self.foundry("outbox", "add", unit, "--channel", "C0123456789", "--kind", "review-ask",
                            "--text", "<@U1> review please https://github.com/example-org/app/pull/1657", ok=(0, 2))

    def test_the_1657_shape_is_approved_and_merged_by_the_lead(self):
        self.assertIsNone(self.pre(APPROVE))
        self.assertIn('--body "1.1.38 release bump (owner standing rule)"',
                      self.reason(self.pre("gh pr review 1657 -R example-org/app --approve")))
        self.assertIsNone(self.pre("gh pr merge 1657 -R example-org/app --squash --match-head-commit abc"))

    def test_a_non_version_pyproject_line_takes_the_review_path(self):
        bad = py_patch() + '\n-requires-python = ">=3.11"\n+requires-python = ">=3.12"'
        self.bump_pr(files=files_1657(**{"packages/api/pyproject.toml": bad}))
        why = self.reason(self.pre(APPROVE))
        self.assertIn("non-version line", why)
        self.assertIn("normal review path", why)
        self.assertEqual(self.outbox_ask()[0], 0)  # not covered, so the ask is allowed

    def test_a_version_other_than_the_branch_target_or_a_third_party_lock_line_fails(self):
        self.bump_pr(files=files_1657(**{"tools/pyproject.toml": py_patch(new="1.1.39")}))
        self.assertIn("not 1.1.38", self.reason(self.pre(APPROVE)))
        self.bump_pr(files=files_1657(**{"uv.lock": THIRD_PARTY_LOCK}))
        self.assertIn("workspace-own", self.reason(self.pre(APPROVE)))
        self.bump_pr(files=files_1657() + [{"filename": "src/app/main.py", "patch": py_patch()}])
        self.assertIn("not a version file", self.reason(self.pre(APPROVE)))

    def test_a_human_author_or_another_branch_is_not_the_class(self):
        for kw in ({"author": "someone"}, {"branch": "feature/bump-v1.1.38"}):
            self.bump_pr(**kw)
            self.assertIsNone(self.pre(APPROVE))  # a self-mode repo's ordinary approve, unchanged
            self.assertEqual(self.outbox_ask()[0], 0)

    def test_not_a_release_unit_is_not_the_class(self):
        snap = self.snap()
        snap["units"]["alpha"].pop("release")
        self.record("snapshot", state=snap)
        self.assertIsNone(self.pre("gh pr review 1657 -R example-org/app --approve"))
        self.assertEqual(self.outbox_ask()[0], 0)

    def test_no_review_ask_and_no_reviewer_pick_for_a_bump_pr(self):
        rc, out = self.outbox_ask()
        self.assertEqual(rc, 2)
        self.assertEqual(self.snap()["outbox"], {})
        rc, out = self.foundry("review", "assign", REF, "--unit", "alpha", ok=(2,))
        self.assertIn("release bump PR", out["error"])
        self.assertIn('--body "1.1.38 release bump (owner standing rule)"', out["error"])
        snap = self.snap()
        self.assertEqual(snap["outbox"], {})
        self.assertFalse(snap["units"]["alpha"].get("review_picks"))
        self.assertFalse([w for w in snap["units"]["alpha"].get("waits") or {} if "pr-review" in str(w)])

    def test_bump_version_and_prefs(self):
        sys.path.insert(0, SCRIPTS)
        import gitpolicy
        import prefs
        self.assertEqual(gitpolicy.bump_version("release/bump-v1.1.38", "release/bump-v*"), "1.1.38")
        self.assertIsNone(gitpolicy.bump_version("release/v1.1.38", "release/bump-v*"))
        mg = prefs.defaults()["merge"]  # the shipped defaults name no bot (0.1.66); the suite's overlay does
        self.assertEqual((mg["bump_pr_authors"], mg["bump_branch_glob"]), (["release-bot"], "release/bump-v*"))
        errs = prefs.validate({"merge": {"bump_branch_glob": "release/bump", "bump_files": "uv.lock"}})
        self.assertTrue(any("bump_branch_glob" in e for e in errs) and any("bump_files" in e for e in errs), errs)

    def test_lead_text_and_goal_name_the_rule(self):
        import release
        goal = release._goal("example-org/app", ["example-org/app#12"], {}, "alpha")
        self.assertIn("release bump (owner standing rule)", goal)
        with open(os.path.join(ROOT, "agents", "line-lead.md")) as f:
            self.assertIn("merge.bump_pr_authors", f.read())


for _name in dir(tg.GitPolicy):  # the base's own tests run in test_gitpolicy, with its slug
    if _name.startswith("test_"):
        setattr(ReleaseBump, _name, None)


if __name__ == "__main__":
    unittest.main()
