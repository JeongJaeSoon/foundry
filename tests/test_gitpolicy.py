"""Git policy guard, end to end: raw commands against a throwaway repo with a bare remote, fed through the hook."""

import json
import os
import shlex
import subprocess

from test_foundry import LEAD, Base

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


class GitPolicy(Base):
    def setUp(self):
        super().setUp()
        self.env.update(GIT_ENV)
        d = self.dir
        self.remote, self.main, self.wt = (os.path.join(d, x) for x in ("remote.git", "main", "wt"))
        self.git("init", "--bare", "-b", "main", self.remote)
        self.git("clone", self.remote, self.main)
        self.git("-C", self.main, "checkout", "-b", "main")
        self.git("-C", self.main, "commit", "--allow-empty", "-m", "init")
        self.git("-C", self.main, "push", "origin", "main")
        self.git("-C", self.main, "worktree", "add", "-b", "foundry/alpha", self.wt)
        repos = {"webapp": {"path": self.main, "remote": "origin", "default_branch": "main", "slug": "example-org/webapp",
                            "merge": "auto", "argo_app": "app1"},
                 "infra": {"path": os.path.join(d, "infra"), "default_branch": "master", "slug": "o/infra",
                           "merge": "owner", "owner_only": True}}
        with open(os.path.join(d, "repos.json"), "w") as f:
            json.dump(repos, f)
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "webapp")
        self.record("unit.worktree", id="alpha", repo="webapp", path=self.wt, branch="foundry/alpha")
        self.start_lead("alpha")

    def git(self, *args):
        r = subprocess.run(["git", *args], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def pre(self, cmd, cwd=None, **who):
        return self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, cwd=cwd or self.wt, **(who or LEAD))

    def reason(self, out):
        self.assertTrue(self.denied(out), out)
        return out["hookSpecificOutput"]["permissionDecisionReason"]

    def run_allowed(self, cmd, stdout=None):
        """PreToolUse allows it, it runs for real, PostToolUse sees its output."""
        out = self.pre(cmd)
        self.assertFalse(self.denied(out), out)
        if stdout is None:
            r = subprocess.run(shlex.split(cmd), env=self.env, capture_output=True, text=True, cwd=self.wt)
            self.assertEqual(r.returncode, 0, r.stderr)
            resp = {"stdout": r.stdout, "stderr": r.stderr}
        else:
            resp = {"stdout": stdout, "stderr": ""}
        self.hook("PostToolUse", tool_name="Bash", tool_input={"command": cmd}, tool_response=resp, cwd=self.wt, **LEAD)

    def test_raw_commands_pass_and_push_is_journaled(self):
        with open(os.path.join(self.wt, "a.txt"), "w") as f:
            f.write("a")
        self.run_allowed(f"git -C {self.wt} add a.txt")
        self.run_allowed(f"git -C {self.wt} commit -m 'add a'")
        self.run_allowed(f"git -C {self.wt} push -u origin foundry/alpha")
        sha = self.git("-C", self.wt, "rev-parse", "HEAD").stdout.strip()
        b = self.snap()["units"]["alpha"]["branches"]
        self.assertEqual([(x["repo"], x["branch"]) for x in b], [("example-org/webapp", "foundry/alpha")])
        self.assertTrue(sha.startswith(b[0]["sha"] or "-"), (sha, b))
        self.run_allowed(f"git -C {self.wt} push --force-with-lease origin foundry/alpha")

    def test_tag_push_is_journaled_and_traced(self):
        import factory
        self.run_allowed(f"git -C {self.wt} commit --allow-empty -m rel")
        self.run_allowed(f"git -C {self.wt} tag v1.2.3")
        self.run_allowed(f"git -C {self.wt} push origin v1.2.3")
        self.run_allowed(f"git -C {self.wt} tag v1.2.4")
        self.run_allowed(f"git -C {self.wt} push origin refs/tags/v1.2.4")
        sha = self.git("-C", self.wt, "rev-parse", "HEAD").stdout.strip()
        tags = [r for r in self.journal() if r["op"] == "tag.pushed"]
        self.assertEqual([(r["tag"], r["repo"], r["sha"]) for r in tags],
                         [("v1.2.3", "example-org/webapp", sha), ("v1.2.4", "example-org/webapp", sha)])
        self.assertEqual(self.snap()["units"]["alpha"]["branches"], [])  # a tag is not a branch push
        self.assertEqual(factory.trace(self.snap(), self.journal(), "alpha")["tags"], ["v1.2.3", "v1.2.4"])

    def test_chains_pipes_and_substitution_are_split(self):
        why = self.reason(self.pre(f"cd {self.wt} && git push origin foundry/alpha"))
        self.assertIn("one command per Bash call", why)
        self.assertIn(f"run it alone: `git -C {self.wt} push origin foundry/alpha`", why)
        why = self.reason(self.pre("git log | head -3"))
        self.assertIn("run it alone: `git log`", why)
        self.assertIn("no pipe filters", why)
        self.assertIn("run 2 separate Bash calls: `git status`, then `git diff`", self.reason(self.pre("git status; git diff")))
        why = self.reason(self.pre("git diff > /tmp/d.txt && git status 2>&1"))
        self.assertIn("`git diff`, then `git status`", why)
        self.assertIn("no redirects or temp files", why)
        self.assertNotIn("/tmp/d.txt", why.split("separate Bash calls")[1])
        self.assertIn("substitution", self.reason(self.pre('git commit -m "$(cat msg)"')))
        # A lone redirect, no chain: still one command's shape rule.
        why = self.reason(self.pre(f"git -C {self.wt} diff > /tmp/d.txt"))
        self.assertIn(f"run it alone: `git -C {self.wt} diff` (no redirects or temp files", why)
        self.assertIn("run it alone: `gh pr view 5`", self.reason(self.pre("gh pr view 5 2>&1")))
        self.assertFalse(self.denied(self.pre(f'git -C {self.wt} commit -m "a > b"')))  # quoted text is no redirect
        self.assertIsNone(self.pre("ls | head -3"))
        denials = self.snap()["denials"]
        self.assertEqual({d["source"] for d in denials}, {"hook"})
        self.assertEqual(len(denials), 7)

    def test_compliant_split_is_not_a_nearmiss(self):
        self.reason(self.pre(f"git -C {self.wt} status && git -C {self.wt} diff"))
        self.assertFalse(self.denied(self.pre(f"git -C {self.wt} status")))
        self.reason(self.pre(f"cd {self.wt} && git log | head -3"))
        self.assertFalse(self.denied(self.pre(f"git -C {self.wt} log -3")))
        self.assertEqual(self.snap().get("nearmiss") or [], [])

    def test_policy_deny_then_another_form_is_a_nearmiss(self):
        self.reason(self.pre(f"git -C {self.wt} push --force origin foundry/alpha"))
        self.pre("git push --force-with-lease origin foundry/alpha")
        kinds = [n["kind"] for n in self.snap().get("nearmiss") or []]
        self.assertEqual(kinds, ["deny-then-similar"])

    def test_default_branch_pushes_are_denied(self):
        for ref in ("main", "HEAD:main", "refs/heads/foundry/alpha:refs/heads/main", "foundry/alpha:main"):
            why = self.reason(self.pre(f"git -C {self.wt} push origin {ref}"))
            self.assertIn("default branch main", why)
            self.assertIn(f"git -C {self.wt} push origin foundry/alpha", why)
        self.git("-C", self.main, "status")
        self.assertIn("default branch", self.reason(self.pre(f"git -C {self.main} push")))

    def test_force_needs_lease_and_the_unit_branch(self):
        why = self.reason(self.pre(f"git -C {self.wt} push --force origin foundry/alpha"))
        self.assertIn(f"git -C {self.wt} push --force-with-lease origin foundry/alpha", why)
        self.assertIn("--force-with-lease", self.reason(self.pre(f"git -C {self.wt} push origin +foundry/alpha")))
        self.assertIn("unit's branches (foundry/alpha", self.reason(
            self.pre(f"git -C {self.wt} push --force-with-lease origin other")))
        self.assertIsNone(self.pre(f"git -C {self.wt} push --force-with-lease origin foundry/alpha"))

    def test_force_push_to_a_stacked_prs_head(self):
        # PROJ-784: the first PR merged, the second (stacked) PR's head is another branch of the same unit.
        self.record("pr.opened", unit="alpha", repo="example-org/webapp", number=1637, url="u1", branch="foundry/alpha")
        self.record("pr.state", unit="alpha", repo="example-org/webapp", number=1637, state="MERGED", branch="foundry/alpha")
        self.run_allowed("gh pr create --base foundry/alpha --head feat/proj-784-b-perms --title b --body b",
                         stdout="https://github.com/example-org/webapp/pull/1641\n")
        pr = [p for p in self.snap()["units"]["alpha"]["prs"] if p["number"] == 1641][0]
        self.assertEqual(pr["branch"], "feat/proj-784-b-perms")
        self.assertIsNone(self.pre(f"git -C {self.wt} push --force-with-lease origin feat/proj-784-b-perms"))
        why = self.reason(self.pre(f"git -C {self.wt} push --force-with-lease origin someone-elses"))
        self.assertIn("feat/proj-784-b-perms", why)
        self.assertIn("default branch", self.reason(self.pre(f"git -C {self.wt} push --force-with-lease origin main")))
        # The watcher learns the head from gh (headRefName); a closed PR's head is no longer the unit's.
        self.record("pr.state", unit="alpha", repo="example-org/webapp", number=1641, state="CLOSED",
                    branch="feat/proj-784-b-perms")
        self.reason(self.pre(f"git -C {self.wt} push --force-with-lease origin feat/proj-784-b-perms"))
        self.record("pr.state", unit="alpha", repo="example-org/webapp", number=1650, state="OPEN", branch="feat/proj-784-c")
        out = self.pre(f"git -C {self.wt} push --force-with-lease origin feat/proj-784-c")
        self.assertNotIn("permissionDecision", (out or {}).get("hookSpecificOutput", {}))

    def test_commit_outside_the_worktree(self):
        why = self.reason(self.pre(f"git -C {self.main} commit -m x"))
        self.assertIn(f"git -C {self.wt} commit -m x", why)
        self.assertTrue(self.denied(self.pre("git commit -m x", cwd=self.main)))
        self.assertFalse(self.denied(self.pre("git commit -m x", cwd=self.wt)))

    def gh_pr(self, ref, **pr):
        path = os.path.join(self.dir, "gh.json")
        data = json.load(open(path)) if os.path.exists(path) else {}
        data[ref] = {"headRefOid": "abc", "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN",
                     "latestReviews": [], "files": []} | pr
        with open(path, "w") as f:
            json.dump(data, f)

    def test_merge_rules(self):
        self.gh_pr("example-org/webapp#12")
        base = "gh pr merge 12 -R example-org/webapp --squash"
        self.assertIn("--match-head-commit <sha>", self.reason(self.pre(base)))
        self.assertIn("--admin", self.reason(self.pre(base + " --admin --match-head-commit abc")))
        self.assertIn("owner's", self.reason(self.pre("gh pr merge 3 -R o/infra --match-head-commit abc")))
        self.assertIsNone(self.pre(base + " --match-head-commit abc"))
        snap = self.snap()
        snap["locks"] = {"argo:app1": {"unit": "beta"}}
        self.record("snapshot", state=snap)
        self.assertIn("locked by unit beta", self.reason(self.pre(base + " --match-head-commit abc")))
        snap["locks"] = {"argo:app1": {"unit": "alpha"}}
        self.record("snapshot", state=snap)
        self.assertIsNone(self.pre(base + " --match-head-commit abc"))

    def test_night_pushes_wait_unless_excepted(self):
        self.env["FOUNDRY_NOW"] = "2026-11-18T23:00:00+09:00"
        why = self.reason(self.pre(f"git -C {self.wt} push origin foundry/alpha"))
        self.assertIn("08:00", why)
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"push_exceptions_at_night": ["webapp"]}, f)
        self.assertIsNone(self.pre(f"git -C {self.wt} push origin foundry/alpha"))

    def test_plan_mode_blocks_outward_acts(self):
        self.record("unit.mode", id="alpha", mode="plan")
        for cmd in (f"git -C {self.wt} push origin foundry/alpha", "gh pr create -R example-org/webapp --title x --body y",
                    "gh pr merge 1 -R example-org/webapp --match-head-commit abc"):
            self.assertIn("plan mode", self.reason(self.pre(cmd)))
        out = self.hook("PreToolUse", tool_name="mcp__gateway-slack__slack_send_message",
                        tool_input={"channel_id": "C1", "message": "hi"}, **LEAD)
        self.assertIn("plan mode", self.reason(out))
        self.assertIsNone(self.pre(f"git -C {self.wt} commit -m x"))

    def test_outside_sessions_are_untouched(self):
        self.assertIsNone(self.pre("cd /x && git push origin main", session_id="OTHER"))

    def test_merge_gate_reads_github(self):
        """Approved + CLEAN + pinned head merges; anything else is refused with the reason (0.1.16)."""
        cmd = "gh pr merge 12 -R example-org/webapp --squash --match-head-commit abc"
        self.assertIn("could not read", self.reason(self.pre(cmd)))  # fails closed
        self.gh_pr("example-org/webapp#12", reviewDecision="REVIEW_REQUIRED")
        self.assertIn("not approved", self.reason(self.pre(cmd)))
        self.gh_pr("example-org/webapp#12", mergeStateStatus="BLOCKED")
        self.assertIn("not CLEAN", self.reason(self.pre(cmd)))
        self.assertNotIn("permissionDecision", (self.pre(cmd.replace("--squash", "--squash --auto")) or {}).get("hookSpecificOutput", {}))  # --auto waits for CI on GitHub
        self.gh_pr("example-org/webapp#12", headRefOid="def456")
        self.assertIn("not abc", self.reason(self.pre(cmd)))
        self.gh_pr("example-org/webapp#12", reviewDecision="", latestReviews=[{"state": "APPROVED"}])
        self.assertIsNone(self.pre(cmd))
        self.assertIn("name the PR", self.reason(self.pre("gh pr merge -R example-org/webapp --match-head-commit abc")))

    def test_merge_default_is_self_except_prod(self):
        """No `merge` key: self for ordinary repos, owner for prod IaC names and infra-ops production paths."""
        repos = json.load(open(os.path.join(self.dir, "repos.json")))
        repos["webapp"].pop("merge")
        repos["infra-ops"] = {"path": os.path.join(self.dir, "co"), "slug": "o/infra-ops"}
        repos["gcp-iac"] = {"path": os.path.join(self.dir, "iac"), "slug": "o/gcp-iac"}
        with open(os.path.join(self.dir, "repos.json"), "w") as f:
            json.dump(repos, f)
        for ref in ("example-org/webapp#12", "o/infra-ops#7", "o/infra-ops#8", "o/gcp-iac#3"):
            self.gh_pr(ref)
        self.gh_pr("o/infra-ops#8", files=[{"path": "apps/x/overlays/production/kustomization.yaml"}])
        self.assertIsNone(self.pre("gh pr merge 12 -R example-org/webapp --match-head-commit abc"))
        self.assertIsNone(self.pre("gh pr merge 7 -R o/infra-ops --match-head-commit abc"))
        self.assertIn("production paths", self.reason(self.pre("gh pr merge 8 -R o/infra-ops --match-head-commit abc")))
        self.assertIn("owner's", self.reason(self.pre("gh pr merge 3 -R o/gcp-iac --match-head-commit abc")))
        # The owner's explicit order, relayed with a quote, opens exactly that PR.
        def order(role, pr, quote, ok=(0,)):
            self.env["FOUNDRY_ROLE"] = role
            try:
                return self.foundry("unit", "order", "alpha", "--pr", pr, "--quote", quote, ok=ok)
            finally:
                self.env.pop("FOUNDRY_ROLE")
        order("foreman", "o/infra-ops#8", " ", ok=(2,))
        order("foreman", "o/infra-ops#8", "8번 prod 머지해")
        self.assertIsNone(self.pre("gh pr merge 8 -R o/infra-ops --match-head-commit abc"))
        order("foreman", "o/gcp-iac#3", "머지해")
        self.assertIsNone(self.pre("gh pr merge 3 -R o/gcp-iac --match-head-commit abc"))
        order("line-lead", "o/gcp-iac#4", "x", ok=(2,))

    def test_pr_open_and_merge_are_journaled(self):
        self.gh_pr("example-org/webapp#12")
        self.run_allowed("gh pr create -R example-org/webapp --head foundry/alpha --title x --body y",
                         stdout="https://github.com/example-org/webapp/pull/12\n")
        self.run_allowed("gh pr merge 12 -R example-org/webapp --squash --match-head-commit abc", stdout="")
        u = self.snap()["units"]["alpha"]
        self.assertEqual([(p["repo"], p["number"], p["state"]) for p in u["prs"]], [("example-org/webapp", 12, "MERGED")])
        self.assertEqual(u["prs"][0]["url"], "https://github.com/example-org/webapp/pull/12")
        self.assertIn("example-org/webapp#12", u["keys"]["pr"])
