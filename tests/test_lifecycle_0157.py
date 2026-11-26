"""0.1.57: a review ask closed with a scheduled-message id is pending until its real ts is recorded: never reminded,
never a thread parent; a reminder threads under the real ts and is due per reviewer from that reviewer's own ask (a
team-only GitHub request starts no clock). The owner list takes only human-only acts with a one-line reason; a
config value is refused there and jira_base heals itself from ticket URLs at preflight."""

import json
import os

import test_foundry as tf
import test_lifecycle_0125 as t125

REF = t125.REF
TS = "1795061530.355009"


class PendingAsks(t125.WaitingPRs):
    def ask(self, oid, to, ts, post_at=None, at="2026-11-16T10:00:00+09:00"):
        self.at(at)
        self.record("outbox.add", oid=oid, unit="alpha", channel="C1", thread=None, kind="review-ask",
                    text=f"review please {oid}", prs=[REF], to=[to])
        self.record("outbox.done", oid=oid, sent_ts=ts, **({"post_at": post_at} if post_at else {}))

    def test_a_scheduled_ask_is_pending_and_never_reminded(self):
        self.ask("ob-s", "U1", "Dr0CL3AN8TB2", post_at="2026-11-20T09:00:00+09:00")
        self.at("2026-11-18T12:00:00+09:00")
        self.foundry("pr", "age")
        self.assertEqual(self.reminders(), [])
        self.assertEqual(self.foundry("pr", "age", "--dry-run")[1]["actions"], {REF: "ask-pending"})

    def test_once_posted_the_reminder_threads_under_the_real_ts(self):
        self.ask("ob-s", "U1", "Dr0CL3AN8TB2", post_at="2026-11-17T09:00:00+09:00")
        self.at("2026-11-17T09:30:00+09:00")
        self.foundry("outbox", "posted", "ob-s", "--ts", "Dr0CL3AN8TB2", ok=(2,))  # a scheduled id is no ts
        self.foundry("outbox", "posted", "ob-s", "--ts", TS)
        self.at("2026-11-18T12:00:00+09:00")
        self.foundry("pr", "age")
        [r] = self.reminders()
        self.assertEqual((r["thread"], r["to"]), (TS, ["U1"]))

    def test_age_is_per_reviewer_from_their_own_ask(self):
        self.ask("ob-a", "U1", TS, at="2026-11-16T10:00:00+09:00")
        self.ask("ob-b", "U2", "1795150000.000001", at="2026-11-17T11:00:00+09:00")
        self.at("2026-11-17T12:00:00+09:00")
        self.foundry("pr", "age")
        [r] = self.reminders()
        self.assertEqual(r["to"], ["U1"])  # U2 was asked an hour ago: not due

    def test_a_team_only_request_starts_no_clock(self):
        # The PR has waited since Monday (its GitHub review request went to a team); nobody was asked by name.
        self.at("2026-11-18T12:00:00+09:00")
        self.foundry("pr", "age")
        self.assertEqual(self.reminders(), [])
        self.assertEqual(self.foundry("pr", "age", "--dry-run")[1]["actions"], {REF: "no-ask-thread"})


class OwnerGate(tf.Base):
    def test_config_items_and_reasonless_items_never_reach_the_owner(self):
        add = ("owner", "todo", "add", "--none", "--by", "foreman")
        self.foundry(*add, "--text", "foundry prefs set jira_base https://x.atlassian.net 실행",
                     "--why-owner", "오너의 선택", ok=(2,))
        self.foundry(*add, "--text", "aws-mcp prod 지시를 기록", "--why-owner", "prod 지시", ok=(2,))
        self.foundry(*add, "--text", "정리 범위", "--why-owner", "알려 줘", ok=(2,))
        _, r = self.foundry(*add, "--text", "OneLogin API credential 발급", "--kind", "credential",
                            "--why-owner", "credential 발급은 오너 계정으로만 가능")
        self.assertEqual(self.snap()["owner_todos"][r["tid"]]["why"], "credential 발급은 오너 계정으로만 가능")

    def test_the_foreman_sets_a_discovered_jira_base_with_evidence(self):
        self.foundry("prefs", "set", "jira_base", "https://x.atlassian.net", "--by", "foreman", ok=(2,))  # no evidence
        self.foundry("prefs", "set", "jira_base", "https://x.atlassian.net", "--by", "foreman",
                     "--evidence", "getAccessibleAtlassianResources")
        with open(os.path.join(self.dir, "prefs.json")) as f:
            self.assertEqual(json.load(f)["jira_base"], "https://x.atlassian.net")
        self.foundry("prefs", "set", "jira_base", "not a url", "--by", "foreman", "--evidence", "x", ok=(2,))


class JiraBaseHeals(tf.Base):
    def test_preflight_fills_jira_base_from_ticket_urls(self):
        import sys
        sys.path.insert(0, tf.SCRIPTS)
        self.foundry("unit", "new", "--id", "u1", "--title", "U1")
        self.record("unit.note", id="u1", text="see https://stub.atlassian.net/browse/PROJ-12 for the plan")
        os.environ_backup = dict(os.environ)
        try:
            os.environ.update({k: v for k, v in self.env.items() if k.startswith("FOUNDRY_")})
            import factory
            import views
            self.assertIsNone(views.jira_base())
            self.assertEqual(factory.heal_jira_base(), "https://stub.atlassian.net")
            self.assertEqual(views.jira_base(), "https://stub.atlassian.net")
        finally:
            os.environ.clear()
            os.environ.update(os.environ_backup)
        ch = [r for r in self.journal() if r["op"] == "prefs.changed"]
        self.assertEqual((ch[-1]["key"], ch[-1]["by"]), ("jira_base", "foundry"))


if __name__ == "__main__":
    import unittest
    unittest.main()
