"""0.1.99: (A1) the sessions stream resolves refs once per cycle and the github stream right after a PR state lands,
so time: and pr: blockers clear on their own; (A3) the pressure stream samples `docker system df` (images, build
cache, cache unused 7 days or more) with a 5 s timeout and nulls when Docker does not answer, shown as a cache tile;
(B2) the foreman's SessionStart journals the hash of the foreman.md it loaded, and a different installed one is said
once ("foreman restart needed") by the sessions stream and after a clear/compact, and flagged on the board; (B5) each
stalls cycle records a health summary and says "stall detector may be broken" once when running leads' transcripts
fail to parse 3 cycles in a row."""

import json
import os
import subprocess
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import watchers  # noqa: E402

ORCH = tf.ORCH


def stream_lines(t, kind):
    r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "watch", "stream", "--kind", kind,
                        "--once"], env=t.env, capture_output=True, text=True)
    t.assertEqual(r.returncode, 0, r.stderr)
    return [x.split("] ", 1)[1] for x in r.stdout.splitlines() if x.startswith(f"[foundry {kind}]")]


class Resolve(tf.Base):
    def test_a_time_blocker_clears_in_the_sessions_cycle(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "U1")
        self.foundry("unit", "block", "u1", "--on", "time:2026-11-18T10:30:00+09:00")
        self.assertEqual(self.snap()["units"]["u1"]["blocked_by"], ["time:2026-11-18T10:30:00+09:00"])
        self.assertFalse([x for x in stream_lines(self, "sessions") if "u1 ready" in x])
        self.env["FOUNDRY_NOW"] = "2026-11-18T10:45:00+09:00"
        lines = stream_lines(self, "sessions")
        self.assertIn("unit u1 ready: its last blocker cleared", lines)
        self.assertTrue(any(r["op"] == "ref.resolved" for r in self.journal()))

    def test_the_github_cycle_resolves_after_a_pr_state(self):
        self.foundry("unit", "new", "--id", "maker", "--title", "Maker", "--pr", "o/r#5")
        self.foundry("unit", "new", "--id", "after", "--title", "After")
        self.foundry("unit", "block", "after", "--on", "pr:o/r#5:merged")
        self.assertFalse(any(r["op"] == "ref.resolved" for r in self.journal()))
        self.gh({"o/r#5": {"state": "MERGED", "title": "t", "headRefOid": "abc", "statusCheckRollup": []}})
        stream_lines(self, "github")
        self.assertTrue(any(r["op"] == "ref.resolved" and r.get("ref") == "pr:o/r#5:merged" for r in self.journal()))


class DockerDisk(tf.Base):
    def fake_docker(self, body, rc=0, sleep=0):
        p = os.path.join(self.dir, "docker.py")
        with open(p, "w") as f:
            f.write(f"import sys, time, json\ntime.sleep({sleep})\nprint(json.dumps({body!r}))\nsys.exit({rc})\n")
        os.environ["FOUNDRY_DOCKER"] = f"{sys.executable} {p}"
        self.addCleanup(os.environ.pop, "FOUNDRY_DOCKER", None)

    def test_images_cache_and_old_cache(self):
        self.fake_docker({"Images": [{}, {}, {}], "BuildCache": [
            {"Size": "1.5GB", "LastUsedSince": "2 weeks ago"}, {"Size": "500MB", "LastUsedSince": "3 hours ago"},
            {"Size": "250MB", "LastUsedSince": "", "CreatedSince": "9 days ago"}]})
        self.assertEqual(watchers._docker_df(), {"docker_images": 3, "docker_build_cache_gb": 2.25,
                                                 "docker_cache_old_gb": 1.75})

    def test_no_daemon_or_a_slow_one_is_null(self):
        self.fake_docker({}, rc=1)
        self.assertEqual(set(watchers._docker_df().values()), {None})
        old = watchers.DOCKER_DF_TIMEOUT_S
        watchers.DOCKER_DF_TIMEOUT_S = 0.2
        self.addCleanup(setattr, watchers, "DOCKER_DF_TIMEOUT_S", old)
        self.fake_docker({"Images": []}, sleep=1)
        self.assertEqual(set(watchers._docker_df().values()), {None})

    def test_the_cache_tile_carries_its_extras(self):
        with open(os.path.join(self.dir, "metrics.jsonl"), "w") as f:
            f.write(json.dumps({"ts": "2026-11-18T09:59:00+09:00", "load_per_cpu": 0.1, "docker_build_cache_gb": 4.0,
                                "docker_cache_old_gb": 3.0, "docker_images": 12}) + "\n")
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), "metrics", "--hours", "3"],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        tile = next(t for t in json.loads(r.stdout)["tiles"] if t["name"] == "cache")
        self.assertEqual((tile["value"], tile["extra"]), (4.0, {"docker_cache_old_gb": 3.0, "docker_images": 12}))


class ForemanDrift(tf.Base):
    def start(self, source):
        return self.hook("SessionStart", source=source, **ORCH)

    def test_recorded_at_startup_said_once_when_the_installed_file_differs(self):
        self.start("startup")
        rec = [r for r in self.journal() if r["op"] == "foreman.def"]
        self.assertEqual(rec[-1]["hash"], watchers.foreman_def_hash())
        self.assertFalse([x for x in stream_lines(self, "sessions") if "foreman restart" in x])
        self.record("foreman.def", session="S", hash="000000000000")  # as if the foreman loaded an older file
        said = [x for x in stream_lines(self, "sessions") if "foreman restart needed" in x]
        self.assertEqual(len(said), 1)
        self.assertFalse([x for x in stream_lines(self, "sessions") if "foreman restart" in x])  # once
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "board.py"), "json"], env=self.env,
                           capture_output=True, text=True)
        self.assertTrue(json.loads(r.stdout)["foreman_restart"])

    def test_a_compaction_says_it_and_does_not_rerecord(self):
        self.record("foreman.def", session="S", hash="000000000000")
        n = len([r for r in self.journal() if r["op"] == "foreman.def"])
        out = self.start("compact")
        self.assertIn("foreman restart needed", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(len([r for r in self.journal() if r["op"] == "foreman.def"]), n)


class StallHealth(tf.Base):
    def setUp(self):
        super().setUp()
        old = os.environ.get("FOUNDRY_STATE_DIR")
        os.environ["FOUNDRY_STATE_DIR"] = self.dir
        self.addCleanup(lambda: os.environ.__setitem__("FOUNDRY_STATE_DIR", old) if old else
                        os.environ.pop("FOUNDRY_STATE_DIR", None))

    def test_three_broken_cycles_say_it_once_and_a_good_one_resets(self):
        lines = [watchers.stall_health(2, 0, 2) for _ in range(4)]
        self.assertEqual([bool(x) for x in lines], [False, False, True, False])
        self.assertIn("stall detector may be broken", lines[2])
        with open(os.path.join(self.dir, "watch-stall-health.json")) as f:
            self.assertEqual(json.load(f)["streak"], 4)
        self.assertIsNone(watchers.stall_health(2, 1, 1))
        self.assertEqual([bool(watchers.stall_health(2, 0, 2)) for _ in range(3)], [False, False, True])

    def test_no_running_lead_is_not_broken(self):
        self.assertEqual([watchers.stall_health(0, 0, 0) for _ in range(4)], [None] * 4)

    def test_a_missing_transcript_counts_as_a_failure(self):
        self.assertEqual(watchers._parse_tail(None, None, {}), (False, None))
        p = os.path.join(self.dir, "t.jsonl")
        with open(p, "w") as f:
            f.write("not json\n")
        self.assertEqual(watchers._parse_tail(p, None, {}), (False, None))
