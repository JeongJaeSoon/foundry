"""Stand-in for `lsof -d cwd -Fpcn`: reports the directories in FAKE_LSOF_CWDS (colon-separated) as process cwds."""

import os
import sys

if os.environ.get("FAKE_LSOF_LOG"):
    with open(os.environ["FAKE_LSOF_LOG"], "a") as f:
        f.write("call\n")
if os.environ.get("FAKE_LSOF_FAIL"):
    sys.exit(2)
for i, d in enumerate(p for p in os.environ.get("FAKE_LSOF_CWDS", "").split(":") if p):
    print(f"p{1000 + i}\nc{os.environ.get('FAKE_LSOF_CMD', 'claude')}\nfcwd\nn{d}")
