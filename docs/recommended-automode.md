# Recommended autoMode rules

foundry's background sessions run in permission mode `auto`. These optional `autoMode.allow` entries for
`~/.claude/settings.json` spare permission prompts for foundry's own commands. Nothing applies them for you: review
each one and paste what you want into the `autoMode.allow` array, after `"$defaults"`.

`install.sh` already adds the marketplace and enables the plugin (`extraKnownMarketplaces`, `enabledPlugins`). foundry
also expects `advisorModel` to be set (the foreman's start command passes it).

Deliberately absent from every rule: `foundry send allow-now` and `foundry approve ... --by owner`. Only the owner
lifts Slack quiet hours and answers tier-3 requests (their own terminal command, the board's approve/deny buttons, or
approving the prompt after an explicit order in chat). The git rules below only spare the prompt:
`scripts/gitpolicy.py` enforces the same limits in PreToolUse, and chained, piped or substituted forms are denied
there anyway.

In each rule, `<foundry plugin root>` is the plugin's directory in your local marketplace or its installed copy under
`~/.claude/plugins/cache`, and the paths are the defaults (`~/.local/state/foundry`, worktree root `~/work/wt`);
adjust them to your `config.json`.

```json
"autoMode": { "allow": [
  "$defaults",
  "Foundry state commands: running the local foundry plugin's own scripts (python3 <foundry plugin root>/scripts/foundry.py, board.py, watch.py poll, and housekeep.py scan / plan / subagent-done), where <foundry plugin root> is ~/.local/share/claude-plugins/foundry or its installed copy under ~/.claude/plugins/cache. They read GitHub through gh read-only and write only under ~/.local/state/foundry/. Not covered: `foundry.py send allow-now` (lifting Slack quiet hours is the owner's call: it must always reach a human permission prompt, or be the owner's own `!` command), `foundry.py approve allow|deny ... --by owner` (answering an owner-only request), editing files under ~/.local/state/foundry/ by any other means (config.json included), or these scripts' arguments pointing FOUNDRY_STATE_DIR anywhere else.",
  "Foundry launcher: python3 <foundry plugin root>/scripts/foundry.py unit launch|resume|stop|status, run by the foundry foreman session. It creates a git worktree under ~/work/wt/<repo>/<unit> from a repo listed in ~/.local/state/foundry/repos.json, starts one background Claude Code session there in permission mode auto with the foundry:line-lead or foundry:shop-boss agent, stops such a session, or resumes it by session id. Not covered: starting sessions with any other permission mode or with permission-skipping flags, worktrees outside ~/work/wt, or answering a workspace trust dialog (that is the owner's step).",
  "Foundry tier-2 approvals: the foundry foreman running python3 <foundry plugin root>/scripts/foundry.py approve allow|deny <rid> [--remember] [--reason ...] for a request the foundry approver recorded at tier 2. Not covered: `--by owner`, tier-3 requests (production, secrets/logins, settings/permissions, payments, external publishing), or any answer that overrides an auto mode classifier denial.",
  "Foundry housekeeping executor: python3 <foundry plugin root>/scripts/housekeep.py execute or foundry.py sweep run (with or without --dry-run). It acts only on candidates it re-checks itself: it removes a git worktree with `git worktree remove` (never --force) when its PR is MERGED or CLOSED, the PR head equals the worktree HEAD, and it has no uncommitted changes, no unpushed commits, no gitignored files and no process using it as cwd (any failed check counts as unsafe); it runs `docker compose -p <name> down --remove-orphans` only for compose projects whose name starts with the configured foundry prefix (foundry- by default); it prunes dangling docker images and build cache older than 7 days; and it stops background foundry sessions of units recorded done. Not covered: running git worktree remove --force, git clean, docker rm / volume rm, or rm -rf directly, or editing ~/.local/state/foundry/config.json to widen the prefix or repo roots.",
  "Foundry unit push: a foundry lead or hand running `git -C <unit worktree> push origin <unit branch>` (optionally with -u, or --force-with-lease to that same branch) as a single command, where the worktree is under ~/work/wt and the branch is the one `foundry unit launch` created for the unit. Not covered: the repository's default branch (main/master, HEAD:main or any refspec naming it), --force or +refspec without lease, any other branch, pushes during the night window, or units in plan mode.",
  "Foundry unit commit: `git -C <unit worktree> add|commit` for files inside that unit's worktree under ~/work/wt, one command per call, message via -m or -F <file in the worktree>. Not covered: commits in any other checkout (the main checkout included), --no-verify, amending commits already pushed, or commit messages built with command substitution.",
  "Foundry PR open: `gh pr create -R <repo in repos.json> --head <unit branch> ...` as a single command from a unit in full mode. Not covered: units in plan mode, other repositories, or PRs whose base is not the repository's default branch.",
  "Foundry pinned merge: `gh pr merge <n> -R <repo> --match-head-commit <sha> [--merge|--squash|--rebase|--auto]` as a single command for a repo whose repos.json entry has merge \"self\" or \"auto\", once the PR is approved (where required) and its checks are green. Not covered: --admin, repos with merge \"owner\" or owner_only, merges without --match-head-commit, or a second production merge into an Argo app another unit holds the lock for.",
  "Foundry review-ask queue: a foundry lead or shop-boss session running `foundry outbox add <its own unit> --kind review-ask|review-remind --channel <C… channel id> [--thread <parent ts>] --text \"…\" [--pr <owner/repo#n>] [--to <U… user id>]` as one command, to queue a review ask or a review reminder in the foundry outbox under ~/.local/state/foundry/. The command posts nothing: the foundry dispatcher sends queued items later through the Slack MCP, where the slackguard ledger enforces dedup and quiet hours, and review asks are the owner's standing delegation to the leads. Not covered: slack_send_message or slack_schedule_message called directly by a lead, any --kind other than review-ask or review-remind, DMs (D… channels), or channels other than the configured review channels (prefs review.channel and the channels of review.people)."
] }
```

Rules, in order: Foundry state commands, Foundry launcher, Foundry tier-2 approvals, Foundry housekeeping executor, Foundry unit push, Foundry unit commit, Foundry PR open, Foundry pinned merge, Foundry review-ask queue.
