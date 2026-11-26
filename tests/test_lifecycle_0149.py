"""0.1.49: every PR ref the board prints carries a url (recorded, else GitHub's page for an owner/repo slug); a
team/unit wait names the unit carrying the work with its PRs; dispatcher asks about merged PRs, or that say there is
nothing to send, drop; stopped hands are listed by name."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import factory  # noqa: E402
import prefs  # noqa: E402
import views  # noqa: E402

APP = "example-org/app"


def unit(uid, **fields):
    return {"id": uid, "kind": "project", "status": "in_progress", "repo": APP, "created": None,
            "keys": {"ticket": [], "pr": [], "topic": []}, **fields}


def row(uid, **fields):
    return {"id": uid, "status": "in_progress", "waits": [], "approvals": [], "hands": [], **fields}


class PrUrl(unittest.TestCase):
    def test_recorded_url_first_then_github_for_a_slug(self):
        self.assertEqual(views.pr_url({"url": "https://example.test/p/1", "repo": APP, "number": 1}), "https://example.test/p/1")
        self.assertEqual(views.pr_url({"repo": APP, "number": 7}), f"https://github.com/{APP}/pull/7")
        self.assertIsNone(views.pr_url({"repo": "app", "number": 7}))  # a bare repo name: no guess
        self.assertIsNone(views.pr_url({"repo": APP}))


class WaitUnit(unittest.TestCase):
    def snap(self, **units):
        return {"units": units}

    def test_a_ref_naming_a_unit_is_that_unit(self):
        s = self.snap(me=unit("me"), other=unit("other"))
        self.assertEqual(views.wait_unit(s, "me", {"kind": "unit", "ref": "other"}), "other")
        self.assertIsNone(views.wait_unit(s, "me", {"kind": "unit", "ref": "me"}))

    def test_a_team_wait_prefers_the_named_open_unit_sharing_the_ticket_then_the_newest(self):
        tk = {"ticket": ["PROJ-794"], "pr": [], "topic": []}
        s = self.snap(
            me=unit("me", keys=tk, notes=[{"text": "asked PROJ-794-infra for the facets"}]),
            **{"PROJ-794-infra": unit("PROJ-794-infra", keys=tk, created="2026-11-17T10:00:00+09:00"),
               "PROJ-794-later": unit("PROJ-794-later", keys=tk, created="2026-11-18T10:00:00+09:00"),
               "PROJ-794-done": unit("PROJ-794-done", keys=tk, status="done", created="2026-11-19T10:00:00+09:00"),
               "elsewhere": unit("elsewhere", created="2026-11-19T10:00:00+09:00")})
        w = {"kind": "team", "ref": "sre"}
        self.assertEqual(views.wait_unit(s, "me", w), "PROJ-794-infra")  # named in the notes
        s["units"]["me"]["notes"] = []
        self.assertEqual(views.wait_unit(s, "me", w), "PROJ-794-later")  # else the newest open one; done never
        self.assertIsNone(views.wait_unit(s, "me", {"kind": "ci", "ref": "x"}))  # only team / unit waits resolve
        s["units"]["me"]["keys"] = {"ticket": [], "pr": [], "topic": []}
        self.assertIsNone(views.wait_unit(s, "me", w))  # no shared ticket: unsure, so none


class DispatcherResolved(unittest.TestCase):
    def test_quiet_and_merged_pr_asks_drop_open_ones_stay(self):
        prs = [{"number": 12, "state": "MERGED"}, {"number": 13, "state": "OPEN"}]
        self.assertTrue(views.dispatcher_resolved("dispatcher: nothing outbound", prs))
        self.assertTrue(views.dispatcher_resolved(f"review ask for {APP}#12 via dispatcher", prs))
        self.assertFalse(views.dispatcher_resolved("review ask for #12 and #13 via dispatcher", prs))
        self.assertFalse(views.dispatcher_resolved("Datadog query still to be posted via dispatcher", prs))


class UnitView(unittest.TestCase):
    def test_wait_target_unit_prs_urls_and_stopped_hands(self):
        tk = {"ticket": ["PROJ-794"], "pr": [], "topic": []}
        s = {"units": {
            "me": unit("me", keys=tk, prs=[{"repo": APP, "number": 12, "state": "MERGED", "title": "collector"}],
                       notes=[{"ts": "2026-11-18T10:00:00+09:00", "text": f"review ask for {APP}#12 via dispatcher"},
                              {"ts": "2026-11-18T10:05:00+09:00", "text": "dispatcher: nothing outbound"}]),
            "infra": unit("infra", keys=tk, status="waiting_others",
                          prs=[{"repo": "example-org/infra", "number": 5, "state": "OPEN", "title": "facets"}]),
        }}
        r = row("me", status="waiting_others", waits=[{"wid": "w", "kind": "team", "ref": "sre"}],
                hands=[{"name": "impl", "role": "hand", "state": "stopped", "ts": "2026-11-18T09:00:00+09:00"},
                       {"name": "fixer", "role": "hand", "state": "running"}])
        v = views.unit_view(s, r, views.cl.now())
        w = v["waits"][0]
        self.assertEqual((w["unit"], w["unit_status"]), ("infra", "waiting_others"))
        self.assertEqual([(p["ref"], p["url"]) for p in w["unit_prs"]],
                         [("example-org/infra#5", "https://github.com/example-org/infra/pull/5")])
        self.assertEqual([p["url"] for p in v["prs"]], [f"https://github.com/{APP}/pull/12"])
        self.assertEqual(v["dispatcher"], [])  # the merged PR's ask and the quiet line are not work
        self.assertEqual(v["hands_stopped_list"], [{"name": "impl", "role": "hand", "state": "stopped",
                                                    "ts": "2026-11-18T09:00:00+09:00"}])


class InventoryUrls(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()  # factory.inventory runs in-process: never the machine's live state dir
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        patcher = mock.patch.dict(os.environ, {"FOUNDRY_STATE_DIR": d})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_branch_and_a_worktree_with_a_pr_carry_its_url(self):
        pr = {"repo": APP, "number": 9, "state": "OPEN", "branch": "feat/a", "opened": "2026-11-18T09:00:00+09:00"}
        s = {"units": {"u1": unit("u1", prs=[pr], branches=["feat/a", "feat/b"],
                                  worktree={"path": "/nonexistent/wt/u1", "repo": APP, "branch": "feat/a"})},
             "nearmiss": [], "costs": {}}
        inv = factory.inventory(s, p=prefs.defaults(), rs={})
        rows = {(r["kind"], r["ref"]): r for r in inv["repos"][APP]}
        url = f"https://github.com/{APP}/pull/9"
        self.assertEqual(rows[("branch", "feat/a")]["url"], url)
        self.assertNotIn("url", rows[("branch", "feat/b")])
        self.assertEqual((rows[("worktree", "/nonexistent/wt/u1")]["pr"], rows[("worktree", "/nonexistent/wt/u1")]["url"]),
                         (f"{APP}#9", url))


if __name__ == "__main__":
    unittest.main()
