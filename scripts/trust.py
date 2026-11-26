"""Workspace trust for foundry worktrees.

A `--bg` session in an untrusted directory exits 1 ("Workspace not trusted") and nobody can answer the
dialog there. Claude Code stops its trust walk at the nearest `.git`, so every worktree needs its own
entry. With prefs `auto_trust_worktrees` (default on) the launcher writes exactly one key,
`projects[<worktree realpath>].hasTrustDialogAccepted = true`, into the `.claude.json` of the config dir
the launch uses — only for worktrees it creates for a registered repo — and removes that key again when
the worktree is removed. Every write is atomic, backed up first, keeps all other keys, and is journaled.
"""

import datetime as dt
import fcntl
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

KEEP_BACKUPS = 20


def config_file():
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.join(d, ".claude.json") if d else os.path.expanduser("~/.claude.json")


def _backup(f):
    d = cl.path("trust-backups")
    os.makedirs(d, exist_ok=True)
    name = f"claude.json.{dt.datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
    shutil.copy2(f, os.path.join(d, name))
    olds = sorted(os.listdir(d))[:-KEEP_BACKUPS]
    for o in olds:
        os.remove(os.path.join(d, o))
    return os.path.join(d, name)


ATTEMPTS = 3


def _stamp(f):
    st = os.stat(f)
    return st.st_mtime_ns, st.st_size


def _read(f):
    with open(f) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{f}: top level is not an object")
    return data


def _rewrite(change, holds):
    """Apply change(projects) -> bool to the config file; (wrote, backup).

    Claude Code writes the same file without our lock: the stamp (mtime, size) read with the data is
    re-checked just before the replace, and holds(projects) is re-read after it. Either check failing
    starts over from a fresh read, up to ATTEMPTS times; then RuntimeError."""
    f = config_file()
    if not os.path.exists(f):
        return False, None
    with open(cl.path("trust.lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        backup, wrote = None, False
        for _ in range(ATTEMPTS):
            stamp = _stamp(f)
            data = _read(f)
            projects = data.setdefault("projects", {})
            if not change(projects):
                return wrote, backup
            backup = backup or _backup(f)
            mode = os.stat(f).st_mode & 0o777
            tmp = f"{f}.foundry-tmp"
            with open(tmp, "w") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, mode)
            if _stamp(f) != stamp:  # someone wrote since our read: their change would be reverted
                os.remove(tmp)
                continue
            os.replace(tmp, f)
            wrote = True
            if holds(_read(f).get("projects") or {}):
                return True, backup
        raise RuntimeError(f"{f} kept changing under the trust write ({ATTEMPTS} attempts)")


def register(path, unit):
    """Trust exactly this worktree. Returns 'registered' | 'present' | 'no-config' | 'failed'."""
    key = os.path.realpath(path)

    def trusted(projects):
        entry = projects.get(key)
        return isinstance(entry, dict) and bool(entry.get("hasTrustDialogAccepted"))

    def change(projects):
        if trusted(projects):
            return False
        entry = projects.get(key)
        projects[key] = {**(entry if isinstance(entry, dict) else {}), "hasTrustDialogAccepted": True}
        return True

    if not os.path.exists(config_file()):
        return "no-config"
    try:
        wrote, backup = _rewrite(change, trusted)
    except (OSError, ValueError, RuntimeError) as e:
        cl.append_log("trust.log", {"op": "register", "path": key, "error": str(e)[:300]})
        return "failed"
    if wrote:
        cl.append("trust.register", unit=unit, path=key, file=config_file(), backup=backup)
        return "registered"
    return "present"


def registered_by_us(key):
    """From the snapshot's trust map (reducers keep it through compaction), not the raw journal."""
    return key in (cl.load().get("trust") or {})


def unregister(path, unit=None):
    """Remove the key only when foundry added it (an owner's own entry stays). Returns True when removed."""
    key = os.path.join(os.path.realpath(os.path.dirname(os.path.abspath(path))), os.path.basename(path))
    if not registered_by_us(key) or not os.path.exists(config_file()):
        return False
    try:
        wrote, backup = _rewrite(lambda projects: projects.pop(key, None) is not None,
                                 lambda projects: key not in projects)
    except (OSError, ValueError, RuntimeError) as e:
        cl.append_log("trust.log", {"op": "unregister", "path": key, "error": str(e)[:300]})
        return False
    if wrote:
        cl.append("trust.unregister", unit=unit, path=key, file=config_file(), backup=backup)
    else:  # already gone from the file: forget it so the rollback stops retrying it
        cl.append("trust.unregister", unit=unit, path=key, file=config_file(), backup=None, note="absent")
    return wrote


def unregister_all():
    """Every key foundry added and has not removed yet (the rollback path)."""
    return [p for p in list((cl.load().get("trust") or {})) if unregister(p)]


def main(argv):
    if argv[:1] == ["register"] and len(argv) == 2:
        print(register(argv[1], None))
    elif argv == ["unregister-all"]:
        print("\n".join(unregister_all()))
    else:
        print("usage: trust.py register <dir> | unregister-all", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
