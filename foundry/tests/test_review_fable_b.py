"""Regression tests for the fable review, CLI/state layer: findings 3, 7, 11, 12, 13, 14, 15, 16, 19."""

import json
import os
import subprocess
import sys
import unittest.mock as mock

from test_foundry import ROOT, SCRIPTS, Base

sys.path.insert(0, SCRIPTS)

import foundrylib as cl  # noqa: E402

FAKE_CLAUDE = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_claude.py')}"
SESSION_ENV = ("FOUNDRY_ROLE", "FOUNDRY_UNIT", "FOUNDRY_SESSION", "CLAUDE_CODE_SESSION_ID")


class Review(Base):
    def setUp(self):
        super().setUp()
        for k in SESSION_ENV:
            self.env.pop(k, None)
        self.env["CLAUDE_CONFIG_DIR"] = self.dir
        self._keep = {k: os.environ.get(k) for k in (*SESSION_ENV, "FOUNDRY_STATE_DIR", "FOUNDRY_NOW", "CLAUDE_CONFIG_DIR")}
        for k in SESSION_ENV:
            os.environ.pop(k, None)
        os.environ.update({"FOUNDRY_STATE_DIR": self.dir, "FOUNDRY_NOW": self.env["FOUNDRY_NOW"],
                           "CLAUDE_CONFIG_DIR": self.dir})

    def tearDown(self):
        for k, v in self._keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def run_f(self, *args, env=None):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args], env={**self.env, **(env or {})},
                           capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def ops(self, op):
        return [r for r in cl.read_journal() if r.get("op") == op]


class OwnerOnly(Review):
    """3: owner-only acts refuse every foundry session; the board (no FOUNDRY_ROLE, no session) passes."""
    LEAD = {"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": "a"}
    FOREMAN = {"FOUNDRY_ROLE": "foreman"}

    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "a", "--title", "A")
        self.foundry("unit", "new", "--id", "b", "--title", "B")

    def refused(self, env, *args):
        rc, out, err = self.run_f(*args, env=env)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("refused", err + out)

    def test_review_3_owner_acts_refuse_foundry_sessions(self):
        self.refused(self.LEAD, "send", "allow-now", "--channel", "C1", "--reason", "x")
        self.refused({"FOUNDRY_SESSION": "1"}, "prefs", "set", "slack_quiet", "12:00-12:01")
        self.refused(self.FOREMAN, "intake", "promote", "a")
        self.refused({"FOUNDRY_ROLE": ""}, "unit", "set", "a", "--status", "done")  # emptied role is not the owner
        self.refused({"CLAUDE_CODE_SESSION_ID": "S1"}, "unit", "set", "a", "--status", "cancelled")  # the foreman's id
        self.foundry("decision", "ask", "--key", "k1", "--question", "q?", ok=(10,))
        self.refused(self.FOREMAN, "decision", "answer", "--key", "k1", "--answer", "yes")
        self.assertEqual(self.ops("send.override") + self.ops("prefs.changed") + self.ops("decision.answer"), [])

    def test_review_3_decision_unblock_lock_release_and_global_resume(self):
        _, card = self.foundry("decide", "open", "--unit", "a", "--question", "q?", "--option", "A", "--option", "B",
                               "--asked-by", "lead", "--key", "dk")
        cid = card["card"]["id"]
        self.refused(self.LEAD, "unit", "unblock", "a", "--ref", f"decision:{cid}")
        self.foundry("lock", "take", "L", "--unit", "b")
        self.refused(self.LEAD, "lock", "release", "L", "--unit", "b")  # another unit's lock
        self.foundry("halt", "--reason", "x")
        self.refused(self.FOREMAN, "resume-line")
        self.refused(self.FOREMAN, "decide", "answer", cid, "A", "--by", "owner")
        # The foreman relays the owner's chat answer, recorded as a relay.
        rc, out, err = self.run_f("decide", "chat", f"{cid} A", "--by", "foreman", env=self.FOREMAN)
        self.assertEqual(rc, 0, err)
        card = self.snap()["cards"][cid]
        self.assertEqual((card["answered_by"], card["answer_note"]), ("foreman", "relayed by the foreman from the owner's chat answer"))
        # The owner, outside foundry (a terminal or the board pane), still does all of it.
        self.foundry("lock", "release", "L", "--unit", "b")
        self.foundry("resume-line")

    def test_review_3_approve_by_owner_and_board_buttons(self):
        self.record("approval.request", rid="ap-1", key="k", session="SL", unit="a", role="lead", tool="Bash",
                    summary="x", tier=3)
        self.refused(self.FOREMAN, "approve", "allow", "ap-1", "--by", "owner")
        # Board buttons: `$.process.run` with no FOUNDRY_ROLE and no session id.
        self.foundry("approve", "allow", "ap-1", "--by", "owner")
        self.foundry("unit", "set", "a", "--status", "parked", "--by", "owner")
        self.assertEqual(self.snap()["approvals"]["ap-1"]["by"], "owner")

    def test_review_3_install_starts_the_foreman_with_its_role(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import firstrun
        import foundrylib as cl
        self.assertIn('"env":{"FOUNDRY_ROLE":"foreman"', firstrun.foreman_command(cl.load_config()))
        with open(os.path.join(ROOT, "install.sh")) as f:  # install.sh prints that line
            self.assertIn('foundry.py" foreman line', f.read())


class UnitIds(Review):
    """7: unit ids become paths; traversal is refused everywhere an id is minted or used as a path."""
    BAD = "../../../../.claude/CLAUDE"

    def test_review_7_traversal_ids_are_refused(self):
        rc, out, _ = self.run_f("unit", "new", "--id", self.BAD, "--title", "x")
        self.assertEqual(rc, 2)
        self.assertIn("invalid unit id", out)
        rc, out, _ = self.run_f("inbox", "route", "--text", "hello", "--new", "a/b")
        self.assertEqual(rc, 2)
        rc, _, err = self.run_f("handoff", "write", self.BAD, "--goal", "pwn")
        self.assertEqual(rc, 2)
        self.assertEqual(self.ops("unit.new"), [])
        for uid in ("a..b", "_x", "a" * 65, ""):
            self.assertIsNotNone(cl.unit_id_error(uid), uid)
        self.assertIsNone(cl.unit_id_error("jira-proj-123.v2"))
        import handoff
        import intake
        with self.assertRaises(ValueError):
            handoff.save(cl.load(), "../x", "write")
        self.assertIsNone(cl.unit_id_error(intake.unit_id_for("github", "../../o/r#1")))


class Replay(Review):
    """11: one bad record or a broken config.json no longer bricks every reader."""

    def test_review_11_bad_records_are_skipped_and_logged(self):
        self.foundry("unit", "new", "--id", "a", "--title", "A")
        with open(os.path.join(self.dir, "journal.jsonl"), "a") as f:
            f.write(json.dumps({"ts": "2026-11-18T10:00:00+09:00", "op": "unit.status"}) + "\n")
            f.write(json.dumps({"ts": "2026-11-18T10:00:00+09:00", "op": "todo.replace", "todos": 5}) + "\n")
        os.remove(os.path.join(self.dir, "snapshot.json"))
        self.assertIn("a", cl.load()["units"])
        _, out = self.foundry("unit", "list")
        self.assertIn("a", out)
        with open(os.path.join(self.dir, "replay.errors")) as f:
            errs = json.load(f)
        self.assertEqual(sorted(e["op"] for e in errs.values()), ["todo.replace", "unit.status"])
        self.foundry("unit", "new", "--id", "b", "--title", "B")  # replays again: logged once, still bounded
        with open(os.path.join(self.dir, "replay.errors")) as f:
            self.assertEqual(len(json.load(f)), 2)

    def test_review_11_broken_config_falls_back_to_defaults(self):
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            f.write("{not json")
        cfg = cl.load_config()
        self.assertEqual(cfg["max_concurrent"], 8)
        self.assertIn("config.json", cfg["config_error"])
        self.foundry("unit", "new", "--id", "a", "--title", "A")


class Reservations(Review):
    """12: a launch reservation whose launcher died stops holding a slot after 5 minutes."""

    def test_review_12_stale_reservation_is_released(self):
        import graph
        import launcher
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump({"max_concurrent": 1}, f)
        self.foundry("unit", "new", "--id", "a", "--title", "A", "--repo", "r1")
        cl.append("session.reserve", name="lead-b", unit="b", role="line-lead")  # killed before launch/release
        cfg = cl.load_config()
        os.environ["FOUNDRY_NOW"] = "2026-11-18T10:04:00+09:00"
        self.assertIn("cap", launcher.held_reason(cl.load(), cfg))
        os.environ["FOUNDRY_NOW"] = "2026-11-18T10:06:00+09:00"
        snap = cl.load()
        self.assertIsNone(launcher.held_reason(snap, cfg))
        self.assertEqual([r["unit"] for r in graph.next_units(snap, {"concurrency_max": 1})], ["a"])
        nxt = graph.next_units(snap, {"concurrency_max": 1})[0]
        self.assertEqual((nxt["priority"], bool(nxt["created"])), (None, True))  # the board's 우선순위 and 경과
        changes, recs = launcher.changes_for(snap, [])
        self.assertEqual([(c["name"], c["to"]) for c in changes], [("lead-b", "lost")])
        cl.append_many(recs)
        self.assertEqual(cl.load()["sessions"]["lead-b"]["state"], "lost")


class Trust(Review):
    """13: the trust writer survives a concurrent .claude.json write and compaction keeps its keys."""

    def setUp(self):
        super().setUp()
        self.cfg_file = os.path.join(self.dir, ".claude.json")
        with open(self.cfg_file, "w") as f:
            json.dump({"projects": {"/keep": {"x": 1}}}, f)
        self.wts = [os.path.realpath(os.path.join(self.dir, n)) for n in ("wt1", "wt2")]
        for p in self.wts:
            os.makedirs(p)

    def projects(self):
        with open(self.cfg_file) as f:
            return json.load(f)["projects"]

    def test_review_13_concurrent_write_retry_and_snapshot_map(self):
        import trust
        self.assertEqual(trust.register(self.wts[0], "a"), "registered")
        cl.compact()  # the journal is now one snapshot line
        self.assertTrue(trust.registered_by_us(self.wts[0]))
        real_backup, hits = trust._backup, []

        def claude_code_writes_meanwhile(f):
            if not hits:
                hits.append(1)
                data = json.load(open(f))
                data["projects"]["/cc"] = {"hasTrustDialogAccepted": True, "added": "by claude code"}
                with open(f, "w") as fh:
                    json.dump(data, fh)
            return real_backup(f)

        with mock.patch.object(trust, "_backup", claude_code_writes_meanwhile):
            self.assertEqual(trust.register(self.wts[1], "b"), "registered")
        self.assertEqual(set(self.projects()), {"/keep", "/cc", *self.wts})  # nothing reverted, our key kept
        self.assertEqual(sorted(trust.unregister_all()), sorted(self.wts))
        self.assertEqual(set(self.projects()), {"/keep", "/cc"})
        self.assertEqual(cl.load()["trust"], {})

    def test_review_13_a_file_that_never_settles_fails_closed(self):
        import itertools
        import trust
        stamps = itertools.count()
        with mock.patch.object(trust, "_stamp", lambda f: next(stamps)):
            self.assertEqual(trust.register(self.wts[0], "a"), "failed")
        self.assertNotIn(self.wts[0], self.projects())
        self.assertEqual(self.ops("trust.register"), [])


class PressureVocabulary(Review):
    """16: one ok|warn|danger vocabulary; `foundry next` and the launcher's hold agree."""

    def test_review_16_warn_allows_one_launch_danger_none(self):
        import graph
        import launcher
        for u in ("a", "b"):
            self.foundry("unit", "new", "--id", u, "--title", u.upper(), "--repo", "r1")
        cfg, p = cl.load_config(), {"concurrency_max": 8}
        cl.append("pressure", level="warn", reasons=["load: 1.5 per CPU"])
        snap = cl.load()
        self.assertEqual(len(graph.next_units(snap, p)), 1)
        self.assertIsNone(launcher.held_reason(snap, cfg))
        os.environ["FOUNDRY_NOW"] = "2026-11-18T10:01:00+09:00"
        cl.append("session.launch", name="lead-a", unit="a", role="line-lead", bg_id="bg1", cwd="/x")
        snap = cl.load()
        self.assertEqual(graph.next_units(snap, p), [])
        self.assertTrue(launcher.held_reason(snap, cfg).startswith("pressure warn"))
        cl.append("pressure", level="high", reasons=["legacy"])
        self.assertEqual(cl.pressure_level(cl.load()), "danger")
        self.assertEqual(cl.pressure_room(cl.load()), 0)

    def test_review_16_sweep_uses_the_watchers_band(self):
        env = {"FOUNDRY_PRESSURE_FAKE": json.dumps({"disk_free_gb": {"/": 5}, "mem_free_pct": 50, "load_per_cpu": 0.1})}
        rc, out, err = self.run_f("sweep", "pressure", env=env)
        self.assertEqual(rc, 0, err)
        self.assertEqual((json.loads(out)["level"], json.loads(out)["reasons"]), ("danger", ["disk: 5 GB free"]))
        env["FOUNDRY_PRESSURE_FAKE"] = json.dumps({"disk_free_gb": {"/": 40}, "mem_free_pct": 50, "load_per_cpu": 0.1})
        self.run_f("sweep", "pressure", env=env)
        self.assertEqual([r["level"] for r in self.ops("pressure")], ["danger", "warn"])


class WaitUntil(Review):
    """19: `wait add --until` takes only a timestamp parse_ts reads."""

    def test_review_19_until_is_validated(self):
        self.foundry("unit", "new", "--id", "a", "--title", "A")
        rc, _, err = self.run_f("wait", "add", "a", "--kind", "time", "--until", "tomorrow 9am")
        self.assertEqual(rc, 2)
        self.assertIn("not an ISO timestamp", err)
        self.assertEqual(self.ops("wait.add"), [])
        rc, _, err = self.run_f("wait", "add", "a", "--kind", "time", "--until", "2026-11-19T09:00:00+09:00")
        self.assertEqual(rc, 0, err)


from test_watchers import API_LOOP_300S, Watchers  # noqa: E402


class StallRelaunch(Watchers):
    """14: a stall relaunch really stops and resumes the lead; a failed one is journaled as such and wakes the foreman."""

    def fake_claude(self):
        self.log = os.path.join(self.dir, "claude.jsonl")
        self.env.update({"FOUNDRY_CLAUDE": FAKE_CLAUDE, "FAKE_CLAUDE_LOG": self.log,
                         "FAKE_CLAUDE_DATA": os.path.join(self.dir, "claude.json")})
        wt = os.path.join(self.dir, "wt-alpha")
        os.makedirs(wt)
        self.record("unit.worktree", id="alpha", path=wt, repo=None, branch=None)

    def test_review_14_relaunch_happens_before_it_is_journaled(self):
        self.fake_claude()
        self.transcript(API_LOOP_300S)
        self.stream("stalls")
        self.assertEqual([r["action"] for r in self.ops("stall.recover")], ["relaunch"])
        with open(self.log) as f:
            argvs = [json.loads(line)["argv"] for line in f]
        self.assertIn(["stop", "bgSL"], argvs)
        self.assertTrue(any("--resume" in a and "SL" in a for a in argvs), argvs)
        snap = self.snap()
        self.assertEqual(snap["sessions"]["lead-alpha"]["launches"], 2)
        self.assertTrue(any("relaunched after a stall" in i["text"] for i in snap["units"]["alpha"]["inbox"]))

    def test_review_14_failed_relaunch_is_not_recorded_as_done(self):
        self.transcript(API_LOOP_300S)  # FOUNDRY_CLAUDE=false: `claude stop` fails
        lines = self.stream("stalls")
        self.assertTrue(any("relaunch failed" in x for x in lines), lines)
        self.assertEqual([r["action"] for r in self.ops("stall.recover")], ["relaunch_failed"])
        self.assertEqual([e["kind"] for e in self.snap()["events"] if not e["consumed"]][-1:], ["stall"])
        self.stream("stalls")  # one failed attempt per episode, no loop
        self.assertEqual(len(self.ops("stall.recover")), 1)


class GithubSeenOrder(Watchers):
    """15: watch-github.json is written only after the pr.state records are in the journal."""

    def test_review_15_seen_map_follows_the_append(self):
        import prefs
        import watch
        import watchers
        self.foundry("unit", "new", "--id", "p1", "--title", "P", "--pr", "o/r#1")
        keep = {k: os.environ.get(k) for k in ("FOUNDRY_STATE_DIR", "FOUNDRY_NOW")}
        os.environ.update({"FOUNDRY_STATE_DIR": self.dir, "FOUNDRY_NOW": "2026-11-18T10:00:00+09:00"})
        seen = os.path.join(self.dir, "watch-github.json")
        pr = {"state": "MERGED", "reviewDecision": None, "statusCheckRollup": [], "headRefOid": "abc1234"}
        try:
            import review
            with mock.patch.object(watch, "gh_pr", return_value=pr), \
                 mock.patch.object(watch, "poll_waits", return_value=([], [])), \
                 mock.patch.object(review, "refresh_others", return_value={}):
                with mock.patch.object(cl, "append_many", side_effect=OSError("stream killed")):
                    with self.assertRaises(OSError):
                        watchers.run_cycle("github", prefs.load())
                self.assertFalse(os.path.exists(seen))
                watchers.run_cycle("github", prefs.load())
            self.assertEqual([r["state"] for r in self.ops("pr.state")], ["MERGED"])
            with open(seen) as f:
                self.assertIn("o/r#1", json.load(f))
        finally:
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
