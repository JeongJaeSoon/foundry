"""The `foundry` command for a plain terminal: a stable shim at ~/.local/bin/foundry.

bin/foundry is on PATH only inside Claude sessions, and the plugin cache path changes with every version, so the
shim resolves the enabled plugin's install path from Claude's plugin registry each time it runs."""

import json
import os
import sys

MARK = "# foundry-shim v1"

# The shim itself: stdlib only, no import of foundry code (it has to find that first).
SHIM = '''#!/usr/bin/env python3
''' + MARK + '''
# Runs the enabled foundry plugin's CLI from any terminal. Written by `foundry setup`; safe to delete.
import json, os, sys

CFG = os.environ.get("CLAUDE_CONFIG_DIR") or %(cfg)r or os.path.expanduser("~/.claude")


def root():
    if os.environ.get("FOUNDRY_PLUGIN_ROOT"):
        return os.environ["FOUNDRY_PLUGIN_ROOT"]
    try:
        with open(os.path.join(CFG, "plugins", "installed_plugins.json")) as f:
            plugins = json.load(f).get("plugins") or {}
    except (OSError, ValueError):
        return None
    try:
        with open(os.path.join(CFG, "settings.json")) as f:
            enabled = json.load(f).get("enabledPlugins") or {}
    except (OSError, ValueError):
        enabled = {}
    names = [k for k in plugins if k.split("@")[0] == "foundry"]
    names.sort(key=lambda k: enabled.get(k) is not True)
    for name in names:
        if enabled and enabled.get(name) is False:
            continue
        rows = sorted(plugins[name] or [], key=lambda r: r.get("scope") != "user")
        for r in rows:
            p = r.get("installPath")
            if p and os.path.isfile(os.path.join(p, "scripts", "foundry.py")):
                return p
    return None


r = root()
if not r:
    sys.stderr.write("foundry: no enabled foundry plugin under " + CFG + "/plugins (installed_plugins.json); "
                     "install it with `claude plugin install foundry@local-foundry`.\\n")
    sys.exit(127)
os.execv(sys.executable, [sys.executable, os.path.join(r, "scripts", "foundry.py")] + sys.argv[1:])
'''


def shim_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".local", "bin", "foundry")


def installed(home=None):
    p = shim_path(home)
    try:
        with open(p) as f:
            return MARK in f.read(4096) and os.access(p, os.X_OK)
    except OSError:
        return False


def cli(home=None):
    """The command to print for the owner's plain terminal: the shim when it is installed, else this plugin's
    bin/foundry by its full path (correct until the next plugin version replaces the cache dir)."""
    if installed(home):
        return "~/.local/bin/foundry"
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "foundry")


def install(home=None, cfg=None, path_env=None):
    """Write the shim; refuse to replace a `foundry` that is not ours. Returns (ok, lines to print)."""
    p = shim_path(home)
    if os.path.exists(p) and not installed(home):
        try:
            with open(p) as f:
                ours = MARK in f.read(4096)
        except OSError:
            ours = False
        if not ours:
            return False, [f"refused: {p} exists and is not the foundry shim; move it away and rerun `foundry setup`."]
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        f.write(SHIM % {"cfg": cfg if cfg is not None else (os.environ.get("CLAUDE_CONFIG_DIR") or "")})
    os.chmod(tmp, 0o755)
    os.replace(tmp, p)
    lines = [f"shim: {p} (runs the enabled foundry plugin from any terminal)"]
    bindir = os.path.dirname(p)
    dirs = [os.path.realpath(os.path.expanduser(d)) for d in (path_env if path_env is not None
                                                                else os.environ.get("PATH", "")).split(os.pathsep) if d]
    if os.path.realpath(bindir) not in dirs:
        lines.append(f"{bindir} is not on PATH. Add it, then open a new terminal tab:")
        lines.append("  echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.zshrc")
    return True, lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["install"]:
        ok, lines = install()
        print("\n".join(lines), file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 2
    if argv[:1] == ["path"]:
        print(cli())
        return 0
    print("usage: shim.py install | path", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
