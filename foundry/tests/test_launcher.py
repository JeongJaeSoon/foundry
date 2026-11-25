"""Tests for the launcher: repo registry, worktrees, and `claude --bg` sessions through a fake claude."""

import json
import os
import subprocess
import sys

from test_foundry import ROOT, SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import launcher  # noqa: E402

GOAL = ("/goal Unit {u} is recorded in foundry state as done, waiting_others or waiting_owner "
        "(check with: foundry unit show {u}), and its report is recorded with foundry unit report {u}; "
        "or stop after {n} turns.")


class Launcher(Base):
    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                              env={**self.env, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}).stdout.strip()

    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.dir, "repo")
        origin = os.path.join(self.dir, "origin.git")
        self.git("init", "-q", "--bare", "-b", "main", origin)
        self.git("init", "-q", "-b", "main", self.repo)
        self.git("commit", "-q", "--allow-empty", "-m", "init", cwd=self.repo)
        self.git("remote", "add", "origin", origin, cwd=self.repo)
        self.git("push", "-q", "origin", "main", cwd=self.repo)
        self.wt_root = os.path.join(self.dir, "wt")
        self.plugin = os.path.join(self.dir, "plugin")
        self.log = os.path.join(self.dir, "claude.jsonl")
        self.env.update({"FOUNDRY_CLAUDE": f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_claude.py')}",
                         "FAKE_CLAUDE_DATA": os.path.join(self.dir, "claude.json"), "FAKE_CLAUDE_LOG": self.log,
                         "FOUNDRY_PLUGIN_DIRS": self.plugin, "CLAUDE_CONFIG_DIR": self.dir})
        self.config(worktree_root=self.wt_root)
        self.claude({})
        with open(os.path.join(self.dir, ".claude.json"), "w") as f:
            # Trust is per repository root: each worktree needs its own entry (a trusted parent does not count).
            json.dump({"projects": {os.path.join(self.wt_root, "app", u): {"hasTrustDialogAccepted": True}
                                    for u in ("alpha", "beta", "gamma", "delta")}}, f)
        self.foundry("repo", "add", "app", "--path", self.repo)
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta", "--repo", "app", "--kind", "repo")

    def config(self, **cfg):
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump(cfg, f)

    def claude(self, data):
        with open(self.env["FAKE_CLAUDE_DATA"], "w") as f:
            json.dump(data, f)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def state_records(self):
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            return sum('"session.state"' in line for line in f)

    def journal(self):
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            return [json.loads(line) for line in f]

    def bg_calls(self):
        return [c for c in self.calls() if "--bg" in c["argv"]]

    def test_repo_add_detects_remote_and_branch(self):
        _, out = self.foundry("repo", "list")
        self.assertEqual(out["app"], {"path": self.repo, "default_branch": "main", "remote": "origin", "tracker": "none"})

    def test_launch_builds_the_command_and_the_worktree(self):
        self.claude({"bg_id": "a1b2", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-alpha", "status": "busy"}]})
        _, out = self.foundry("unit", "launch", "alpha")
        path = os.path.join(self.wt_root, "app", "alpha")
        self.assertEqual(out["launched"], "lead-alpha")
        self.assertEqual(out["bg_id"], "a1b2")
        self.assertEqual(out["attach"], "claude attach lead-alpha")
        self.assertEqual(out["trust"], "path")
        call = self.bg_calls()[-1]
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(path))
        argv = call["argv"]
        settings = json.loads(argv[argv.index("--settings") + 1])
        if "--agents" in argv:  # 0.1.81: the MCP-mapped definition of the lead, when the mapping changes it
            i = argv.index("--agents")
            self.assertIn("foundry:line-lead", json.loads(argv[i + 1]))
            argv = argv[:i] + argv[i + 2:]
        self.assertEqual(argv[:argv.index("--settings")], ["--bg", "-n", "lead-alpha", "--agent", "foundry:line-lead",
                                                           "--model", "opus", "--effort", "medium", "--permission-mode", "auto"])
        self.assertEqual(argv[argv.index("--settings") + 2:], ["--plugin-dir", self.plugin, GOAL.format(u="alpha", n=200)])
        self.assertEqual(settings["env"], {"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "alpha", "FOUNDRY_STATE_DIR": self.dir,
                                           "CLAUDE_CONFIG_DIR": self.dir})
        self.assertEqual(settings["advisorModel"], "claude-fable-5-2")
        for rule in ("Bash(gh pr merge:*)", "Edit(~/.claude/**)", "Read(~/.ssh/**)",
                     "mcp__plugin_acme_acme-slack__slack_send_message"):
            self.assertIn(rule, settings["permissions"]["ask"])
        self.assertEqual(self.git("-C", path, "rev-parse", "--abbrev-ref", "HEAD"), "foundry/alpha")
        snap = self.snap()
        self.assertEqual(snap["units"]["alpha"]["worktree"], {"repo": "app", "path": path, "branch": "foundry/alpha"})
        sess = snap["sessions"]["lead-alpha"]
        self.assertEqual((sess["bg_id"], sess["session_id"], sess["state"]), ("a1b2", "S-alpha", "running"))

    def test_repo_unit_gets_a_shop_boss_with_overrides_and_extra_settings(self):
        self.config(worktree_root=self.wt_root, extra_settings={"env": {"X": "1", "FOUNDRY_ROLE": "nope"}, "k": 2})
        _, out = self.foundry("unit", "launch", "beta", "--model", "haiku", "--effort", "low", "--max-turns", "5")
        self.assertEqual(out["launched"], "boss-beta")
        argv = self.bg_calls()[-1]["argv"]
        self.assertEqual(argv[argv.index("--agent") + 1], "foundry:shop-boss")
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertEqual(argv[argv.index("--effort") + 1], "low")
        s = json.loads(argv[argv.index("--settings") + 1])
        self.assertEqual((s["env"], s["k"]), ({"X": "1", "FOUNDRY_ROLE": "shop-boss", "FOUNDRY_UNIT": "beta",
                                               "FOUNDRY_STATE_DIR": self.dir, "CLAUDE_CONFIG_DIR": self.dir}, 2))
        self.assertEqual(argv[-1], GOAL.format(u="beta", n=5))

    def test_dry_run_changes_nothing(self):
        _, out = self.foundry("unit", "launch", "alpha", "--dry-run")
        self.assertTrue(out["dry_run"])
        self.assertEqual(self.calls(), [])
        self.assertFalse(os.path.exists(out["cwd"]))

    def test_one_branch_per_unit(self):
        self.foundry("unit", "launch", "alpha", "--branch", "feat")
        rc, _ = self.foundry("unit", "launch", "beta", "--branch", "feat", ok=(2,))
        self.assertEqual(len(self.bg_calls()), 1)

    def test_repo_missing_from_registry(self):
        self.foundry("unit", "new", "--id", "gamma", "--title", "Gamma", "--repo", "nowhere")
        self.foundry("unit", "launch", "gamma", ok=(2,))
        self.foundry("unit", "new", "--id", "delta", "--title", "Delta")
        self.foundry("unit", "launch", "delta", ok=(2,))
        self.assertEqual(self.calls(), [])
        scratch = os.path.join(self.dir, "scratch")
        os.makedirs(scratch)
        self.foundry("unit", "launch", "delta", "--cwd", scratch, ok=(7,))  # untrusted: never started
        self.assertEqual(self.calls(), [])
        with open(os.path.join(self.dir, ".claude.json"), "w") as f:
            json.dump({"projects": {scratch: {"hasTrustDialogAccepted": True}}}, f)
        _, out = self.foundry("unit", "launch", "delta", "--cwd", scratch)
        self.assertEqual(os.path.realpath(self.bg_calls()[-1]["cwd"]), os.path.realpath(scratch))
        self.assertEqual(out["trust"], "path")

    def test_worktree_trust_is_registered_exactly_and_removed_with_the_worktree(self):
        cfg_file = os.path.join(self.dir, ".claude.json")
        with open(cfg_file, "w") as f:
            json.dump({"numStartups": 3, "projects": {self.wt_root: {"hasTrustDialogAccepted": True, "x": 1}}}, f)
        os.chmod(cfg_file, 0o600)
        _, out = self.foundry("unit", "launch", "alpha")
        self.assertEqual(out["trust"], "registered")
        path = os.path.realpath(os.path.join(self.wt_root, "app", "alpha"))
        with open(cfg_file) as f:
            data = json.load(f)
        self.assertEqual(data["numStartups"], 3)  # every other key kept
        self.assertEqual(data["projects"][self.wt_root], {"hasTrustDialogAccepted": True, "x": 1})
        self.assertEqual(data["projects"][path], {"hasTrustDialogAccepted": True})
        self.assertEqual(os.stat(cfg_file).st_mode & 0o777, 0o600)
        self.assertTrue(os.listdir(os.path.join(self.dir, "trust-backups")))
        ops = [r["op"] for r in self.journal()]
        self.assertIn("trust.register", ops)
        sys.path.insert(0, SCRIPTS)
        import trust
        self.git("worktree", "remove", "--force", path, cwd=self.repo)
        os.environ["FOUNDRY_STATE_DIR"], prev = self.dir, os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["CLAUDE_CONFIG_DIR"], prev_c = self.dir, os.environ.get("CLAUDE_CONFIG_DIR")
        try:
            self.assertTrue(trust.unregister(path, "alpha"))
            self.assertFalse(trust.unregister(self.wt_root))  # an entry foundry did not add stays
        finally:
            for k, v in (("FOUNDRY_STATE_DIR", prev), ("CLAUDE_CONFIG_DIR", prev_c)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        with open(cfg_file) as f:
            data = json.load(f)
        self.assertNotIn(path, data["projects"])
        self.assertIn(self.wt_root, data["projects"])

    def test_auto_trust_off_refuses_and_records_an_owner_todo(self):
        with open(os.path.join(self.dir, ".claude.json"), "w") as f:
            json.dump({"projects": {}}, f)
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"auto_trust_worktrees": False}, f)
        self.foundry("unit", "launch", "alpha", ok=(7,))
        self.assertEqual(self.bg_calls(), [])
        self.assertIsNone(self.snap()["sessions"].get("lead-alpha"))
        self.assertIn("trust:", " ".join(self.snap()["owner_todos"]))

    def test_session_refusing_trust_is_escalated_not_retried(self):
        with open(os.path.join(self.dir, ".claude.json"), "w") as f:
            json.dump({"projects": {}}, f)
        self.claude({"bg_fail": True, "bg_err": "Workspace not trusted. Run `claude` in /x once"})
        self.foundry("unit", "launch", "alpha", ok=(7,))
        self.assertEqual(len(self.bg_calls()), 1)
        self.assertIn("trust.failed", [r["op"] for r in self.journal()])
        self.assertIsNone(self.snap()["sessions"].get("lead-alpha"))

    def test_cap_reached_holds_without_calling_claude(self):
        self.config(worktree_root=self.wt_root, max_concurrent=1)
        self.foundry("unit", "launch", "alpha")
        rc, out = self.foundry("unit", "launch", "beta", ok=(6,))
        self.assertIn("cap", out["held"]["reason"])
        self.assertEqual(len(self.bg_calls()), 1)
        self.assertIsNotNone(self.snap()["units"]["beta"]["held"])

    def test_concurrent_launches_cannot_both_pass_the_cap(self):
        # Review 9: the cap check and the slot reservation are one transaction.
        self.config(worktree_root=self.wt_root, max_concurrent=1)
        procs = [subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "unit", "launch", u],
                                  env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for u in ("alpha", "beta")]
        codes = sorted(p.wait() for p in procs)
        self.assertEqual(codes, [0, 6])
        self.assertEqual(len(self.bg_calls()), 1)

    def test_pressure_holds(self):
        self.record("pressure", level="high", reasons=["memory 92%"])
        rc, out = self.foundry("unit", "launch", "alpha", ok=(6,))
        self.assertIn("memory 92%", out["held"]["reason"])
        self.assertEqual(self.calls(), [])

    def test_live_unit_is_not_launched_twice(self):
        self.foundry("unit", "launch", "alpha")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), "unit", "launch", "alpha"],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("resume or message it", r.stderr)
        self.assertEqual(len(self.bg_calls()), 1)

    def test_done_unit_is_refused(self):
        self.foundry("unit", "status", "alpha", "done")
        self.foundry("unit", "launch", "alpha", ok=(2,))

    def test_claude_failure_records_no_session(self):
        self.claude({"bg_fail": True})
        self.foundry("unit", "launch", "alpha", ok=(1,))
        self.assertNotIn("lead-alpha", self.snap()["sessions"])

    def test_resume_carries_the_launch_settings_and_records_the_copy(self):
        # Review 10: a bare --resume drops FOUNDRY_ROLE/UNIT and the permission mode (measured).
        self.claude({"bg_id": "a1", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-alpha", "status": "busy"}]})
        self.foundry("unit", "launch", "alpha")
        self.foundry("unit", "resume", "alpha", ok=(1,))  # still live
        self.foundry("unit", "stop", "alpha")
        self.claude({"bg_id": "a2", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-copy", "status": "busy"}]})
        _, out = self.foundry("unit", "resume", "alpha")
        argv = self.bg_calls()[-1]["argv"]
        self.assertEqual(argv[:3], ["--bg", "--resume", "S-alpha"])
        for flag, val in (("-n", "lead-alpha"), ("--agent", "foundry:line-lead"), ("--permission-mode", "auto")):
            self.assertEqual(argv[argv.index(flag) + 1], val)
        s = json.loads(argv[argv.index("--settings") + 1])
        self.assertEqual((s["env"]["FOUNDRY_ROLE"], s["env"]["FOUNDRY_UNIT"]), ("line-lead", "alpha"))
        self.assertEqual(os.path.realpath(self.bg_calls()[-1]["cwd"]), os.path.realpath(os.path.join(self.wt_root, "app", "alpha")))
        sess = self.snap()["sessions"]["lead-alpha"]
        self.assertEqual((sess["bg_id"], sess["session_id"], sess["launches"]), ("a2", "S-copy", 2))
        self.assertIn("note", out)

    def test_stop(self):
        self.claude({"bg_id": "a1"})
        self.foundry("unit", "launch", "alpha")
        _, out = self.foundry("unit", "stop", "alpha", "--reason", "owner asked")
        self.assertEqual(self.calls()[-1]["argv"], ["stop", "a1"])
        sess = self.snap()["sessions"]["lead-alpha"]
        self.assertEqual((sess["state"], sess["note"]), ("stopped", "owner asked"))

    def test_status_refresh_maps_states_and_marks_lost(self):
        self.claude({"bg_id": "a1"})
        self.foundry("unit", "launch", "alpha")
        self.claude({"bg_id": "b1"})
        self.foundry("unit", "launch", "beta")
        self.claude({"agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-a", "status": "idle"}]})
        _, out = self.foundry("unit", "status", "--refresh")
        self.assertEqual(self.calls()[-1]["argv"], ["agents", "--json", "--all"])
        moved = {c["name"]: c["to"] for c in out["changes"]}
        self.assertEqual(moved, {"lead-alpha": "idle", "boss-beta": "lost"})
        snap = self.snap()
        self.assertEqual(snap["sessions"]["lead-alpha"]["session_id"], "S-a")
        self.assertEqual(snap["sessions"]["lead-alpha"]["note"], "status=idle")
        n = self.state_records()
        _, out = self.foundry("unit", "status", "alpha", "--refresh")
        self.assertEqual(out["changes"], [])
        self.assertEqual([r["name"] for r in out["sessions"]], ["lead-alpha"])
        self.assertEqual(n, self.state_records())

    def test_unit_status_still_records_a_status(self):
        self.foundry("unit", "status", "alpha", "waiting_owner", "--note", "x")
        self.assertEqual(self.snap()["units"]["alpha"]["status"], "waiting_owner")

    def test_inbox_queued_while_done_reaches_the_next_launch(self):
        self.foundry("unit", "launch", "alpha")
        self.foundry("unit", "status", "alpha", "done")
        self.foundry("unit", "stop", "alpha")
        _, out = self.foundry("inbox", "route", "--unit", "alpha", "--text", "also bump the version")
        self.assertFalse(out["lead_running"])
        self.foundry("unit", "status", "alpha", "in_progress")
        self.foundry("unit", "launch", "alpha")  # reuses the unit's worktree
        self.assertEqual(len(self.bg_calls()), 2)
        self.assertFalse(any(i["delivered"] for i in self.snap()["units"]["alpha"]["inbox"]))
        env = dict(self.env)
        self.env.update({"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "alpha"})
        try:
            ctx = self.hook("SessionStart", session_id="SL2", source="startup",
                            agent_type="foundry:line-lead")["hookSpecificOutput"]["additionalContext"]
        finally:
            self.env = env
        self.assertIn("also bump the version", ctx)
        self.assertTrue(all(i["delivered"] for i in self.snap()["units"]["alpha"]["inbox"]))
        self.assertEqual(self.snap()["sessions"]["lead-alpha"]["session_id"], "SL2")


class Classify(Base):
    CREW = False

    def test_classify(self):
        cases = [({"kind": "bg", "status": "busy"}, "running"), ({"status": "running"}, "running"),
                 ({"status": "idle"}, "idle"), ({"state": "waiting"}, "idle"), ({"status": "done"}, "done"),
                 ({"status": "completed"}, "done"), ({"status": "exited"}, "done"), ({"status": "weird"}, None),
                 ({"pid": 1}, None)]
        for entry, want in cases:
            self.assertEqual(launcher.classify(entry)[0], want, entry)
        self.assertEqual(launcher.classify({"status": "weird"})[1], "weird")

    def test_trust_status(self):
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.dir
        try:
            self.assertEqual(launcher.trust_status(os.path.join(self.dir, "x")), "none")
            with open(os.path.join(self.dir, ".claude.json"), "w") as f:
                json.dump({"projects": {self.dir: {"hasTrustDialogAccepted": True}}}, f)
            self.assertEqual(launcher.trust_status(self.dir), "path")
            self.assertEqual(launcher.trust_status(os.path.join(self.dir, "a", "b")), "parent")
            # A repository root bounds the walk: the trusted parent above it does not count.
            repo = os.path.join(self.dir, "repo")
            os.makedirs(os.path.join(repo, ".git", "x"))
            self.assertEqual(launcher.trust_status(repo), "none")
            self.assertEqual(launcher.trust_status(os.path.join(repo, "src")), "none")
            self.assertEqual(launcher.trust_status(os.path.join(self.dir, "new-wt"), git_root=True), "none")
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR")
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
