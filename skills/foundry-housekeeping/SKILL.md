---
name: foundry-housekeeping
description: Foundry housekeeping run — the watcher scans worktrees, docker, lead sessions and PC pressure; the cheap judge classifies the candidates; the self-checking executor acts only on the safe classes; everything else goes to the owner as a proposal list. Use on a `housekeeping` event, when the owner asks for cleanup, or daily from the foreman.
---

# Housekeeping

Split: **watchers = scripts (no model), judge = cheap agent, executor = script that re-checks the safe-class rules itself.**

The foreman's sessions stream (`foundry watch stream --kind sessions`) runs the scan every cycle. When candidates appear that need a verdict, it records one `housekeeping` event for you; that is your cue to run this skill. `foundry sweep scan` shows the same scan by hand.

Safe classes (acted on without asking):
1. a git worktree whose PR is MERGED or CLOSED and whose PR head is the worktree HEAD, with no uncommitted changes, no unpushed commits, no gitignored files and no process using it as its cwd (any git/gh/lsof failure counts as unsafe; gitignored files or a live process make it a proposal). Scanned under `repo_roots` and `worktree_root` (`~/work/wt`; `FOUNDRY_WORKTREE_DIRS` replaces the list);
2. a docker compose project whose name starts with the foundry prefix (`foundry-` by default, `docker_project_prefix` in `~/.local/state/foundry/config.json`, at least 3 characters or no project is ever safe) — name throwaway projects `foundry-<unit>-<purpose>`;
3. dangling docker images and build cache older than 7 days — pruned by every executor run, only when `docker info` succeeds; never `-a`, volumes, networks or containers;
4. a lead session (running, idle or done) whose unit is done — stopped through the launcher;
5. a finished (stopped) foundry subagent.

Proposals for the owner: stuck sessions (no journal update for `stuck_hours`, default 6), orphaned sessions (a `lead-*`/`boss-*` in `claude agents` that foundry does not know, or a session whose unit is missing), lost sessions with an in-progress unit (`foundry unit resume <id>`), other worktrees, other containers.

PC pressure: each poll samples load per CPU, reclaimable memory, the lowest disk free and docker CPU with the same sampler and the same prefs `pressure` thresholds as the foreman's pressure stream, and records `ok`, `warn` or `danger` when the level changes. `warn` lets one more launch through (`foundry next` offers at most one), `danger` holds every launch, notifies the owner, and runs safe classes 1 and 3 at once, before any proposal. `foundry sweep pressure` runs the same check by hand.

## Run (foreman)

1. Spawn `foundry:sweeper-judge` (no `UNIT:` needed): "Judge the current housekeeping scan." It runs the scan and records a verdict per candidate.
2. Execute: `foundry sweep run` (same as `foundry-housekeep execute`). It acts only on `safe` verdicts it re-checks itself (`git worktree remove` without `--force`; `docker compose -p <name> down`; the launcher's session stop), prunes the docker cache, records each result, closes every finished subagent with a `safe` verdict itself (it is already stopped, so there is nothing to TaskStop), and prints `foreman_taskstop`: only subagent ids the journal still shows running. TaskStop those, and treat an "is not running" answer as done, not a failure; after each TaskStop: `foundry-housekeep subagent-done <id>`. Every finished subagent (a unit's hands, dispatchers and judges alike) already carries a scripted `safe` verdict and never raises the `housekeeping` event; the judge never sees one. Run `foundry sweep run` after any judge finishes so they are closed.
3. Consume the `housekeeping` event. Proposals appear on the board (section "Housekeeping proposals") and in the judge's report. Give the owner the list in one message: target, reason, the exact command you would run. Do nothing about them without the owner's word.
4. A worktree the owner approves removing goes only through `foundry sweep remove-worktree <path> --by foreman --reason "<the owner's words>"`. Never `git worktree remove` or `rm -r` on a worktree (the hooks deny both), and never delete its files by hand first to make it pass. The command refuses a worktree any process uses as its directory (a live session, yours included), recording `hk.refused` with the processes. A refusal is reported to the owner as it is, never worked around. A worktree in use is never a proposal: the scan marks it `skip` with "in use by <process>".

`--dry-run` on `sweep run` / `execute` shows what would be acted on.

Finished foundry rows in `claude agents`: `foundry sweep agents` (also inside `sweep run` and every sessions cycle; `foundry sweep agents --dry-run` previews) removes them with a plain `claude rm <id>` (never `--discard-unpushed` or `--force-remove-worktree`; the conversation stays in `claude --resume`). A lead/boss row goes when its name is a session foundry recorded, it is done, stopped or failed, older than prefs `sweep.agent_session_ttl_minutes` (30), has no Claude-owned worktree, and its unit is done or cancelled or a newer row of the same name exists and it is not the recorded one (an open unit keeps its newest row). A `foreman` row goes when foundry recorded it as a foreman, it is not the current foreman, and it is finished, or `foundry foreman prune` stopped it and it is still listed with no live process. Other names, and working or waiting rows with a live process, are never touched. Each removal is journaled as `session.pruned`; a failed `claude rm` is journaled once (`session.rm_failed`), retried after 24 h, and shows on the board as "claude rm failed: N" meanwhile. Never remove a row by hand or with ctrl+x for a unit that is still open.
