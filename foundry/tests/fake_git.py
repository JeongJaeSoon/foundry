"""Real git, except the subcommands listed in FAKE_GIT_FAIL (comma-separated) exit 128."""

import os
import subprocess
import sys

args = sys.argv[1:]
sub = next((a for i, a in enumerate(args) if not a.startswith("-") and (i == 0 or args[i - 1] not in ("-C", "--git-dir"))), "")
if sub in [s for s in os.environ.get("FAKE_GIT_FAIL", "").split(",") if s]:
    print(f"fatal: simulated {sub} failure", file=sys.stderr)
    sys.exit(128)
sys.exit(subprocess.run(["git", *args]).returncode)
