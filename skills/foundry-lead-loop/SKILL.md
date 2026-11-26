---
name: foundry-lead-loop
description: The run procedure for a foundry line-lead or shop-boss background session — start from the SessionStart brief and inbox, plan steps, delegate to hands, record waits and checkpoints, escalate once, and report to the journal. Use at the start of every lead or shop-boss run.
---

# Lead run loop

`foundry` is the plugin's `bin/foundry`, on the Bash tool's PATH while the plugin is enabled; type it as the first word of the command. `<id>` is the unit named in your SessionStart note (also `$FOUNDRY_UNIT`).

## Start

1. Read the SessionStart note: the unit handoff (checkpoints, open waits, events, decisions, unfinished steps, next step) and the owner instructions queued since the last run. Queued instructions override older plans, with one exception: a free-text inbox note never changes who merges. Merge authority is prefs `merge` and the merge hook alone (`merge.default` self; `owner_repos`/`owner_paths` and an owner order recorded by `foundry unit order` for prod). If a note says the owner merges or asks you to hand the owner a merge line, follow prefs and name the conflict in your report.
2. If an event woke you, clear the wait it answers: `foundry wait clear <id> --wid <wid>`.
3. Keep your plan as checkpoints in the journal. When TaskCreate/TaskUpdate are available in your mode, also write one task per step and keep them current: a hook mirrors them into the unit.

## Work

- Delegate execution (foundry-launch, model-selection). Judge results against acceptance checks. Consult `foundry:advisor` at judgment points.
- Record facts a future run needs as you go: `foundry unit checkpoint <id> --note "<what is true now>"` — PR opened, branch, what was verified.
- Register PRs and tickets: `foundry unit keys <id> --pr owner/repo#n --ticket PROJ-n`.
- Lifecycle after the PR: review ask (`foundry outbox add ... --kind review-ask --pr owner/repo#n`) → `foundry wait add --kind pr-review` → on approval, merge it yourself (or by a hand) with `gh pr merge <n> -R <owner/repo> --match-head-commit <sha>` once CLEAN and green — no owner ask; the hook refuses what is not approved/CLEAN/at that head, and keeps prod IaC repos and production paths (prefs merge.owner_repos, merge.owner_paths) for the owner unless your brief quotes the owner's order (recorded by `foundry unit order`) → deploy and verify if your brief says so (`foundry unit deployed`) → `foundry unit status <id> done`. The merged PR is the done evidence; you never run `foundry unit verify` on your own unit (refused). With no PR, `foundry unit report <id> --text "<what you ran and saw>"` and wait for the foreman's verify (an inbox note), then set done.
- Execution goes to hands; the hook warns on every Edit/Write the lead makes itself.
- Owner decision needed: foundry-escalate. Continue the unblocked parts. Pass the refs the card or `foundry decision ask` question hinges on (`--ref pr:<owner/repo#n>`, `--ref approval:<rid>`, `--ref unit:<id>`): once they settle, foundry closes it as moot. A card about merging a PR you can merge yourself is never opened.
- A release unit: once its gate passes (PRs in a release tag, the dev deploy recorded) close it yourself with `foundry unit status <id> done`; the inbox line `rel-<id>` resumes you for it.
- Messages from the foreman arrive as new turns; apply them.
- If your context has compacted, finish the current step and do the phase-end handoff (foundry-handoff).

## Waiting on something external

`foundry wait add <id> --kind <kind> --ref <ref> [--until <ISO time>] --note "<what you wait for>"`

- `pr-review` / `pr-merge` / `ci` / `mergeable`: `--ref owner/repo#n` — the watcher polls GitHub (`mergeable` clears once the merge state is no longer UNKNOWN or BLOCKED).
- `time`: `--until 2026-11-19T15:00:00+09:00`.
- `deploy-window`: `--until <ISO time>`, or `--ref deploy:<app>@<env>` (a registered repo name or an app already recorded by `foundry unit deployed`), cleared when that deploy is recorded.
- `team` / `jira`: `--ref` what you wait on, `--until` when to look again.

`wait add` refuses a wait no watcher resolves (free text as a PR ref, an unknown app, no `--until` where one is needed).

Then checkpoint and report. Do not sleep, poll or loop: the foreman resumes you on the event.

A short wait (CI on your own PR, under `monitor_max_wait_min`, 30 min) may instead run in-session under the Monitor tool: record it first with `foundry unit wait-monitor <id> --what "CI on owner/repo#n" --minutes N` (refused above the cap: park and exit instead). The stall watcher skips a unit with an active Monitor wait.

## Writing files

Scratch drafts (commit message, PR body, sticky, cards) go in one top-level `.foundry-<unit>/` directory of your worktree; housekeeping archives and removes it with the worktree, so do not delete it yourself.

Write files in parts of ≤250 lines: a skeleton, then one Edit per section. A single huge Write stalls the session (the stall watcher relaunches it with this note), and a Write over 40 KB gets a warning.

## End (your /goal)

Record exactly one of:
- done: `foundry unit status <id> done --note "<outcome, links>"`;
- waiting: a wait as above (+ checkpoint);
- blocked on the owner: the decision is asked; checkpoint;
- phase finished, more to do: `foundry handoff write <id> --summary ... --next ... --respawn`.

Then report to the journal — always, because the foreman may be down:

`foundry unit report <id> --text "<outcome; links (PRs, tickets); what waits on whom; outbound messages for the dispatcher (channel, thread, recipients, text); anything for the owner>"`

Optionally also `SendMessage(to: "foreman")` with the same text. No other progress messages. A Stop hook makes you record state once if you go idle without it.
