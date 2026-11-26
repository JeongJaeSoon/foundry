# foundry architecture

The short reference. [`concepts.md`](concepts.md) explains each part with diagrams, and
[`snapshots.md`](snapshots.md) shows the board, a unit's detail, a decision card and the handover as text.

## Roles

| Role | Runs as | Does | Never |
| --- | --- | --- | --- |
| owner | a person | gives instructions, answers decision cards and tier-3 permission requests, merges what policy keeps for them | — |
| foreman | one background Claude Code session (`--agent foundry:foreman`) | turns instructions into units, launches and resumes leads, answers tier-2 permission requests with the advisor, relays escalations | spawns hands, edits code |
| line-lead | one background session per project unit, in the unit's worktree | plans, delegates to hands, judges results, opens PRs, records state | talks to the owner directly |
| shop-boss | one background session per repository | small loose tasks in that repo | long projects |
| hand (light / normal / heavy) | a subagent inside a lead | one well-specified task | spawns agents |
| dispatcher | a subagent of the foreman | outward tracking: tracker comments and status, notes, the Slack outbox | spawns agents |
| advisor, sweeper-judge | subagents | a second opinion; housekeeping verdicts | act |

A unit is one piece of work: one ticket (or none), one worktree, one branch, one lead session.

## State

All state is under `~/.local/state/foundry/` (or `FOUNDRY_STATE_DIR`):

- `journal.jsonl`: append-only records; `foundry.py` is the only writer for agents. Every feature module
  contributes reducers, and `snapshot.json` is the replay of the journal.
- `config.json` (mode 600): machine settings and the owner's Slack ids.
- `prefs.json`: the user's preferences over `defaults/prefs.json`; `prefs.valid.json` is the last valid merge.
- `repos.json`: the repo registry.
- `board.json`, `board.html`: the rendered board.

## Hooks

- **PreToolUse**: the chain (who may spawn whom), the git and `gh` policy, the Slack send guard (quiet hours,
  dedup, the appended footer), protected paths.
- **PermissionRequest**: tiers. Tier 1 is allowed by policy, tier 2 goes to the foreman and advisor, tier 3 waits for
  the owner (board buttons or a terminal), and a timeout denies and queues the request.
- **PostToolUse**: journals pushes, opened PRs, merges and sends.
- **SessionStart**: briefs a lead or the foreman from the journal and its handoff.

## Watchers

Model-free streams run under the foreman's Monitor tool: `github` (PRs, reviews, CI, merges, release tags),
`sessions` (lead liveness), `pressure` (load, memory, disk, docker), `sync` (tracker lag), `stalls`. They write
events to the journal; `foundry events wait` wakes the foreman, which resumes the waiting unit's lead.

## Policy

- Merges: `self` by default after approval, CLEAN and green at a pinned head; owner-only repos and paths need the
  owner's recorded order; one production merge per Argo app at a time.
- Releases: batch-release repos get a release unit once merged PRs have been quiet; dev deploys are the lead's,
  production waits for the owner's go.
- Night window and Slack quiet hours: no pushes, no sends; cards wait for the morning handover.

## Surfaces

- Board: text (`foundry board`), JSON for the side pane (`hooks/board-pane.tsx`), and `board.html`.
- UI language: `defaults/i18n/<language>/<namespace>.json`, chosen by prefs `ui.language`. Text already written to
  the journal stays in the language it was written in.
