"""0.1.59: 오너 대기 lines are `kind · ticket/unit · first question line · links (id)`, grouped 결정 / 승인 / 할 일 /
확인, never a card body and never [REDACTED]; items naming a local path stay on the board; 가동 라인 counts units
being worked now; the report's day runs 05:00 to 05:00 local."""

import datetime as dt
import sys

import test_report_0150 as base
from test_report_0150 import DAY

sys.path.insert(0, base.tf.SCRIPTS)
import report  # noqa: E402

J = dt.timezone(dt.timedelta(hours=9))


class OwnerLines(base.Base):
    def parent(self):
        _, out = self.foundry("report", "daily", "--format", "slack")
        return out["messages"][0]["text"].split("\n")

    def test_one_line_per_item_grouped_by_kind_with_the_id_last(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "U1", "--ticket", "PROJ-5")
        self.foundry("decide", "open", "--question", "sample-svc-ja 를 prod 로 올릴까요?\n- 상황: dev 검증 끝\n- 추천: 올림",
                     "--option", "A:올림", "--option", "B:보류", "--asked-by", "lead", "--key", "u1:go", "--unit", "u1")
        self.foundry("owner", "todo", "add", "--none", "--by", "foreman", "--text", "[REDACTED] 를 sops 에 등록",
                     "--kind", "secret", "--why-owner", "secret 값은 오너만 안다")
        self.foundry("owner", "todo", "add", "--none", "--by", "foreman", "--kind", "other",
                     "--text", "trust 등록 실패: /Users/someone/work/wt 를 신뢰해 주세요", "--why-owner", "사람만 클릭")
        lines = self.parent()
        i = lines.index("**결정 필요**")  # 0.1.88: decisions are their own block, then 오너 대기
        decs = lines[i + 1:lines.index("", i)]
        self.assertEqual(len(decs), 1)
        self.assertTrue(decs[0].startswith("결정 · PROJ-5 · sample-svc-ja 를 prod 로 올릴까요? (d-"), decs)
        i = lines.index("**오너 대기**")
        owner = lines[i + 1:lines.index("", i) if "" in lines[i:] else len(lines)]
        self.assertTrue(owner[0].startswith("할 일 · … 를 sops 에 등록 (카드 참조) (ot-"), owner)
        self.assertEqual(len(owner), 1)  # the local-path item stays on the board
        self.assertFalse(any("상황" in x or "/Users/" in x for x in lines))
        self.assertEqual(report.slack_lint([{"text": "\n".join(lines)}]), [])


class LiveLines(base.Base):
    def test_in_progress_units_count_as_lines_after_midnight(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "U1")
        self.record("unit.status", id="u1", status="in_progress")
        _, out = self.at("2026-11-19T00:30:00+09:00", "report", "daily", "--format", "slack")
        text = out["messages"][0]["text"]
        self.assertIn("가동 라인 1", text)
        self.assertIn(f"일보 ({DAY})", text)  # 00:30 still belongs to the day that ended


class DayBoundary(base.Base):
    def test_before_five_is_the_previous_day(self):
        self.assertEqual(report._day(dt.datetime(2026, 11, 20, 4, 59, tzinfo=J), J), "2026-11-19")
        self.assertEqual(report._day(dt.datetime(2026, 11, 20, 5, 0, tzinfo=J), J), "2026-11-20")
        self.assertTrue(report._on("2026-11-20T01:00:00+09:00", "2026-11-19", J))


if __name__ == "__main__":
    import unittest
    unittest.main()
