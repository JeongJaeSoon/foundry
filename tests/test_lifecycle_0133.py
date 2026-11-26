"""0.1.33: gated merges in registered repos never reach the owner; a background lead's owner approval is queued at
once and its answer resumes the lead; cards are Korean with no shell homework; reviewer load counts what is really
asked; one ticket may span repos; accents follow registration; stale --bg copies are pruned; gateway env names."""

import glob
import json
import os
import re
import sys
import unittest

import test_foundry as tf
from test_foundry import ROOT, SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import launcher  # noqa: E402
import review  # noqa: E402
import views  # noqa: E402
import watchers  # noqa: E402

LEAD = {"agent_type": "foundry:line-lead", "session_id": "SL"}
FAKE_CLAUDE = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_claude.py')}"


class StateDir:
    """Run in-process code against this test's state dir."""

    def __enter__(self):
        self.old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.case.dir
        return self

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("FOUNDRY_STATE_DIR")
        else:
            os.environ["FOUNDRY_STATE_DIR"] = self.old

    def __init__(self, case):
        self.case = case


class LeadBase(Base):
    def setUp(self):
        super().setUp()
        self.wt = os.path.join(self.dir, "wt-alpha")
        os.makedirs(self.wt)
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump({"approval_wait_seconds": 1}, f)
        self.env["FOUNDRY_NOTIFY_CMD"] = "true"
        with open(os.path.join(self.dir, "repos.json"), "w") as f:
            json.dump({"app": {"path": os.path.join(self.dir, "app"), "default_branch": "main",
                               "remote": "origin", "slug": "example-org/app"}}, f)
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")
        self.record("unit.worktree", id="alpha", repo="app", path=self.wt, branch="b")
        self.start_lead("alpha")

    def perm(self, cmd):
        out = self.hook("PermissionRequest", tool_name="Bash", tool_input={"command": cmd}, cwd=self.wt, **LEAD)
        return out["hookSpecificOutput"]["decision"]


class GatedMerge(LeadBase):
    def test_registered_repo_merge_with_pinned_head_is_tier0(self):
        d = self.perm("gh pr merge 1632 -R example-org/app --squash --match-head-commit 0123abcd4567")
        self.assertEqual(d, {"behavior": "allow"})
        self.assertEqual(self.snap()["approvals"], {})

    def test_unregistered_org_merge_is_still_the_owners(self):
        d = self.perm("gh pr merge 7 -R other-org/app --squash --match-head-commit 0123abcd4567")
        self.assertEqual(d["behavior"], "deny")
        self.assertIn("queued as", d["message"])
        (a,) = self.snap()["approvals"].values()
        self.assertEqual(a["tier"], 3)

    def test_admin_or_unpinned_merge_is_not_allowed_by_the_rule(self):
        for cmd in ("gh pr merge 1 -R example-org/app --squash --admin --match-head-commit 0123abcd",
                    "gh pr merge 1 -R example-org/app --squash --match-head-commit 0123abcd; kubectl apply -f x"):
            self.assertNotEqual(self.perm(cmd), {"behavior": "allow"}, cmd)

    def test_prod_kubectl_is_still_the_owners(self):
        d = self.perm("kubectl apply -f x.yaml --context prod")
        self.assertEqual(d["behavior"], "deny")
        self.assertEqual(next(iter(self.snap()["approvals"].values()))["tier"], 3)


class QueuedApproval(LeadBase):
    """In a bg lead session the hook never blocks: queued at once, the unit waits, the answer resumes the lead."""

    def test_queued_at_once_then_answer_wakes_the_parked_lead(self):
        self.foundry("unit", "set", "alpha", "--status", "in_progress", "--by", "owner")
        d = self.perm("kubectl apply -f x.yaml --context prod")
        self.assertEqual(d["behavior"], "deny")
        rid = re.search(r"queued as (ap-[\w-]+)", d["message"]).group(1)
        self.assertNotIn("run it by hand", d["message"].replace("do not ask anyone to run it by hand", ""))
        u = self.snap()["units"]["alpha"]
        self.assertEqual([w["wid"] for w in u["waits"]], [f"approval:{rid}"])
        self.assertEqual(u["status"], "waiting_owner")
        # parked meanwhile (as the sessions stream does)
        self.record("session.state", name="lead-alpha", state="stopped", note=launcher.PARK_NOTE + "waits approval")
        with StateDir(self):
            import foundrylib as cl
            self.assertNotIn("owner approval answered", [w for _, w in launcher.unparkable(cl.load())])
        self.foundry("approve", "allow", rid, "--by", "owner")
        u = self.snap()["units"]["alpha"]
        self.assertEqual(u["waits"], [])
        self.assertTrue(any(i["iid"] == f"apr-{rid}" for i in u["inbox"]))
        with StateDir(self):
            import foundrylib as cl
            self.assertEqual(launcher.unparkable(cl.load()), [("alpha", "owner approval answered")])
        # no new request id: the same command now runs on the recorded answer, once
        self.assertEqual(self.perm("kubectl apply -f x.yaml --context prod"), {"behavior": "allow"})
        self.assertEqual(list(self.snap()["approvals"]), [rid])

    def test_an_idle_live_lead_is_stopped_and_resumed(self):
        d = self.perm("kubectl apply -f y.yaml --context prod")
        rid = re.search(r"queued as (ap-[\w-]+)", d["message"]).group(1)
        self.record("session.state", name="lead-alpha", state="idle")
        self.foundry("approve", "deny", rid, "--by", "owner")
        with StateDir(self):
            import foundrylib as cl
            self.assertEqual(launcher.unparkable(cl.load()), [("alpha", launcher.IDLE_WAKE)])


class KoreanCards(LeadBase):
    def ask(self, q, ok=(0, 10)):
        env = dict(self.env)
        self.env.update({"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "alpha"})
        try:
            return self.foundry("decision", "ask", "--key", "alpha:x", "--unit", "alpha", "--question", q, ok=ok)
        finally:
            self.env = env

    def test_a_question_with_no_hangul_is_refused_in_a_session(self):
        self.ask("Merge now? Options: go / wait", ok=(2,))
        self.assertEqual(self.snap()["decisions"], {})

    def test_a_shell_command_for_the_owner_is_refused(self):
        self.ask("지금 머지할까요? Please run: gh pr merge 1 -R example-org/app --squash", ok=(2,))
        self.ask("머지 여부: `foundry unit new --ticket PROJ-1` 실행 부탁", ok=(2,))

    def test_a_korean_question_with_refs_passes(self):
        self.ask("example-org/app#12 (sha 0123abc) 지금 머지할까요? 선택지: 진행 / 대기 · 추천: 진행")
        self.assertIn("alpha:x", self.snap()["decisions"])

    def test_the_approve_slash_command_is_the_one_owner_act_named(self):
        self.ask("승인 필요: 보드의 [allow] 또는 `/foundry approve allow ap-1` 로 답해 주세요")

    def test_headline_has_no_unit_prefix(self):
        hl = views.headline({"unit": "alpha", "question": "머지할까요?"}, {"units": {"alpha": {"status": "in_progress"}}})
        self.assertTrue(hl.startswith("머지할까요?"), hl)


HOMEWORK = re.compile(r"(?i)please run:?\s*`?gh\b|hand the owner this line|ask the owner to run `?foundry unit "
                      r"(new|attach|route)")


class NoOwnerHomework(unittest.TestCase):
    def test_agents_skills_and_scripts_give_the_owner_no_shell_homework(self):
        hits = []
        for f in sorted(glob.glob(os.path.join(ROOT, "agents", "*.md")) + glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md"))
                        + glob.glob(os.path.join(ROOT, "scripts", "*.py"))):
            for n, line in enumerate(open(f, encoding="utf-8").read().splitlines(), 1):
                if HOMEWORK.search(line) and "HOMEWORK" not in line:
                    hits.append(f"{os.path.relpath(f, ROOT)}:{n}")
        self.assertEqual(hits, [])

    def test_the_pattern_catches_the_bad_shapes(self):
        for bad in ("Please run: gh pr merge 1", "hand the owner this line: gh pr merge",
                    "ask the owner to run `foundry unit new --ticket X`"):
            self.assertTrue(HOMEWORK.search(bad), bad)


class ReviewerLoad(Base):
    def test_owner_requests_beside_a_team_count_unless_they_open_the_review(self):
        ev = [{"event": "review_requested", "created_at": "t0", "actor": {"login": "me"}, "requested_team": {"slug": "t"}},
              {"event": "review_requested", "created_at": "t0", "actor": {"login": "me"}, "requested_reviewer": {"login": "co"}},
              {"event": "review_requested", "created_at": "t1", "actor": {"login": "me"}, "requested_team": {"slug": "t"}},
              {"event": "review_requested", "created_at": "t1", "actor": {"login": "me"}, "requested_reviewer": {"login": "hand"}},
              {"event": "review_requested", "created_at": "t2", "actor": {"login": "x[bot]", "type": "Bot"},
               "requested_reviewer": {"login": "botpick"}}]
        self.assertEqual(review.auto_requested(ev, owner="me"), ["botpick", "co"])
        self.assertEqual(review.auto_requested(ev), ["botpick", "co", "hand"])  # owner unknown: fail closed

    def test_approved_prs_still_load_the_others_and_an_approver_is_done(self):
        prs = {"example-org/app#1": {"repo": "example-org/app", "number": 1, "state": "OPEN", "review": "APPROVED",
                                     "reviewers": ["dave"], "approvers": ["carol"], "auto_reviewers": []},
               "example-org/app#2": {"repo": "example-org/app", "number": 2, "state": "OPEN",
                                     "reviewers": ["carol", "dave"], "approvers": ["dave"], "auto_reviewers": []},
               "example-org/app#3": {"repo": "example-org/app", "number": 3, "state": "OPEN",
                                     "reviewers": ["carol"], "auto_reviewers": []},
               "example-org/app#4": {"repo": "example-org/app", "number": 4, "state": "MERGED",
                                     "reviewers": ["carol"], "auto_reviewers": []}}
        with StateDir(self):
            rows = {r["login"]: r["mine"] for r in review.load_rows({"units": {}}, prs, p={"review": {}})}
        self.assertEqual(rows, {"carol": 2, "dave": 1})


class MultiRepoTicket(Base):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.dir, "repos.json"), "w") as f:
            json.dump({"a": {"path": "/x/a", "slug": "example-org/a"}, "b": {"path": "/x/b", "slug": "example-org/b"}}, f)

    def test_same_ticket_in_another_repo_is_a_linked_child(self):
        self.foundry("unit", "new", "--id", "t-a", "--title", "Fix", "--repo", "a", "--ticket", "PROJ-9")
        _, out = self.foundry("unit", "new", "--id", "t-b", "--title", "Fix", "--repo", "b", "--ticket", "PROJ-9")
        self.assertEqual(out["parent"], "t-a")
        s = self.snap()["units"]
        self.assertEqual((s["t-b"]["parent"], s["t-a"]["children"]), ("t-a", ["t-b"]))
        # same ticket, same repo: still refused
        self.foundry("unit", "new", "--id", "t-c", "--title", "Fix", "--repo", "a", "--ticket", "PROJ-9", ok=(1,))

    def test_parent_done_waits_for_the_child(self):
        self.foundry("unit", "new", "--id", "t-a", "--title", "Fix", "--repo", "a", "--ticket", "PROJ-9")
        self.foundry("unit", "new", "--id", "t-b", "--title", "Fix", "--repo", "b", "--ticket", "PROJ-9")
        self.record("verify.pass", unit="t-a", evidence="ran it")
        r = __import__("subprocess").run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "unit", "set", "t-a",
                                          "--status", "done"], env={**self.env, "FOUNDRY_ROLE": "line-lead",
                                                                    "FOUNDRY_UNIT": "t-a"}, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("t-b", r.stderr + r.stdout)

    def test_a_merged_child_pr_wakes_the_parent(self):
        self.foundry("unit", "new", "--id", "t-a", "--title", "Fix", "--repo", "a", "--ticket", "PROJ-9")
        self.foundry("unit", "new", "--id", "t-b", "--title", "Fix", "--repo", "b", "--ticket", "PROJ-9")
        self.foundry("wait", "add", "t-b", "--kind", "pr-merge", "--ref", "example-org/b#3")
        self.gh({"example-org/b#3": {"state": "MERGED"}})
        self.script("watch.py", "poll")
        ev = [e for e in self.snap()["events"] if e["unit"] == "t-a"]
        self.assertEqual([e["kind"] for e in ev], ["child"])
        self.assertEqual(self.snap()["units"]["t-b"]["waits"], [])


class Accents(unittest.TestCase):
    def test_registration_order_gives_distinct_colours_and_overrides_win(self):
        rs = {f"r{i}": {"slug": f"example-org/r{i}"} for i in range(8)}
        m = views.accent_map(rs)
        self.assertEqual(len({json.dumps(m[f"r{i}"]) for i in range(8)}), 8)
        self.assertEqual(m["r0"], m["example-org/r0"])
        rs["r7"]["accent"] = 0
        m2 = views.accent_map(rs)
        self.assertEqual(m2["r7"], views.accent_map({"x": {}})["x"])
        self.assertNotEqual(m2["r0"], m2["r7"])
        self.assertEqual(views.repo_accent("unregistered/zz", m), views.repo_accent("unregistered/zz", {}))


class PruneCopies(Base):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.dir, "claude.jsonl")
        self.env.update({"FOUNDRY_CLAUDE": FAKE_CLAUDE, "FAKE_CLAUDE_LOG": self.log,
                         "FAKE_CLAUDE_DATA": os.path.join(self.dir, "claude.json")})
        self.wt = os.path.join(self.dir, "wt")
        os.makedirs(self.wt)

    def run_prune(self, agents):
        with open(self.env["FAKE_CLAUDE_DATA"], "w") as f:
            json.dump({"agents": agents}, f)
        old = {k: os.environ.get(k) for k in ("FOUNDRY_CLAUDE", "FAKE_CLAUDE_LOG", "FAKE_CLAUDE_DATA")}
        os.environ.update({k: self.env[k] for k in old})
        try:
            with StateDir(self):
                return launcher.prune_copies("lead-alpha", self.wt, {"new"})
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_only_finished_foundry_copies_go_and_one_live_entry_stays(self):
        agents = [{"id": "new", "name": "lead-alpha", "cwd": self.wt, "state": "busy"},
                  {"id": "old1", "name": "lead-alpha", "cwd": self.wt, "state": "stopped"},
                  {"id": "old2", "name": "lead-alpha", "cwd": self.wt, "state": "done"},
                  {"id": "work", "name": "lead-alpha", "cwd": self.wt, "state": "busy"},
                  {"id": "ask", "name": "lead-alpha", "cwd": self.wt, "state": "waiting"},
                  {"id": "other", "name": "mine", "cwd": self.wt, "state": "done"},
                  {"id": "elsewhere", "name": "lead-alpha", "cwd": "/somewhere", "state": "done"},
                  {"id": "own-wt", "name": "lead-alpha", "cwd": self.wt, "state": "done", "worktree": "/x"}]
        self.assertEqual(self.run_prune(agents), ["old1", "old2"])

    def test_nothing_goes_until_the_new_copy_runs(self):
        agents = [{"id": "new", "name": "lead-alpha", "cwd": self.wt, "state": "stopped"},
                  {"id": "old1", "name": "lead-alpha", "cwd": self.wt, "state": "stopped"}]
        self.assertEqual(self.run_prune(agents), [])


class GatewayEnv(Base):
    def test_names_only_on_disk_and_values_reach_the_spawn(self):
        with StateDir(self):
            names = launcher.record_gateway_env({"ANTHROPIC_BASE_URL": "https://gw.example", "HOME": "/h",
                                                 "ANTHROPIC_AUTH_TOKEN": "s3cret"})
            self.assertEqual(names, ["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"])
            with open(os.path.join(self.dir, launcher.GATEWAY_FILE)) as f:
                self.assertNotIn("s3cret", f.read())
            old = {k: os.environ.get(k) for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")}
            os.environ["ANTHROPIC_BASE_URL"] = "https://gw.example"
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            try:
                vals, missing = launcher.gateway_env()
                cfg = {"lead_ask_rules": []}
                env = json.loads(launcher.settings_json(cfg, "line-lead", "alpha"))["env"]
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://gw.example")
        self.assertIn("ANTHROPIC_AUTH_TOKEN", missing)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", vals)

    def test_a_lead_dead_on_auth_is_named_and_gets_one_owner_step(self):
        entries = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "API Error: 401 "
                    "{\"type\":\"error\",\"error\":{\"type\":\"authentication_error\"}}"}]}}]
        self.assertTrue(watchers.auth_failure(entries))
        self.assertIsNone(watchers.auth_failure([{"type": "assistant", "message": {"content": [{"type": "text",
                                                                                                "text": "done"}]}}]))
        self.assertEqual(watchers.STALL_TEXT["auth"], "인증/게이트웨이 오류")
        self.assertIn("ANTHROPIC_BASE_URL", watchers.auth_step(["ANTHROPIC_BASE_URL"]))


if __name__ == "__main__":
    unittest.main()
