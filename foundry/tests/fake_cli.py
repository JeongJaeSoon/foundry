"""Stand-in for `docker` and `claude` (first argument names which): logs each argv as a JSON line to
FAKE_CLI_LOG and acts on nothing. `docker info` fails when FAKE_DOCKER_DOWN is set; `claude agents --json`
prints FAKE_CLAUDE_AGENTS (default [])."""

import json
import os
import sys

tool, args = sys.argv[1], sys.argv[2:]
with open(os.environ["FAKE_CLI_LOG"], "a") as f:
    f.write(json.dumps([tool, *args]) + "\n")
if tool == "docker" and args[:1] == ["info"] and os.environ.get("FAKE_DOCKER_DOWN"):
    sys.exit(1)
if tool == "docker" and args[:2] == ["compose", "ls"]:
    print(os.environ.get("FAKE_COMPOSE", "[]"))
if tool == "claude" and args[:1] == ["stop"] and os.environ.get("FAKE_CLAUDE_STOP_FAIL"):
    sys.exit(1)
if tool == "claude" and args[:2] == ["agents", "--json"]:
    print(os.environ.get("FAKE_CLAUDE_AGENTS", "[]"))
