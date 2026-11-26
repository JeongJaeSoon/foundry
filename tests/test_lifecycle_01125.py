"""0.1.125: a stale foreman entry that `claude agents` still shows as working but that has no pid, or a dead one, is
stopped (busy entries, and unknown ones with a live pid, still wait); a merged PR in a repo with no tag at all is
no-release, not 머지됨·릴리스 대기, from one cached read-only `gh api repos/<slug>/tags?per_page=1` per repo, and a gh
that cannot be read leaves it release-wait. Fake listings, a fake liveness check and a fake gh only."""

import datetime as dt
import os
import sys
from unittest import mock

from test_foundry import SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import factory  # noqa: E402
import launcher  # noqa: E402

CUR = "cur-0000"
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 27, 15, 0, tzinfo=KST)
P = {"release": {"none": [], "tag": "*", "watch_days": 14}}


def agent(sid, status=None, state="working", pid=None):
    return {"name": "foreman", "sessionId": sid, "id": sid[:8], "kind": "background",
            **({"status": status} if status else {}), **({"state": state} if state else {}),
            **({"pid": pid} if pid is not None else {})}


class StaleForemen(Base):
    CREW = False

    def test_no_pid_or_dead_pid_stops_busy_and_live_unknown_wait(self):
        snap = {"foreman_current": {"session": CUR},
                "foreman_sessions": {s: "t" for s in (CUR, "nopid", "dead", "live", "busy-nopid", "idle-live")}}
        listing = [agent(CUR, "busy", pid=1), agent("nopid"), agent("dead", pid=4242), agent("live", pid=4343),
                   agent("busy-nopid", "busy"), agent("idle-live", "idle", pid=4343)]
        alive = {4242: False, 4343: True}.get
        got = {e["sessionId"]: act for e, act in launcher.stale_foremen(snap, listing, alive=alive)}
        self.assertEqual(got, {"nopid": "stop", "dead": "stop", "live": "wait", "busy-nopid": "wait",
                               "idle-live": "stop"})

    def test_pid_alive(self):
        self.assertTrue(launcher.pid_alive(os.getpid()))
        with mock.patch.object(launcher.os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(launcher.pid_alive(99999))
        with mock.patch.object(launcher.os, "kill", side_effect=PermissionError):
            self.assertTrue(launcher.pid_alive(1))  # someone else's live process


class NoTags(Base):
    CREW = False

    def setUp(self):
        super().setUp()
        factory._has_tags.clear()
        self.calls = []
        env = mock.patch.dict(os.environ, {"FOUNDRY_STATE_DIR": self.dir, "FOUNDRY_NOW": NOW.isoformat()})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(factory._has_tags.clear)

    def fake_gh(self, answers):
        def gh(*args):
            self.calls.append(args)
            return answers.get(args[1])
        return mock.patch.object(factory, "gh", gh)

    def state(self, repo, n=1):
        pr = {"repo": repo, "number": n, "state": "MERGED", "merged": (NOW - dt.timedelta(days=1)).isoformat()}
        return factory.pr_release_state(pr, P, {}, None, NOW)

    def test_a_repo_with_no_tags_is_no_release_and_gh_is_asked_once(self):
        with self.fake_gh({"repos/example-org/notags/tags?per_page=1": [],
                           "repos/example-org/app/tags?per_page=1": [{"name": "v1.0.0"}]}):
            self.assertEqual([self.state("example-org/notags", n) for n in (1, 2, 3)], ["no-release"] * 3)
            self.assertEqual(self.state("example-org/app"), "release-wait")
            self.assertEqual(self.state("example-org/app", 2), "release-wait")
        self.assertEqual(self.calls, [("api", "repos/example-org/notags/tags?per_page=1"),
                                      ("api", "repos/example-org/app/tags?per_page=1")])

    def test_the_state_cache_spares_a_new_process_and_expires(self):
        with self.fake_gh({"repos/example-org/notags/tags?per_page=1": []}):
            self.assertEqual(self.state("example-org/notags"), "no-release")
        factory._has_tags.clear()  # a new process: only repo_tags.json is left
        with self.fake_gh({}):
            self.assertEqual(self.state("example-org/notags"), "no-release")
        self.assertEqual(len(self.calls), 1)
        factory._has_tags.clear()
        later = (NOW + factory.TAGS_TTL + dt.timedelta(minutes=1)).isoformat()
        with mock.patch.dict(os.environ, {"FOUNDRY_NOW": later}), \
                self.fake_gh({"repos/example-org/notags/tags?per_page=1": [{"name": "v0.1.0"}]}):
            self.assertEqual(self.state("example-org/notags"), "release-wait")  # its first tag was cut
        self.assertEqual(len(self.calls), 2)

    def test_an_unreadable_gh_is_release_wait_never_no_release(self):
        with self.fake_gh({}):  # every call fails (None)
            self.assertEqual(self.state("example-org/notags"), "release-wait")
        factory._has_tags.clear()  # 0.1.129: a new process within TAGS_UNKNOWN_TTL does not ask gh again
        with self.fake_gh({"repos/example-org/notags/tags?per_page=1": []}):
            self.assertEqual(self.state("example-org/notags"), "release-wait")
        self.assertEqual(len(self.calls), 1)
        factory._has_tags.clear()
        later = (NOW + factory.TAGS_UNKNOWN_TTL + dt.timedelta(minutes=1)).isoformat()
        with mock.patch.dict(os.environ, {"FOUNDRY_NOW": later}), \
                self.fake_gh({"repos/example-org/notags/tags?per_page=1": []}):
            self.assertEqual(self.state("example-org/notags"), "no-release")  # asked again once it expired
        factory._has_tags.clear()
        os.remove(os.path.join(self.dir, "repo_tags.json"))
        with self.fake_gh({"repos/example-org/notags/tags?per_page=1": {"message": "Not Found"}}):
            self.assertEqual(self.state("example-org/notags"), "release-wait")

    def test_the_real_gh_command_shape_through_the_fake_gh(self):
        self.gh({"api repos/example-org/notags/tags?per_page=1": []})
        with mock.patch.dict(os.environ, {"FOUNDRY_GH": self.env["FOUNDRY_GH"],
                                          "FAKE_GH_DATA": self.env["FAKE_GH_DATA"]}):
            self.assertEqual(self.state("example-org/notags"), "no-release")
            self.assertEqual(self.state("example-org/other"), "release-wait")  # not in the fake: gh fails
