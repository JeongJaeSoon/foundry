---
name: foundry-handoff
description: Wrap up and restart fresh from a compact handoff instead of a bloated context — for the foundry foreman (handoff, then /clear, then reload) and for line-leads and shop-bosses (handoff at a phase end, then a fresh session). Use after repeated compaction, at the end of a lead phase, before /clear, or when a session feels slow or confused from long context.
---

# Handoff and fresh restart

`foundry` is the plugin's `bin/foundry`, on the Bash tool's PATH while the plugin is enabled; type it as the first word of the command.

A handoff is a short document rendered from foundry state: goal, done, in progress, waits, decisions (open ones marked "do not re-ask"), next step, pointers (tickets, PRs, files). It holds no transcript. It is the brief a lead session starts from (its SessionStart note; `foundry unit brief` = `foundry handoff show`), so "park, then resume on an event" and "clear and reload" are one mechanism.

Only three things are not derivable from state, so only they are written by hand:

```
foundry handoff write <unit|_foreman> --goal "<one line>" --next "<the very next action>" --file <path> [--file ...] [--summary "<what just finished>"] [--respawn]
```

Everything else (steps from TaskCreate/TaskUpdate, checkpoints, waits, events, decisions, PR/ticket keys) is already in state.

## Save points (automatic, by hooks)

- **PreCompact**: `handoffs/<unit>.md` (lead sessions) or `_foreman.md` is written; the foreman's compaction count goes up.
- **SessionStart**: a lead session always starts from its unit handoff plus the queued inbox. The foreman gets its handoff on `compact`, `clear` and `resume`, and from the second compaction on it is told to do the clear cycle.
- **SessionEnd**: the foreman handoff, or the lead's unit handoff (and its session is recorded stopped).

## Foreman: the clear cycle

When the SessionStart note says the session compacted twice, or the owner asks for a fresh start:

1. Put every live instruction into state: route pending owner instructions (foundry-feedback), record decisions (foundry-escalate).
2. `foundry handoff write _foreman --next "<what you were about to do>" [--file ...]`.
3. Read `foundry handoff show _foreman` once. If something the owner said is missing, put it into state first.
4. Ask the owner to type `/clear` (a model cannot run it). The SessionStart hook reloads the handoff; re-arm `foundry events wait` and continue from "Next step".

Lead sessions are separate processes and are not affected by the foreman's /clear.

A foreman started without `--agents` (before 0.1.83) keeps its agent file's generic MCP names once it is started
fresh, and /clear does not change its start flags. To restart it with the mapped definition, at a time the owner
picks: steps 1-3 above, then the owner runs `claude stop <id>` (the current foreman's short id, as the board's
"foreman restart needed" hint prints it; not `claude stop foreman`, which is ambiguous once stale sessions share
the name) and `foundry foreman start` from a plain terminal
(it builds the same start line, `--agents` included, and refuses while a foreman is still running or idle), then
`claude attach foreman` and `foundry up` as the first prompt. `foundry foreman line` prints the line for a start by hand.

## Lead or shop-boss: fresh at a phase end

At the end of a phase (design done, PR opened, a sub-milestone verified), or after your context compacted:

1. Mark finished steps completed (TaskUpdate).
2. `foundry handoff write <id> --summary "<phase result>" --next "<first step of the next phase>" --file <key files> --respawn`.
3. `foundry unit report <id> --text "phase done: ..."` and let the session idle. The `respawn` event makes the foreman stop this session and launch a fresh one from the handoff.

When you wait on something external instead, use `foundry wait add` (foundry-lead-loop); the resume on the event uses the same handoff.
