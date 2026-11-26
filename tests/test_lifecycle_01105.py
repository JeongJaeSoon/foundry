"""0.1.105: `foundry foreman line` prints the start line install.sh prints; `foundry foreman start` runs it as argv
(no shell), refuses while a foreman is running or idle (naming it and its attach command), and says to send
`foundry up` first unless --prompt sent it. Owner only, from a plain terminal. Fake `claude` shims only."""

import json
import os
import sys

import test_foundry as tf


class Foreman(tf.Base):
    def setUp(self):
        super().setUp()
        self.env.pop("CLAUDECODE", None)
        self.env.pop("FOUNDRY_ROLE", None)
        self.calls = os.path.join(self.dir, "claude-calls.jsonl")
        self.home = os.path.join(self.dir, "fhome")
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump({"foreman_dir": self.home, "advisor_model": "claude-test-advisor"}, f)
        self.agents([])

    def agents(self, entries):
        p = os.path.join(self.dir, "claude.py")
        with open(p, "w") as f:
            f.write("import json, os, sys\n"
                    f"open({self.calls!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}) + '\\n')\n"
                    f"if sys.argv[1:2] == ['agents']:\n    print(json.dumps({entries!r}))\n"
                    "elif '--bg' in sys.argv:\n    print('backgrounded · fm123456')\n")
        self.env["FOUNDRY_CLAUDE"] = f"{sys.executable} {p}"

    def launches(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls) as f:
            return [c for c in map(json.loads, f) if "--bg" in c["argv"]]

    def test_line_is_the_install_line(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "foreman", "line"], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.strip()
        self.assertTrue(line.startswith(f"cd {self.home} && "))
        self.assertIn("--agent foundry:foreman", line)
        self.assertIn('"advisorModel":"claude-test-advisor"', line)
        self.assertEqual(self.launches(), [])

    def test_start_runs_argv_and_says_what_next(self):
        _, out = self.foundry("foreman", "start")
        self.assertEqual(out["started"], "fm123456")
        self.assertEqual(out["attach"], "claude attach fm123456")
        self.assertIn("foundry up", out["next"])
        (call,) = self.launches()
        argv = call["argv"]
        self.assertEqual(argv[:3], ["--bg", "-n", "foreman"])
        self.assertIn("--agent", argv)
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertEqual(settings["advisorModel"], "claude-test-advisor")
        self.assertEqual(settings["env"]["FOUNDRY_ROLE"], "foreman")
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(self.home))

    def test_prompt_goes_last(self):
        _, out = self.foundry("foreman", "start", "--prompt", "foundry up")
        self.assertNotIn("next", out)
        self.assertEqual(self.launches()[0]["argv"][-1], "foundry up")

    def test_refused_while_a_foreman_runs_or_idles(self):
        for status in ("running", "idle", "busy"):
            self.agents([{"name": "foreman", "id": "fm999", "status": status}])
            rc, out = self.foundry("foreman", "start", ok=(1,))
            self.assertEqual((out["id"], out["attach"]), ("fm999", "claude attach fm999"))
        self.assertEqual(self.launches(), [])
        self.agents([{"name": "foreman", "id": "fm999", "status": "stopped"}, {"name": "lead-u1", "status": "running"}])
        self.assertEqual(self.foundry("foreman", "start")[1]["started"], "fm123456")

    def test_refused_inside_a_session_or_without_the_listing(self):
        self.env["CLAUDECODE"] = "1"
        self.foundry("foreman", "start", ok=(2,))
        self.env.pop("CLAUDECODE")
        self.env["FOUNDRY_CLAUDE"] = "false"
        self.foundry("foreman", "start", ok=(2,))
        self.assertEqual(self.launches(), [])
