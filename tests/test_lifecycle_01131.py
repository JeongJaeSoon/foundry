"""0.1.131: PRs that are not the owner's leave the board: a pr-aging card answered `drop`, `foundry pr drop --repo
owner/repo | owner/*` for every PR there, `foundry pr undrop`; cards on a dropped PR close by themselves. The board's
foreman restart hint names the current foreman's short id for `claude stop`. The housekeeping scan's texts are safe
for the judge to copy into `plan --reason`, and its refusal names the character. A `claude stop` answered "No session
matching" is a stopped session, not a failure to retry."""

import json
import os
import shlex
import sys
import unittest
from unittest import mock

import test_foundry as tf
from test_lifecycle_0125 import REF, _Clock

sys.path.insert(0, tf.SCRIPTS)
import board  # noqa: E402
import hook  # noqa: E402
import housekeep  # noqa: E402
import i18n  # noqa: E402
import wtproof  # noqa: E402

LIST = "someone/awesome-list#3"
MINE = "example-org/app#9"


def owner_pr(repo, number):
    return {"repo": repo, "number": number, "title": f"PR {number}", "url": f"https://github.com/{repo}/pull/{number}",
            "createdAt": "2026-11-02T10:00:00+09:00", "isDraft": False}


class AgingDrop(_Clock):
    def setUp(self):
        super().setUp()
        self.at("2026-11-16T10:00:00+09:00")
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "app")
        self.record("pr.state", unit="alpha", repo="o/r", number=5, state="OPEN", title="t",
                    created="2026-11-16T10:00:00+09:00")
        self.record("outbox.add", oid="ob-ask", unit="alpha", channel="C1", thread=None, kind="review-ask",
                    text="review please", prs=[REF], to=["U1"])
        self.record("outbox.done", oid="ob-ask", sent_ts="1795061530.355009")

    def refs(self):
        return [r["ref"] for rs in self.board()["inventory"]["repos"].values() for r in rs if r["kind"] == "pr"]

    def test_a_drop_answer_drops_the_pr_and_an_undrop_is_not_undone_by_that_answer(self):
        self.at("2026-11-24T12:00:00+09:00")
        self.foundry("pr", "age")
        card = self.cards("pr-aging:")[f"pr-aging:{REF}"]
        self.assertIn({"key": "drop", "label": "보드에서 제외 (내 담당 아님)"}, card["options"])
        self.foundry("decide", "answer", card["id"], "drop", "--by", "owner")
        self.foundry("pr", "age")
        self.assertEqual(self.snap()["pr_dropped"][REF]["reason"], f"owner answered {card['id']}: drop")
        self.assertNotIn(REF, self.refs())
        self.foundry("pr", "undrop", REF)
        self.foundry("pr", "age")
        self.assertNotIn(REF, self.snap()["pr_dropped"])
        self.assertIn(REF, self.refs())
        self.foundry("pr", "undrop", REF, ok=(2,))  # not a drop key any more


class RepoDrop(_Clock):
    def setUp(self):
        super().setUp()
        self.at("2026-11-24T12:00:00+09:00")
        self.record("pr.owner_open", prs=[owner_pr("someone/awesome-list", 3),
                                          owner_pr("example-org/app", 9)])

    def refs(self):
        return [r["ref"] for rs in self.board()["inventory"]["repos"].values() for r in rs if r["kind"] == "pr"]

    def test_a_repo_or_owner_pattern_hides_only_matching_prs_and_undrop_restores(self):
        self.assertEqual(sorted(self.refs()), [MINE, LIST])
        self.foundry("pr", "drop", "--repo", "someone/awesome-list", "--reason", "내 담당 아님")
        self.assertIn("someone/awesome-list#*", self.snap()["pr_dropped"])
        self.assertEqual(self.refs(), [MINE])
        actions = self.foundry("pr", "age", "--dry-run")[1]["actions"]
        self.assertNotIn(LIST, actions)
        self.assertIn(MINE, actions)
        self.foundry("pr", "undrop", "--repo", "someone/awesome-list")
        self.assertEqual(sorted(self.refs()), [MINE, LIST])
        self.foundry("pr", "drop", "--repo", "someone/*", "--reason", "개인 실험")
        self.assertEqual(self.refs(), [MINE])
        self.foundry("pr", "undrop", LIST, ok=(2,))  # hidden by the pattern, not by its own key

    def test_a_bad_pattern_or_both_forms_are_refused(self):
        for args in (("--repo", "*"), ("--repo", "*/*"), ("--repo", "someone/awesome-list#3"),
                     (LIST, "--repo", "someone/awesome-list"), ()):
            self.foundry("pr", "drop", *args, "--reason", "x", ok=(2,))
        self.assertEqual(self.snap().get("pr_dropped") or {}, {})

    def test_cards_on_a_dropped_pr_close_as_foundry_without_reading_github(self):
        import decide
        self.record("decide.open", id="d-0001", key=f"pr-aging:{LIST}", unit=None, repo="someone/awesome-list",
                    question="waiting", options=[{"key": "close", "label": "close"}, {"key": "drop", "label": "drop"}])
        self.assertEqual(decide.untracked_pr_refs(self.snap()), [LIST])
        self.foundry("pr", "drop", "--repo", "someone/awesome-list", "--reason", "내 담당 아님")
        self.assertEqual(decide.untracked_pr_refs(self.snap()), [])  # no `gh pr view` for it
        self.foundry("pr", "age")
        c = self.snap()["cards"]["d-0001"]
        self.assertEqual((c["answer"], c["answered_by"], c["moot"]), ("moot", "foundry", f"{LIST} 보드에서 제외됨"))


class RestartHint(tf.Base):
    def test_the_restart_hint_names_the_current_foremans_short_id(self):
        self.record("foreman.current", session="abcdef12-3456-7890")
        self.record("foreman.def", session="abcdef12-3456-7890", hash="000000000000")
        _, m = self.foundry("board", "--format", "json")
        self.assertEqual(m["foreman_stop"], "claude stop abcdef12")
        with mock.patch.object(i18n, "lang", return_value="en"):
            self.assertIn("foreman restart needed (`claude stop abcdef12`)", board.band(m))
        with open(os.path.join(tf.ROOT, "skills", "foundry-handoff", "SKILL.md")) as f:
            self.assertNotIn("runs `claude stop foreman`", f.read())


class JudgeSafeTexts(tf.Base):
    JUDGE = {"agent_type": "foundry:sweeper-judge", "agent_id": "J1"}

    def test_scan_texts_have_no_forbidden_character_and_a_copied_reason_passes(self):
        note = "in use by " + housekeep.users_text([{"cmd": "zsh", "pid": "10099"}])
        self.assertEqual(note, "in use by zsh pid 10099")
        nowhere = wtproof.nowhere({"files": [{"path": "foo.json", "summary": "+1 -1"}],
                                   "commits": [{"sha": "0123456789ab", "subject": "fix (wip); a|b $x `y`"}]})
        self.assertIn("foo.json +1 -1", nowhere)
        for text in (note, hook.judge_safe("found nowhere else: " + nowhere)):
            self.assertFalse(set(text) & hook.JUDGE_FORBIDDEN, text)
            cmd = (f"python3 {os.path.join(tf.SCRIPTS, 'housekeep.py')} plan --hid worktree:/w/x "
                   f"--verdict propose --reason {shlex.quote(text)}")
            self.assertTrue(hook.judge_command_ok(cmd), cmd)
        self.assertEqual(hook.JUDGE_FORBIDDEN, set("\n\r;&|<>$`()"))  # the guard itself is unchanged

    def test_the_refusal_names_the_character(self):
        hk = os.path.join(tf.SCRIPTS, "housekeep.py")
        out = self.hook("PreToolUse", tool_name="Bash", **self.JUDGE, tool_input={
            "command": f'python3 {hk} plan --hid worktree:/w/x --verdict propose --reason "in use by zsh (10099)"'})
        self.assertIn("contains `(` `)`", json.dumps(out, ensure_ascii=False))
        out = self.hook("PreToolUse", tool_name="Bash", **self.JUDGE, tool_input={"command": f"python3 {hk} execute"})
        self.assertIn("only runs `housekeep.py scan` and `housekeep.py plan`; the executor acts",
                      json.dumps(out, ensure_ascii=False))


FAKE_KEYS = ("FOUNDRY_CLAUDE", "FAKE_CLAUDE_DATA", "FAKE_CLAUDE_LOG")


class GoneSessions(tf.Base):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.dir, "claude.jsonl")
        self.data = os.path.join(self.dir, "claude.json")
        fake = {"FOUNDRY_CLAUDE": f"{sys.executable} {os.path.join(tf.ROOT, 'tests', 'fake_claude.py')}",
                "FAKE_CLAUDE_DATA": self.data, "FAKE_CLAUDE_LOG": self.log}
        getattr(self, "env").update(fake)
        keep = {k: os.environ.get(k) for k in FAKE_KEYS}
        os.environ.update(fake)  # stop_session runs in this process
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.update({k: v})
                                 for k, v in keep.items()])

    def fake(self, **d):
        with open(self.data, "w") as f:
            json.dump(d, f)

    def stop(self, name):
        import launcher
        keep = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            return launcher.stop_session(name, "sweep: unit is done")
        finally:
            os.environ.pop("FOUNDRY_STATE_DIR", None) if keep is None else os.environ.update(FOUNDRY_STATE_DIR=keep)

    def test_no_session_matching_is_stopped_and_other_failures_stay_failures(self):
        self.record("session.launch", name="lead-a", unit="a", role="line-lead", bg_id="bgA", cwd="/wt/a")
        self.record("session.launch", name="lead-b", unit="b", role="line-lead", bg_id="bgB", cwd="/wt/b")
        self.fake(stop_gone=True)
        self.assertEqual(self.stop("lead-a"), {"stopped": "lead-a", "bg_id": "bgA",
                                               "note": "claude: no session matching: already gone"})
        self.assertEqual(self.snap()["sessions"]["lead-a"]["state"], "stopped")
        self.fake(stop_rc=1)
        self.assertIn("error", self.stop("lead-b"))
        self.assertNotEqual(self.snap()["sessions"]["lead-b"]["state"], "stopped")

    def test_a_gone_stale_foreman_is_stopped_once_and_not_retried(self):
        from test_lifecycle_01119 import agent
        self.record("foreman.current", session="S0")
        self.record("foreman.current", session="S1")
        self.fake(agents=[agent("S1", "busy"), agent("S0", "idle")], stop_gone=True)
        for _ in range(2):
            r = tf.subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "foreman", "prune"],
                                  env=getattr(self, "env"), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.log) as f:
            stops = [json.loads(x)["argv"] for x in f if json.loads(x)["argv"][:1] == ["stop"]]
        self.assertEqual(stops, [["stop", "S0"]])
        self.assertEqual(self.snap()["foreman_prune"]["stop_failed"], {})
        self.assertIn("S0", self.snap()["foreman_gone"])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (AgingDrop, RepoDrop, RestartHint, JudgeSafeTexts, GoneSessions):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == "__main__":
    unittest.main()
