"""0.1.46: the 오너 대기 section is summary.owner_wait's own union on every surface; a stopped lead resumes under its
own `claude agents` row; `foundry sweep` removes finished lead/boss rows of done units and superseded sessions."""

import datetime
import json
import os
import subprocess
import sys

import test_foundry as tf
import test_launcher as tl

sys.path.insert(0, tf.SCRIPTS)
import board  # noqa: E402

NOW = datetime.datetime.fromisoformat("2026-11-18T10:00:00+09:00").timestamp()


def ms(minutes_ago):
    return int((NOW - minutes_ago * 60) * 1000)


class OwnerSection(tf.Base):
    def setUp(self):
        super().setUp()
        for u in ("bare", "carded", "todo", "asked", "busy"):
            self.foundry("unit", "new", "--id", u, "--title", u.upper())
        for u in ("bare", "carded", "todo", "asked"):
            self.foundry("unit", "status", u, "waiting_owner")
        self.foundry("decide", "open", "--question", "Which DB?", "--option", "A:Postgres", "--option", "B:SQLite",
                     "--recommend", "B", "--reason", "no ops", "--asked-by", "lead", "--key", "db", "--unit", "carded")
        self.foundry("decision", "ask", "--key", "asked:merge", "--unit", "asked", "--question", "Merge today?", ok=(10,))
        self.foundry("owner", "todo", "add", "--unit", "todo", "--text", "sops secret 등록", "--kind", "secret", "--why-owner", "secret 값은 오너만 안다",
                     "--by", "foreman")
        self.foundry("owner", "todo", "add", "--none", "--text", "정리 제안 검토", "--by", "foreman",
                     "--why-owner", "무엇을 지울지는 오너의 선택")

    def board(self, fmt):
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), fmt], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if fmt == "json" else r.stdout

    def test_header_count_is_owner_wait_and_every_row_is_backed_by_the_union(self):
        m = self.board("json")
        sec = next(s for s in m["sections"] if s["key"] == "waiting_owner")
        self.assertEqual(sec["label"], "오너 대기")
        self.assertEqual(sec["count"], m["summary"]["owner_wait"])
        self.assertEqual(len(sec["items"]), m["summary"]["owner_wait"])
        self.assertEqual(m["summary"]["counts"]["waiting_owner"], m["summary"]["owner_wait"])
        todos = {t["tid"] for t in m["owner_todos"]}
        waiting = {r["id"] for r in sec["rows"]}
        for x in sec["items"]:
            backing = {"todo": todos, "unit": waiting}[x["kind"]]  # 0.1.88: decisions are 결정 필요's alone
            self.assertIn(x["ref"], backing, x)
        by_unit = {x["unit"]: x["state"] for x in sec["items"]}
        self.assertNotIn("carded", by_unit)
        self.assertNotIn("asked", by_unit)
        self.assertEqual(by_unit["todo"], "할 일: sops secret 등록")
        self.assertEqual(by_unit["bare"], "오너 확인")
        self.assertEqual(by_unit[None], "할 일: 정리 제안 검토")
        self.assertNotIn("busy", by_unit)
        self.assertEqual(m["summary"]["owner_wait"], 3)

    def test_a_waiting_unit_with_a_card_or_todo_is_not_flagged_without_a_target(self):
        rows = {r["id"]: r for s in self.board("json")["sections"] for r in s["rows"]}
        self.assertIsNone(rows["carded"]["unwatched"])
        self.assertIsNone(rows["todo"]["unwatched"])
        self.assertEqual(rows["bare"]["unwatched"], "결정 대상 없음")

    def test_text_and_html_headers_match(self):
        text = self.board("text")
        self.assertIn("## 오너 대기 (3)", text)
        self.assertIn("오너 대기 3", text.splitlines()[1])
        self.assertIn("할 일: sops secret 등록", text)
        with open(self.board("html").strip(), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("<h2>오너 대기 <span>3</span></h2>", html)
        self.assertNotIn("결정: asked:merge", html)  # 0.1.88: in 결정 필요 only

    def test_band_has_one_owner_count(self):
        m = self.board("json")
        line = board.band(m)
        self.assertEqual(line.count("오너 대기"), 1)


class ResumeInPlace(tl.Launcher):
    def started(self):
        self.claude({"bg_id": "a1", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-alpha", "status": "busy"}]})
        self.foundry("unit", "launch", "alpha")
        self.foundry("unit", "stop", "alpha")

    def test_stopped_lead_resumes_under_its_own_row(self):
        self.started()
        self.claude({"bg_id": "a1", "agents": [{"id": "a1", "name": "lead-alpha", "sessionId": "S-alpha", "state": "done"}]})
        _, out = self.foundry("unit", "resume", "alpha")
        self.assertTrue(out["in_place"])
        self.assertEqual(self.calls()[-3]["argv"], ["stop", "a1"])  # the lingering process first
        self.assertEqual(self.bg_calls()[-1]["argv"], ["--bg", "--resume", "S-alpha", tl.GOAL.format(u="alpha", n=200)])
        sess = self.snap()["sessions"]["lead-alpha"]
        self.assertEqual((sess["bg_id"], sess["session_id"], sess["launches"]), ("a1", "S-alpha", 2))
        self.assertFalse([c for c in self.calls() if c["argv"][:1] == ["rm"]])

    def test_a_copy_is_removed_and_the_launch_flags_path_follows(self):
        self.started()
        self.claude({"bg_id": "a2", "resume_id": "c9",
                     "agents": [{"id": "a1", "name": "lead-alpha", "sessionId": "S-alpha", "state": "stopped"}]})
        _, out = self.foundry("unit", "resume", "alpha")
        argvs = [c["argv"] for c in self.calls()]
        self.assertEqual(argvs.count(["stop", "a1"]), 1)  # only `unit stop`: the row was already stopped
        self.assertIn(["stop", "c9"], argvs)
        self.assertIn(["rm", "c9"], argvs)
        last = self.bg_calls()[-1]["argv"]
        self.assertEqual(last[:3], ["--bg", "--resume", "S-alpha"])
        self.assertIn("-n", last)
        self.assertNotIn("in_place", out)
        self.assertEqual(self.snap()["sessions"]["lead-alpha"]["bg_id"], "a2")


class SweepAgents(tl.Launcher):
    def setUp(self):
        super().setUp()
        self.claude({"bg_id": "a1", "agents": [{"name": "lead-alpha", "kind": "bg", "sessionId": "S-alpha", "status": "busy"}]})
        self.foundry("unit", "launch", "alpha")
        self.claude({"bg_id": "b1", "agents": [{"name": "boss-beta", "kind": "bg", "sessionId": "S-beta", "status": "busy"}]})
        self.foundry("unit", "launch", "beta")
        self.foundry("unit", "status", "beta", "done")
        snap = self.snap()
        self.a_cwd = snap["sessions"]["lead-alpha"]["cwd"]
        self.b_cwd = snap["sessions"]["boss-beta"]["cwd"]

    def rows(self):
        a, b = self.a_cwd, self.b_cwd
        return [
            {"id": "a0", "name": "lead-alpha", "state": "done", "cwd": a, "startedAt": ms(300), "sessionId": "S-old"},
            {"id": "a1", "name": "lead-alpha", "state": "done", "cwd": a, "startedAt": ms(200), "sessionId": "S-alpha"},
            {"id": "a5", "name": "lead-alpha", "state": "working", "cwd": a, "startedAt": ms(250)},
            {"id": "a6", "name": "lead-alpha", "state": "stopped", "cwd": a, "startedAt": ms(5)},
            {"id": "a7", "name": "lead-alpha", "state": "done", "cwd": "/elsewhere", "startedAt": ms(400)},
            {"id": "a8", "name": "lead-alpha", "state": "done", "cwd": a, "startedAt": ms(350), "worktree": "/w"},
            {"id": "b1", "name": "boss-beta", "state": "stopped", "cwd": b, "startedAt": ms(90)},
            {"id": "b2", "name": "boss-beta", "state": "done", "cwd": b, "startedAt": ms(10)},
            {"id": "x1", "name": "lead-zzz", "state": "done", "cwd": a, "startedAt": ms(900)},
            {"id": "o1", "name": "orchestrator", "state": "done", "cwd": a, "startedAt": ms(900)},
        ]

    def rm_calls(self):
        return [c["argv"][1] for c in self.calls() if c["argv"][:1] == ["rm"]]

    def test_removes_superseded_and_done_unit_rows_only(self):
        self.claude({"agents": self.rows()})
        _, out = self.foundry("sweep", "agents", "--dry-run")
        # 0.1.132: a7 (another cwd: the worktree moved) goes too; the name is what makes a row foundry's
        self.assertEqual(sorted(r["id"] for r in out["would_remove"]), ["a0", "a7", "b1"])
        self.assertEqual(self.rm_calls(), [])
        _, out = self.foundry("sweep", "agents")
        self.assertEqual(sorted(self.rm_calls()), ["a0", "a7", "b1"])
        reasons = {r["id"]: r["reason"] for r in out["removed"]}
        self.assertEqual(reasons, {"a0": "superseded by a newer session of the unit",
                                   "a7": "superseded by a newer session of the unit", "b1": "unit done"})
        pruned = [r for r in self.journal() if r["op"] == "session.pruned"]
        self.assertEqual(sorted(r["ids"][0] for r in pruned), ["a0", "a7", "b1"])

    def test_ttl_comes_from_prefs(self):
        with open(os.path.join(self.dir, "prefs.json"), "w") as f:
            json.dump({"sweep": {"agent_session_ttl_minutes": 120}}, f)
        self.claude({"agents": self.rows()})
        _, out = self.foundry("sweep", "agents", "--dry-run")
        self.assertEqual([r["id"] for r in out["would_remove"]], ["a0", "a7"])

    def test_a_failed_listing_removes_nothing(self):
        self.env["FOUNDRY_CLAUDE"] = "false"
        _, out = self.foundry("sweep", "agents")
        self.assertIn("error", out)

    def test_sweep_run_includes_it(self):
        self.claude({"agents": self.rows()})
        _, out = self.foundry("sweep", "run", "--dry-run")
        self.assertEqual(sorted(r["id"] for r in out["agent_sessions"]["would_remove"]), ["a0", "a7", "b1"])


for _cls in (ResumeInPlace, SweepAgents):  # the launcher fixture, not its tests
    for _name in dir(tl.Launcher):
        if _name.startswith("test_"):
            setattr(_cls, _name, None)
