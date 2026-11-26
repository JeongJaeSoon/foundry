"""0.1.30: the 인계 section is structured, with 결정 대기 read live; PR and branch states follow GitHub's colours."""

import os
import sys
import unittest

import test_foundry as tf
from test_foundry import SCRIPTS

sys.path.insert(0, SCRIPTS)
import board  # noqa: E402
import factory  # noqa: E402
import views  # noqa: E402

TEXT = """# 인수인계 2026-11-19 08:00 (기준: 11-18 08:00 이후)
## 완료 (1)
- alpha: Alpha project
## 결정 대기 (2)
- alpha · Go for prod? Merge lines: `gh pr merge 1 -R example-org/app --squash` · 추천 go · 막는 것: alpha
- beta · Already answered question
## 아차 사고 (1)
- [deny-then-similar] alpha: after deny of `gh pr view 1 -R example-org/app --json state` (hook): `gh pr view 1`
## 정체 (0)
- 없음"""


def snap_with(cards=None, units=None, ts="2026-11-19T08:00:00+09:00"):
    return {"handover": {"text": TEXT, "ts": ts},
            "units": units or {"alpha": {"id": "alpha", "updated": "2026-11-19T09:00:00+09:00"},
                               "beta": {"id": "beta", "updated": "2026-11-19T07:00:00+09:00"}},
            "cards": cards or {}, "decisions": {}}


class HandoverView(unittest.TestCase):
    def test_sections_are_unit_and_one_line_and_decisions_are_live(self):
        cards = {"c1": {"id": "c1", "unit": "alpha", "question": "Ship the `foundry x` fix now? Long details follow.",
                        "opened": "2026-11-19T10:00:00+09:00"},
                 "c2": {"id": "c2", "unit": "beta", "question": "answered", "answer": "go",
                        "answered": "2026-11-19T09:30:00+09:00"}}
        v = factory.handover_view(snap_with(cards))
        secs = {x["title"]: x for x in v["sections"]}
        self.assertEqual(secs["완료"]["items"], [{"unit": "alpha", "line": "Alpha project", "id": None}])
        # the snapshot listed two decisions; only the still-open one shows, read now
        self.assertEqual([(x["unit"], x["id"]) for x in secs["결정 대기"]["items"]], [("alpha", "c1")])
        self.assertTrue(secs["결정 대기"]["live"])
        self.assertEqual(secs["결정 대기"]["count"], 1)
        self.assertEqual(secs["결정 대기"]["items"][0]["line"], "Ship the (명령) fix now?")
        # tags dropped, commands folded, the unit recognised
        self.assertEqual(secs["아차 사고"]["items"][0], {"unit": "alpha", "line": "after deny of (명령) (hook): (명령)", "id": None})
        self.assertEqual(secs["정체"]["items"], [])
        self.assertEqual(v["at"], "11-19 08:00")
        # alpha updated after 08:00, c1 opened and c2 answered after it; beta's update was before
        self.assertEqual(v["changes"], 3)

    def test_no_handover_is_none(self):
        self.assertIsNone(factory.handover_view({"units": {}}))

    def test_board_text_and_html_never_print_the_raw_markdown(self):
        m = {"handover": factory.handover_view(snap_with())}
        self.assertEqual(board.handover_head(m["handover"]), "인계 · 11-19 08:00 기준 · 이후 변경 1건")
        sections = board.handover_sections(m["handover"])
        self.assertNotIn("## ", "".join(x["title"] for x in sections))
        self.assertEqual([x["title"] for x in sections], ["완료", "결정 대기", "아차 사고", "정체", "PR 변경"])

    def test_an_item_with_no_known_unit_prints_its_line_alone(self):
        snap = snap_with()
        snap["handover"]["text"] += "\n## 기타 (1)\n- example-org/app#9 (review, carol 대기)"
        v = factory.handover_view(snap)
        sec = next(x for x in v["sections"] if x["title"] == "기타")
        self.assertEqual(sec["items"], [{"unit": None, "line": "example-org/app#9 (review, carol 대기)", "id": None}])


class GitHubStates(unittest.TestCase):
    def test_lifecycle_words(self):
        self.assertEqual([views.gh_state("review"), views.gh_state("draft"), views.gh_state("merged"),
                          views.gh_state(None, "CLOSED"), views.gh_state(None, "MERGED"), views.gh_state("release-wait")],
                         ["open", "draft", "merged", "closed", "merged", "merged"])
        self.assertEqual([views.gh_review("APPROVED"), views.gh_review("CHANGES_REQUESTED"), views.gh_review(None, "review"),
                          views.gh_review(None, "draft")], ["approved", "changes", "pending", None])

    def test_board_html_colours_pass_contrast_in_both_themes(self):
        def lum(h):
            c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

        def ratio(a, b):
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)
        self.assertEqual(set(board.GH_LIGHT), set(board.GH_DARK))
        for k in board.GH_LIGHT:
            self.assertGreaterEqual(ratio(board.GH_LIGHT[k], "#ffffff"), 4.5, k)
            self.assertGreaterEqual(ratio(board.GH_DARK[k], "#0d1117"), 4.5, k)
        self.assertEqual((board.GH_LIGHT["open"], board.GH_LIGHT["merged"], board.GH_LIGHT["closed"]),
                         ("#1a7f37", "#8250df", "#d1242f"))

    def test_gh_html_carries_the_state_class_icon_and_review_accent(self):
        out = board.gh_html("merged", "approved")
        self.assertIn("class='gh gh-merged'", out)
        self.assertIn('aria-label="머지됨"', out)
        self.assertIn("rv-approved", out)
        self.assertIn("✓ 승인", out)


class HandoverRendered(tf.Base):
    def test_board_prints_a_unitless_item_without_a_placeholder(self):
        self.record("report.handover", text="# 인수인계\n## 기타 (1)\n- example-org/app#9 (review, carol 대기)")
        snap = self.snap()
        self.assertIn("<li>example-org/app#9 (review, carol 대기)</li>", board.render_html(snap))
        self.assertIn("\n    example-org/app#9 (review, carol 대기)", board.render_text(snap))


class BranchRows(tf.Base):
    def test_a_branch_shows_its_pr_state_or_no_pr(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha", "--repo", "example-org/app")
        for b in ("feat/a", "feat/b"):
            self.record("branch.pushed", unit="alpha", repo="example-org/app", branch=b, sha="abc1234")
        self.record("pr.state", unit="alpha", repo="example-org/app", number=7, state="MERGED", branch="feat/a",
                    review="APPROVED", title="a")
        snap = self.snap()
        keep = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        try:
            inv = factory.inventory(snap, live={}, rs={})
        finally:
            os.environ["FOUNDRY_STATE_DIR"] = keep or ""
            if keep is None:
                os.environ.pop("FOUNDRY_STATE_DIR")
        rows = {r["ref"]: r for rs in inv["repos"].values() for r in rs if r["kind"] == "branch"}
        self.assertEqual((rows["feat/a"]["gh"], rows["feat/a"]["gh_review"], rows["feat/a"]["pr"]),
                         ("merged", "approved", "example-org/app#7"))
        self.assertEqual((rows["feat/b"]["gh"], rows["feat/b"]["pr"]), (None, None))


if __name__ == "__main__":
    unittest.main()
