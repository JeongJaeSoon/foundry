"""0.1.84: housekeeping checks a finished worktree's leftovers itself. Lead memo files, commits already in the
default branch or in another PR of the repo, and (no remote) commits merged into the local default branch are
accounted for: the worktree is safe, backed up under archive/ and removed. A change found nowhere else stays a
proposal that names it; a worktree in use is skipped; a docker project foundry did not make is never proposed."""

import json
import os
import subprocess
import tarfile

import test_foundry as tf

URL = "https://github.com/o/r/pull/1"


class Leftovers(tf.Housekeeping):
    def setUp(self):
        super().setUp()
        self.base = self.rev(self.repo)
        self.gh({"merged-clean": {"state": "MERGED", "number": 1, "url": URL, "headRefOid": self.base},
                 "merged-dirty": {"state": "MERGED", "number": 2, "url": URL, "headRefOid": self.base},
                 "open-clean": {"state": "OPEN", "number": 3, "url": URL, "headRefOid": self.base}})

    def rev(self, path):
        return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def wt(self, name="merged-clean"):
        return os.path.join(self.dir, name)

    def cands(self):
        return {c["branch"]: c for c in self.script("housekeep.py", "scan") if c["cls"] == "worktree"}

    def run_safe(self, c):
        self.script("housekeep.py", "plan", "--hid", c["hid"], "--verdict", "safe", "--reason", "judge")
        return {r["hid"]: r["result"] for r in self.script("housekeep.py", "execute")["results"]}[c["hid"]]

    def archives(self):
        top = os.path.join(self.dir, "archive")
        return [os.path.join(top, n) for n in sorted(os.listdir(top))] if os.path.isdir(top) else []

    def commit(self, path, name, text, msg):
        with open(os.path.join(path, name), "w") as f:
            f.write(text)
        self.git("add", name, cwd=path)
        self.git("commit", "-q", "-m", msg, cwd=path)

    def test_memo_files_only_is_removed_with_a_backup(self):
        for n in (".pr-body.md", ".foundry-card-prod.md"):
            with open(os.path.join(self.wt(), n), "w") as f:
                f.write("note")
        c = self.cands()["merged-clean"]
        self.assertEqual((c["safe"], c["suggest"]), (True, "safe"))
        self.assertTrue(self.run_safe(c).startswith("removed (backup "))
        self.assertFalse(os.path.exists(self.wt()))
        [a] = self.archives()
        with tarfile.open(os.path.join(a, "files.tar")) as t:
            self.assertEqual(sorted(t.getnames()), [".foundry-card-prod.md", ".pr-body.md"])
        self.assertTrue(json.load(open(os.path.join(a, "proof.json")))["proven"])

    def test_a_memo_directory_of_a_finished_unit_is_removed_with_a_backup(self):
        # 0.1.114, the live shape: drafts in a `.foundry-765/` dir beside a top-level card.
        os.makedirs(os.path.join(self.wt(), ".foundry-765"))
        names = [".foundry-765/u765-commit.txt", ".foundry-765/u765-pr-body.md", ".foundry-765/u765-sticky.md",
                 ".foundry-card-765.md"]
        for n in names:
            with open(os.path.join(self.wt(), n), "w") as f:
                f.write("note")
        c = self.cands()["merged-clean"]
        self.assertEqual((c["safe"], c["suggest"]), (True, "safe"))
        self.assertTrue(self.run_safe(c).startswith("removed (backup "))
        self.assertFalse(os.path.exists(self.wt()))
        [a] = self.archives()
        with tarfile.open(os.path.join(a, "files.tar")) as t:
            self.assertEqual(sorted(t.getnames()), sorted(names))

    def test_a_non_memo_untracked_file_beside_a_memo_dir_stays_a_proposal(self):
        os.makedirs(os.path.join(self.wt(), ".foundry-765"))
        with open(os.path.join(self.wt(), ".foundry-765", "u765-commit.txt"), "w") as f:
            f.write("note")
        os.makedirs(os.path.join(self.wt(), "scratch"))
        with open(os.path.join(self.wt(), "scratch", "out.txt"), "w") as f:
            f.write("result")
        c = self.cands()["merged-clean"]
        self.assertEqual((c["safe"], c["suggest"]), (False, "propose"))
        self.assertIn("scratch/out.txt", c["note"])

    def test_only_a_foundry_dir_holds_memos(self):
        os.makedirs(os.path.join(self.wt(), ".reports"))
        with open(os.path.join(self.wt(), ".reports", "x.md"), "w") as f:
            f.write("note")
        c = self.cands()["merged-clean"]
        self.assertEqual((c["safe"], c["suggest"]), (False, "propose"))
        self.assertIn(".reports/x.md", c["note"])

    def test_a_commit_found_in_another_pr_is_removed_with_its_patch(self):
        other = self.wt("open-clean")
        self.commit(other, "limit.yaml", "memory: 1.5Gi\n", "raise memory limit")
        patch = subprocess.run(["git", "-C", other, "format-patch", "-1", "--stdout"], capture_output=True,
                               text=True).stdout
        self.commit(self.wt(), "limit.yaml", "memory: 1.5Gi\n", "raise memory limit (local copy)")
        with open(self.env["FAKE_GH_DATA"]) as f:
            data = json.load(f)
        data.update({"o/r#-R": [{"number": 7}], "o/r#7": {"_raw": patch}})
        self.gh(data)
        c = self.cands()["merged-clean"]
        self.assertEqual([x["where"] for x in c["facts"]["proof"]["commits"]], ["o/r#7"])
        self.assertTrue(c["safe"])
        self.assertTrue(self.run_safe(c).startswith("removed"))
        [a] = self.archives()
        self.assertEqual(len(os.listdir(os.path.join(a, "patches"))), 1)

    def test_a_change_found_nowhere_stays_a_proposal_that_names_it(self):
        c = self.cands()["merged-dirty"]
        self.assertEqual((c["safe"], c["suggest"]), (False, "propose"))
        self.assertIn("wip.txt (new, 0 lines)", c["note"])
        self.commit(self.wt(), "app.py", "print(1)\n", "unpushed fix")
        c = self.cands()["merged-clean"]
        self.assertEqual(c["suggest"], "propose")
        self.assertIn("unpushed fix", c["note"])
        self.script("housekeep.py", "plan", "--hid", c["hid"], "--verdict", "propose", "--reason", "owner")
        [p] = [p for p in self.snap()["housekeeping"]["plans"] if p["hid"] == c["hid"]]
        self.assertIn("unpushed fix", p["nowhere"])
        self.assertTrue(self.run_safe(c).startswith("skipped"))  # a wrong judge cannot delete it
        self.assertEqual(self.archives(), [])

    def test_in_use_is_skipped_even_with_only_memo_files(self):
        with open(os.path.join(self.wt(), ".report.txt"), "w") as f:
            f.write("x")
        self.env["FAKE_LSOF_CWDS"] = self.wt()
        c = self.cands()["merged-clean"]
        self.assertEqual((c["safe"], c["suggest"]), (False, "skip"))

    def test_a_moved_head_with_nothing_unpushed_is_safe(self):
        self.gh({"merged-clean": {"state": "MERGED", "number": 1, "url": URL, "headRefOid": "0" * 40}})
        self.assertTrue(self.cands()["merged-clean"]["safe"])

    def test_no_remote_repo_is_judged_by_its_local_default_branch(self):
        local = os.path.join(self.dir, "local")
        self.git("init", "-q", "-b", "main", local)
        self.git("commit", "-q", "--allow-empty", "-m", "init", cwd=local)
        wt = os.path.join(self.dir, "local-rel")
        self.git("worktree", "add", "-q", "-b", "rel", wt, cwd=local)
        self.commit(wt, "a.txt", "a\n", "release a")
        self.env["FOUNDRY_REPO_ROOTS"] = f"{self.repo}:{local}"
        c = self.cands()["rel"]
        self.assertFalse(c["safe"])  # not merged yet
        self.git("merge", "-q", "--no-ff", "rel", "-m", "merge rel", cwd=local)
        c = self.cands()["rel"]
        self.assertTrue(c["safe"])
        self.env["FAKE_LSOF_CWDS"] = wt
        self.assertEqual(self.cands()["rel"]["suggest"], "skip")

    def test_an_open_unit_keeps_its_worktree_and_a_fresh_local_branch_is_not_finished(self):
        with open(os.path.join(self.wt(), ".pr-body.md"), "w") as f:
            f.write("note")
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta")
        self.record("unit.worktree", id="beta", path=self.wt(), branch="merged-clean")
        self.record("unit.status", id="beta", status="waiting_others", note="prod verify")
        c = self.cands()["merged-clean"]
        self.assertEqual((c["safe"], c["suggest"]), (False, "propose"))
        self.record("unit.status", id="beta", status="done", note="verified")
        self.assertTrue(self.cands()["merged-clean"]["safe"])
        local = os.path.join(self.dir, "local")
        self.git("init", "-q", "-b", "main", local)
        self.git("commit", "-q", "--allow-empty", "-m", "init", cwd=local)
        self.git("worktree", "add", "-q", "-b", "fresh", os.path.join(self.dir, "local-fresh"), cwd=local)
        self.env["FOUNDRY_REPO_ROOTS"] = f"{self.repo}:{local}"
        self.assertEqual(self.cands()["fresh"]["suggest"], "skip")

    def test_docker_projects_foundry_did_not_make_are_not_proposed(self):
        fake = os.path.join(self.dir, "fake_docker.sh")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\necho \'[{"Name": "other-app"}, {"Name": "foundry-x"}]\'\n')
        os.chmod(fake, 0o755)
        self.env["FOUNDRY_DOCKER"] = fake
        d = {c["target"]: c for c in self.script("housekeep.py", "scan") if c["cls"] == "docker"}
        self.assertEqual(d["other-app"]["suggest"], "skip")
        self.assertEqual(d["foundry-x"]["suggest"], "propose")
