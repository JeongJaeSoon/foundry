"""0.1.25: near-misses and waiting PRs drain (`foundry nearmiss triage`, `foundry pr age|adopt|drop`)."""

import os
import sys
import unittest

import test_foundry as tf

MULTILINE = "after deny of `git commit -m \"fix\nbody\"`: retried `git commit -m \"fix\nbody\"`"
REF = "o/r#5"


class _Clock(tf.Base):
    def at(self, ts):
        self.env["FOUNDRY_NOW"] = ts
        os.environ["FOUNDRY_NOW"] = ts

    def tearDown(self):
        os.environ.pop("FOUNDRY_NOW", None)
        super().tearDown()

    def board(self):
        return self.foundry("board", "--format", "json")[1]

    def cards(self, prefix):
        return {c["key"]: c for c in (self.snap().get("cards") or {}).values() if (c.get("key") or "").startswith(prefix)}


class NearMissLifecycle(_Clock):
    def miss(self, detail, kind="deny-then-similar", unit="u1"):
        self.record("nearmiss", kind=kind, unit=unit, detail=detail)

    def test_entries_dedup_by_pattern_and_known_shapes_fold_into_a_count(self):
        self.at("2026-11-18T10:00:00+09:00")
        self.miss(MULTILINE, unit="u1")
        self.miss(MULTILINE, unit="u2")
        self.miss("after deny of `gh pr view 5; gh pr diff 5`: retried")
        m = self.board()
        self.assertEqual({(g["pattern"], g["count"]) for g in m["nearmiss_groups"]},
                         {("deny-then-similar:multiline-message", 2), ("deny-then-similar:compound", 1)})
        self.assertEqual(len([e for e in m["escalations"] if e["kind"] == "nearmiss"]), 2)
        res = self.foundry("nearmiss", "triage")[1]
        self.assertEqual({r["pattern"]: r["outcome"] for r in res["resolved"]},
                         {"deny-then-similar:multiline-message": "fixed", "deny-then-similar:compound": "ack"})
        m = self.board()
        self.assertEqual((m["nearmiss_count"], m["nearmiss_resolved"]), (0, 3))
        self.assertEqual([e for e in m["escalations"] if e["kind"] == "nearmiss"], [])

    def test_a_recurring_unknown_pattern_is_one_card_and_its_answer_resolves_it(self):
        self.at("2026-11-18T10:00:00+09:00")
        for u in ("u1", "u2"):
            self.miss("odd thing", kind="weird", unit=u)
        self.assertEqual(self.foundry("nearmiss", "triage")[1]["open"], [{"pattern": "weird:other", "count": 2}])
        self.miss("odd thing", kind="weird", unit="u3")
        self.assertEqual(len(self.foundry("nearmiss", "triage")[1]["cards"]), 1)
        self.foundry("nearmiss", "triage")  # the card exists: no second one
        cards = self.cards("nearmiss:")
        self.assertEqual(list(cards), ["nearmiss:weird:other"])
        self.foundry("decide", "answer", cards["nearmiss:weird:other"]["id"], "ack", "--by", "owner")
        res = self.foundry("nearmiss", "triage")[1]
        self.assertEqual(res["resolved"], [{"pattern": "weird:other", "count": 3, "outcome": "ack"}])
        self.assertEqual(self.board()["nearmiss_count"], 0)

    def test_resolved_entries_expire_and_a_fixed_pattern_that_recurs_is_open_again(self):
        self.at("2026-11-10T10:00:00+09:00")
        self.miss(MULTILINE)
        self.foundry("nearmiss", "triage")
        self.at("2026-11-18T10:00:00+09:00")
        self.miss(MULTILINE, unit="u9")
        m = self.board()
        self.assertEqual((m["nearmiss_count"], m["nearmiss_resolved"]), (1, 1))
        res = self.foundry("nearmiss", "triage")[1]
        self.assertEqual(res["expired"], 1)
        self.assertEqual([n["unit"] for n in self.snap()["nearmiss"]], ["u9"])

    def test_the_hook_reason_for_inline_multiline_text_names_the_file_forms(self):
        sys.path.insert(0, tf.SCRIPTS)
        import gitpolicy
        why = gitpolicy.chain_reason('git -C /w commit -m "fix\nbody"')
        self.assertIn("commit -F", why)
        self.assertIn("--body-file", why)


class WaitingPRs(_Clock):
    def setUp(self):
        super().setUp()
        self.at("2026-11-16T10:00:00+09:00")  # Monday
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")
        self.record("pr.state", unit="alpha", repo="o/r", number=5, state="OPEN", title="t",
                    created="2026-11-16T10:00:00+09:00")

    def ask_thread(self):
        self.record("outbox.add", oid="ob-ask", unit="alpha", channel="C1", thread=None, kind="review-ask",
                    text="review please", prs=[REF], to=["U1"])
        self.record("outbox.done", oid="ob-ask", sent_ts="1795061530.355009")

    def reminders(self):
        return [o for o in (self.snap().get("outbox") or {}).values() if o["kind"] == "review-remind"]

    def test_one_reminder_in_the_ask_thread_after_one_business_day(self):
        self.ask_thread()
        self.at("2026-11-17T12:00:00+09:00")
        self.foundry("pr", "age")
        self.foundry("pr", "age")
        rs = self.reminders()
        self.assertEqual(len(rs), 1)
        self.assertEqual((rs[0]["channel"], rs[0]["thread"], rs[0]["to"], rs[0]["prs"]), ("C1", "1795061530.355009", ["U1"], [REF]))
        self.assertEqual(self.foundry("pr", "age", "--dry-run")[1]["actions"], {REF: "reminded"})

    def test_no_ask_thread_is_said_not_invented(self):
        self.at("2026-11-17T12:00:00+09:00")
        self.foundry("pr", "age")
        self.assertEqual(self.reminders(), [])
        self.assertEqual(self.foundry("pr", "age", "--dry-run")[1]["actions"], {REF: "no-ask-thread"})

    def test_after_five_business_days_one_owner_card(self):
        self.ask_thread()
        self.at("2026-11-24T12:00:00+09:00")
        self.foundry("pr", "age")
        self.foundry("pr", "age")
        cards = self.cards("pr-aging:")
        self.assertEqual(list(cards), [f"pr-aging:{REF}"])
        self.assertEqual([o["key"] for o in cards[f"pr-aging:{REF}"]["options"]], ["close", "reassign", "wait"])

    def test_a_done_units_open_pr_asks_adopt_or_drop_and_drop_leaves_the_board(self):
        self.foundry("unit", "status", "alpha", "done", "--note", "shipped")
        self.at("2026-11-17T12:00:00+09:00")
        self.foundry("pr", "age")
        card = self.cards("pr-orphan:")[f"pr-orphan:{REF}"]
        self.foundry("decide", "answer", card["id"], "drop", "--by", "owner")
        self.foundry("pr", "age")
        self.assertIn(REF, self.snap()["pr_dropped"])
        refs = [r["ref"] for rs in self.board()["inventory"]["repos"].values() for r in rs if r["kind"] == "pr"]
        self.assertNotIn(REF, refs)

    def test_adopt_moves_the_pr_to_a_live_unit(self):
        self.foundry("unit", "status", "alpha", "done", "--note", "shipped")
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta", "--repo", "app")
        self.foundry("pr", "adopt", REF, "--unit", "alpha", ok=(2,))
        self.foundry("pr", "adopt", REF, "--unit", "beta")
        self.at("2026-11-17T12:00:00+09:00")
        self.assertEqual(self.foundry("pr", "age", "--dry-run")[1]["actions"].get(REF), "no-ask-thread")

    def test_the_github_stream_still_reads_a_done_units_open_pr(self):
        sys.path.insert(0, tf.SCRIPTS)
        import watchers
        self.foundry("unit", "status", "alpha", "done", "--note", "shipped")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            self.assertIn(REF, watchers._pr_refs(self.snap()))
            self.record("pr.state", unit="alpha", repo="o/r", number=5, state="MERGED")
            self.assertNotIn(REF, watchers._pr_refs(self.snap()))
        finally:
            os.environ.pop("FOUNDRY_STATE_DIR", None)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (NearMissLifecycle, WaitingPRs):
        suite.addTests(cls(n) for n in sorted(vars(cls)) if n.startswith("test_"))
    return suite
