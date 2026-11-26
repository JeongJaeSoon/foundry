"""graph.py: readiness by ref kind, cycles, locks, next ordering, the verify gate, role scope, andon, notes."""

import json
import os
import subprocess
import sys

from test_foundry import SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import graph  # noqa: E402


class GraphBase(Base):
    def run_f(self, *args, env=None):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args],
                           env={**self.env, **(env or {})}, capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def new(self, uid, repo="r1", at=None):
        env = {"FOUNDRY_NOW": at} if at else None
        rc, out, err = self.run_f("unit", "new", "--id", uid, "--title", uid.upper(), "--repo", repo, env=env)
        self.assertEqual(rc, 0, err)

    def unit(self, uid):
        return self.snap()["units"][uid]

    def journal(self):
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            return [json.loads(x) for x in f if x.strip()]

    def set_now(self, ts):
        """The clock for both CLI subprocesses and in-process record()."""
        old = os.environ.get("FOUNDRY_NOW")
        self.addCleanup(lambda: os.environ.pop("FOUNDRY_NOW") if old is None else os.environ.update(FOUNDRY_NOW=old))
        os.environ["FOUNDRY_NOW"] = ts
        self.env["FOUNDRY_NOW"] = ts

    def as_lead(self, uid):
        return {"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": uid}


class Readiness(GraphBase):
    def setUp(self):
        super().setUp()
        self.new("a")
        self.new("b")

    def assert_ready(self, uid):
        u = self.unit(uid)
        self.assertEqual(u["blocked_by"], [])
        self.assertIsNotNone(u["ready"])
        self.assertTrue(any(e["kind"] == "unit.ready" and e["unit"] == uid for e in self.snap()["events"]))

    def test_unit_ref_resolves_when_upstream_is_done(self):
        self.foundry("unit", "block", "a", "--on", "unit:b")
        self.assertEqual(self.unit("a")["blocked_by"], ["unit:b"])
        self.foundry("unit", "set", "b", "--status", "done", "--by", "owner")
        self.assert_ready("a")

    def test_pr_ref_from_merged_or_state(self):
        self.foundry("unit", "block", "a", "--on", "pr:o/r#5:merged")
        self.foundry("unit", "block", "b", "--on", "pr:o/r#6:merged")
        self.record("pr.state", repo="o/r", number=5, state="OPEN")
        self.foundry("graph", "resolve")
        self.assertEqual(self.unit("a")["blocked_by"], ["pr:o/r#5:merged"])
        self.record("pr.merged", repo="o/r", number=5)
        self.record("pr.state", repo="o/r", number=6, state="MERGED")
        self.foundry("graph", "resolve")
        self.assert_ready("a")
        self.assert_ready("b")

    def test_deploy_and_ticket_refs(self):
        self.foundry("unit", "block", "a", "--on", "deploy:app@prod", "--on", "ticket:PROJ-1:Done")
        self.record("deploy.done", app="app", env="prod")
        self.record("ticket.state", key="PROJ-1", status="In Review")
        self.foundry("graph", "resolve")
        self.assertEqual(self.unit("a")["blocked_by"], ["ticket:PROJ-1:Done"])
        self.record("ticket.state", key="PROJ-1", status="done")
        self.foundry("graph", "resolve")
        self.assert_ready("a")

    def test_deploy_ref_resolves_from_the_deployed_verb(self):
        self.foundry("unit", "block", "a", "--on", "deploy:app@prod")
        lead_b = self.as_lead("b")
        args = ("unit", "deployed", "b", "--app", "app", "--env", "prod", "--evidence", "x")
        self.assertEqual(self.run_f("unit", "deployed", "a", *args[3:], "--by", "lead", env=lead_b)[0], 2)  # not b's
        for by in ("foreman", "hand"):
            self.assertEqual(self.run_f(*args, "--by", by, env={"FOUNDRY_ROLE": by})[0], 2)
        self.assertEqual(self.unit("a")["blocked_by"], ["deploy:app@prod"])
        rc, out, err = self.run_f(*args[:-1], "argo synced v1.0.0", "--tag", "v1.0.0", "--by", "lead", env=lead_b)
        self.assertEqual(rc, 0, err)
        self.assert_ready("a")
        import factory
        tr = factory.trace(self.snap(), self.journal(), "b")
        self.assertEqual(tr["tags"], ["v1.0.0"])
        self.assertEqual([(d["app"], d["env"], d["tag"]) for d in tr["deploys"]], [("app", "prod", "v1.0.0")])

    def test_time_ref(self):
        self.foundry("unit", "block", "a", "--on", "time:2026-11-18T11:00:00+09:00")
        self.foundry("graph", "resolve")
        self.assertTrue(self.unit("a")["blocked_by"])
        self.set_now("2026-11-18T11:00:00+09:00")
        self.foundry("graph", "resolve")
        self.assert_ready("a")

    def test_unknown_ref_and_unknown_unit_refused(self):
        self.assertEqual(self.run_f("unit", "block", "a", "--on", "nope:x")[0], 2)
        self.assertEqual(self.run_f("unit", "block", "a", "--on", "unit:zzz")[0], 2)
        self.assertEqual(self.run_f("unit", "block", "a", "--on", "pr:o/r#5:open")[0], 2)

    def test_unblock(self):
        self.foundry("unit", "block", "a", "--on", "unit:b", "--on", "deploy:x@dev")
        _, out = self.foundry("unit", "unblock", "a", "--ref", "deploy:x@dev")
        self.assertFalse(out["ready"])
        self.assertEqual(self.unit("a")["blocked_by"], ["unit:b"])
        _, out = self.foundry("unit", "unblock", "a", "--ref", "unit:b")
        self.assertTrue(out["ready"])
        self.assert_ready("a")
        self.assertEqual(self.run_f("unit", "unblock", "a", "--ref", "unit:b")[0], 2)

    def test_cycle_refused_with_path(self):
        self.new("c")
        self.foundry("unit", "block", "a", "--on", "unit:b")
        self.foundry("unit", "block", "b", "--on", "unit:c")
        rc, _, err = self.run_f("unit", "block", "c", "--on", "unit:a")
        self.assertEqual(rc, 2)
        self.assertIn("c -> a -> b -> c", err)
        self.assertEqual(self.unit("c")["blocked_by"], [])
        self.assertEqual(self.run_f("unit", "block", "a", "--on", "unit:a")[0], 2)


class Locks(GraphBase):
    def setUp(self):
        super().setUp()
        self.new("a", at="2026-11-18T08:00:00+09:00")
        self.new("b", at="2026-11-18T09:00:00+09:00")

    def test_lock_is_exclusive_and_passes_on(self):
        self.foundry("unit", "block", "b", "--on", "lock:db")
        self.foundry("unit", "block", "a", "--on", "lock:db")
        snap = self.snap()
        self.assertEqual(snap["locks"]["db"]["unit"], "b")  # b asked first, a waits
        self.assertEqual(snap["units"]["a"]["blocked_by"], ["lock:db"])
        self.assertIn("held by b", graph.launch_block(snap, "a"))
        self.assertEqual(self.run_f("lock", "take", "db", "--unit", "a")[0], 2)
        self.assertEqual(self.run_f("lock", "release", "db", "--unit", "a")[0], 2)
        self.foundry("lock", "release", "db", "--unit", "b")
        snap = self.snap()
        self.assertEqual(snap["locks"]["db"]["unit"], "a")
        self.assertEqual(snap["units"]["a"]["blocked_by"], [])

    def test_done_holder_frees_the_lock(self):
        self.foundry("lock", "take", "db", "--unit", "a")
        self.foundry("unit", "block", "b", "--on", "lock:db")
        self.assertEqual(self.unit("b")["blocked_by"], ["lock:db"])
        self.foundry("unit", "set", "a", "--status", "cancelled", "--by", "owner")
        self.assertEqual(self.snap()["locks"]["db"]["unit"], "b")
        self.assertEqual(self.unit("b")["blocked_by"], [])

    def test_waiter_with_other_blockers_does_not_take_the_lock(self):
        self.foundry("unit", "block", "a", "--on", "lock:db", "--on", "deploy:x@dev")
        self.assertNotIn("db", self.snap()["locks"])
        self.foundry("unit", "block", "b", "--on", "lock:db")
        self.assertEqual(self.snap()["locks"]["db"]["unit"], "b")


class Next(GraphBase):
    def setUp(self):
        super().setUp()
        self.new("old", at="2026-11-17T08:00:00+09:00")
        self.new("young", at="2026-11-18T08:00:00+09:00")
        self.new("hub", at="2026-11-18T09:00:00+09:00")
        self.new("p1", at="2026-11-18T09:30:00+09:00")
        self.new("p2", at="2026-11-18T09:40:00+09:00")
        for d in ("d1", "d2"):
            self.new(d)
            self.foundry("unit", "block", d, "--on", "unit:hub")
        self.foundry("unit", "set", "p1", "--priority", "2", "--by", "owner")
        self.foundry("unit", "set", "p2", "--priority", "1", "--by", "foreman")

    def ids(self):
        _, out = self.foundry("next", "--json")
        return [x["unit"] for x in out]

    def test_order_priority_then_downstream_then_age(self):
        self.assertEqual(self.ids(), ["p2", "p1", "hub", "old", "young"])
        _, out = self.foundry("next", "--json")
        self.assertIn("unblocks 2", out[2]["why"])

    def test_cut_by_concurrency_and_pressure(self):
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"concurrency_max": 3}, f)
        self.record("session.launch", name="lead-x", unit=None, role="line-lead", bg_id="b1", cwd="/x")
        self.assertEqual(self.ids(), ["p2", "p1"])
        self.record("pressure", level="warn", reasons=["load"])
        self.assertEqual(self.ids(), ["p2"])
        self.record("pressure", level="danger", reasons=["disk"])
        self.assertEqual(self.ids(), [])

    def test_halt_and_status_exclude(self):
        self.foundry("unit", "set", "p2", "--status", "parked", "--by", "owner")
        self.foundry("halt", "--repo", "r2", "--reason", "x", "--by", "owner")
        self.assertEqual(self.ids(), ["p1", "hub", "old", "young"])
        self.foundry("halt", "--repo", "r1", "--reason", "broken main", "--by", "owner")
        self.assertEqual(self.ids(), [])


class VerifyAndScope(GraphBase):
    def setUp(self):
        super().setUp()
        self.new("a")
        self.new("b")

    def test_done_refused_without_verify_after_last_change(self):
        lead = self.as_lead("a")
        rc, _, err = self.run_f("unit", "set", "a", "--status", "done", "--by", "lead", env=lead)
        self.assertEqual(rc, 2)
        self.assertIn("done needs evidence foundry records itself", err)
        self.assertEqual(self.run_f("unit", "status", "a", "done", env=lead)[0], 2)  # the alias gates too
        foreman = {"FOUNDRY_ROLE": "foreman"}
        self.assertEqual(self.run_f("unit", "verify", "a", "--evidence", "pytest green", env=foreman)[0], 0)
        self.set_now("2026-11-18T10:05:00+09:00")
        self.record("branch.pushed", unit="a", branch="foundry/a")
        self.assertEqual(self.run_f("unit", "set", "a", "--status", "done", "--by", "lead", env=lead)[0], 2)
        self.set_now("2026-11-18T10:10:00+09:00")
        self.assertEqual(self.run_f("unit", "verify", "a", "--evidence", "re-ran", env=foreman)[0], 0)
        self.assertEqual(self.run_f("unit", "status", "a", "done", env=lead)[0], 0)
        self.assertEqual(self.unit("a")["status"], "done")

    def test_owner_override_with_inbox_note_for_live_lead(self):
        self.start_lead("a")
        _, out = self.foundry("unit", "set", "a", "--status", "done", "--by", "owner", "--note", "shipped by hand")
        self.assertEqual(out["status"], "done")
        self.assertIn("owner.override", [r["op"] for r in self.journal()])
        inbox = self.unit("a")["inbox"]
        self.assertTrue(any("Owner override" in i["text"] and "shipped by hand" in i["text"] for i in inbox))

    def test_foreman_scope(self):
        f = ("--by", "foreman")
        self.assertEqual(self.run_f("unit", "set", "a", "--status", "done", *f)[0], 2)
        self.assertEqual(self.run_f("unit", "set", "a", "--status", "in_progress", *f)[0], 2)
        self.assertEqual(self.run_f("unit", "set", "a", "--status", "cancelled", *f)[0], 2)
        self.assertEqual(self.run_f("unit", "set", "a", "--status", "parked", *f)[0], 0)
        snap = self.snap()
        intake = {"id": "i", "status": "queue", "intake": {"tier": 1, "kind": "x", "source": "slack"}}
        self.assertIsNone(graph.scope_refusal(snap, "foreman", intake, "cancelled", None))
        self.assertIsNotNone(graph.scope_refusal(snap, "foreman", dict(intake, status="in_progress"), "cancelled", None))

    def test_lead_scope(self):
        lead = self.as_lead("a")
        self.assertEqual(self.run_f("unit", "set", "b", "--status", "parked", "--by", "lead", env=lead)[0], 2)
        self.assertEqual(self.run_f("unit", "set", "a", "--status", "cancelled", "--by", "lead", env=lead)[0], 2)
        self.assertEqual(self.run_f("unit", "set", "a", "--priority", "1", "--by", "lead", env=lead)[0], 2)
        rc, _, err = self.run_f("unit", "set", "a", "--status", "done", "--by", "owner", env=lead)
        self.assertEqual(rc, 2)
        self.assertIn("FOUNDRY_ROLE", err)
        self.assertEqual(self.run_f("unit", "set", "a", "--status", "blocked", "--by", "lead", env=lead)[0], 0)
        self.assertEqual(self.run_f("unit", "status", "a", "waiting_owner", env=lead)[0], 0)
        # Without FOUNDRY_UNIT, the lead's unit is the one whose live session has its role.
        self.start_lead("b")
        no_unit = {"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": ""}
        self.assertEqual(self.run_f("unit", "set", "b", "--status", "parked", "--by", "lead", env=no_unit)[0], 0)
        self.assertEqual(self.run_f("unit", "set", "a", "--status", "parked", "--by", "lead", env=no_unit)[0], 2)

    def test_hands_and_staff_refused(self):
        for by in ("hand", "dispatcher", "sweeper-judge"):
            self.assertEqual(self.run_f("unit", "set", "a", "--status", "parked", "--by", by)[0], 2)


class Andon(GraphBase):
    def setUp(self):
        super().setUp()
        self.new("a", repo="r1")
        self.new("b", repo="r2")

    def test_global_halt_only_owner_lifts(self):
        self.foundry("halt", "--reason", "prod incident", "--by", "foreman")
        self.assertIn("line halted by foreman", graph.launch_block(self.snap(), "a"))
        rc, out, _ = self.run_f("unit", "launch", "a", "--cwd", self.dir)
        self.assertEqual(rc, 6)
        self.assertIn("halted", json.loads(out)["held"]["reason"])
        self.assertIn("launch.held", [r["op"] for r in self.journal()])
        self.assertEqual(self.run_f("resume-line", "--by", "foreman")[0], 2)
        self.assertEqual(self.run_f("resume-line", "--by", "lead", env=self.as_lead("a"))[0], 2)
        self.foundry("resume-line", "--by", "owner")
        self.assertEqual(self.snap()["halts"], [])
        self.assertIsNone(graph.launch_block(self.snap(), "a"))

    def test_repo_halt_by_its_lead(self):
        lead = self.as_lead("a")
        self.assertEqual(self.run_f("halt", "--repo", "r2", "--reason", "x", "--by", "lead", env=lead)[0], 2)
        self.assertEqual(self.run_f("halt", "--repo", "r1", "--reason", "main red", "--by", "lead", env=lead)[0], 0)
        snap = self.snap()
        self.assertIsNotNone(graph.launch_block(snap, "a"))
        self.assertIsNone(graph.launch_block(snap, "b"))
        self.assertEqual(self.run_f("resume-line", "--repo", "r1", "--by", "lead", env=self.as_lead("b"))[0], 2)
        self.assertEqual(self.run_f("resume-line", "--repo", "r1", "--by", "lead", env=lead)[0], 0)
        self.assertEqual(self.run_f("halt", "--reason", "x", "--by", "hand")[0], 2)


class Coordination(GraphBase):
    def test_main_moved_notes_live_units_on_that_repo(self):
        self.new("a", repo="r1")
        self.new("b", repo="r1")
        self.new("c", repo="r2")
        self.start_lead("a", sid="SA")
        self.start_lead("c", sid="SC")
        _, out = self.foundry("repo", "main-moved", "r1", "--sha", "abc123")
        self.assertEqual(out["noted"], ["a"])
        self.assertIn("abc123", self.unit("a")["inbox"][-1]["text"])
        self.assertEqual(self.unit("b")["inbox"], [])
        self.assertEqual(self.unit("c")["inbox"], [])

    def test_unit_note(self):
        self.new("a")
        self.foundry("unit", "note", "a", "--text", "boss: rebase on main", "--by", "shop-boss")
        self.assertEqual(self.unit("a")["inbox"][-1]["text"], "boss: rebase on main")
        self.assertIn("unit.note", [r["op"] for r in self.journal()])


class BoardAndReplay(GraphBase):
    def test_board_rows_mismatch_and_chain(self):
        self.new("a")
        self.new("b")
        self.foundry("unit", "block", "a", "--on", "unit:b")
        self.foundry("unit", "set", "b", "--status", "in_progress", "--by", "owner")
        self.foundry("halt", "--repo", "r9", "--reason", "x", "--by", "owner")
        _, m = self.foundry("board", "--format", "json")
        rows = {r["id"]: r for s in m["sections"] for r in s["rows"]}
        self.assertEqual(rows["a"]["blocked_by"], ["unit:b"])
        self.assertEqual(rows["a"]["chain"], {"up": ["b"], "down": []})
        self.assertEqual(rows["b"]["chain"], {"up": [], "down": ["a"]})
        self.assertIn("세션 없음", rows["b"]["mismatch"])  # renderers add the "상태 불일치" label
        self.assertIsNone(rows["a"]["mismatch"])
        self.assertEqual(m["halts"][0]["scope"], "r9")
        self.assertEqual(m["next"], [])

    def test_reboot_replay_gives_the_same_graph(self):
        for uid in ("a", "b", "c"):
            self.new(uid)
        self.foundry("unit", "block", "a", "--on", "unit:b", "--on", "lock:db")
        self.foundry("unit", "block", "c", "--on", "lock:db")
        self.foundry("unit", "block", "b", "--on", "time:2026-11-19T00:00:00+09:00")
        with open(os.path.join(self.dir, "snapshot.json")) as f:
            cached = json.load(f)
        os.remove(os.path.join(self.dir, "snapshot.json"))
        self.foundry("compact")
        replayed = self.snap()
        for uid in ("a", "b", "c"):
            self.assertEqual(graph.chain(cached, uid), graph.chain(replayed, uid))
            self.assertEqual(graph.launch_block(cached, uid), graph.launch_block(replayed, uid))
        self.assertEqual(cached["locks"], replayed["locks"])
        self.assertEqual(graph.resolve(replayed, graph.cl.parse_ts("2026-11-18T10:00:00+09:00")), [])
        self.set_now("2026-11-19T00:00:00+09:00")
        self.foundry("graph", "resolve")
        self.foundry("unit", "set", "b", "--status", "done", "--by", "owner")
        self.foundry("lock", "release", "db", "--unit", "c")
        self.assertEqual(self.unit("a")["blocked_by"], [])
