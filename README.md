# foundry

A local Claude Code plugin that runs software work through a fixed chain: owner → foreman → line-lead / shop-boss →
hand. The foreman is a background session that turns the owner's instructions into units of work. Each unit gets its
own git worktree and its own `claude --bg` lead session; hands are subagents inside the lead. Hooks enforce the chain
and answer permission requests in tiers, all state lives in append-only files under `~/.local/state/foundry/`, and a
side pane shows the board.

- Architecture: [`docs/architecture.md`](docs/architecture.md) · concepts with diagrams: [`docs/concepts.md`](docs/concepts.md) ·
  screens as text: [`docs/snapshots.md`](docs/snapshots.md)
- Changes: [`CHANGELOG.md`](CHANGELOG.md) · contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Optional autoMode rules: [`docs/recommended-automode.md`](docs/recommended-automode.md) · license: MIT ([`LICENSE`](LICENSE))

## Quickstart

Requirements: Claude Code, python3 (stdlib only), `git`, `gh` (logged in), and `lsof` for housekeeping. macOS or
Linux.

1. Install the plugin from its marketplace (this repository's root is a single-plugin marketplace named `foundry`:
   `.claude-plugin/marketplace.json` beside `plugin.json`):

   ```sh
   claude plugin marketplace add JeongJaeSoon/foundry
   claude plugin install foundry@foundry
   ```

   then, in a Claude Code session, type `/foundry setup`: it creates what `install.sh` below creates (state,
   foreman and worktree directories, `config.json`, the foreman directory's trust entry, preflight and the
   `~/.local/bin/foundry` shim) and is safe to re-run. Until it has run, each session starts with the one line
   `foundry is not set up: run /foundry setup`; no hook sets anything up by itself.

   Or install from a clone, from a plain terminal, not a shell escape inside a Claude session:

   ```sh
   git clone https://github.com/JeongJaeSoon/foundry && sh foundry/install.sh
   ```

   `install.sh` creates the state, foreman and worktree directories, writes `config.json` (mode 600), adds the
   marketplace it sits in (by the `name` in that marketplace's `marketplace.json`) and installs the plugin from it,
   trusts the foreman directory, runs `foundry preflight`, and installs `~/.local/bin/foundry`, a shim that runs the
   enabled plugin version from any terminal. It is idempotent. Rollback: `sh .../uninstall.sh` (state and worktrees
   stay).

2. Set up with `foundry init` (re-runnable; values already set are kept unless their flag is given):

   ```sh
   foundry init --repo app=~/src/app --tracker none          # --dry-run shows the plan first
   ```

   It discovers your GitHub login (`gh api user`) and the machine's timezone, takes `--worktree-root`,
   `--foreman-dir`, `--tracker jira|linear|github|none`, `--repo NAME=PATH` (repeatable) and optional Slack ids
   (`--owner-slack-user U... --owner-dm D...`), and prints the foreman start command and what is still missing.

3. Restart open Claude Code sessions (or `/reload-plugins`), then, from a plain terminal, start the foreman, attach,
   and send `foundry up` as its first prompt:

   ```sh
   foundry foreman start      # refuses while a foreman is already running or idle, and names it
   claude attach foreman      # then, in the foreman: foundry up
   ```

   `foundry foreman start --prompt "foundry up"` sends the first prompt at launch. Fallback: `foundry foreman line`
   prints the same start line to run by hand:

   ```sh
   cd ~/work/foundry && claude --bg -n foreman --agents "$(foundry mcp agent foundry:foreman 2>/dev/null || echo {})" --agent foundry:foreman --model opus --effort medium --permission-mode auto --settings '{"advisorModel":"claude-fable-5-2","env":{"FOUNDRY_ROLE":"foreman"}}'
   ```

4. Open the board with `/foundry-board`.

`--agents` hands the foreman its agent definition with this install's MCP server names (`foundry mcp agent`): a
`--bg` session takes its main agent from the agent file, whose generic names (`mcp__atlassian__…`) match no server,
before the plugin re-registers it. To restart a running foreman with it: in the foreman, write its handoff (the
foundry-handoff skill), then `claude stop foreman`, then run the start line above.

Nothing is installed as a system service. The foreman arms `foundry watch stream --kind github|sessions|pressure|sync|stalls`
under the Monitor tool; CronCreate is used only for prefs `schedules`. `foundry up` prints what to arm.

## Configuration

Preferences are `defaults/prefs.json` deep-merged with `~/.local/state/foundry/prefs.json`
(`foundry prefs show|validate|set`). An invalid file is refused and the last valid merge is used. The shipped
defaults carry no people, organization or service values; set yours:

- `timezone` (any IANA zone) drives the clock and every displayed time.
- `ui.language`: `en` (default), `ja` or `ko` for the board, pane, board.html and CLI text. Catalogs live in
  `defaults/i18n/<language>/`. `language` is the language of decision cards; `sync.languages` sets the language of
  outward comments per tracker.
- Reviewers and Slack: `review.people` (GitHub login, Slack id, alias), `review.channel`, `review.repos` (reviewer
  pools per repo glob), `slack.footer`.
- Merge and release policy: `merge.*` (owner-only repos and paths, Argo apps, deploy and bump bot authors),
  `release.*` (repos without a release step, batch-release repos and their procedure).
- Review requests: only those addressed to you by name are yours (team-only requests are a count). Skip PRs you
  never review with `review.ignore` rules (author, title, repo, label globs), e.g.
  `foundry prefs set review.ignore '[{"author": "app/*"}, {"title": "[Auto] *"}]'`.
- Trackers: `trackers` (`[]`, `["jira"]`, `["linear"]`, `["github"]`), `sync.jira` (project key, which work gets a ticket, the
  Atlassian site).
- `required_mcp`: MCP servers a stage needs (preflight checks them).
- `schedules`: cron entries the foreman arms.

Per-repo facts (path, default branch, tracker, merge mode, release step, Argo app) live in `repos.json`; see
`defaults/repos.example.json`. Machine settings (worktree root, foreman directory, model and effort per role) are in
`config.json`.

### Integrations

Every integration is off until configured. With GitHub alone you get units, leads, hands, PRs, reviews and the board:

- **Trackers**: none by default. Jira (comments and status only) and Linear go through MCP servers, GitHub Issues
  through `gh` (comments, labels from prefs `sync.github.labels`, close and reopen); the dispatcher agent posts.
- **Slack**: outbox items are filed by leads and sent by the dispatcher through a Slack MCP server; quiet hours,
  dedup are enforced by a hook, which also appends prefs `slack.footer` to every top-level message. Without a channel nothing is sent.
- **Notes**: Obsidian through its MCP server, optional.
- **MCP server names**: agents name generic servers (`atlassian`, `slack`, `datadog`, `kubernetes-stg|prd`,
  `obsidian`, `linear`). If yours are named differently, foundry maps the one installed server ending in
  `-<name>`; set prefs `mcp_servers` (`{"slack": ["my_slack"]}`) otherwise. `foundry mcp map` shows the result.
- **Deploys**: Argo app locks and the deploy-bot approval path apply only to repos and paths you configure.
- **Notifications**: `FOUNDRY_NOTIFY_CMD`, else `osascript` on macOS or `notify-send` on Linux.

## What it does

- **Watchers and stalls**: model-free streams wake waiting units, track PRs and sessions, sample PC load, flag sync
  lag, and detect stalled leads (API loop → relaunch, no progress → nudge then relaunch, repeated error → triage).
- **Dependency graph**: `foundry unit block --on unit:|pr:|deploy:|ticket:|time:|lock:|decision:`, cycle refusal,
  `foundry next`, a verify gate before done, role-scoped `foundry unit set`, andon `foundry halt` / `resume-line`,
  and locks.
- **Decision cards**: `foundry decide open|answer|chat|list|withdraw` with options, a recommendation, a dedup key and
  an optional deadline default.
- **Git policy guard**: one raw `git -C <abs>` / `gh` command per call; denies chains, default-branch pushes, force
  without lease, night pushes, `--admin` and unpinned merges, owner-only merges, and merges while another unit holds
  an Argo app lock. Each deny returns the corrected command.
- **Permission tiers**: a PermissionRequest hook answers what policy allows, asks the foreman (with an advisor) for
  tier 2, and leaves tier 3 to the owner.
- **Denial triage and near misses**, **intake** (auto-start, plan-mode proposals, listing), **night window** (no
  pushes, no Slack; cards wait for the morning handover).
- **Factory**: inventory with stale PRs and reviewer load, preflight, handover, weekly kaizen, cost, and
  `foundry trace <unit>`.
- **Board**: `/foundry-board` opens the pane; the same command folds and expands it; `/foundry-board close` closes
  it. A plugin cannot register a key, so bind one in `~/.claude/keybindings.json`:

  ```json
  { "bindings": [{ "context": "Chat", "bindings": { "ctrl+x f": "command:foundry-board" } }] }
  ```
- **Icons**: prefs `glyphs` `auto` (default), `nerd` or `emoji`; `foundry setup` offers the Nerd Font and checks it
  renders.

## Layout

- `agents/`: foreman, line-lead, shop-boss, hand-light / hand / hand-heavy, dispatcher, sweeper-judge, advisor
- `skills/`: model-selection, foundry-launch, foundry-resume, foundry-handoff, foundry-escalate, foundry-feedback,
  foundry-lead-loop, foundry-housekeeping, foundry-status, foundry-approve
- `hooks/`: `hooks.json` (command hooks), `board-pane.tsx` (board pane) and `spawn.ts` (agent.spawn hook)
- `scripts/`: `foundry.py` (the only state writer for agents) and its modules
- `defaults/`: `prefs.json`, `i18n/`, `prices.json`, `repos.example.json`
- `tests/`: `python3 -m unittest discover -s tests`; `claude plugin test .` for the pane

Only the owner lifts Slack quiet hours (`foundry send allow-now --channel <id>`) and answers tier-3 permission
requests; neither is in any autoMode allow rule.
