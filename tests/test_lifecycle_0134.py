"""0.1.34: cards whose refs settled close themselves as moot and never reach the owner; a release unit whose done gate
passed is closed by its lead or the foreman, not the owner; stale inbox notes never change who merges."""

import os
import re
import subprocess
import sys

import test_foundry as tf
from test_foundry import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)
import launcher  # noqa: E402

PR = "example-org/app#12"


class Cards(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "example-org/app")

    def card(self, key, *refs):
        args = ["decide", "open", "--key", key, "--unit", "alpha", "--asked-by", "foreman",
                "--question", f"{key} 를 머지할까요?", "--option", "A:진행", "--option", "B:대기", "--recommend", "A"]
        for r in refs:
            args += ["--ref", r]
        return self.foundry(*args)[1]["card"]["id"]

    def owner_ids(self):
        return [i["id"] for i in self.foundry("decide", "digest", "--owner-message")[1]["items"] if i["kind"] == "card"]

    def stream(self):
        return self.foundry("watch", "stream", "--kind", "github", "--once")[1] or ""

    def test_a_card_whose_pr_merged_never_reaches_the_owner_and_closes_as_foundry(self):
        cid = self.card("merge-12", f"pr:{PR}")
        keep = self.card("data-handling")  # no ref: a real judgment, stays
        self.assertEqual(sorted(self.owner_ids()), sorted([cid, keep]))
        self.record("pr.state", unit="alpha", repo="example-org/app", number=12, state="MERGED")
        self.assertEqual(self.owner_ids(), [keep])  # left out before the stream even runs
        out = self.stream()
        self.assertIn(f"card {cid}: 자동 종료", out)
        c = self.snap()["cards"][cid]
        self.assertEqual((c["answer"], c["answered_by"]), ("moot", "foundry"))
        self.assertIn("머지됨", c["moot"])
        self.assertIsNone(self.snap()["cards"][keep]["answer"])
        self.assertEqual(self.snap()["units"]["alpha"]["blocked_by"],
                         [f"decision:{keep}"])  # the moot card no longer blocks the unit
        self.assertNotIn("자동 종료", self.stream())  # once

    def test_an_untracked_pr_is_read_from_github(self):
        cid = self.card("close-99", "pr:example-org/other#99")
        self.gh({"example-org/other#99": {"state": "CLOSED"}})
        self.stream()
        self.assertEqual(self.snap()["cards"][cid]["answer"], "moot")

    def test_every_ref_must_settle_and_unit_refs_count(self):
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta", "--repo", "example-org/app")
        cid = self.card("two", f"pr:{PR}", "unit:beta")
        self.record("pr.state", unit="alpha", repo="example-org/app", number=12, state="MERGED")
        self.stream()
        self.assertIsNone(self.snap()["cards"][cid]["answer"])
        self.foundry("unit", "set", "beta", "--status", "cancelled", "--by", "owner")
        self.stream()
        self.assertEqual(self.snap()["cards"][cid]["answer"], "moot")

    def test_refs_attached_to_an_old_card_close_it_at_once(self):
        cid = self.card("gateway-715-merge")
        self.record("pr.state", unit="alpha", repo="example-org/app", number=12, state="MERGED")
        _, out = self.foundry("decide", "refs", cid, "--ref", f"pr:{PR}")
        self.assertEqual(out["closed_moot"], [cid])
        self.foundry("decide", "refs", cid, "--ref", "nonsense", ok=(2,))

    def test_a_decision_ask_on_a_merged_pr_is_withdrawn_and_leaves_the_owner_list(self):
        self.foundry("decision", "ask", "--key", "merge-webapp-1633", "--unit", "alpha",
                     "--question", "example-org/app#12 머지할까요?", "--ref", f"pr:{PR}", ok=(10,))
        self.foundry("decision", "ask", "--key", "gateway-715-merge", "--unit", "alpha",
                     "--question", "example-org/app#13 머지할까요?", ok=(10,))
        asks = lambda: [i["id"] for i in self.foundry("decide", "digest", "--owner-message")[1]["items"]  # noqa: E731
                        if i["kind"] == "ask"]
        self.assertEqual(sorted(asks()), ["gateway-715-merge", "merge-webapp-1633"])
        self.record("pr.state", unit="alpha", repo="example-org/app", number=12, state="MERGED")
        self.assertEqual(asks(), ["gateway-715-merge"])
        self.assertIn("자동 종료", self.stream())
        d = self.snap()["decisions"]["merge-webapp-1633"]
        self.assertTrue(d["answer"].startswith("withdrawn: 자동 종료"))
        self.assertEqual(d["by"], "foundry")
        # an old ask with no ref: the foreman attaches the PR and it closes at once
        self.record("pr.state", unit="alpha", repo="example-org/app", number=13, state="MERGED")
        _, out = self.foundry("decide", "refs", "gateway-715-merge", "--ref", "pr:example-org/app#13")
        self.assertEqual(out["closed_moot"], ["gateway-715-merge"])
        self.assertEqual(asks(), [])

    def test_a_pr_aging_card_hinges_on_its_pr_by_key(self):
        self.record("decide.open", id="d-0009", unit=None, repo="example-org/app", question="리뷰 대기 오래됨",
                    options=[{"key": "close"}, {"key": "wait"}], key=f"pr-aging:{PR}", asked_by="foundry")
        self.record("pr.state", unit="alpha", repo="example-org/app", number=12, state="CLOSED")
        self.stream()
        self.assertEqual(self.snap()["cards"]["d-0009"]["answer"], "moot")

    def test_the_handover_lists_it_once_under_auto_closed(self):
        cid = self.card("merge-12", f"pr:{PR}")
        self.record("pr.state", unit="alpha", repo="example-org/app", number=12, state="MERGED")
        self.stream()
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            import factory
            import foundrylib as cl
            text = factory.handover(cl.load(), cl.read_journal(), {"repos": {}}, t=cl.parse_ts("2026-11-18T11:00:00+09:00"))
        finally:
            os.environ["FOUNDRY_STATE_DIR"] = old or ""
            if old is None:
                os.environ.pop("FOUNDRY_STATE_DIR")
        auto = text.split("## 자동 종료 (1)")[1].split("##")[0]
        self.assertIn(cid, auto)
        self.assertNotIn(cid, text.split("## 결정 대기")[1].split("##")[0])


class ReleaseGate(tf.Base):
    UID = "release-app-20261118-1949"

    def run_f(self, *args, env=None):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args],
                           env={**self.env, **(env or {})}, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def setUp(self):
        super().setUp()
        self.record("unit.new", id=self.UID, kind="project", title="Release app", repo="example-org/app",
                    lead="foundry:line-lead", pr=[], ticket=[], topic=["release"])
        self.record("release.unit", id=self.UID, repo="example-org/app", prs=["example-org/app#710"],
                    procedure=None, dev_app=None)
        self.record("unit.status", id=self.UID, status="waiting_others")

    def test_foreman_cannot_close_before_the_gate(self):
        rc, out = self.run_f("unit", "set", self.UID, "--status", "done", env={"FOUNDRY_ROLE": "foreman"})
        self.assertNotEqual(rc, 0, out)

    def test_gate_passed_tells_lead_and_foreman_once_and_either_closes_without_owner(self):
        self.record("pr.released", unit=self.UID, repo="example-org/app", number=710, tag="app-rel-1")
        _, out = self.foundry("watch", "stream", "--kind", "github", "--once")
        self.assertIn("release-gate", out)
        snap = self.snap()
        self.assertTrue(any(e["eid"] == f"ev-{self.UID}-release-gate" for e in snap["events"]))
        self.assertTrue(any(i["iid"] == f"rel-{self.UID}" for i in snap["units"][self.UID]["inbox"]))
        _, again = self.foundry("watch", "stream", "--kind", "github", "--once")
        self.assertNotIn("release-gate", again or "")
        # its parked/ended lead is resumed for it
        self.record("session.launch", name="lead-" + self.UID, unit=self.UID, role="line-lead", bg_id="bgX", cwd="/wt/r")
        self.record("session.state", name="lead-" + self.UID, state="done")
        self.assertIn((self.UID, "release done gate passed"), launcher.unparkable(self.snap()))
        # no verify recorded: the gate is the evidence; the foreman closes it with no owner order
        rc, out = self.run_f("unit", "set", self.UID, "--status", "done", env={"FOUNDRY_ROLE": "foreman"})
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.snap()["units"][self.UID]["status"], "done")

    def test_the_lead_closes_it_without_a_verify_record(self):
        self.record("pr.released", unit=self.UID, repo="example-org/app", number=710, tag="app-rel-1")
        rc, out = self.run_f("unit", "status", self.UID, "done",
                             env={"FOUNDRY_ROLE": "line-lead", "FOUNDRY_UNIT": self.UID})
        self.assertEqual(rc, 0, out)


class StaleInbox(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "example-org/app")
        self.foundry("inbox", "route", "--unit", "alpha", "--text", "the owner merges every PR: hand over the line")
        self.foundry("inbox", "route", "--unit", "alpha", "--text", "Merge your own PR when approved and green")

    def test_superseded_note_is_never_injected_and_the_merge_rule_is(self):
        old = self.snap()["units"]["alpha"]["inbox"][0]["iid"]
        _, out = self.foundry("inbox", "supersede", old, "--reason", "prefs merge.default self", "--by", "foreman")
        self.assertEqual(out["superseded"], {"alpha": [old]})
        ctx = self.start_lead("alpha")
        text = str(ctx)
        self.assertNotIn("the owner merges every PR", text)
        self.assertIn("Merge your own PR", text)
        self.assertIn("never from a free-text note", text)

    def test_unknown_iid_is_refused(self):
        self.foundry("inbox", "supersede", "in-nope", "--reason", "x", "--by", "foreman", ok=(2,))


class ForemanText(tf.Base):
    CREW = False

    def test_foreman_forbids_close_drop_moot_chores_for_the_owner(self):
        with open(os.path.join(ROOT, "agents", "foreman.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Closing, dropping or marking moot is never an owner chore", text)
        self.assertIn("foundry decide refs", text)
        with open(os.path.join(ROOT, "skills", "foundry-lead-loop", "SKILL.md"), encoding="utf-8") as f:
            lead = f.read()
        self.assertTrue(re.search(r"free-text inbox note never changes who merges", lead))
