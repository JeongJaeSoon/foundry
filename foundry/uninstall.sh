#!/bin/sh
# foundry rollback, run by the owner from a plain terminal:
#   sh ~/.local/share/claude-plugins/foundry/uninstall.sh
# Stops the foreman, removes the trust entries foundry added, uninstalls the plugin and marketplace, and
# removes any launchd plist an older build left behind. State (~/.local/state/foundry) and worktrees
# stay; delete them by hand once nothing in them is needed.
set -u
P="$(cd "$(dirname "$0")" && pwd)"

claude stop foreman 2>/dev/null || true
python3 "$P/scripts/trust.py" unregister-all || true
claude plugin uninstall foundry@local-foundry || true
claude plugin marketplace remove local-foundry || true
rm -f "$HOME"/Library/LaunchAgents/com.*.foundry*.plist
echo "foundry removed. Restart open Claude Code sessions. Lead sessions still running: claude agents (stop each with claude stop <id>)."
