"""0.1.115: one board state per unit. The group a row sits in and its state cell both come from board.unit_state: a
finished unit with an open PR is 팀 대기, one with a merged PR no release tag holds yet is 머지됨·릴리스 대기 (only in
a repo with a release step), a done release unit or a released PR is 릴리스 완료, cancelled is 취소, and a closed cell
never shows a wait, plan or deadline. The groups are fixed and ordered on every surface. `foundry board archive`
records finished units into the archive view (`foundry board --archived`) and deletes nothing; prefs
board.archive_days moves them there by age."""

import datetime as dt
import json
import os
import subprocess
import sys

from test_foundry import SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import board  # noqa: E402

NOW = dt.datetime(2026, 11, 27, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
CTX = {"p": {"release": {"none": ["*ops"], "tag": "*"}, "board": {"archive_days": 7}}, "rs": {}, "now": NOW}
FOREMAN = {"FOUNDRY_ROLE": "foreman"}
CLOSED_AT = "2026-11-15T10:00:00+09:00"


def unit(status="done", prs=(), uid="u1", **kw):
    return {"id": uid, "kind": "project", "status": status, "prs": list(prs), "waits": [],
            "updated": "2026-11-26T10:00:00+09:00", **kw}


def pr(n, state="MERGED", repo="example-org/app", **kw):
    return {"repo": repo, "number": n, "state": state, **kw}


class UnitState(Base):
    def group(self, u):
        st = board.unit_state(u, CTX)
        return st["group"], st["detail"]

    def test_a_finished_unit_lands_by_where_its_prs_stand(self):
        self.assertEqual(self.group(unit(prs=[pr(1, "OPEN")])), ("waiting_others", "PR #1"))
        self.assertEqual(self.group(unit(prs=[pr(2)])), ("release_wait", "#2"))
        self.assertEqual(self.group(unit(prs=[pr(3, repo="example-org/cluster-ops")])), ("done", None))
        self.assertEqual(self.group(unit(prs=[pr(4, released="v1.2.0")])), ("released", "v1.2.0"))
        self.assertEqual(self.group(unit(prs=[pr(5, "CLOSED")])), ("done", None))
        self.assertEqual(self.group(unit()), ("done", None))
        self.assertEqual(self.group(unit("cancelled", [pr(6)])), ("cancelled", None))

    def test_a_done_release_unit_is_released_even_with_its_own_deploy_prs(self):
        u = unit(uid="release-app-1", prs=[pr(7, repo="example-org/cluster-ops")],
                 release={"repo": "example-org/app", "prs": ["example-org/app#2"]},
                 deployed={"prod": {"tag": "v1.3.0"}})
        self.assertEqual(self.group(u), ("released", "v1.3.0"))

    def test_open_units_keep_their_status_group(self):
        self.assertEqual(self.group(unit("waiting_owner")), ("waiting_owner", None))
        self.assertEqual(self.group(unit("blocked")), ("waiting_others", None))
        self.assertEqual(self.group(unit("parked")), ("queue", None))

    def test_a_closed_row_carries_no_wait_plan_or_deadline(self):
        self.record("unit.new", id="u1", title="U1")
        self.record("wait.add", id="u1", wid="w1", kind="time", until="2026-11-25T17:10:00+09:00", note="window")
        self.record("unit.status", id="u1", status="done")
        snap = self.snap()
        snap["units"]["u1"]["mode"] = "plan"
        m = board.model(snap)
        [r] = [r for s in m["sections"] for r in s["rows"] if r["id"] == "u1"]
        self.assertEqual((r["group"], r["summary"], board.flags(r)), ("done", "", []))
        self.assertNotIn("17:10", board.text_of(m))


class Groups(Base):
    def test_fixed_groups_in_order_in_each_language(self):
        want = {"ko": ["진행 중", "오너 대기", "팀 대기", "대기열", "머지됨·릴리스 대기", "릴리스 완료", "완료", "취소"],
                "ja": ["進行中", "オーナー待ち", "チーム待ち", "キュー", "マージ済み・リリース待ち", "リリース完了", "完了", "取消"],
                "en": ["In progress", "Waiting on owner", "Team wait", "Queue", "Merged · release wait", "Released",
                       "Done", "Cancelled"]}
        root = os.path.dirname(SCRIPTS)
        for lang, labels in want.items():
            with open(os.path.join(root, "defaults", "i18n", lang, "board.json")) as f:
                cat = json.load(f)
            self.assertEqual([cat[f"section_{k}"] for k in board.SECTION_KEYS], labels)


class Archive(Base):
    def cli(self, *args, env=None):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args],
                           env={**self.env, **(env or {})}, capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr

    def setUp(self):
        super().setUp()
        for uid, status in (("fin", "done"), ("gone", "cancelled"), ("open", "in_progress"), ("rw", "done")):
            self.record("unit.new", id=uid, title=uid, ts=CLOSED_AT)
            self.record("unit.status", id=uid, status=status, ts=CLOSED_AT)
        self.record("pr.opened", unit="rw", repo="example-org/app", number=9, ts=CLOSED_AT)

    def board_ids(self, *extra):
        rc, out, err = self.cli("board", "--format", "json", *extra)
        self.assertEqual(rc, 0, err)
        m = json.loads(out)
        return {r["id"]: r["group"] for s in m["sections"] for r in s["rows"]}, m["archived_count"]

    def test_archive_records_finished_units_only_and_deletes_nothing(self):
        rc, _, err = self.cli("board", "archive", env=FOREMAN)
        self.assertEqual(rc, 2)
        self.assertIn("--older", err)
        rc, out, _ = self.cli("board", "archive", "--all-done", "--dry-run")
        self.assertEqual(sorted(x["id"] for x in json.loads(out)["would_archive"]), ["fin", "gone"])
        self.assertEqual(self.cli("board", "archive", "--all-done", env=FOREMAN)[0], 2)  # the foreman needs a quote
        rc, out, err = self.cli("board", "archive", "--all-done", "--quote", "완료된 거 정리해줘", env=FOREMAN)
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(json.loads(out)["archived"]), ["fin", "gone"])
        ids, n = self.board_ids()
        self.assertEqual((sorted(ids), n), (["open", "rw"], 2))
        self.assertEqual(ids["rw"], "waiting_others")  # its PR is still open: never archived
        self.assertEqual(sorted(self.board_ids("--archived")[0]), ["fin", "gone"])
        self.assertEqual(len(self.snap()["units"]), 4)
        self.record("unit.status", id="fin", status="in_progress")  # reopened: back on the board
        self.assertIsNone(self.snap()["units"]["fin"]["archived"])

    def test_age_archives_after_archive_days(self):
        self.assertEqual(self.board_ids()[1], 0)  # FOUNDRY_NOW 11-18: closed 3 days ago
        rc, out, _ = self.cli("board", "archive", "--older", "3d", "--dry-run")
        self.assertEqual(sorted(x["id"] for x in json.loads(out)["would_archive"]), ["fin", "gone"])
        rc, out, _ = self.cli("board", "archive", "--older", "4d", "--dry-run")
        self.assertEqual(json.loads(out)["would_archive"], [])
        self.env["FOUNDRY_NOW"] = "2026-11-30T10:00:00+09:00"
        ids, n = self.board_ids()
        self.assertEqual((sorted(ids), n), (["open", "rw"], 2))
