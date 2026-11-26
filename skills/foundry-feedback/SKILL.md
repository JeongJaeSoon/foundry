---
name: foundry-feedback
description: Attach every owner follow-up instruction to the unit it is about (by unit id, ticket, PR, or topic) and deliver it to that unit's lead session, instead of queueing it as new work. Use for every owner message in a foundry session before creating anything new.
---

# Route owner feedback

`foundry` is the plugin's `bin/foundry`, on the Bash tool's PATH while the plugin is enabled; type it as the first word of the command.

The past failure: follow-ups about the same task piled up as separate queue items. The rule: **an instruction about existing work is appended to that work's inbox, never made into a new item.**

1. Route it with every key you can see:
   `foundry inbox route --text "<owner's words, verbatim>" [--unit <id>] [--ticket PROJ-1] [--pr owner/repo#12] [--topic <word>]`
   Ticket keys and GitHub PR URLs in the text are found automatically.
2. Read the result:
   - `attached`: done. If `lead_running` is true, also `SendMessage(to: <session>)` with the instruction now. Otherwise it is queued and the lead's SessionStart hook applies it at the next `foundry unit launch|resume`. A finished unit is never woken: its old session is not messaged (the hook refuses it). If the instruction reopens a done unit, `foundry unit status <id> in_progress --note "reopened: ..."` and launch it. Tell the owner which unit it went to, in one line.
   - `ambiguous` (exit 4): several units match. Pick by context, or ask the owner, then re-run with `--unit`.
   - `no_match` (exit 5): the result lists the open units. If one is clearly it, re-run with `--unit`. Only really new work gets `--new <id> --title ... [--kind repo --repo <name>]`, then launch (foundry-launch).
3. A decision the owner states that answers an open decision key goes through `foundry decision answer --key <key> --answer "<the answer>" --quote "<the owner's exact words>"`, not the inbox.

When the owner names a PR or ticket that belongs to a unit but no unit holds yet, add it: `foundry unit keys <id> --pr owner/repo#n`. Future feedback then attaches by itself.
