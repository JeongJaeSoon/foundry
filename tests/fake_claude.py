"""A stand-in for `claude`: logs argv and cwd to FAKE_CLAUDE_LOG, answers from the JSON in FAKE_CLAUDE_DATA.

Data keys: bg_id (default "bg1"), resume_id (the id a bare `--bg --resume` answers; default bg_id), bg_out
(overrides the whole --bg stdout), bg_fail, agents (list), rm_rc, stop_rc, stop_gone
(stop answers "No session matching <id>", exit 1).
"""

import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps({"argv": args, "cwd": os.getcwd()}) + "\n")
try:
    with open(os.environ["FAKE_CLAUDE_DATA"]) as f:
        data = json.load(f)
except OSError:
    data = {}

if args[:1] == ["agents"]:
    print(json.dumps(data.get("agents", [])))
elif args[:1] == ["rm"]:
    print("removed " + args[1])
    sys.exit(data.get("rm_rc", 0))
elif args[:1] == ["stop"]:
    if data.get("stop_gone"):
        print(f"No session matching {args[1]}", file=sys.stderr)
        sys.exit(1)
    print("stopped " + args[1])
    sys.exit(data.get("stop_rc", 0))
elif "--bg" in args:
    if data.get("bg_fail"):
        print(data.get("bg_err") or "error: simulated failure", file=sys.stderr)
        sys.exit(1)
    bid = data.get("bg_id", "bg1")
    if "--resume" in args and "-n" not in args:
        bid = data.get("resume_id", bid)
    print(data.get("bg_out") or f"backgrounded · {bid}")
else:
    sys.exit(2)
