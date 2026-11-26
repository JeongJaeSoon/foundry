---
name: foundry-launch
description: How the foundry foreman creates a unit and launches its line-lead or shop-boss as a background session (`foundry unit launch|resume|stop|status`), and how a lead spawns hands. Use whenever new work starts, a lead session must be started, resumed or stopped, or a lead needs a hand.
---

# Launching in the foundry

`foundry` is the plugin's `bin/foundry`, on the Bash tool's PATH while the plugin is enabled; type it as the first word of the command.

## Foreman: new work

1. Make sure it is new: `foundry unit find --text "<owner's words>" [--ticket PROJ-1] [--pr owner/repo#1] [--topic x]`. Any hit means feedback, not new work: use foundry-feedback.
2. The repo must be in the registry (`foundry repo list`; add once with `foundry repo add <name> --path <local clone> --tracker jira|linear|github|none`; a tracker is refused unless prefs `trackers` lists it).
3. Create the unit:
   - project: `foundry unit new --title "<short title>" --repo <name> [--ticket PROJ-123] [--pr owner/repo#n] [--topic <word>] [--tracker jira|linear|github|none]`.
   - loose task in a repo: the repo's standing unit, created once with `foundry unit new --id repo-<name> --kind repo --repo <name> --title "<name> loose tasks"`; then `foundry inbox route --unit repo-<name> --text "<task>"`.
   `unit new` refuses a ticket or PR another unit already holds. A unit on a Jira repo without a ticket: have the dispatcher create one (assigned to the owner).
4. `foundry unit launch <id>`. It:
   - refuses a unit whose session is still live (message or resume it instead);
   - holds the launch (exit 6, `held`) while `max_concurrent` sessions (default 8) are live or the PC is under pressure — retry when a session ends or pressure clears;
   - checks the MCP servers the unit's stages need (prefs `required_mcp`): one scoped to another project is added with `--mcp-config` (`mcp.added`), and none at all refuses the launch (exit 8) with an owner to-do;
   - creates the worktree `~/work/wt/<repo>/<unit>` on branch `foundry/<unit>` (one unit = one worktree = one branch; it refuses a branch or path another unit holds);
   - starts `claude --bg -n lead-<unit> --agent foundry:line-lead --model opus --effort medium --permission-mode auto --settings '{"env":{"FOUNDRY_ROLE":"line-lead","FOUNDRY_UNIT":"<unit>"}}' '/goal <unit recorded done or waiting, and reported; or stop after N turns>'` in the worktree (a shop-boss is `boss-<unit>` on Sonnet) and records the session in the journal. The lead's SessionStart hook hands it the unit's handoff and queued inbox.
5. Queued units (`foundry next`) are launched the same way, in that order, whenever a slot is free (prefs `auto_launch`); no owner start order.
6. Do not wait on it. Its reports land in the journal and wake `foundry events wait`. The owner can watch it with `claude attach lead-<unit>`.

## Foreman: later

- `foundry unit status [<id>] --refresh`: live states from `claude agents --json` (running, idle, done, lost).
- `foundry unit resume <id>`: continues the same session under its own `claude agents` row: it stops the old process if it lingers, then runs a bare `claude --bg --resume <sessionId> <goal>`, which re-applies the launch's flags and env. If Claude answers with a different id anyway (a copy), foundry stops and removes that copy and resumes with the launch flags instead (a copy carrying the conversation under a new id). Use it for a parked unit whose event arrived, or a lost session.
- `foundry unit stop <id>`: `claude stop` plus a journal record. The sweeper stops the sessions of done units by itself.
- A message for a live lead: `SendMessage(to: "lead-<unit>")`. For a finished unit the hook refuses it; `foundry inbox route --unit <id>` queues it for the next launch.
- Every session runs in permission mode `auto`: messages between sessions in different modes are held.

## Lead or shop-boss: hands

- Pick by tier (model-selection): `foundry:hand-light`, `foundry:hand`, `foundry:hand-heavy`. Built-in `Explore` is fine for read-only search. `foundry:advisor` is the judgment-point consultant.
- Give each hand a self-contained task: GOAL (one sentence), absolute paths in your worktree, ACCEPTANCE checks, VERIFY commands, what to REPORT. The acceptance checks are its stop condition; check them when it returns.
- The worktree is the unit's. For parallel edits give a hand `isolation: "worktree"`; never let two hands edit one tree.
- Run independent hands in parallel in one message; sequence dependent ones.

## What the hooks refuse (logged to `~/.local/state/foundry/enforce.log`)

- foreman -> hand or built-in worker, and foreman -> line-lead/shop-boss as a subagent (launch it instead)
- a raw `claude --bg --agent foundry:line-lead|shop-boss` (use `foundry unit launch`)
- anyone but the foreman starting, resuming or stopping sessions (`foundry unit launch|resume|stop`, `claude --bg|-p|--resume|stop`)
- lead/shop-boss -> lead, shop-boss or dispatcher; hands, built-in workers, dispatcher, sweeper-judge, advisor -> anything
- foreman SendMessage -> hand; anyone's SendMessage -> a finished unit's session

A refusal is a routing mistake: fix the route. Never work around it.
