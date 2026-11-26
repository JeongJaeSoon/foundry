#!/bin/sh
# Builds a sample foundry state in $FOUNDRY_STATE_DIR (default ~/.local/state/foundry-sample) for the board screenshot.
set -e
export FOUNDRY_STATE_DIR="${FOUNDRY_STATE_DIR:-$HOME/.local/state/foundry-sample}"
rm -rf "$FOUNDRY_STATE_DIR"
C="python3 $(dirname "$0")/../scripts/foundry.py"
H="python3 $(dirname "$0")/../scripts/hook.py"

$C unit new --id apikey-expiry --title "API key 90-day expiry" --ticket PROJ-801 --pr example-org/webapp#1650 --topic apikey >/dev/null
$C unit new --id notify-channels --title "Notify channel split" --ticket PROJ-812 --pr example-org/webapp#1655 >/dev/null
$C unit new --id gateway-health --title "gateway health wrapper" --ticket PROJ-820 --lead foundry:line-lead >/dev/null
$C unit new --id repo-notes-repo --kind repo --repo example-org/notes-repo --title "notes-repo loose tasks" >/dev/null
$C unit new --id dup-pr-cleanup --title "Close duplicate PRs" --ticket PROJ-790 >/dev/null
$C unit new --id okr-q4-sync --title "OKR Q4 sync" >/dev/null

printf '%s' '{"session_id":"S","agent_type":"foundry:foreman","tool_name":"Agent","tool_use_id":"t","tool_input":{"subagent_type":"foundry:line-lead","prompt":"UNIT: apikey-expiry\nwork"}}' | $H PreToolUse >/dev/null
echo '{"session_id":"S","agent_id":"a1b2c3","agent_type":"foundry:line-lead"}' | $H SubagentStart >/dev/null
echo '{"session_id":"S","agent_id":"a1b2c3","agent_type":"foundry:line-lead","tool_name":"TaskCreate","tool_input":{"subject":"Add expiry column migration"},"tool_response":{"task":{"id":"1"}}}' | $H PostToolUse >/dev/null
echo '{"session_id":"S","agent_id":"a1b2c3","agent_type":"foundry:line-lead","tool_name":"TaskUpdate","tool_input":{"taskId":"1","status":"completed"}}' | $H PostToolUse >/dev/null
echo '{"session_id":"S","agent_id":"a1b2c3","agent_type":"foundry:line-lead","tool_name":"TaskCreate","tool_input":{"subject":"Middleware 4xx on expired key"},"tool_response":{"task":{"id":"2"}}}' | $H PostToolUse >/dev/null
echo '{"session_id":"S","agent_id":"a1b2c3","agent_type":"foundry:line-lead","tool_name":"TaskUpdate","tool_input":{"taskId":"2","status":"in_progress"}}' | $H PostToolUse >/dev/null
$C unit checkpoint apikey-expiry --note "migration merged to feature branch; middleware next" >/dev/null
$C inbox route --text "PROJ-801: expired keys should return 401, not 403" >/dev/null

$C unit status notify-channels in_progress >/dev/null
$C wait add notify-channels --kind pr-review --ref example-org/webapp#1655 --note "review by alice" >/dev/null
$C wait add notify-channels --kind ci --ref example-org/webapp#1655 >/dev/null

$C unit status gateway-health in_progress >/dev/null
$C decision ask --key gateway-health:prod-window --unit gateway-health --question "Deploy the health wrapper to prod today 15:00 or tomorrow 10:00?" >/dev/null || true

$C unit status dup-pr-cleanup done --note "closed 3 duplicate PRs, linked PROJ-790" >/dev/null
$C inbox route --unit repo-notes-repo --text "Fix the broken link in projects/README.md" >/dev/null

python3 "$(dirname "$0")/../scripts/housekeep.py" plan --hid "disk:~" --verdict propose --reason "disk 91% used: ~/Library/Caches 38G" >/dev/null
$C board --format html
