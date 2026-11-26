#!/bin/sh
# foundry end-to-end smoke, run by a person from a plain terminal (never from a foundry session):
#   sh foundry/scripts/smoke.sh [OWNER/REPO#ISSUE]
# Everything happens in a throwaway HOME, CLAUDE_CONFIG_DIR and state dir, removed at the end (SMOKE_KEEP=1
# keeps them). Real things it touches: one `claude --bg` lead session (stopped before the script ends) and one
# read-only `gh issue view` (default cli/cli#1, a public issue). Nothing is written to GitHub, Slack or a tracker.
#
# Auth comes from the real machine: gh reads the real gh config (GH_CONFIG_DIR), and the lead launch needs a
# Claude login that works under a fresh config dir (an API key in the environment, or SMOKE_CLAUDE_CONFIG_DIR
# pointing at a logged-in throwaway config). Without one the launch step fails and says so.
#
# Steps: (1) a clean install.sh; (2) `foundry prefs set ui.language en` and a board render with no Hangul;
# (3) the github tracker on a unit whose ticket is the issue, and `gh issue view` read-only; (4) one lead launched
# --bg in a temp git repo, its --agents definition checked for a tools list (and which MCP servers mapped), then
# stopped. Exit 0 only when every step passed.
set -u

SRC="$(cd "$(dirname "$0")/.." && pwd)"         # the plugin tree this script belongs to
TOP="$(dirname "$SRC")"                          # the local layout's marketplace root, when there is one
ISSUE="${1:-cli/cli#1}"
REAL_HOME="$HOME"
export GH_CONFIG_DIR="${GH_CONFIG_DIR:-$REAL_HOME/.config/gh}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/foundry-smoke.XXXXXX")"
export HOME="$WORK/home"
export CLAUDE_CONFIG_DIR="${SMOKE_CLAUDE_CONFIG_DIR:-$WORK/claude-config}"
export FOUNDRY_STATE_DIR="$WORK/state"
export PYTHONDONTWRITEBYTECODE=1   # no __pycache__ appearing under the throwaway tree after cleanup starts
unset FOUNDRY_ROLE FOUNDRY_UNIT FOUNDRY_SESSION FOUNDRY_PREFS_OVERLAY
mkdir -p "$HOME/.local/share/claude-plugins" "$CLAUDE_CONFIG_DIR" "$FOUNDRY_STATE_DIR"
[ -f "$CLAUDE_CONFIG_DIR/.claude.json" ] || printf '{"numStartups": 1, "projects": {}}\n' > "$CLAUDE_CONFIG_DIR/.claude.json"

PLUG="$HOME/.local/share/claude-plugins"
[ -f "$TOP/.claude-plugin/marketplace.json" ] && cp -R "$TOP/.claude-plugin" "$PLUG/.claude-plugin"
cp -R "$SRC" "$PLUG/foundry"
rm -rf "$PLUG/foundry/node_modules"
F="python3 $PLUG/foundry/scripts/foundry.py"

PASS=0
FAIL=0
LAUNCHED=""
ok() { PASS=$((PASS + 1)); echo "PASS  $1"; }
bad() { FAIL=$((FAIL + 1)); echo "FAIL  $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/      /'; }

cleanup() {
  if [ -n "$LAUNCHED" ]; then
    $F unit stop "$LAUNCHED" --reason "smoke done" >/dev/null 2>&1 || true
  fi
  if [ "${SMOKE_KEEP:-0}" = "1" ]; then
    echo "kept: $WORK"
  else
    # A stopped session's daemon may still rewrite files under the throwaway config for a moment: retry.
    for _ in 1 2 3; do
      rm -rf "$WORK" 2>/dev/null && break
      sleep 2
    done
    [ -e "$WORK" ] && echo "could not remove $WORK (remove it by hand)"
  fi
}
trap cleanup EXIT INT TERM

echo "foundry smoke: $(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$SRC/.claude-plugin/plugin.json") in $WORK"

# (1) a clean install
if out=$(FOUNDRY_INSTALL_REPOS="" FOUNDRY_INSTALL_TRACKERS="" sh "$PLUG/foundry/install.sh" 2>&1); then
  case "$out" in *"foundry installed."*) ok "install.sh";; *) bad "install.sh: no 'foundry installed.' line" "$out";; esac
else
  bad "install.sh exited non-zero" "$out"
fi

# (2) English UI: the board has no Hangul
if out=$($F prefs set ui.language en --by owner 2>&1); then ok "prefs set ui.language en"; else bad "prefs set ui.language en" "$out"; fi
$F unit new --id smoke-board --title "Smoke board unit" >/dev/null 2>&1 || true
board=$($F board 2>&1)
if printf '%s' "$board" | python3 -c 'import re, sys; sys.exit(1 if re.search("[가-힣]", sys.stdin.read()) else 0)'; then
  ok "board renders in English (no Hangul)"
else
  bad "board shows Hangul under ui.language en" "$(printf '%s' "$board" | head -20)"
fi

# (3) the github tracker, read-only
REPO="${ISSUE%#*}"
NUM="${ISSUE##*#}"
if out=$($F prefs set trackers '["github"]' --by owner 2>&1); then ok "prefs set trackers github"; else bad "prefs set trackers github" "$out"; fi
if out=$($F unit new --id smoke-issue --title "Smoke issue unit" --ticket "$ISSUE" --tracker github 2>&1); then
  ok "unit with ticket $ISSUE"
else
  bad "unit new --ticket $ISSUE --tracker github" "$out"
fi
# gh with the real HOME: a wrapper such as ghtkn reads its own config under $HOME, not GH_CONFIG_DIR.
if out=$(HOME="$REAL_HOME" gh issue view "$NUM" -R "$REPO" --json number,title,state 2>&1); then
  ok "gh issue view $ISSUE (read-only): $(printf '%s' "$out" | python3 -c 'import json, sys; d = json.load(sys.stdin); print(d.get("state"), "-", d.get("title"))' 2>/dev/null)"
else
  bad "gh issue view $ISSUE" "$out"
fi

# (4) one lead --bg in a temp git repo: its --agents tools, then stop
REPO_DIR="$WORK/repo"
git init -q -b main "$REPO_DIR" && git -C "$REPO_DIR" -c user.name=smoke -c user.email=smoke@example.invalid \
  commit -q --allow-empty -m init
if out=$($F repo add smoke-repo --path "$REPO_DIR" --tracker none 2>&1); then ok "repo add (temp git repo)"; else bad "repo add" "$out"; fi
$F unit new --id smoke-lead --title "Smoke lead: report and stop" --repo smoke-repo >/dev/null 2>&1
# {} is right for an install whose MCP server names need no mapping: the lead runs on the plugin's own agent
# file, and the launch passes no --agents. Otherwise the mapped definition must carry a tools list.
agents=$($F mcp agent foundry:line-lead 2>&1)
if printf '%s' "$agents" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if not d:
    print('no MCP mapped: the lead uses the plugin agent file')
    sys.exit(0)
tools = d.get('foundry:line-lead', {}).get('tools') or []
mcp = sorted({t.split('__')[1] for t in tools if t.startswith('mcp__')})
print(str(len(tools)) + ' tools, MCP servers named: ' + (', '.join(mcp) or 'none'))
sys.exit(0 if tools else 1)" >"$WORK/agents.txt" 2>&1; then
  ok "--agents for foundry:line-lead: $(cat "$WORK/agents.txt")"
else
  bad "--agents definition has no tools list" "$agents
$(cat "$WORK/agents.txt")"
fi
MAPPED=no
[ "$agents" = "{}" ] || MAPPED=yes
if out=$($F unit launch smoke-lead 2>&1); then
  LAUNCHED=smoke-lead
  rec=$(python3 -c '
import json, os, sys
recs = [json.loads(x) for x in open(os.path.join(os.environ["FOUNDRY_STATE_DIR"], "journal.jsonl")) if "session.launch" in x]
r = next((x for x in reversed(recs) if x.get("op") == "session.launch" and x.get("unit") == "smoke-lead"), None)
print(json.dumps({"bg_id": (r or {}).get("bg_id"), "agents": bool((r or {}).get("agents"))}))' 2>&1)
  case "$MAPPED:$rec" in
    yes:*'"agents": true'*) ok "lead launched --bg with the mapped --agents: $rec";;
    no:*'"agents": false'*) ok "lead launched --bg on the plugin agent file (nothing to map): $rec";;
    *) bad "lead launch record does not match the mapping (mapped: $MAPPED)" "$rec
$out";;
  esac
  if claude agents --json 2>/dev/null | grep -q "lead-smoke-lead\|smoke-lead"; then
    ok "claude agents lists the lead"
  else
    bad "claude agents does not list the lead" "$(claude agents --json 2>&1 | head -5)"
  fi
  if out=$($F unit stop smoke-lead --reason "smoke done" 2>&1); then LAUNCHED=""; ok "lead stopped"; else bad "unit stop" "$out"; fi
else
  bad "unit launch smoke-lead (a Claude login under $CLAUDE_CONFIG_DIR is needed)" "$out"
fi

echo "smoke: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
