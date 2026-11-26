"""Install step: the enabled trackers into prefs.json, and the owner's Slack identity into config.json (mode 600).

Merges into existing files instead of replacing them, so other keys survive a re-run; a trackers choice the
owner already made is kept.
  python3 install_config.py --trackers jira [--owner-user U... --owner-dm D...]
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

PLACEHOLDER = re.compile(r"^<.*>$|^FILL", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner-user", help="the owner's Slack user id (U...)")
    ap.add_argument("--owner-dm", help="the owner's self-DM channel id (D...)")
    ap.add_argument("--trackers", help="comma-separated trackers to enable when prefs has none yet, e.g. jira")
    a = ap.parse_args()
    if bool(a.owner_user) != bool(a.owner_dm):
        print("--owner-user and --owner-dm go together", file=sys.stderr)
        return 2
    if a.trackers:
        rc = write_trackers([t.strip() for t in a.trackers.split(",") if t.strip()])
        if rc:
            return rc
    if not a.owner_user:
        return 0
    for name, value, prefix in (("--owner-user", a.owner_user, ("U", "W")), ("--owner-dm", a.owner_dm, ("D",))):
        if PLACEHOLDER.match(value) or not value.startswith(prefix):
            print(f"{name} {value!r} is not a Slack id starting with {'/'.join(prefix)}; fill it in first", file=sys.stderr)
            return 2
    p = cl.path("config.json")
    cfg = {}
    if os.path.exists(p):
        with open(p) as f:
            cfg = json.load(f)
    cfg["owner_slack_user_id"] = a.owner_user
    dms = cfg.get("owner_dm_channels") or []
    if a.owner_dm not in dms:
        dms.append(a.owner_dm)
    cfg["owner_dm_channels"] = dms
    tmp = f"{p}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    os.chmod(p, 0o600)
    print(json.dumps({"config": p, "owner_slack_user_id": a.owner_user, "owner_dm_channels": dms}))
    return 0


def write_trackers(names):
    import prefs
    p = prefs.user_path()
    user = {}
    if os.path.exists(p):
        with open(p) as f:
            user = json.load(f)
    if "trackers" in user:
        print(json.dumps({"prefs": p, "trackers": user["trackers"], "kept": True}))
        return 0
    user["trackers"] = names
    errs = prefs.validate(user)
    if errs:
        print("refused: " + "; ".join(errs), file=sys.stderr)
        return 2
    cl.write_atomic(p, json.dumps(user, ensure_ascii=False, indent=1))
    print(json.dumps({"prefs": p, "trackers": names}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
