"""Foreman Monitor streams, stall detection, foreman liveness, Monitor waits. Run from the plugin root."""

import datetime as dt
import json
import os
import subprocess
import sys

from test_foundry import ORCH, Base, ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

import watchers  # noqa: E402

NOW = dt.datetime(2026, 11, 18, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
FAKE_CLAUDE = f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_claude.py')}"


def at(minute, second=0):
    """A transcript timestamp (UTC, Z) at 09:<minute> JST on the test day."""
    t = NOW.replace(minute=0) - dt.timedelta(hours=1) + dt.timedelta(minutes=minute, seconds=second)
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def tool_use(ts, mid, name="Write", tid=None):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "content": [{"type": "tool_use", "id": tid or "tu-" + mid, "name": name, "input": {}}]}}


def thinking(ts, mid):
    return {"type": "assistant", "timestamp": ts, "message": {"id": mid, "content": [{"type": "thinking", "thinking": ""}]}}


def result(ts, tid="tu-x", text="ok", err=False):
    return {"type": "user", "timestamp": ts,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": err}]}}


def say(ts, text="done"):
    return {"type": "assistant", "timestamp": ts, "message": {"id": "t" + ts, "content": [{"type": "text", "text": text}]}}


# Tonight's pattern: a Write tool call that never returns, then a model request every 300 s, no tool_result.
API_LOOP_300S = [result(at(40)), tool_use(at(45), "m1"), thinking(at(50), "m2"), thinking(at(55), "m3")]


class Watchers(Base):
    def setUp(self):
        super().setUp()
        self.env["FOUNDRY_TRANSCRIPTS"] = os.path.join(self.dir, "transcripts")
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.start_lead("alpha", sid="SL")

    def transcript(self, entries, sid="SL", cwd="/wt/alpha"):
        d = os.path.join(self.env["FOUNDRY_TRANSCRIPTS"], cwd.replace("/", "-"))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, sid + ".jsonl"), "w") as f:
            f.write("".join(json.dumps(e) + "\n" for e in entries))

    def stream(self, kind, now=None):
        if now:
            self.env["FOUNDRY_NOW"] = now
        _, out = self.foundry("watch", "stream", "--kind", kind, "--once")
        return [line for line in (out or "").splitlines() if line] if isinstance(out, str) else ([] if out is None else [json.dumps(out)])

    def run_cli(self, *args, script="foundry.py"):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args], env=self.env,
                              capture_output=True, text=True)

    def ops(self, op):
        with open(os.path.join(self.dir, "journal.jsonl")) as f:
            return [r for r in map(json.loads, f) if r["op"] == op]

    def board(self):
        _, m = self.foundry("board", "--format", "json")
        return m

    def row(self, uid="alpha"):
        return next(r for sec in self.board()["sections"] for r in sec["rows"] if r["id"] == uid)


class Detect(Watchers):
    P = {"stall_warn_min": 10, "stall_kill_min": 20}

    def test_api_loop_300s_pattern(self):
        self.assertEqual(watchers.detect(API_LOOP_300S, NOW, self.P)[0], "api_loop")
        self.assertEqual(watchers.detect(API_LOOP_300S, NOW, self.P)[2], 3)

    def test_parallel_tool_calls_of_one_message_are_not_a_loop(self):
        e = [result(at(58)), tool_use(at(59), "m1", tid="a"), tool_use(at(59), "m1", tid="b")]
        self.assertIsNone(watchers.detect(e, NOW, self.P))

    def test_text_output_resets_the_run(self):
        e = [tool_use(at(50), "m1"), say(at(55), "progress"), tool_use(at(58), "m3")]
        self.assertIsNone(watchers.detect(e, NOW, self.P))

    def test_no_progress_after_stall_warn_min(self):
        e = [result(at(40)), tool_use(at(45), "m1", name="Bash")]
        kind, _, idle = watchers.detect(e, NOW, self.P)
        self.assertEqual((kind, idle), ("no_progress", 20))  # since the last tool result
        self.assertIsNone(watchers.detect([result(at(55)), tool_use(at(55), "m1", name="Bash")], NOW, self.P))

    def test_finished_turn_is_idle_not_stalled(self):
        self.assertIsNone(watchers.detect([result(at(10)), say(at(11))], NOW, self.P))

    def test_same_error_three_times(self):
        e = [result(at(50), text="Permission denied: rm", err=True), tool_use(at(51), "m1"),
             result(at(52), text="Permission denied: rm", err=True), tool_use(at(53), "m2"),
             result(at(54), text="Permission denied: rm", err=True)]
        self.assertEqual(watchers.detect(e, NOW, self.P)[:1], ("repeat_error",))
        e[2] = result(at(52), text="other error", err=True)
        self.assertNotEqual((watchers.detect(e, NOW, self.P) or ("",))[0], "repeat_error")

    def test_active_monitor_call_is_exempt(self):
        e = [result(at(40)), tool_use(at(41), "m1", name="Monitor"), thinking(at(50), "m2"), thinking(at(55), "m3")]
        self.assertIsNone(watchers.detect(e, NOW, self.P))


class StallStream(Watchers):
    def setUp(self):
        super().setUp()
        # A relaunch really stops and resumes the lead (review 14): a fake claude and an existing worktree.
        self.env.update({"FOUNDRY_CLAUDE": FAKE_CLAUDE, "FAKE_CLAUDE_LOG": os.path.join(self.dir, "claude.jsonl"),
                         "FAKE_CLAUDE_DATA": os.path.join(self.dir, "claude.json")})
        os.makedirs(os.path.join(self.dir, "wt-alpha"))
        self.record("unit.worktree", id="alpha", path=os.path.join(self.dir, "wt-alpha"), repo=None, branch=None)

    def test_api_loop_journals_recovers_once_and_shows_on_the_board(self):
        self.transcript(API_LOOP_300S)
        lines = self.stream("stalls")
        self.assertTrue(any("stall alpha api_loop" in x for x in lines), lines)
        self.assertEqual(len(self.ops("unit.stalled")), 1)
        self.assertEqual(self.ops("nearmiss")[0]["kind"], "stall")
        rec = self.ops("stall.recover")
        self.assertEqual([(r["action"], r["note"]) for r in rec], [("relaunch", "write in parts of ≤250 lines")])
        self.assertTrue(self.row()["stall"]["text"].startswith("막힘"))
        self.assertEqual(self.stream("stalls"), [])  # same episode: nothing printed, no second recovery
        self.assertEqual(len(self.ops("stall.recover")), 1)

    def test_no_progress_nudges_then_relaunches_after_kill_min(self):
        self.transcript([result(at(40)), tool_use(at(45), "m1", name="Bash")])
        self.stream("stalls")
        self.assertEqual([r["action"] for r in self.ops("stall.recover")], ["nudge"])
        self.assertTrue(any("stall" in i["text"] for i in self.snap()["units"]["alpha"]["inbox"]))
        self.stream("stalls", now="2026-11-18T10:05:00+09:00")
        self.assertEqual([r["action"] for r in self.ops("stall.recover")], ["nudge", "relaunch"])
        self.assertEqual(len(self.ops("unit.stalled")), 1)

    def test_repeat_error_triages(self):
        self.transcript([result(at(50 + i), text="denied by policy", err=True) for i in range(3)])
        self.stream("stalls")
        self.assertEqual([r["action"] for r in self.ops("stall.recover")], ["triage"])

    def test_progress_clears_and_a_second_stall_escalates_without_recovery(self):
        self.transcript(API_LOOP_300S)
        self.stream("stalls")
        self.transcript(API_LOOP_300S + [result(at(58)), say(at(59))])
        self.assertIn("cleared", " ".join(self.stream("stalls")))
        self.assertEqual(len(self.ops("stall.cleared")), 1)
        self.assertIsNone(self.row()["stall"])
        self.transcript(API_LOOP_300S + [result(at(58)), tool_use(at(58), "n1"), thinking(at(59), "n2")])
        lines = self.stream("stalls")
        self.assertIn("escalated", " ".join(lines))
        self.assertEqual(len(self.ops("stall.escalate")), 1)
        self.assertEqual(len(self.ops("stall.recover")), 1)  # only the first episode's
        self.assertEqual(len(self.ops("nearmiss")), 2)

    def test_parked_units_and_monitor_waits_are_exempt(self):
        self.transcript(API_LOOP_300S)
        self.foundry("unit", "wait-monitor", "alpha", "--what", "CI on o/r#1", "--minutes", "20")
        self.assertEqual(self.stream("stalls"), [])
        self.assertEqual(self.row()["summary"], "CI 대기 (monitor): CI on o/r#1")
        self.stream("stalls", now="2026-11-18T10:25:00+09:00")  # the Monitor wait is over
        self.assertEqual(len(self.ops("unit.stalled")), 1)
        self.foundry("unit", "status", "alpha", "waiting_others")
        self.assertIn("cleared", " ".join(self.stream("stalls")))

    def test_wait_monitor_cap(self):
        r = self.run_cli("unit", "wait-monitor", "alpha", "--what", "CI", "--minutes", "45")
        self.assertEqual(r.returncode, 2)
        self.assertIn("park and exit instead", r.stderr)
        self.assertEqual(self.ops("wait.monitor"), [])


class Pressure(Watchers):
    def sample(self, **s):
        p = os.path.join(self.dir, "sample.json")
        with open(p, "w") as f:
            json.dump({"load_per_cpu": 0.2, "mem_reclaimable_pct": 60, "disk_free_gb": 300, "docker_cpu_pct": 0} | s, f)
        self.env["FOUNDRY_SAMPLE_JSON"] = p

    def test_prints_only_on_band_change(self):
        self.sample()
        self.assertEqual(self.stream("pressure"), [])
        self.sample(load_per_cpu=1.5)
        self.assertEqual(len(self.stream("pressure")), 1)
        self.assertEqual(self.snap()["pressure"]["level"], "warn")
        self.assertEqual(self.stream("pressure"), [])
        self.sample(disk_free_gb=10)
        self.assertIn("danger", self.stream("pressure")[0])
        self.assertEqual([r["level"] for r in self.ops("pressure")], ["warn", "danger"])
        tiles = {t["name"]: t for t in self.board()["monitor"]["tiles"]}
        self.assertEqual(tiles["disk"]["value"], 10)
        self.assertEqual(len(tiles["load"]["spark"]), 4)
        # Each tile carries its own level, not the band's: only disk is past a limit here.
        self.assertEqual({k: t["status"] for k, t in tiles.items()}["disk"], "danger")
        self.assertEqual(tiles["docker"]["status"], "ok")
        self.assertEqual(set(self.board()["monitor"]["leads"]), {"live", "cap", "launch"})

    def test_heading_is_the_worst_tile_when_the_band_has_not_moved(self):
        with open(os.path.join(self.dir, "metrics.jsonl"), "w") as f:
            f.write(json.dumps({"ts": "2026-11-18T10:00:00+09:00", "load_per_cpu": 1.5, "mem_reclaimable_pct": 60}) + "\n")
        self.assertEqual(self.board()["monitor"]["status"], "warn")

    def test_metrics_ring_drops_lines_older_than_keep_hours(self):
        old = {"ts": "2026-11-17T09:00:00+09:00", "load_per_cpu": 9}
        recent = {"ts": "2026-11-17T11:00:00+09:00", "load_per_cpu": 1}
        with open(os.path.join(self.dir, "metrics.jsonl"), "w") as f:
            f.write(json.dumps(old) + "\n" + json.dumps(recent) + "\n")
        self.sample()
        self.stream("pressure")
        with open(os.path.join(self.dir, "metrics.jsonl")) as f:
            rows = [json.loads(x) for x in f]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ts"], recent["ts"])


class Foreman(Watchers):
    def setUp(self):
        super().setUp()
        with open(os.path.join(self.dir, "config.json"), "w") as f:  # a set-up machine (0.1.117)
            f.write("{}")

    def test_down_board_and_owner_session_start(self):
        m = self.board()
        self.assertFalse(m["foreman"]["alive"])
        self.assertFalse(m["monitor"]["collecting"])
        self.assertEqual(m["monitor"]["footer"], "수집 중단 (foreman down)")
        out = self.hook("SessionStart", session_id="OWN", source="startup")
        self.assertIn("foundry foreman is down — run `foundry up`", out["hookSpecificOutput"]["additionalContext"])

    def test_a_stream_heartbeat_marks_it_alive_for_35_minutes(self):
        self.stream("sync")  # a one-off run outside the foreman (e2e finding): no heartbeat
        self.assertEqual(self.ops("foreman.state"), [])
        self.env["FOUNDRY_ROLE"] = "foreman"
        self.stream("sync")
        self.stream("sync")
        self.assertEqual(len(self.ops("foreman.state")), 1)  # at most once a minute
        del self.env["FOUNDRY_ROLE"]
        m = self.board()
        self.assertTrue(m["foreman"]["alive"])
        self.assertTrue(m["monitor"]["collecting"])
        self.assertIsNone(self.hook("SessionStart", session_id="OWN", source="startup"))
        self.env["FOUNDRY_NOW"] = "2026-11-18T10:36:00+09:00"
        self.assertFalse(self.board()["foreman"]["alive"])

    def test_up_prints_streams_crons_and_lost_units(self):
        self.record("session.state", name="lead-alpha", state="lost")
        _, plan = self.foundry("up")
        self.assertEqual(len(plan["streams"]), 5)
        self.assertTrue(all(s["tool"] == "Monitor" and s["timeout_ms"] <= 1800000 for s in plan["streams"]))
        self.assertIn("watch stream --kind stalls", plan["streams"][-1]["command"])
        self.assertEqual({c["name"] for c in plan["crons"]},
                         {"handover", "handover-refresh", "preflight-night", "preflight-morning", "kaizen", "daily-report",
                          "review-remind", "intake-scan"})
        self.assertTrue(all(c["tool"] == "CronCreate" and c["recurring"] for c in plan["crons"]))
        self.assertEqual([x["unit"] for x in plan["lost"]], ["alpha"])
        self.assertEqual(len(self.ops("foundry.up")), 1)
        self.assertNotIn("LaunchAgents", json.dumps(plan))

    def test_up_commands_are_path_stable_and_crons_fire_by_the_pref_time(self):
        _, plan = self.foundry("up")
        self.assertNotIn("CLAUDE_PLUGIN_ROOT", json.dumps(plan))  # only hooks get that variable
        self.assertNotIn("/plugins/cache/", json.dumps(plan))
        for st in plan["streams"]:
            self.assertTrue(st["command"].startswith("foundry watch stream --kind "), st)
        # The command works in a plain shell that has the plugin's bin/ on PATH, as the Bash tool does.
        env = dict(self.env, PATH=os.path.join(ROOT, "bin") + os.pathsep + os.environ["PATH"])
        r = subprocess.run(["sh", "-c", plan["streams"][1]["command"] + " --once"], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        crons = {c["name"]: (c["pref_cron"], c["cron"], c["prompt"]) for c in plan["crons"]}
        self.assertEqual(crons["handover"][:2], ("0 8 * * *", "30 7 * * *"))
        self.assertTrue(crons["handover"][2].startswith("[foundry-cron handover "), crons["handover"])
        self.assertTrue(crons["handover"][2].endswith("] Run `foundry report handover`"), crons["handover"])
        self.assertEqual(crons["preflight-night"][1], "25 21 * * *")
        self.assertEqual(crons["preflight-morning"][1], "25 7 * * *")
        self.assertEqual(crons["kaizen"][1], "30 8 * * 1")
        self.assertEqual(watchers.cron_lead("10 0 * * 1"), "40 23 * * 0")
        self.assertEqual(watchers.cron_lead("10 0 1 * *"), "10 0 1 * *")
        self.assertEqual(watchers.cron_lead("*/5 * * * *"), "*/5 * * * *")

    def test_agents_and_skills_never_type_the_hook_only_variable(self):
        import glob
        for p in glob.glob(os.path.join(ROOT, "agents", "*.md")) + glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")):
            with open(p) as f:
                text = f.read().replace("never `${CLAUDE_PLUGIN_ROOT}`", "")
            self.assertNotIn("CLAUDE_PLUGIN_ROOT", text, p)

    def test_no_launchd_left(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "launchd")))
        for base, _, files in os.walk(os.path.join(ROOT, "scripts")):
            for name in files:
                with open(os.path.join(base, name), errors="replace") as f:
                    self.assertNotIn("LaunchAgents", f.read(), name)


class Streams(Watchers):
    def test_large_write_warns_never_denies(self):
        big = self.hook("PreToolUse", tool_name="Write", tool_input={"file_path": "/wt/alpha/x.py", "content": "x" * 50000},
                        agent_type="foundry:line-lead", session_id="SL")
        ctx = big["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", ctx)
        self.assertIn("Large Write (~48 KB): write in parts of ≤250 lines", ctx["additionalContext"])
        small = self.hook("PreToolUse", tool_name="Write", tool_input={"file_path": "/wt/alpha/x.py", "content": "x"},
                          agent_type="foundry:line-lead", session_id="SL")["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", small)
        self.assertNotIn("Large Write", small["additionalContext"])
        self.assertIn("Leads delegate execution", small["additionalContext"])  # 0.1.16
        hand = self.hook("PreToolUse", tool_name="Write", tool_input={"file_path": "/wt/alpha/x.py", "content": "x"},
                         agent_type="foundry:hand", agent_id="h1", session_id="SL")
        self.assertIsNone(hand)

    def test_large_write_never_denied_even_when_the_hook_crashes(self):
        broken = os.path.join(self.dir, "not-a-dir")
        open(broken, "w").close()
        self.env["FOUNDRY_STATE_DIR"] = broken  # every state read fails: the hook crashes
        bash = self.hook("PreToolUse", tool_name="Bash", tool_input={"command": "ls"},
                         agent_type="foundry:line-lead", session_id="SL")
        self.assertTrue(self.denied(bash), bash)  # the crash path is real: a foundry Bash call fails closed
        out = self.hook("PreToolUse", tool_name="Write", tool_input={"file_path": "/wt/alpha/x.py", "content": "x" * 50000},
                        agent_type="foundry:line-lead", session_id="SL")
        self.assertFalse(self.denied(out), out)

    def test_cron_age_is_journaled_and_recreate_is_said_once(self):
        cron = {"cron": "55 21 * * *", "prompt": "Run `foundry preflight`", "recurring": True}
        self.hook("PostToolUse", tool_name="CronCreate", tool_input=cron, tool_response={"id": "c1"}, **ORCH)
        self.hook("PostToolUse", tool_name="CronCreate", tool_input=cron, tool_response={"id": "c9"},
                  session_id="OWNER")  # not a foundry session: not ours to track
        self.assertEqual([r["id"] for r in self.ops("cron.created")], ["c1"])
        self.assertFalse(any("crons recreate" in x for x in self.stream("sessions", now="2026-11-23T09:00:00+09:00")))
        lines = self.stream("sessions", now="2026-11-24T10:30:00+09:00")
        self.assertTrue(any("crons recreate now: 1 foundry cron(s)" in x for x in lines), lines)
        self.assertFalse(any("crons recreate" in x for x in self.stream("sessions")))  # said once
        self.hook("PostToolUse", tool_name="CronDelete", tool_input={"id": "c1"}, **ORCH)
        self.hook("PostToolUse", tool_name="CronCreate", tool_input=cron, tool_response={"id": "c2"}, **ORCH)
        self.assertEqual(list(self.snap()["crons"].values())[0]["id"], "c2")
        self.assertFalse(any("crons recreate" in x for x in self.stream("sessions", now="2026-11-29T10:00:00+09:00")))
        self.assertTrue(any("crons recreate" in x for x in self.stream("sessions", now="2026-11-30T11:00:00+09:00")))

    def test_github_journals_pr_state_and_prints_on_change(self):
        self.foundry("unit", "keys", "alpha", "--pr", "o/r#5")
        pr = {"state": "OPEN", "reviewDecision": "REVIEW_REQUIRED", "headRefOid": "a" * 40, "latestReviews": [],
              "statusCheckRollup": [{"status": "IN_PROGRESS"}]}
        self.gh({"o/r#5": pr})
        self.assertEqual(self.stream("github"), [])  # the baseline is journaled, not printed
        self.assertEqual(self.ops("pr.state")[0]["ci"], "pending")
        self.assertEqual(self.stream("github"), [])
        self.gh({"o/r#5": pr | {"reviewDecision": "APPROVED", "statusCheckRollup": [{"conclusion": "SUCCESS"}]}})
        lines = self.stream("github")
        self.assertEqual(len(lines), 1)
        self.assertIn("review=APPROVED ci=success", lines[0])

    def test_github_fills_pr_title_and_created_once_without_a_line(self):
        self.foundry("unit", "keys", "alpha", "--pr", "o/r#5")
        pr = {"state": "OPEN", "reviewDecision": "REVIEW_REQUIRED", "headRefOid": "a" * 40, "latestReviews": [],
              "statusCheckRollup": [{"status": "IN_PROGRESS"}]}
        self.gh({"o/r#5": pr})
        self.assertEqual(self.stream("github"), [])
        self.assertNotIn("title", self.snap()["units"]["alpha"]["prs"][0])
        # gh now answers the title (an upgraded watcher): the record is filled once, with nothing printed
        self.gh({"o/r#5": pr | {"title": "Release wait fix", "createdAt": "2026-11-17T09:00:00Z"}})
        self.assertEqual(self.stream("github"), [])
        rec = self.snap()["units"]["alpha"]["prs"][0]
        self.assertEqual((rec["title"], rec["opened"]), ("Release wait fix", "2026-11-17T09:00:00Z"))
        n = len(self.ops("pr.state"))
        self.assertEqual(self.stream("github"), [])
        self.assertEqual(len(self.ops("pr.state")), n)  # filled: no more records until something changes

    def test_merged_pr_is_released_once_a_tag_contains_its_merge_commit(self):
        self.record("pr.state", unit="alpha", repo="o/r", number=5, state="MERGED")
        self.record("pr.state", unit="alpha", repo="example-org/infra-ops", number=6, state="MERGED")
        tags = [{"name": "v1.9.0"}, {"name": "v1.10.0"}, {"name": "nightly"}]
        self.gh({"o/r#5": {"mergeCommit": {"oid": "abc"}}, "api repos/o/r/tags?per_page=100": tags,
                 "api repos/o/r/compare/abc...v1.10.0": {"status": "behind"}})
        self.assertEqual(self.stream("github"), [])
        self.assertEqual(self.ops("pr.released"), [])
        self.gh({"o/r#5": {"mergeCommit": {"oid": "abc"}}, "api repos/o/r/tags?per_page=100": tags + [{"name": "v1.11.0"}],
                 "api repos/o/r/compare/abc...v1.11.0": {"status": "ahead"}})
        self.assertEqual(self.stream("github", now="2026-11-18T12:00:00+09:00"), ["[foundry github] pr o/r#5 (alpha): released in v1.11.0"])
        self.assertEqual([(r["number"], r["tag"]) for r in self.ops("pr.released")], [(5, "v1.11.0")])
        prs = {p["number"]: p for p in self.snap()["units"]["alpha"]["prs"]}
        self.assertEqual(prs[5]["released"], "v1.11.0")
        self.assertNotIn("released", prs[6])  # infra-ops has no release step: never checked, dropped from stock
        sys.path.insert(0, SCRIPTS)
        import factory
        import prefs
        inv = factory.inventory(self.snap(), p=prefs.defaults(), rs={})
        self.assertEqual([r["ref"] for rows in inv["repos"].values() for r in rows if r["kind"] == "pr"], [])

    def test_github_stream_fires_due_waits(self):
        self.foundry("wait", "add", "alpha", "--kind", "time", "--ref", "deploy", "--until", "2026-11-18T09:00:00+09:00")
        self.assertIn("event alpha time", " ".join(self.stream("github")))

    def test_sessions_prints_state_changes(self):
        data = os.path.join(self.dir, "claude.json")
        with open(data, "w") as f:
            json.dump({"agents": []}, f)
        self.env.update({"FOUNDRY_CLAUDE": FAKE_CLAUDE, "FAKE_CLAUDE_DATA": data,
                         "FAKE_CLAUDE_LOG": os.path.join(self.dir, "claude.log")})
        lines = self.stream("sessions")
        self.assertIn("[foundry sessions] session lead-alpha (alpha): running -> lost", lines)
        self.assertEqual(self.stream("sessions"), [])

    def test_sync_lag(self):
        snap = {"units": {"a": {"id": "a", "sync": {"jira": {"last_event_at": "2026-11-18T09:00:00+09:00",
                                                             "last_synced_at": None,
                                                             "pending": [{"queued": "2026-11-18T09:00:00+09:00"}]}}},
                          "b": {"id": "b", "sync": {"jira": {"last_event_at": "2026-11-18T09:30:00+09:00",
                                                             "last_synced_at": "2026-11-18T09:00:00+09:00",
                                                             "pending": []}}}}}
        # The lag is the age of what is still queued; an empty queue is no lag, whatever the timestamps say.
        self.assertEqual(watchers.sync_lags(snap, NOW), {"a": {"jira": 60}})
        self.assertEqual(self.stream("sync"), [])  # no unit carries sync state: nothing to say

    def test_watch_py_stream_still_works(self):
        self.env["FOUNDRY_ROLE"] = "foreman"
        r = self.run_cli("stream", "--kind", "sync", "--once", script="watch.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.ops("foreman.state")), 1)
