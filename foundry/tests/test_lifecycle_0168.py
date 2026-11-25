"""0.1.68 report polish: a secret-looking 오너 대기 line keeps its unit and subject; an approval reads as what it does
(`webapp#1647 머지`); `foundry intake status` and an intake board line; one near-miss
list (kaizen's) on the board; `foundry up --check` finds drifted crons; the band's pressure label comes from the
catalog; init lists the repos it keeps."""

import json
import os
import subprocess
import sys

import test_foundry as tf
import test_report_0150 as base

sys.path.insert(0, tf.SCRIPTS)
import report  # noqa: E402


class OwnerLines(base.Base):
    def parent(self):
        _, out = self.foundry("report", "daily", "--format", "slack")
        return out["messages"][0]["text"].split("\n")

    def owner(self):
        lines = self.parent()
        i = lines.index("**오너 대기**")
        return lines[i + 1:lines.index("", i)]

    def test_secret_lines_keep_unit_and_subject(self):
        self.foundry("unit", "new", "--id", "u771", "--title", "stg n8n 시크릿 설정", "--ticket", "PROJ-771")
        self.foundry("owner", "todo", "add", "--unit", "u771", "--by", "foreman", "--kind", "secret",
                     "--text", "[REDACTED]", "--why-owner", "secret 값은 오너만 안다")
        self.foundry("owner", "todo", "add", "--none", "--by", "foreman", "--kind", "credential",
                     "--text", "token", "--why-owner", "사람만 발급")
        owner = self.owner()
        self.assertTrue(any(x.startswith("할 일 · PROJ-771 · stg n8n 시크릿 설정 (카드 참조) (ot-") for x in owner), owner)
        self.assertTrue(any(x.startswith("할 일 · 자격 증명 (카드 참조) (ot-") for x in owner), owner)
        self.assertFalse(any("[REDACTED]" in x for x in owner))

class Approval(tf.Base):
    def test_approval_reads_as_what_it_does(self):
        self.assertEqual(report.approval_what("Bash: gh pr merge 1647 -R example-org/webapp --squash"), "webapp#1647 머지")
        self.assertEqual(report.approval_what("Bash: gh pr review 9 -R o/x --approve"), "x#9 승인")
        line = report.owner_line({"id": "ap-1", "kind": "approval", "where": "PROJ-795",
                                  "text": "Bash: gh pr merge 1647 -R example-org/webapp --squash --match-head-commit a"},
                                 "md")
        self.assertEqual(line, "승인 · PROJ-795 · webapp#1647 머지 (ap-1)")


class IntakeStatus(tf.Base):
    def test_status_and_board_line(self):
        self.gh({"search prs --review-requested @me --state open": [], "search issues --assignee @me --state open": []})
        self.foundry("intake", "scan")
        self.record("intake.scan", source="github", seen=["o/x#1"], proposed=["o/x#2"], capped=["o/x#3"], unread=[])
        _, st = self.foundry("intake", "status")
        self.assertEqual((st["last"]["proposed"], st["last"]["capped"]), (1, 1))
        self.assertEqual(st["schedule"], "intake-scan")
        self.assertTrue(st["next_run"].startswith("2026-11-18T10:15"), st)  # FOUNDRY_NOW is 10:00, cron 15 9-19 * * 1-5
        _, text = self.foundry("board")
        self.assertIn("intake · 마지막 스캔", text)
        self.assertIn("제안 1 · 상한 보류 1 · 다음 11-18 10:15", text)


class OneNearMissList(tf.Base):
    def test_handover_near_misses_give_way_to_kaizen(self):
        import factory
        text = "# 인수인계\n## 완료 (0)\n- 없음\n## 아차 사고 (1)\n- u1: after deny of x\n"
        snap = {"units": {"u1": {"id": "u1"}}, "cards": {}, "decisions": {}, "handover": {"text": text, "ts": None}}
        titles = lambda s: [x["title"] for x in factory.handover_view(s, inv={"repos": {}})["sections"]]  # noqa: E731
        self.assertIn("아차 사고", titles(snap))
        snap["kaizen"] = {"text": "# 주간 카이젠\n## 아차 사고 (1)\n- x: 1", "ts": None}
        self.assertNotIn("아차 사고", titles(snap))


class CronDrift(tf.Base):
    def check(self, live):
        p = os.path.join(self.dir, "crons.json")
        with open(p, "w") as f:
            json.dump(live, f)
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "up", "--check", "--crons", p],
                           env=self.env, capture_output=True, text=True)
        return r.returncode, json.loads(r.stdout)

    def test_drifted_missing_and_extra(self):
        import prefs
        import watchers
        scheds = prefs.load()["schedules"]
        live = [{"id": f"c{i}", "cron": watchers.cron_lead(s["cron"]), "prompt": watchers.cron_prompt(s)}
                for i, s in enumerate(scheds)]
        rc, out = self.check(live)
        self.assertEqual((rc, out["recreate"], out["missing"]), (0, [], []))
        live[0]["prompt"] = watchers.cron_base_prompt(scheds[0]) + " --model sonnet"  # an untagged legacy cron
        gone = live.pop()
        live.append({"id": "x9", "cron": "1 2 * * *", "prompt": "Run `foundry old thing`"})
        rc, out = self.check(live)
        self.assertEqual(rc, 1)
        self.assertEqual([(x["name"], x["delete"]) for x in out["recreate"]], [(scheds[0]["name"], "c0")])
        self.assertEqual([x["name"] for x in out["missing"]], [scheds[-1]["name"]])
        self.assertEqual([x["id"] for x in out["extra"]], ["x9"])
        self.assertNotEqual(gone["id"], "x9")

    def test_check_without_crons_is_the_stream_check(self):  # 0.1.92 (test_lifecycle_0192)
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "up", "--check"],
                           env=self.env, capture_output=True, text=True)
        self.assertIn("rearm", json.loads(r.stdout))


class PressureLabel(tf.Base):
    def test_every_language_band_names_pressure(self):
        for lang, want in (("en", "| pressure ok"), ("ja", "| 負荷 ok"), ("ko", "| pressure ok")):
            self.foundry("prefs", "set", "ui.language", lang, "--by", "owner")
            _, text = self.foundry("board")
            self.assertIn(want, text.split("\n")[1])


class InitKeptRepos(tf.Base):
    CREW = False

    def test_known_repos_are_listed_as_kept(self):
        clone = os.path.join(self.dir, "src", "app")
        subprocess.run(["git", "init", "-q", clone], check=True)
        self.foundry("repo", "add", "app", "--path", clone)
        _, out = self.foundry("init", "--dry-run", "--github-login", "octo")
        self.assertEqual(out["kept"]["repos"], ["app"])


if __name__ == "__main__":
    import unittest
    unittest.main()
