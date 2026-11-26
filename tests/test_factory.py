"""Tests for the factory views: inventory, preflight, handover, kaizen/rework, near-misses, cost, trace."""

import datetime as dt
import json
import os
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import factory  # noqa: E402
import foundrylib  # noqa: E402
import prefs  # noqa: E402

JST = foundrylib.JST
# A read-only `gh pr list` stand-in: prints FAKE_GH_LIST for `pr list`, fails for anything else.
GH_LIST = ("import os, sys; a = sys.argv[1:]; "
           "print(os.environ['FAKE_GH_LIST']) if a[:2] == ['pr', 'list'] else sys.exit(1)")


def at(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=JST)


def snap_with(units):
    s = {"units": {}, "nearmiss": [], "costs": {}}
    for uid, fields in units.items():
        s["units"][uid] = {"id": uid, "kind": "project", "status": "in_progress", "repo": "o/r", "created": None,
                           "keys": {"ticket": [], "pr": [], "topic": []}, **fields}
    return s


class FactoryBase(tf.Base):
    def rec(self, op, **fields):
        old = os.environ.get("FOUNDRY_NOW")
        os.environ["FOUNDRY_NOW"] = self.env["FOUNDRY_NOW"]
        try:
            self.record(op, **fields)
        finally:
            if old is None:
                os.environ.pop("FOUNDRY_NOW")
            else:
                os.environ["FOUNDRY_NOW"] = old


class Inventory(tf.unittest.TestCase):
    def setUp(self):
        # factory.inventory runs in-process: review.load_rows' 참고 cache must come from an empty state dir, never
        # from the machine's live ~/.local/state/foundry
        from unittest import mock
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        patcher = mock.patch.dict(os.environ, {"FOUNDRY_STATE_DIR": d})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_stages_and_waits(self):
        prs = [{"repo": "o/r", "number": n, "state": st, "opened": "2026-11-18T09:00:00+09:00", **extra}
               for n, st, extra in [(1, "draft", {}), (2, "open", {"reviewers": ["alice"], "auto_reviewers": []}), (3, "approved", {}),
                                    (4, "merged", {}), (5, "merged", {"released": True}),
                                    (6, "merged", {"released": True, "deployed": True}), (7, "closed", {})]]
        s = snap_with({"u1": {"prs": prs, "branches": ["feat/x"], "worktree": {"path": "/wt/u1", "repo": "o/r"}}})
        inv = factory.inventory(s, t=at("2026-11-18T10:00:00"), p=prefs.defaults(), rs={})
        rows = {r["ref"]: r for r in inv["repos"]["o/r"]}
        # Released and deployed PRs are out of stock: only what still waits on someone is listed.
        self.assertEqual({k: rows[k]["stage"] for k in rows if rows[k]["kind"] == "pr"},
                         {"o/r#1": "draft", "o/r#2": "review", "o/r#3": "approved", "o/r#4": "merged"})
        self.assertEqual(rows["o/r#2"]["waits_on"], "alice")
        self.assertEqual(rows["o/r#3"]["waits_on"], "merge")
        self.assertEqual(rows["o/r#2"]["age_h"], 1.0)
        self.assertEqual({r["kind"] for r in rows.values()}, {"pr", "branch", "worktree"})
        self.assertEqual([(r["login"], r["pending"], r["mine"], r["warn"]) for r in inv["reviewers"]], [("alice", 1, 1, False)])

    def test_merged_pr_leaves_release_wait_without_a_release_step_or_once_tagged(self):
        prs = [{"repo": "example-org/infra-ops", "number": 1, "state": "MERGED"},
               {"repo": "o/app", "number": 2, "state": "MERGED"},
               {"repo": "o/app", "number": 3, "state": "MERGED", "released": "v1.2.0"},
               {"repo": "o/tool", "number": 4, "state": "MERGED"}]
        s = snap_with({"u1": {"prs": prs}})
        rs = {"tool": {"path": "/x/tool", "slug": "o/tool", "release_tag": "none"}}
        inv = factory.inventory(s, t=at("2026-11-18T10:00:00"), p=prefs.defaults(), rs=rs)
        refs = [r["ref"] for rows in inv["repos"].values() for r in rows if r["kind"] == "pr"]
        self.assertEqual(refs, ["o/app#2"])  # infra-ops by prefs, o/tool by repos.json, #3 by its tag
        self.assertIsNone(factory.release_tag("o/aws-infra", prefs.defaults(), {}))
        self.assertEqual(factory.release_tag("o/app", prefs.defaults(), {"app": {"release_tag": "rel-*"}}), "rel-*")

    def test_one_repo_is_one_group(self):
        prs = [{"repo": "example-org/webapp", "number": 7, "state": "open"}]
        s = snap_with({"u1": {"repo": "webapp", "prs": prs, "worktree": {"path": "/wt/u1", "repo": "webapp"}},
                       "u2": {"repo": "gateway", "worktree": {"path": "/wt/u2", "repo": "gateway"}}})
        rs = {"gateway": {"path": "/x/gateway"}, "example-org/gateway": {"path": "/x/gateway"}}
        inv = factory.inventory(s, t=at("2026-11-18T10:00:00"), p=prefs.defaults(), rs=rs)
        self.assertEqual(sorted(inv["repos"]), ["example-org/gateway", "example-org/webapp"])
        self.assertEqual(len(inv["repos"]["example-org/webapp"]), 2)

    def test_short_repo_name_resolves_through_the_worktree_remote(self):
        import subprocess
        import tempfile
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", d], check=True)
        subprocess.run(["git", "-C", d, "remote", "add", "origin", "git@github.com:acme/widget.git"], check=True)
        self.assertEqual(factory.canonical_repo("widget", (), {}, d), "acme/widget")
        self.assertEqual(factory.canonical_repo("widget", (), {}, None), "widget")

    def test_stale_counts_business_days_only(self):
        s = snap_with({"u1": {"prs": [{"repo": "o/r", "number": 9, "state": "open",
                                       "opened": "2026-11-13T18:00:00+09:00"}]}})  # a Friday evening
        p = prefs.defaults()
        monday = factory.inventory(s, t=at("2026-11-16T10:00:00"), p=p, rs={})["repos"]["o/r"][0]
        self.assertFalse(monday["stale"])  # 6 h Friday + 10 h Monday; the weekend does not count
        tuesday = factory.inventory(s, t=at("2026-11-17T19:00:00"), p=p, rs={})["repos"]["o/r"][0]
        self.assertTrue(tuesday["stale"])
        self.assertEqual(factory.business_hours(at("2026-11-13T18:00:00"), at("2026-11-16T10:00:00"), JST), 16)

    def test_no_reviewer_cap_display_and_warn_only(self):
        prs = [{"repo": "o/r", "number": n, "state": "open", "reviewers": ["alice"], "auto_reviewers": []}
               for n in range(1, 7)]
        s = snap_with({"u1": {"prs": prs}})
        p = prefs.defaults()
        self.assertEqual(p["reviewer_load_warn"], 6)
        self.assertNotIn("max_open_prs_per_reviewer", p)
        self.assertNotIn("reviewer_caps", p)
        self.assertFalse(hasattr(factory, "reviewer_cap_block"))  # owner decision: no cap, display only
        self.assertEqual([(r["login"], r["pending"], r["warn"]) for r in factory.inventory(s, p=p, rs={})["reviewers"]],
                         [("alice", 6, True)])


class Rework(tf.unittest.TestCase):
    def test_rounds_ci_and_revert(self):
        j = [{"op": "pr.state", "unit": "u1", "state": "CHANGES_REQUESTED", "ts": "2026-11-10T10:00:00+09:00"},
             {"op": "ci.state", "unit": "u1", "state": "failure", "ts": "2026-11-10T10:05:00+09:00"},
             {"op": "ci.state", "unit": "u1", "state": "failure", "ts": "2026-11-10T10:06:00+09:00"},
             {"op": "ci.state", "unit": "u1", "state": "success", "ts": "2026-11-10T10:07:00+09:00"},
             {"op": "pr.merged", "unit": "u1", "repo": "o/r", "number": 41, "title": "Add x",
              "ts": "2026-11-10T11:00:00+09:00"},
             {"op": "pr.opened", "unit": "u2", "repo": "o/r", "number": 42, "title": "Revert \"Add x\" (#41)",
              "ts": "2026-11-12T11:00:00+09:00"}]
        rw = factory.rework(j)
        self.assertEqual(rw["u1"], {"review_rounds": 1, "ci_failures": 2, "reverts": 1, "merged": 1, "rate": 4.0})


class Preflight(FactoryBase):
    def test_failure_exits_one_and_records(self):
        self.env["FOUNDRY_PREFLIGHT_FAKE"] = json.dumps({"docker": False, "vpn": {"ok": False, "detail": "no route"}})
        rc, out = self.foundry("preflight", "--json", ok=(1,))
        self.assertEqual(sorted(out["failures"]), ["docker", "vpn"])
        self.assertEqual(self.snap()["preflight"]["ok"], False)
        self.env["FOUNDRY_PREFLIGHT_FAKE"] = "{}"
        rc, out = self.foundry("preflight", "--json")
        self.assertTrue(out["ok"])


class Handover(FactoryBase):
    def test_handover_sections(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "Ship A")
        self.foundry("unit", "new", "--id", "u2", "--title", "Ship B")
        self.rec("unit.status", id="u1", status="done")
        self.foundry("unit", "new", "--id", "u3", "--title", "Ship C")
        self.rec("unit.set", id="u3", status="done", by="lead")  # e2e: `unit set` finishes units too
        self.rec("decision.ask", key="k1", unit="u2", question="Which DB?")
        self.rec("nearmiss", kind="push-main", unit="u2", detail="blocked push to main")
        self.rec("unit.stalled", unit="u2", kind="no-output", text="20분 무응답")
        self.rec("unit.keys", id="u2", pr=["o/r#7"])
        self.rec("cost", unit="u1", usd=1.5, by_model={})
        self.env["FAKE_GH_LIST"] = json.dumps([{"number": 7, "title": "B", "isDraft": False, "reviewDecision": "",
                                                "createdAt": "2026-11-17T08:00:00+09:00",
                                                "reviewRequests": [{"login": "alice"}]}])
        self.env["FOUNDRY_GH"] = f'{sys.executable} -c "{GH_LIST}"'
        self.env["FOUNDRY_NOW"] = "2026-11-19T08:00:00+09:00"
        _, out = self.foundry("report", "handover", "--json")
        text = out["text"]
        for frag in ("## 완료 (2)", "- u1: Ship A", "- u3: Ship C", "## 결정 대기 (1)", "Which DB?", "## 정체 (1)", "## 아차 사고 (1)",
                     "push-main", "## PR 변경 (1)", "- u2: r#7 1영업일 넘게 리뷰 대기 — B", "PR 탭에서 전체 보기",
                     "foundry board --tab pr",
                     "foundry decide show k1", "$1.50"):
            self.assertIn(frag, text)
        self.assertNotIn("blocked push to main", text)  # patterns and counts, never the command line
        self.assertNotIn("표시용 추정", text)
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["handover"]["text"], text)
        # The next handover starts where this one ended.
        self.env["FOUNDRY_NOW"] = "2026-11-20T08:00:00+09:00"
        _, out = self.foundry("report", "handover", "--json")
        self.assertIn("## 완료 (0)", out["text"])


class NearMiss(FactoryBase):
    def test_pair_from_two_emitters(self):
        self.rec("nearmiss", kind="perm-denied", unit="u1", detail="rm -rf blocked")
        self.rec("nearmiss", unit="u1", text="watcher saw it")  # watchers' shape: no kind, text not detail
        _, xs = self.foundry("nearmiss", "list")
        self.assertEqual([(n["kind"], n["detail"]) for n in xs],
                         [("perm-denied", "rm -rf blocked"), ("unknown", "watcher saw it")])
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["nearmiss_count"], 2)
        _, k = self.foundry("report", "kaizen", "--json")
        self.assertEqual(k["nearmiss"], {"perm-denied": 1, "unknown": 1})


class Cost(FactoryBase):
    def write(self, path, rows):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_sum_by_unit_and_model(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "A")
        self.start_lead("u1", sid="SL")
        root = os.path.join(self.dir, "transcripts")
        opus = {"sessionId": "SL", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": {
            "input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 10000}}}
        self.write(os.path.join(root, "proj", "SL.jsonl"), [
            opus, opus,  # the same message logged twice counts once
            {"sessionId": "SL", "message": {"id": "m2", "model": "claude-haiku-5-5", "usage": {
                "input_tokens": 100000, "output_tokens": 10000}}},
            {"sessionId": "SL", "type": "user", "message": {"role": "user", "content": "hi"}}])
        self.write(os.path.join(root, "proj", "OTHER.jsonl"), [
            {"sessionId": "OTHER", "message": {"id": "m3", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1000000}}}])
        self.env["FOUNDRY_TRANSCRIPTS"] = root
        _, out = self.foundry("cost")
        self.assertAlmostEqual(out["units"]["u1"]["usd"], 0.031)
        self.assertAlmostEqual(out["units"]["u1"]["by_model"]["claude-opus-5-5"]["usd"], 0.016)
        self.assertAlmostEqual(out["units"]["_other"]["usd"], 2.0)
        self.assertAlmostEqual(out["total_usd"], 2.031)
        _, m = self.foundry("board", "--format", "json")
        row = next(r for s in m["sections"] for r in s["rows"] if r["id"] == "u1")
        self.assertAlmostEqual(row["cost_usd"], 0.031)


class Trace(FactoryBase):
    def test_unit_to_deploy(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "A", "--ticket", "PROJ-9", "--pr", "o/r#3")
        self.gh({"o/r#3": {"title": "Add A", "state": "MERGED", "url": "https://github.com/o/r/pull/3",
                           "commits": [{"oid": "abc"}], "mergeCommit": {"oid": "def"}}})
        self.rec("deploy.done", unit="u1", env="prod", tag="v1.2.3")
        _, out = self.foundry("trace", "u1")
        self.assertEqual(out["tickets"], ["PROJ-9"])
        self.assertEqual(out["prs"][0]["commits"], ["abc"])
        self.assertEqual(out["prs"][0]["merge_commit"], "def")
        self.assertEqual(out["tags"], ["v1.2.3"])
        self.assertEqual(out["deploys"][0]["env"], "prod")
