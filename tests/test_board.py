"""Tests for the board model the mod pane reads. Run: python3 -m unittest discover -s tests (from the plugin root)."""

import datetime as dt
import json
import os
import subprocess
import sys
import unittest
import unittest.mock

from test_foundry import SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import board  # noqa: E402
import foundrylib  # noqa: E402
cl = foundrylib

JOURNAL = [
    {"ts": "2026-11-18T09:00:00+09:00", "op": "unit.new", "id": "alpha", "title": "Alpha", "ticket": ["PROJ-1"], "pr": ["example-org/x#1"]},
    {"ts": "2026-11-18T09:01:00+09:00", "op": "session.launch", "name": "lead-alpha", "unit": "alpha", "role": "line-lead", "bg_id": "b1"},
    {"ts": "2026-11-18T09:02:00+09:00", "op": "session.bind", "name": "lead-alpha", "session_id": "SL"},
    {"ts": "2026-11-18T09:03:00+09:00", "op": "unit.tracker", "id": "alpha", "tracker": "jira"},
    {"ts": "2026-11-18T09:04:00+09:00", "op": "unit.checkpoint", "id": "alpha", "note": "pushed the fix"},
    {"ts": "2026-11-18T09:05:00+09:00", "op": "unit.new", "id": "beta", "title": "Beta"},
    {"ts": "2026-11-18T09:06:00+09:00", "op": "launch.held", "id": "beta", "reason": "max_concurrent reached"},
    {"ts": "2026-11-18T09:07:00+09:00", "op": "unit.new", "id": "gamma", "title": "Gamma"},
    {"ts": "2026-11-18T09:08:00+09:00", "op": "decision.ask", "key": "g-merge", "unit": "gamma", "question": "merge now?"},
    {"ts": "2026-11-18T09:09:00+09:00", "op": "unit.new", "id": "delta", "title": "Delta"},
    {"ts": "2026-11-18T09:10:00+09:00", "op": "wait.add", "id": "delta", "wid": "w1", "kind": "pr-review", "ref": "example-org/x#2"},
    {"ts": "2026-11-18T09:11:00+09:00", "op": "unit.new", "id": "eps", "title": "Eps"},
    {"ts": "2026-11-18T09:12:00+09:00", "op": "unit.status", "id": "eps", "status": "done", "note": "merged"},
    {"ts": "2026-11-18T09:13:00+09:00", "op": "approval.request", "rid": "r1", "unit": "alpha", "role": "line-lead",
     "tool": "Bash", "summary": "git push", "tier": 2},
    {"ts": "2026-11-18T09:14:00+09:00", "op": "approval.request", "rid": "r2", "unit": "alpha", "role": "line-lead",
     "tool": "Bash", "summary": "kubectl apply", "tier": 3},
    {"ts": "2026-11-18T09:15:00+09:00", "op": "approval.request", "rid": "r3", "unit": "alpha", "tool": "Bash", "summary": "ls", "tier": 1},
    {"ts": "2026-11-18T09:16:00+09:00", "op": "approval.answer", "rid": "r3", "behavior": "allow", "by": "owner"},
    {"ts": "2026-11-18T09:17:00+09:00", "op": "unit.report", "id": "alpha", "eid": "e1", "text": "blocked on review"},
    {"ts": "2026-11-18T09:18:00+09:00", "op": "event", "eid": "e2", "unit": "delta", "kind": "pr-review", "summary": "approved"},
    {"ts": "2026-11-18T09:19:00+09:00", "op": "pressure", "level": "high", "reasons": ["8 sessions running"]},
]


class BoardModelTest(Base):
    CREW = False

    def model(self):
        old = os.environ.get("FOUNDRY_NOW")
        os.environ["FOUNDRY_NOW"] = "2026-11-18T10:00:00+09:00"
        try:
            return board.model(foundrylib.replay(JOURNAL))
        finally:
            if old is None:
                os.environ.pop("FOUNDRY_NOW")
            else:
                os.environ["FOUNDRY_NOW"] = old

    def test_columns_carry_korean_labels_and_counts(self):
        m = self.model()
        self.assertEqual([(s["key"], s["label"]) for s in m["sections"]],
                         [("done", "완료"), ("in_progress", "진행 중"), ("waiting_others", "타인 대기"),
                          ("waiting_owner", "오너 대기"), ("queue", "대기열")])
        cols = {s["key"]: [r["id"] for r in s["rows"]] for s in m["sections"]}
        self.assertEqual(cols, {"done": ["eps"], "in_progress": ["alpha"], "waiting_others": ["delta"],
                                "waiting_owner": ["gamma"], "queue": ["beta"]})
        self.assertEqual(m["counts"], {"done": 1, "in_progress": 1, "waiting_others": 1, "waiting_owner": 0, "queue": 1})  # 0.1.88: gamma waits on its own card, shown in 결정 필요 only
        self.assertEqual([s["count"] for s in m["sections"]], [1, 1, 1, 0, 1])

    def test_band_and_board_share_one_count(self):
        m = self.model()
        sm = m["summary"]
        self.assertEqual(sm["counts"], {s["key"]: len(s.get("items", s["rows"])) for s in m["sections"]})
        self.assertEqual(sm["counts"], m["counts"])
        # gamma's open decision.ask counts toward 결정 필요 even with no decision card, and needs the owner.
        self.assertEqual(sm["decisions"], 1)
        self.assertEqual([a["key"] for a in m["asks"]], ["g-merge"])
        self.assertTrue(sm["attention"])
        text = board.text_of(m)
        band = text.splitlines()[1]
        for sec in m["sections"]:
            self.assertIn(f"{sec['label']} {sec['count']}", band)
            self.assertIn(f"{sec['label']} ({sec['count']})", text)
        self.assertIn("결정 필요 1", band)
        self.assertTrue(band.startswith("오너 확인 필요"))
        self.assertIn("gamma · merge now? · 답 필요 · 막는 것: gamma 진행  [g-merge]", text)
        # A repo filter narrows the rows; the band follows the same rows.
        f = board.apply_view(self.model(), "status", "example-org/x")
        f["summary"] = board.summary(f)
        self.assertEqual(f["summary"]["counts"], {s["key"]: len(s.get("items", s["rows"])) for s in f["sections"]})

    def test_answered_decision_leaves_the_band_calm(self):
        j = JOURNAL + [{"ts": "2026-11-18T09:20:00+09:00", "op": "decision.answer", "key": "g-merge", "answer": "yes"}]
        m = board.model(foundrylib.replay(j))
        self.assertEqual((m["summary"]["decisions"], m["asks"]), (0, []))

    def test_row_fields(self):
        rows = {r["id"]: r for s in self.model()["sections"] for r in s["rows"]}
        a = rows["alpha"]
        self.assertEqual(a["session"]["name"], "lead-alpha")
        self.assertEqual(a["session"]["state"], "running")
        self.assertEqual(a["tracker"], "jira")
        self.assertEqual(a["tickets"], ["PROJ-1"])
        self.assertEqual(a["prs"], ["example-org/x#1"])
        self.assertEqual(a["note"], "[report] blocked on review")
        self.assertEqual([x["rid"] for x in a["approvals"]], ["r2", "r1"])
        self.assertEqual(rows["beta"]["held"]["reason"], "max_concurrent reached")
        self.assertIn("max_concurrent", rows["beta"]["summary"])
        self.assertEqual([d["key"] for d in rows["gamma"]["open_decisions"]], ["g-merge"])
        self.assertEqual(rows["delta"]["waits"][0]["ref"], "example-org/x#2")
        self.assertIsNone(rows["delta"]["session"])

    def test_an_approval_the_hook_gave_up_on_stays_offered_as_queued(self):
        # 0.1.32: a late answer still applies on the session's next identical request (approve.py), so the owner
        # must be able to give it from the pane.
        snap = foundrylib.replay(JOURNAL + [{"ts": "2026-11-18T09:20:00+09:00", "op": "approval.mark", "rid": "r1",
                                             "field": "gave_up"}])
        with unittest.mock.patch.dict(os.environ, FOUNDRY_NOW="2026-11-18T10:00:00+09:00"):  # inside the 0.1.43 TTL
            m = board.model(snap)
        self.assertEqual([(a["rid"], a["queued"]) for a in m["approvals"]], [("r2", False), ("r1", True)])
        alpha = next(r for s in m["sections"] for r in s["rows"] if r["id"] == "alpha")
        self.assertEqual([a["rid"] for a in alpha["approvals"]], ["r2", "r1"])

    def test_a_hand_spawned_long_ago_with_no_agent_is_lost(self):
        import hands
        now = foundrylib.parse_ts("2026-11-18T13:30:00+09:00")
        snap = {"agents": {}, "pending_spawns": [], "hands": {
            "k1": {"key": "k1", "unit": "u", "name": "facts", "role": "hand", "state": "spawned", "ts": "2026-11-18T12:42:00+09:00"},
            "k2": {"key": "k2", "unit": "u", "name": "fresh", "role": "hand", "state": "spawned", "ts": "2026-11-18T13:20:00+09:00"}}}
        self.assertEqual([(h["name"], h["state"]) for h in hands.unit_hands(snap, "u", now)],
                         [("facts", "lost"), ("fresh", "spawned")])

    def test_top_level_approvals_escalations_pressure_sessions(self):
        m = self.model()
        self.assertEqual([a["rid"] for a in m["approvals"]], ["r2", "r1"])  # unanswered, newest first
        ids = [x["id"] for x in m["toasts"]]
        self.assertEqual(sorted(ids), ["approval:r2", "decision:g-merge", "event:e1"])  # tier 3 only; pr-review event is no escalation
        self.assertEqual(ids[0], "event:e1")
        self.assertEqual(m["escalations"], [])  # 0.1.88: each of those has its own section (card, approval, timeline)
        self.assertEqual(m["pressure"], {"level": "high", "reasons": ["8 sessions running"]})
        self.assertEqual([(x["name"], x["state"]) for x in m["sessions"]], [("lead-alpha", "running")])

    def test_held_in_progress_unit_sits_in_queue(self):
        j = JOURNAL + [{"ts": "2026-11-18T09:30:00+09:00", "op": "launch.held", "id": "alpha", "reason": "pressure high"}]
        m = board.model(foundrylib.replay(j))
        cols = {s["key"]: [r["id"] for r in s["rows"]] for s in m["sections"]}
        self.assertIn("alpha", cols["queue"])
        self.assertNotIn("alpha", cols["in_progress"])

    def test_old_snapshot_without_new_keys(self):
        snap = foundrylib.replay(JOURNAL[:5])
        for k in ("sessions", "approvals", "pressure"):
            snap.pop(k)
        m = board.model(snap)
        self.assertEqual(m["approvals"], [])
        self.assertEqual(m["pressure"]["level"], "ok")

    def test_renderers_show_the_new_columns(self):
        snap = foundrylib.replay(JOURNAL)
        text = board.render_text(snap)
        for want in ("## 진행 중 (1)", "## 오너 대기 (0)", "승인 대기 (2)", "pressure high", "[running]", "PROJ-1"):
            self.assertIn(want, text)
        page = board.render_html(snap)
        for want in ("타인 대기", "승인 대기", "kubectl apply", "lead-alpha (running)", "pressure high"):
            self.assertIn(want, page)

    def test_board_json_cli(self):
        with open(os.path.join(self.dir, "journal.jsonl"), "w") as f:
            f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in JOURNAL))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "board.py"), "json"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        m = json.loads(r.stdout)
        self.assertEqual(m["counts"]["queue"], 1)
        self.assertEqual((len(m["escalations"]), len(m["toasts"])), (0, 3))


FULL = {
    "halts": [{"scope": "all", "reason": "prod incident", "by": "owner", "ts": None}],
    "next": [{"unit": "beta", "title": "Beta", "repo": "example-org/y", "why": "unblocked"}],
    "cards": [{"id": "d-0123", "unit": "gamma", "repo": "example-org/x", "question": "merge now?", "recommended": "B",
               "options": [{"key": "A", "label": "wait", "description": "hold a day"},
                           {"key": "B", "label": "merge", "description": "ship it"}]}],
    "inventory": {"repos": {"example-org/x": [{"kind": "pr", "ref": "example-org/x#1", "age_h": 30.0, "waits_on": "alice",
                                        "stage": "review", "stale": True}]},
                  "reviewers": [{"login": "alice", "pending": 4, "warn": True}]},
    "monitor": {"status": "warn", "collecting": False, "footer": "수집 중단 (foreman down)",
                "tiles": [{"name": "load", "value": 1.2, "unit": "/cpu", "worse": "high", "spark": [0.1, 0.5, 1.2]}]},
    "handover": {"text": "morning: 2 PRs merged"},
}


class BoardViewsAndSections(Base):
    CREW = False

    def full_model(self, view="status", repo=None):
        snap = foundrylib.replay(JOURNAL + [
            {"ts": "2026-11-18T09:20:00+09:00", "op": "unit.new", "id": "multi", "title": "Multi", "pr": ["example-org/x#5", "example-org/y#6"]}])
        m = board.model(snap)
        m.update(json.loads(json.dumps(FULL)))
        rows = {r["id"]: r for sec in m["sections"] for r in sec["rows"]}
        rows["alpha"].update(stall={"kind": "idle", "text": "40분 무응답"}, mismatch="PR merged, unit open",
                             sync_text="동기화 지연 45분", mode="plan", blocked_by=["decision:d-0123"],
                             chain={"upstream": ["beta"], "downstream": ["gamma"]},
                             hands=[{"name": "fixer", "role": "hand", "state": "running"}])
        board._finish(snap, m)
        return board.apply_view(m, view, repo)

    def test_multi_repo_units_list_every_repo_and_group_in_each(self):
        m = self.full_model("repo")
        rows = {r["id"]: r for sec in m["sections"] for r in sec["rows"]}
        self.assertEqual(rows["multi"]["repos"], ["example-org/x", "example-org/y"])
        groups = {g["repo"]: g["units"] for g in m["repo_groups"]}
        self.assertIn("multi", groups["example-org/x"])
        self.assertIn("multi", groups["example-org/y"])
        self.assertEqual(m["repo_names"], ["example-org/x", "example-org/y"])
        text = board.text_of(m)
        self.assertIn("## example-org/y (1)", text)
        self.assertIn("진행 중", text.split("## example-org/x")[1])  # status column in repo view

    def test_short_repo_names_join_their_owner_repo_in_the_filter_and_cards(self):
        snap = foundrylib.replay(JOURNAL + [
            {"ts": "2026-11-18T09:20:00+09:00", "op": "unit.new", "id": "short", "title": "Short", "repo": "x"}])
        m = board.model(snap)
        m.update(json.loads(json.dumps(FULL)))
        m["cards"][0]["repo"] = "x"
        board._finish(snap, m)
        m = board.apply_view(m, "repo")
        rows = {r["id"]: r for sec in m["sections"] for r in sec["rows"]}
        self.assertEqual(rows["short"]["repos"], ["example-org/x"])  # example-org/x is already known from the inventory
        self.assertNotIn("x", m["repo_names"])
        self.assertEqual(m["cards"][0]["repo"], "example-org/x")

    def test_repo_filter_and_view_state(self):
        m = self.full_model("status", "example-org/y")
        self.assertEqual((m["view"], m["repo_filter"]), ("status", "example-org/y"))
        self.assertEqual([r["id"] for sec in m["sections"] for r in sec["rows"]], ["multi"])
        m = self.full_model("bogus", "all")
        self.assertEqual((m["view"], m["repo_filter"]), ("status", "all"))
        self.assertGreater(sum(sec["count"] for sec in m["sections"]), 1)

    def test_sections_render_when_keys_present(self):
        m = self.full_model()
        self.assertEqual(m["cards_open"], 2)  # one decision card plus gamma's open decision.ask
        self.assertTrue(m["foreman_down"])
        text = board.text_of(m)
        for want in ("ANDON 1", "!! ANDON all: prod incident", "## 결정 필요 (2)", "추천 B", "[d-0123 A/B]", "상세: foundry decide show <id>",
                     "## 다음 작업 (1)", "## 재고", "PR 1 · 리뷰 대기 1  → foundry board --tab pr", "## PC 부하: warn", "▁", "█",
                     "foreman down", "## 인계", "막힘 40분 무응답", "상태 불일치", "동기화 지연 45분", "<plan>"):
            self.assertIn(want, text)
        page = board.html_of(m)
        for want in ("class='andon'", "결정 필요", "재고", "PC 부하", "인계", "앞: beta → 뒤: gamma",
                     "fixer (hand)", "id=tab-pr"):
            self.assertIn(want, page)

    def test_absent_keys_do_not_crash(self):
        snap = foundrylib.replay(JOURNAL[:5])
        m = board.model(snap)
        for k in ("cards", "next", "halts", "inventory", "monitor"):
            m.pop(k, None)
        board._finish(snap, m)
        text = board.text_of(board.apply_view(m))
        self.assertNotIn("결정 필요 (", text)
        self.assertIn("결정 필요 0", text)
        board.html_of(m)

    def test_spark_glyphs(self):
        self.assertEqual(board.spark([0, 7, None, 3.5]), "▁█ ▄")
        self.assertEqual(board.spark([None]), "")
        self.assertEqual(board.spark([2, 2]), "▁▁")

    def test_metrics_window(self):
        """Fixed buckets per window, each the worst sample with its own level; the peak and its time."""
        rows = [{"ts": f"2026-11-18T{h:02d}:00:00+09:00", "load_per_cpu": h / 10, "mem_reclaimable_pct": 50 - h}
                for h in range(0, 10)]
        rows.insert(0, {"ts": "2026-11-17T01:00:00+09:00", "load_per_cpu": 9})
        rows.append({"ts": "2026-11-18T09:30:00+09:00", "load_per_cpu": 2.5, "mem_reclaimable_pct": 15})
        with open(os.path.join(self.dir, "metrics.jsonl"), "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
        for hours, n, buckets, step in (("3", 4, 24, 7.5), ("6", 7, 24, 15), ("24", 11, 24, 60)):
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "board.py"), "metrics", "--hours", hours],
                               env=self.env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual((out["samples"], out["bucket_min"]), (n, step), hours)
            tiles = {t["name"]: t for t in out["tiles"]}
            load, mem = tiles["load"], tiles["memory"]
            self.assertEqual((len(load["glyphs"]), len(load["levels"])), (buckets, buckets))
            self.assertEqual((load["value"], load["status"], load["peak"]), (2.5, "danger", 2.5))
            self.assertTrue(load["peak_ts"].endswith("09:30:00+09:00"))
            i = load["levels"].index("danger")
            self.assertEqual(load["glyphs"][i], "█")
            self.assertEqual(load["spark"][i], 2.5)
            # memory is worse when low: the bucket keeps its minimum and the peak is the lowest value.
            self.assertEqual((mem["status"], mem["peak"]), ("warn", 15))
            self.assertIn(None, load["spark"])

    def test_metrics_reads_only_the_window_tail_and_reuses_an_unchanged_file(self):
        """24 h of samples (1440 lines): a 3 h window reads the tail; an unchanged file is answered from the cache."""
        import watchers
        base = cl.parse_ts("2026-11-18T10:00:00+09:00")
        path = os.path.join(self.dir, "metrics.jsonl")
        with open(path, "w") as f:
            for i in range(1440):
                ts = (base - dt.timedelta(minutes=1439 - i)).isoformat()
                f.write(json.dumps({"ts": ts, "load_per_cpu": 0.5, "mem_reclaimable_pct": 60}) + "\n")
        keep = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            since = base - dt.timedelta(hours=3)
            tail = watchers.read_metrics(since)
            self.assertEqual(tail, [r for r in watchers.read_metrics() if cl.parse_ts(r["ts"]) >= since])
            self.assertEqual(len(tail), 181)
            self.assertEqual(watchers.read_metrics(base - dt.timedelta(hours=30)), watchers.read_metrics())
        finally:
            os.environ.pop("FOUNDRY_STATE_DIR", None) if keep is None else os.environ.update(FOUNDRY_STATE_DIR=keep)
        cli = [sys.executable, os.path.join(SCRIPTS, "board.py"), "metrics", "--hours", "3"]
        run = lambda: json.loads(subprocess.run(cli, env=self.env, capture_output=True, text=True).stdout)
        self.assertEqual(run()["samples"], 181)
        cache = os.path.join(self.dir, "metrics-cache.json")
        with open(cache) as f:
            hit = json.load(f)
        with open(cache, "w") as f:
            json.dump({"key": hit["key"], "out": json.dumps({"cached": True})}, f)
        self.assertEqual(run(), {"cached": True})
        with open(path, "a") as f:  # a new sample: computed again
            f.write(json.dumps({"ts": base.isoformat(), "load_per_cpu": 0.7}) + "\n")
        self.assertIn("tiles", run())


if __name__ == "__main__":
    unittest.main()
