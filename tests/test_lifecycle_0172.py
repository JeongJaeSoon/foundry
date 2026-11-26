"""0.1.72: a finished unit's detail keeps only open asks; decisions come as outlines (open) or `key → answer` (answered);
a PR the notes or questions name shows as a related PR; a stale trailing state leaves the listed title."""

import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import views  # noqa: E402


class FinishedDetail(tf.Base):
    def view(self, uid):
        _, m = self.foundry("board", "--format", "json")
        return next(r for s in m["sections"] for r in s["rows"] if r["id"] == uid)

    def test_done_unit(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "group keys — dev 検証待ち")
        self.record("unit.checkpoint", id="u1", note="Parked: the owner must run the dev verify in example-org/webapp#1627.")
        self.foundry("decision", "ask", "--key", "u1:scope", "--unit", "u1", "--question",
                     "범위? 확인됨: dev 배포 끝 확인 불가: prod 영향 옵션 A: 지금 옵션 B: 나중 추천: A", ok=(0, 10))
        open_view = self.view("u1")["view"]
        self.assertTrue(any("dev verify" in x for x in open_view["owner_todos"]))
        self.assertTrue(open_view["decisions"][0]["open"])
        self.assertGreater(len(open_view["decisions"][0]["outline"]), 3)
        self.assertEqual([x["ref"] for x in open_view["related_prs"]], ["example-org/webapp#1627"])
        self.foundry("decision", "answer", "--key", "u1:scope", "--answer", "A")
        self.record("unit.status", id="u1", status="done")
        r = self.view("u1")
        v = r["view"]
        self.assertTrue(v["closed"])
        self.assertEqual(v["owner_todos"], [])
        self.assertEqual((v["decisions"][0]["open"], v["decisions"][0]["answer"]), (False, "A"))
        self.assertEqual((r["title"], r["title_full"]), ("group keys", "group keys — dev 検証待ち"))

    def test_display_title(self):
        self.assertEqual(views.display_title("x — dev 検証待ち", "in_progress"), "x — dev 検証待ち")
        self.assertEqual(views.display_title("x — review 대기", "done"), "x")
        self.assertEqual(views.display_title("OL-6a: keys - client labels", "done"), "OL-6a: keys - client labels")


if __name__ == "__main__":
    import unittest
    unittest.main()
