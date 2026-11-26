#!/bin/sh
# foundry install, run by the owner from a plain terminal:
#   sh ~/.local/share/claude-plugins/foundry/install.sh [OWNER_SLACK_USER_ID OWNER_DM_CHANNEL_ID]
# Idempotent. Installs no launchd agent and needs no per-worktree trust step (the launcher trusts the
# worktrees it creates). Rollback: sh ~/.local/share/claude-plugins/foundry/uninstall.sh
set -eu
# Every path comes from where this script sits or from foundry's config (config.json over its defaults).
P="$(cd "$(dirname "$0")" && pwd)"
# The marketplace: the local layout's parent (marketplace.json beside foundry/), else the plugin's own
# single-plugin marketplace (a clone of the public repository, whose root is the plugin). Its name is read from
# its marketplace.json: local-foundry in the private layout, foundry in the public one.
ROOT="$(dirname "$P")"
[ -f "$ROOT/.claude-plugin/marketplace.json" ] || ROOT="$P"
MARKET="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("name") or "")' "$ROOT/.claude-plugin/marketplace.json")"
[ -n "$MARKET" ] || { echo "no marketplace name in $ROOT/.claude-plugin/marketplace.json" >&2; exit 1; }
config() {  # config() KEY: the effective config value, ~ expanded
  python3 -c 'import os, sys; sys.path.insert(0, sys.argv[1]); import foundrylib as cl
v = sys.argv[2] == "state_dir" and cl.state_dir() or cl.load_config().get(sys.argv[2]) or ""
print(os.path.expanduser(v))' "$P/scripts" "$1"
}
STATE="$(config state_dir)"
REPOS_DIR="${FOUNDRY_REPOS_DIR:-$(config repos_dir)}"
# Repos to register when a clone exists under REPOS_DIR (space-separated); `foundry init --repo NAME=PATH` adds more.
INSTALL_REPOS="${FOUNDRY_INSTALL_REPOS:-}"
# Claude Code reads $CLAUDE_CONFIG_DIR/.claude.json when the variable is set,
# else ~/.claude.json. Plugin install, trust and the foreman below all use this same one.
CFG_DIR="${CLAUDE_CONFIG_DIR:-}"

# Trackers are an install setting, none unless FOUNDRY_INSTALL_TRACKERS names them (e.g. jira).
TRACKERS="${FOUNDRY_INSTALL_TRACKERS:-}"
TRACKER_ARGS=""
if [ -n "$TRACKERS" ]; then TRACKER_ARGS="--trackers $TRACKERS"; fi
REPO_TRACKER="${TRACKERS%%,*}"
[ -n "$REPO_TRACKER" ] || REPO_TRACKER=none
if [ "$#" -ge 2 ]; then
  python3 "$P/scripts/install_config.py" $TRACKER_ARGS --owner-user "$1" --owner-dm "$2"
else
  python3 "$P/scripts/install_config.py" $TRACKER_ARGS
fi
chmod +x "$P/bin/foundry" "$P/bin/foundry-housekeep" "$P/install.sh" "$P/uninstall.sh"

claude plugin marketplace add "$ROOT"
claude plugin install "foundry@$MARKET"

for r in $INSTALL_REPOS; do
  if [ -d "$REPOS_DIR/$r/.git" ]; then
    python3 "$P/scripts/foundry.py" repo add "$r" --path "$REPOS_DIR/$r" --tracker "$REPO_TRACKER"
  else
    echo "skip repo $r: no checkout at $REPOS_DIR/$r"
  fi
done

# What `/foundry setup` sets up besides the font (firstrun.base_setup): the state, foreman and worktree
# directories, config.json (mode 600), the foreman directory's trust entry, preflight and the ~/.local/bin/foundry
# shim. A refused shim does not stop the install, as before.
python3 "$P/scripts/foundry.py" setup --shim-only || echo "foundry setup --shim-only failed (exit $?): fix what it printed, then rerun it" >&2

if [ -n "$CFG_DIR" ]; then
  CFG_SHOWN="$CFG_DIR (CLAUDE_CONFIG_DIR)"
  CFG_PREFIX="CLAUDE_CONFIG_DIR=\"$CFG_DIR\" "
  CFG_ENV=",\"CLAUDE_CONFIG_DIR\":\"$CFG_DIR\""
else
  CFG_SHOWN="$HOME/.claude.json (CLAUDE_CONFIG_DIR not set)"
  CFG_PREFIX=""
  CFG_ENV=""
fi

# --effort stays explicit: the CLI ignores a user-settings effortLevel for Opus 5.5. config `foreman_effort` sets it.
FOREMAN_EFFORT=$(FOUNDRY_STATE_DIR="$STATE" python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import foundrylib; print(foundrylib.load_config().get("foreman_effort") or "medium")' "$P/scripts" 2>/dev/null || echo medium)
case "$FOREMAN_EFFORT" in low|medium|high|xhigh|max) ;; *) FOREMAN_EFFORT=medium ;; esac
FOREMAN_LINE=$(FOUNDRY_STATE_DIR="$STATE" python3 "$P/scripts/foundry.py" foreman line 2>/dev/null || echo "foundry foreman line")

cat <<EOF

foundry installed. Claude config used: $CFG_SHOWN
Restart open Claude Code sessions (or /reload-plugins) so the plugin's hooks load.
Start the foreman (same config dir), then attach and send "foundry up" as its first prompt:
  ${CFG_PREFIX}foundry foreman start
  ${CFG_PREFIX}claude attach foreman
Fallback, the same start line by hand (\`foundry foreman line\` prints it):
  $FOREMAN_LINE
foreman effort: $FOREMAN_EFFORT (config foreman_effort; lead_effort and boss_effort set the lead and boss launches)
EOF
[ "$#" -ge 2 ] || echo "Owner Slack ids not set: re-run with OWNER_SLACK_USER_ID OWNER_DM_CHANNEL_ID to enable the owner-DM exception."
