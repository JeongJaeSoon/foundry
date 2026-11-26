#!/bin/sh
# foundry rollback, run by the owner from a plain terminal:
#   sh ~/.local/share/claude-plugins/foundry/uninstall.sh
# Stops the foreman, removes the trust entries foundry added, uninstalls the plugin and marketplace, and
# removes any launchd plist an older build left behind. State (~/.local/state/foundry) and worktrees
# stay; delete them by hand once nothing in them is needed.
set -u
P="$(cd "$(dirname "$0")" && pwd)"
# The marketplace install.sh added: the local layout's parent, else the plugin's own (name from its marketplace.json).
ROOT="$(dirname "$P")"
[ -f "$ROOT/.claude-plugin/marketplace.json" ] || ROOT="$P"
MARKET="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("name") or "")' "$ROOT/.claude-plugin/marketplace.json")"
[ -n "$MARKET" ] || { echo "no marketplace name in $ROOT/.claude-plugin/marketplace.json" >&2; exit 1; }

claude stop foreman 2>/dev/null || true
python3 "$P/scripts/trust.py" unregister-all || true
claude plugin uninstall "foundry@$MARKET" || true
claude plugin marketplace remove "$MARKET" || true
rm -f "$HOME"/Library/LaunchAgents/com.*.foundry*.plist
echo "foundry removed. Restart open Claude Code sessions. Lead sessions still running: claude agents (stop each with claude stop <id>)."
