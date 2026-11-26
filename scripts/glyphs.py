"""Which icon set the board pane draws: Nerd Font glyphs or emoji (prefs `glyphs`: nerd | emoji | auto).

`auto` draws Nerd Font glyphs only once the owner has seen them render in their own terminal (`foundry setup`
records that) and a Nerd Font is still installed; otherwise emoji, which every terminal draws. An installed font
alone is not enough: the terminal has to use it, and only the person looking at it can tell.

`foundry setup` installs the symbols-only Nerd Font with Homebrew after a y/N prompt (or `--install`, the same
consent given up front where no prompt can be shown), prints how to point iTerm2 at it, and asks whether
the sample line renders. Nothing here runs from a hook: fonts are installed by the owner's own command only.
"""

import glob
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

CASK = "font-symbols-only-nerd-font"
MODES = ("nerd", "emoji", "auto")
# The glyphs the pane uses (nf-oct-*), shown together as the render test.
SAMPLE = "        "

TERMINAL_STEPS = """\
Point the terminal at the font (the symbols-only font adds icons; keep your own font for text):
- iTerm2 3.5+: Settings > Profiles > Text > Font > "Use different fonts for specific Unicode ranges" >
  Edit... > add "Symbols Nerd Font Mono" for the Nerd Font ranges (the "Install Nerd Font Bundle" preset
  does it in one step). Older iTerm2: set "Non-ASCII Font" to a full Nerd Font such as
  "JetBrainsMono Nerd Font Mono" (the symbols-only font has no Hangul).
Then reopen the pane (/foundry-board close, then /foundry-board)."""


def font_dirs():
    env = os.environ.get("FOUNDRY_FONT_DIRS")
    if env:
        return [d for d in env.split(os.pathsep) if d]
    return [os.path.expanduser("~/Library/Fonts"), "/Library/Fonts"]


def nerd_font_installed():
    """True when a Nerd Font file is installed (any family: the symbols-only one or a patched font)."""
    for d in font_dirs():
        for f in glob.glob(os.path.join(d, "*")):
            name = os.path.basename(f).replace(" ", "").lower()
            if "nerdfont" in name and name.endswith((".ttf", ".otf", ".ttc")):
                return True
    return False


def record_path():
    return cl.path("glyphs.json")


def recorded():
    try:
        with open(record_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def resolve(mode=None):
    """'nerd' or 'emoji' for a prefs `glyphs` value."""
    if mode is None:
        import prefs
        mode = prefs.load().get("glyphs", "auto")
    if mode in ("nerd", "emoji"):
        return mode
    return "nerd" if recorded().get("render_ok") is True and nerd_font_installed() else "emoji"


def board(snap, m):
    m["glyphs"] = resolve()


def _ask(question):
    if not sys.stdin.isatty():
        return None
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return None


def _record(ok):
    cl.write_atomic(record_path(), json.dumps({"render_ok": ok, "at": cl.iso()}))
    cl.append("glyphs.render", ok=ok)


def cmd_setup(a):
    err = cl.require_owner("foundry setup")
    if err:
        print(err, file=sys.stderr)
        return 2
    import shim
    ok, lines = shim.install()
    print("\n".join(lines))
    if a.shim_only:
        return 0 if ok else 2
    if nerd_font_installed():
        print("Nerd Font: installed.")
    else:
        cmd = ["brew", "install", "--cask", CASK]
        consent = True if a.install else _ask(f"Install the {CASK} font with Homebrew ({' '.join(cmd)})?")
        if consent is None:
            print(f"Nerd Font: not installed. Run `foundry setup` in a terminal to be asked, or `foundry setup --install`.")
            print("The board keeps drawing emoji meanwhile.")
            return 0
        if not consent:
            print("Nerd Font: not installed (declined). The board keeps drawing emoji.")
            return 0
        if not shutil.which("brew"):
            print("refused: Homebrew (brew) is not on PATH; install it or the font by hand.", file=sys.stderr)
            return 2
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(f"brew failed ({r.returncode}); nothing changed. The board keeps drawing emoji.", file=sys.stderr)
            return r.returncode
        cl.append("glyphs.font_installed", cask=CASK)
        print("Nerd Font: installed.")
    print()
    print(TERMINAL_STEPS)
    print()
    print(f"Render test: {SAMPLE}")
    ok = True if a.render_ok else False if a.render_bad else _ask("Do you see nine icons above (not boxes or '?')?")
    if ok is None:
        print("Once the line above shows icons, run `foundry setup --render-ok` (or --render-bad).")
    else:
        _record(ok)
        print(f"Recorded. With prefs glyphs=auto the board draws {'Nerd Font glyphs' if ok else 'emoji'}.")
    return 0


def add_cli(sp):
    s = sp.add_parser("setup", help="install the Nerd Font (asks first) and record whether its glyphs render")
    s.add_argument("--install", action="store_true", help="consent to the brew install without a prompt")
    s.add_argument("--shim-only", action="store_true", help="only install ~/.local/bin/foundry, skip the font steps")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--render-ok", action="store_true", help="the render test line shows icons")
    g.add_argument("--render-bad", action="store_true", help="the render test line shows boxes")
    s.set_defaults(func=cmd_setup)
