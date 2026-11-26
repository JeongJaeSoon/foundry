"""0.1.48: decision cards read as a bullet outline (개조식) on the text board, board.html, the pane and
`decide show`: line breaks and `- ` bullets a card was written with are kept, and an old one-paragraph card is cut
at its section labels and (1) (2) enumerations."""

import json
import os
import subprocess
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import views  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY = ("PROJ-783(OL-6a, example-org/app#1627) dev 검증 범위. 확인됨: dev 가 v1.1.37 로 동작(#1627 포함), PreSync "
          "migration Job(v1.1.37-app) Complete, alembic 체인에 6a1c2d3e4f50 포함(head 7b3d5e9f1a24), api-server/admin-ui "
          "재시작 0. 확인 불가: (1) apikey.last_used_at 증가 - a- 키 호출과 DB 조회가 필요한데 개인 PAT 라 그 경로를 타지 못함 "
          "(2) MySQL 1054 / write-failed / DB 연결 수 - dev 로그와 RDS 메트릭이 Datadog 에 없음 (3) example-org/mcp#703 dev "
          "재접속 - 대화형 OAuth 가 필요(이미지에는 포함 확인). 참고: last_seen_at, write-failed, 30일 가드는 PROJ-786(#1628) "
          "항목이고 PROJ-786 은 이미 resolve. 옵션 A: 확인 범위와 미검증 항목을 명시한 Jira 코멘트 후 PROJ-783 resolve, #703 "
          "재접속은 PROJ-791 쪽 확인으로 넘김. 옵션 B: 오너가 dev 에서 a- 키로 1회 호출하고 last_used_at 을 확인한 뒤 resolve. "
          "추천: A (Jira 수용 조건은 테스트 기반이며 #1627 에서 충족, migration 성공과 파드 정상으로 스키마 반영은 확인됨). "
          "막는 것: PROJ-783 의 Jira 코멘트와 resolve.")
STRUCTURED = ("prod 배포 시점\n- 확인됨\n  - dev 에서 D1-D3 통과\n  - 파드 재시작 0\n- 옵션 A: 오늘 배포\n- 옵션 B: 내일 오전\n"
              "- 추천: A (변경이 태그뿐)\n- 막는 것: 릴리스 마감")


class Outline(tf.Base):
    def test_the_legacy_single_line_card_becomes_sections_with_items(self):
        ol = views.outline(LEGACY)
        top = [x["text"] for x in ol if x["depth"] == 0]
        self.assertEqual(top[0], "PROJ-783(OL-6a, example-org/app#1627) dev 검증 범위")
        self.assertEqual(top[1:3], ["확인됨", "확인 불가"])
        self.assertTrue(top[3].startswith("참고: last_seen_at, write-failed"), top[3])  # short commas stay one line
        self.assertTrue(top[4].startswith("옵션 A: ") and top[5].startswith("옵션 B: "), top)
        self.assertTrue(top[6].startswith("추천: A (Jira 수용 조건은 테스트 기반이며 #1627 에서 충족, migration"), top[6])
        self.assertEqual(top[7], "막는 것: PROJ-783 의 Jira 코멘트와 resolve")
        items = [x["text"] for x in ol if x["depth"] == 1]
        self.assertEqual(items[:4], ["dev 가 v1.1.37 로 동작(#1627 포함)", "PreSync migration Job(v1.1.37-app) Complete",
                                     "alembic 체인에 6a1c2d3e4f50 포함(head 7b3d5e9f1a24)", "api-server/admin-ui 재시작 0"])
        self.assertEqual(len(items), 7)
        self.assertTrue(items[4].startswith("apikey.last_used_at 증가") and items[6].startswith("example-org/mcp#703"))

    def test_line_breaks_and_bullets_are_kept(self):
        ol = views.outline(STRUCTURED)
        self.assertEqual([(x["depth"], x["text"]) for x in ol][:4],
                         [(0, "prod 배포 시점"), (0, "확인됨"), (1, "dev 에서 D1-D3 통과"), (1, "파드 재시작 0")])
        self.assertEqual(ol[-1], {"depth": 0, "text": "막는 것: 릴리스 마감"})
        self.assertTrue(views.headline({"question": STRUCTURED}, {}).startswith("prod 배포 시점 · "))

    def test_plain_prose_stays_one_line(self):
        self.assertEqual(views.outline("이 PR 을 오늘 머지해도 되나요?"), [{"depth": 0, "text": "이 PR 을 오늘 머지해도 되나요?"}])
        self.assertEqual(views.outline(""), [])


class Surfaces(tf.Base):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "u1", "--title", "U1")
        self.foundry("decision", "ask", "--key", "u1:dev-verify-scope", "--unit", "u1", "--question", LEGACY, ok=(10,))
        qf = os.path.join(self.dir, "card.md")
        with open(qf, "w", encoding="utf-8") as f:
            f.write(STRUCTURED + "\n\n")
        self.foundry("decide", "open", "--question-file", qf, "--option", "A:오늘", "--option", "B:내일",
                     "--recommend", "A", "--asked-by", "lead", "--key", "u1:prod", "--unit", "u1")

    def board(self, fmt):
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), fmt], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if fmt == "json" else r.stdout

    def test_board_json_carries_the_outline(self):
        m = self.board("json")
        ask = next(d for d in m["asks"] if d["key"] == "u1:dev-verify-scope")
        self.assertIn({"depth": 0, "text": "확인 불가"}, ask["outline"])
        card = m["cards"][0]
        self.assertIn({"depth": 1, "text": "파드 재시작 0"}, card["outline"])

    def test_text_board_lists_bullets_under_each_headline(self):
        lines = self.board("text").splitlines()
        self.assertIn("      - dev 가 v1.1.37 로 동작(#1627 포함)", lines)
        self.assertIn("    - 확인 불가", lines)
        self.assertIn("    - 추천: A (변경이 태그뿐)", lines)
        self.assertIn("      - 파드 재시작 0", lines)

    def test_html_board_draws_nested_bullets(self):
        with open(self.board("html").strip(), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("<ul class=outline><li class=d0>PROJ-783(OL-6a, example-org/app#1627) dev 검증 범위</li>", page)
        self.assertIn("<li class=d1>dev 에서 D1-D3 통과</li>", page)

    def test_decide_show_adds_the_outline(self):
        _, out = self.foundry("decide", "show", "u1:dev-verify-scope", "--format", "json")
        self.assertIn("  - PreSync migration Job(v1.1.37-app) Complete", out["outline"])
        self.assertEqual(out["question"], LEGACY)

    def test_question_file_keeps_lines_and_refuses_a_missing_file(self):
        _, out = self.foundry("decide", "show", self.board("json")["cards"][0]["id"], "--format", "json")
        self.assertEqual(out["question"], STRUCTURED)
        qf = os.path.join(self.dir, "ask.md")
        with open(qf, "w", encoding="utf-8") as f:
            f.write("머지 시점\n- 추천: 오늘\n")
        self.foundry("decision", "ask", "--key", "u1:when", "--unit", "u1", "--question-file", qf, ok=(10,))
        self.assertEqual(self.snap()["decisions"]["u1:when"]["question"], "머지 시점\n- 추천: 오늘")
        rc, _ = self.foundry("decision", "ask", "--key", "u1:x", "--question-file", qf + ".missing", ok=(2,))
        self.assertEqual(rc, 2)

    def test_lead_texts_ask_for_bullets(self):
        for name in ("agents/line-lead.md", "agents/foreman.md", "skills/foundry-escalate/SKILL.md"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                self.assertIn("개조식", f.read(), name)
