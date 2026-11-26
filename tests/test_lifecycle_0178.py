"""0.1.78: a worktree a live process uses as its directory is never removed: every removal goes through one function
that re-checks the cwd users right before it (hk.refused with pid and command), the scan skips such a worktree
instead of proposing it, foundry sessions cannot `git worktree remove` or `rm -r` a worktree directly, and the MCP
check reports servers under their generic name as installed, wherever the Claude config lives."""

import json
import os
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import mcpmap  # noqa: E402


class CwdGuard(tf.Housekeeping):
    def test_scan_skips_a_worktree_in_use_and_names_the_process(self):
        self.env["FAKE_LSOF_CWDS"] = os.path.join(self.dir, "merged-clean", "sub")
        cands = {c["branch"]: c for c in self.script("housekeep.py", "scan") if c["cls"] == "worktree"}
        c = cands["merged-clean"]
        self.assertEqual((c["safe"], c["suggest"]), (False, "skip"))
        self.assertEqual(c["note"], "in use by claude (1000)")

    def test_the_executor_refuses_when_a_process_moved_in_after_the_verdict(self):
        cands = {c["branch"]: c for c in self.script("housekeep.py", "scan") if c["cls"] == "worktree"}
        self.script("housekeep.py", "plan", "--hid", cands["merged-clean"]["hid"], "--verdict", "safe", "--reason", "j")
        self.env["FAKE_LSOF_CWDS"] = os.path.join(self.dir, "merged-clean")
        res = self.script("housekeep.py", "execute")["results"]
        self.assertTrue(res[0]["result"].startswith("skipped"), res)  # the re-check sees it in use first
        self.assertTrue(os.path.isdir(os.path.join(self.dir, "merged-clean")))

    def test_owner_approved_removal_refuses_a_live_cwd_and_journals_it(self):
        target = os.path.join(self.dir, "merged-clean")
        self.env["FAKE_LSOF_CWDS"] = target
        rc, out = self.foundry("sweep", "remove-worktree", target, "--by", "foreman", "--reason", "owner: 지워",
                               ok=(1,))
        self.assertEqual(out["result"], "refused: in use by claude (1000)")
        self.assertTrue(os.path.isdir(target))
        [r] = [r for r in self.journal() if r["op"] == "hk.refused"]
        self.assertEqual((r["by"], r["procs"]), ("foreman", [{"pid": "1000", "cmd": "claude"}]))
        self.env["FAKE_LSOF_FAIL"] = "1"  # lsof cannot tell: refused too
        self.env.pop("FAKE_LSOF_CWDS")
        self.foundry("sweep", "remove-worktree", target, "--by", "foreman", "--reason", "x", ok=(1,))
        self.env.pop("FAKE_LSOF_FAIL")
        _, out = self.foundry("sweep", "remove-worktree", target, "--by", "owner", "--reason", "x")
        self.assertEqual(out["result"], "removed")
        self.assertFalse(os.path.exists(target))

    def test_dirty_worktrees_are_not_forced(self):
        target = os.path.join(self.dir, "merged-dirty")
        rc, out = self.foundry("sweep", "remove-worktree", target, "--by", "owner", "--reason", "x", ok=(1,))
        self.assertTrue(out["result"].startswith("failed"))
        self.assertTrue(os.path.exists(os.path.join(target, "wip.txt")))

    def test_plans_carry_the_in_use_mark(self):
        target = os.path.join(self.dir, "open-clean")
        self.env["FAKE_LSOF_CWDS"] = target
        self.script("housekeep.py", "plan", "--hid", f"worktree:{target}", "--verdict", "propose", "--reason", "r")
        [p] = [p for p in self.snap()["housekeeping"]["plans"] if p["target"] == target]
        self.assertEqual(p["in_use"], "claude (1000)")


class DirectRemovalDenied(tf.Base):
    def test_git_worktree_remove_and_rm_r_of_a_worktree_are_denied(self):
        import tempfile
        base = tempfile.mkdtemp(dir=tf.SCRATCH)  # outside the state dir, which has its own write guard
        self.addCleanup(__import__("shutil").rmtree, base, True)
        wt = os.path.join(base, "wt1")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: /x\n")
        os.makedirs(os.path.join(wt, "build"))
        for cmd in (f"git -C {base} worktree remove {wt}", f"rm -rf {wt}", f"rm -r {wt}/"):
            out = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, cwd=base, **tf.ORCH)
            self.assertTrue(self.denied(out), cmd)
            self.assertIn("foundry sweep remove-worktree", out["hookSpecificOutput"]["permissionDecisionReason"])
        out = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": f"rm -rf {wt}/build"}, cwd=base,
                        **tf.ORCH)
        self.assertFalse(self.denied(out))  # a directory inside a worktree is fine


class McpSameName(tf.Base):
    def test_a_server_with_the_generic_name_is_reported_installed(self):
        self.env["FOUNDRY_MCP_SERVERS"] = json.dumps(["obsidian", "plugin_acme_acme-slack"])
        _, out = self.foundry("mcp", "map")
        self.assertIn("installed (same name): obsidian", out["detail"])
        self.assertNotIn("obsidian", out["detail"].split("not installed:")[-1] if "not installed" in out["detail"] else "")

    def test_home_config_is_read_under_another_config_dir(self):
        home = os.path.join(self.dir, "home")
        cdir = os.path.join(self.dir, "profile")
        os.makedirs(cdir)
        os.makedirs(os.path.join(home, ".claude"))
        with open(os.path.join(home, ".claude.json"), "w") as f:
            json.dump({"mcpServers": {"obsidian": {}}}, f)
        saved = {k: os.environ.get(k) for k in ("HOME", "CLAUDE_CONFIG_DIR", "FOUNDRY_MCP_SERVERS")}
        os.environ.update({"HOME": home, "CLAUDE_CONFIG_DIR": cdir})
        os.environ.pop("FOUNDRY_MCP_SERVERS")
        try:
            self.assertIn("obsidian", mcpmap.installed_servers())
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
