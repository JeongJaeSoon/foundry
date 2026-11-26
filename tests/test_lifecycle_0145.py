"""0.1.45: every flag a refusal or usage hint names is one its command takes; guessed CLI shapes are corrected from
the message itself (--version, nearest ids, transition commands); a finished subagent is never TaskStopped; and done
comes from evidence foundry records, never from a lead verifying its own unit."""

import argparse
import ast
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import foundry  # noqa: E402

CMD = re.compile(r"\bfoundry((?: [a-z][a-z-]*)+)")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z-]*)")
STOP = re.compile(r"`|\n|;|\)| then |\bfoundry ")


def script_texts(scripts):
    """(file:line, text) for every string literal under scripts/, f-string holes as X."""
    for path in sorted(glob.glob(os.path.join(scripts, "*.py"))):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield f"{os.path.basename(path)}:{node.lineno}", node.value
            elif isinstance(node, ast.JoinedStr):
                yield (f"{os.path.basename(path)}:{node.lineno}",
                       "".join(v.value if isinstance(v, ast.Constant) else "X" for v in node.values))


def doc_texts(root):
    for path in sorted(glob.glob(os.path.join(root, "agents", "*.md")) + glob.glob(os.path.join(root, "skills", "*", "SKILL.md"))
                       + [os.path.join(root, "README.md")]):
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                yield f"{os.path.relpath(path, root)}:{n}", line


def subparsers(p):
    return next((a.choices for a in p._actions if isinstance(a, argparse._SubParsersAction)), {})


def unknown_flags(parser, texts):
    """[(where, command, flag)]: each `foundry <command> ... --flag` whose command's parser lacks the flag."""
    bad = []
    for where, text in texts:
        for m in CMD.finditer(text):
            rest = text[m.end():]
            cut = STOP.search(rest)
            seg = m.group(1) + " " + (rest[:cut.start()] if cut else rest)
            p, path = parser, []
            for w in m.group(1).split():
                subs = subparsers(p)
                if w not in subs:
                    break
                p, path = subs[w], path + [w]
            if not path:
                continue  # prose ("foundry records ..."), not a command
            bad += [(where, " ".join(path), fl) for fl in FLAG.findall(seg) if fl not in p._option_string_actions]
    return bad


class Base(tf.Base):
    FOREMAN = {"FOUNDRY_ROLE": "foreman"}

    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")
        self.foundry("unit", "new", "--id", "beta-api", "--title", "Beta", "--repo", "app")
        self.lead = {"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "alpha"}

    def run_f(self, *args, env=None):
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), *args],
                           env={**self.env, **(env or {})}, capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def set_now(self, ts):
        old = os.environ.get("FOUNDRY_NOW")
        self.addCleanup(lambda: os.environ.pop("FOUNDRY_NOW") if old is None else os.environ.update(FOUNDRY_NOW=old))
        os.environ["FOUNDRY_NOW"] = ts
        self.env["FOUNDRY_NOW"] = ts

    def unit(self, uid="alpha"):
        return self.snap()["units"][uid]


class A_RefusalFlags(Base):
    def test_unit_status_takes_the_quote_its_refusal_names(self):
        rc, _, err = self.run_f("unit", "status", "alpha", "cancelled", env=self.FOREMAN)
        self.assertEqual(rc, 2)
        self.assertIn('--quote "<the owner\'s exact words>"', err)
        rc, out, err = self.run_f("unit", "status", "alpha", "cancelled", "--quote", "알파는 취소해줘", env=self.FOREMAN)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["quote"], "알파는 취소해줘")
        self.assertEqual(self.unit()["status"], "cancelled")
        sets = [r for r in self.journal() if r["op"] == "unit.set"]
        self.assertEqual((sets[-1]["by"], sets[-1]["quote"]), ("foreman", "알파는 취소해줘"))

    def test_both_status_commands_accept_every_flag_their_refusal_names(self):
        for argv in (["unit", "set", "alpha", "--status", "done"], ["unit", "status", "alpha", "done"]):
            rc, _, err = self.run_f(*argv, env=self.FOREMAN)
            self.assertEqual(rc, 2)
            for fl in set(FLAG.findall(err)):
                rc2, _, err2 = self.run_f(*argv, fl, "x", "--note", "probe", env={"FOUNDRY_ROLE": "hand"})
                self.assertNotIn("unrecognized arguments", err2, (argv, fl))

    def test_every_flag_named_in_scripts_and_docs_is_accepted(self):
        texts = list(script_texts(tf.SCRIPTS)) + list(doc_texts(tf.ROOT))
        self.assertGreater(sum(1 for _, t in texts if CMD.search(t) and FLAG.search(t)), 50)
        self.assertEqual(unknown_flags(foundry.parser(), texts), [])

    def test_the_scan_catches_a_flag_the_command_lacks(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.py"), "w") as f:
                f.write('MSG = f"refused: run `foundry unit status {uid} done --force` instead"\n'
                        'OK = "foundry unit set a --status done --quote q"\n')
            self.assertEqual(unknown_flags(foundry.parser(), script_texts(d)), [("x.py:1", "unit status", "--force")])


class B_GuessedShapes(Base):
    def test_version(self):
        with open(os.path.join(tf.ROOT, ".claude-plugin", "plugin.json")) as f:
            want = json.load(f)["version"]
        rc, out, _ = self.run_f("--version")
        self.assertEqual((rc, out.strip()), (0, f"foundry {want}"))

    def test_unknown_id_lists_the_nearest_open_ids(self):
        rc, _, err = self.run_f("unit", "status", "alpah", "done", env=self.lead)
        self.assertEqual(rc, 2)
        self.assertIn("unknown unit alpah; nearest open unit(s): alpha", err)
        rc, _, err = self.run_f("unit", "show-me", env=self.lead)  # argparse's own error stays argparse's
        self.assertEqual(rc, 2)
        rc, _, err = self.run_f("unit", "note", "beta", "--text", "hi", env=self.FOREMAN)
        self.assertIn("nearest open unit(s): beta-api", err)

    def test_a_closed_unit_is_not_offered(self):
        self.record("unit.status", id="beta-api", status="done")
        _, _, err = self.run_f("unit", "note", "beta", "--text", "hi", env=self.FOREMAN)
        self.assertNotIn("beta-api", err)

    def test_bad_status_lists_the_valid_ones_and_the_transition(self):
        self.record("unit.status", id="alpha", status="parked")
        rc, _, err = self.run_f("unit", "status", "alpha", "launch", env=self.FOREMAN)
        self.assertEqual(rc, 2)
        self.assertIn("is not one of: queue, in_progress", err)
        self.assertIn("`foundry unit status alpha queue`, then `foundry unit launch alpha`", err)
        self.record("unit.status", id="alpha", status="done")
        _, _, err = self.run_f("unit", "status", "alpha", "launch", env=self.FOREMAN)
        self.assertIn("`foundry unit status alpha queue --quote \"<the owner's exact words>\"`, then "
                      "`foundry unit launch alpha`", err)
        _, _, err = self.run_f("unit", "status", "alpha", "progress", env=self.FOREMAN)
        self.assertNotIn("unit launch", err)  # a typo gets the list only

    def test_launch_of_a_parked_or_done_unit_names_the_way_back(self):
        self.record("unit.status", id="alpha", status="parked")
        rc, _, err = self.run_f("unit", "launch", "alpha", env=self.FOREMAN)
        self.assertEqual(rc, 2)
        self.assertIn("launch takes queue, in_progress, waiting_others, waiting_owner", err)
        self.assertIn("foundry unit status alpha queue", err)
        self.assertNotIn("launch.held", [r["op"] for r in self.journal()])
        self.record("unit.status", id="alpha", status="done")
        _, _, err = self.run_f("unit", "launch", "alpha", env=self.FOREMAN)
        self.assertIn("--quote", err)
        # the hint works as written
        rc, _, err = self.run_f("unit", "status", "alpha", "queue", "--quote", "알파 다시 열어줘", env=self.FOREMAN)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.unit()["status"], "queue")


class C_TaskStop(Base):
    def test_a_finished_subagent_is_closed_without_a_taskstop(self):
        self.record("agent.start", agent_id="ag1", type="foundry:advisor")
        self.record("agent.stop", agent_id="ag1")
        self.script("housekeep.py", "plan", "--hid", "subagent:ag1", "--verdict", "safe", "--reason", "finished")
        _, run = self.foundry("sweep", "run")
        self.assertEqual(run["foreman_taskstop"], [])
        done = [d for d in self.snap()["housekeeping"]["done"] if d["hid"] == "subagent:ag1"]
        self.assertEqual(len(done), 1)
        self.assertIn("no TaskStop needed", done[0]["result"])
        self.assertNotIn("subagent:ag1", {c["hid"] for c in self.script("housekeep.py", "scan")})
        _, run = self.foundry("sweep", "run")
        self.assertEqual(len([d for d in self.snap()["housekeeping"]["done"] if d["hid"] == "subagent:ag1"]), 1)

    def test_only_a_running_subagent_is_listed_for_taskstop(self):
        self.record("agent.start", agent_id="ag2", type="foundry:advisor")
        self.script("housekeep.py", "plan", "--hid", "subagent:ag2", "--verdict", "safe", "--reason", "x")
        _, run = self.foundry("sweep", "run")
        self.assertEqual(run["foreman_taskstop"], ["ag2"])

    def test_the_foreman_stops_only_running_tasks(self):
        with open(os.path.join(tf.ROOT, "agents", "foreman.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("TaskStop only a task the harness still shows as running", text)
        self.assertIn('"is not running", which means it is done, not that anything failed', text)


class D_DoneEvidence(Base):
    def test_a_lead_never_verifies_its_own_unit(self):
        rc, _, err = self.run_f("unit", "verify", "alpha", "--evidence", "tests green", "--by", "lead", env=self.lead)
        self.assertEqual(rc, 2)
        self.assertIn("does not verify its own work", err)
        self.assertIn("foundry unit report alpha --text", err)
        self.assertNotIn("verify.pass", [r["op"] for r in self.journal()])

    def test_a_merged_pr_since_the_last_change_is_the_evidence(self):
        rc, _, err = self.run_f("unit", "status", "alpha", "done", env=self.lead)
        self.assertEqual(rc, 2)
        self.assertIn("done needs evidence foundry records itself", err)
        self.set_now("2026-11-18T10:05:00+09:00")
        self.record("pr.opened", unit="alpha", repo="example-org/app", number=5)
        self.record("pr.opened", unit="alpha", repo="example-org/app", number=6)
        self.set_now("2026-11-18T11:00:00+09:00")
        self.record("pr.merged", unit="alpha", repo="example-org/app", number=5)
        self.assertEqual(self.run_f("unit", "status", "alpha", "done", env=self.lead)[0], 2)  # #6 still open
        self.record("pr.state", unit="alpha", repo="example-org/app", number=6, state="CLOSED")
        self.set_now("2026-11-18T11:10:00+09:00")
        self.record("branch.pushed", unit="alpha", branch="foundry/alpha")  # code after the merge
        self.assertEqual(self.run_f("unit", "status", "alpha", "done", env=self.lead)[0], 2)
        self.set_now("2026-11-18T12:00:00+09:00")
        self.record("pr.opened", unit="alpha", repo="example-org/app", number=7)
        self.set_now("2026-11-18T12:30:00+09:00")
        self.record("pr.merged", unit="alpha", repo="example-org/app", number=7)
        rc, _, err = self.run_f("unit", "status", "alpha", "done", env=self.lead)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.unit()["status"], "done")

    def test_without_a_pr_the_foreman_verifies_the_report(self):
        self.start_lead("alpha")
        self.assertEqual(self.run_f("unit", "report", "alpha", "--text", "ran the query, 0 rows", env=self.lead)[0], 0)
        rc, _, err = self.run_f("unit", "verify", "alpha", "--evidence", "lead report: query returns 0 rows",
                                env=self.FOREMAN)
        self.assertEqual(rc, 0, err)
        v = self.unit()["verify"]
        self.assertEqual((v["passed"], v["by"]), (True, "foreman"))
        self.assertTrue(any("Verified by the foreman" in n["text"] and "unit status alpha done" in n["text"]
                            for n in self.unit()["inbox"]))
        rc, _, err = self.run_f("unit", "status", "alpha", "done", env=self.lead)
        self.assertEqual(rc, 0, err)

    def test_the_lead_docs_send_no_lead_to_verify(self):
        for rel in ("agents/line-lead.md", "skills/foundry-lead-loop/SKILL.md"):
            with open(os.path.join(tf.ROOT, rel), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("never", text[text.find("foundry unit verify") - 40:text.find("foundry unit verify")], rel)


if __name__ == "__main__":
    unittest.main()
