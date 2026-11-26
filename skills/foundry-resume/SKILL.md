---
name: foundry-resume
description: Foundry foreman procedure for waking on journal events (lead reports, watcher events, respawn requests), resuming parked or lost lead sessions, and recovering after a restart or reboot. Use at foreman session start, when `foundry events wait` returns, or when the SessionStart hook lists units without a live session.
---

# Resume from state

`foundry` is the plugin's `bin/foundry`, on the Bash tool's PATH while the plugin is enabled; type it as the first word of the command.

## Why parked leads cost nothing

A lead waiting on a review, CI, a deploy window, another team or Jira records the wait (`foundry wait add`) and lets its background session idle (its /goal is met). The foreman's github stream (`foundry watch stream --kind github`, under Monitor) polls the waits and records an event on each change; the sweeper stops sessions whose unit is done.

## Keep the waker armed

Once per foreman session, in the background (Bash `run_in_background: true`):

```
foundry events wait --timeout 3300
```

It exits as soon as an unconsumed event exists (or after 55 minutes with "no events"); either way you are woken. Then run `foundry up --check` and arm what it lists under `rearm` (a PC sleep kills the streams and this wait; a kind still running is not listed); re-arm the wait after handling.

## On wake

1. `foundry events pending`, grouped by unit.
2. Per event kind:
   - `report`: a lead's report (also in the unit notes). Relay what the owner needs; hand outbound messages to the dispatcher.
   - watcher events (`pr-review`, `pr-merge`, `ci`, `time`, ...): if the unit's session is live (`foundry unit status <id> --refresh`), SendMessage `lead-<unit>` the summary; otherwise `foundry unit resume <id>` — the lead's SessionStart brief lists the event.
   - `respawn`: the lead finished a phase and wants a clean context: `foundry unit stop <id>`, then `foundry unit launch <id>` (a new session from the handoff).
3. `foundry events consume <eid> ...` for what you handled.
4. Re-arm the waker. A wait that returned `no events` is re-armed silently: no owner list, no board (the list comes only from `foundry decide digest` when it changed).

## After a restart or reboot

The SessionStart hook rebuilds the snapshot from the journal, marks subagents of earlier foreman sessions `lost`, regenerates the board and lists:
- units in progress with no live session -> `foundry unit resume <id>` (or `launch` if it never had a session); run `foundry unit status --refresh` first so states are current;
- held launches -> `foundry unit launch <id>` again;
- unconsumed events -> as above;
- open owner decisions and permission requests -> remind the owner once, in one message;
- waiting units -> nothing; the watcher covers them.

Whether `--bg` sessions survive a reboot is unmeasured: assume they do not, and resume.

Older foreman sessions left by a /clear or a new session stop by themselves: each foreman SessionStart and
`foundry up` stop the idle ones foundry started that are not this session (a busy one waits for the next check).
`foundry foreman prune --dry-run` lists them; the board says "stale foremen: N" until they are gone. Never stop a
foreman session by hand.

## Jira waits

The watcher polls Jira only if `FOUNDRY_JIRA_STATUS_CMD` is configured. Otherwise give a Jira wait an `--until` re-check time; when it comes due, read the ticket yourself and resume or re-wait.
