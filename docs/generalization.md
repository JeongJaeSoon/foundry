# foundry generalization: inventory and status (G1–G6)

foundry was built for one owner on one Mac in one organization. This file records where that showed in the
plugin (inventory taken at 0.1.59) and where each assumption lives now.

## Inventory and status

### 1. People, accounts and ids

| Assumption | Now |
| --- | --- |
| Reviewers (GitHub login, Slack user id, alias), the review channel and the reviewer pool shipped in `defaults/prefs.json` | user prefs `review.*`; shipped defaults are empty (0.1.66) |
| A release/deploy bot account as the bump and deploy PR author, also a code fallback | prefs `merge.bump_pr_authors`, `merge.deploy_pr_authors`; no code fallback (0.1.65) |
| The owner's GitHub login | config `owner_github_login`, filled by `foundry init` from `gh api user` (0.1.60) |

### 2. Organization: repos, trackers, services

| Assumption | Now |
| --- | --- |
| Repo globs for no-release repos, batch-release repos and their procedure, owner-only production paths, Argo apps, MCP servers required per stage | user prefs `release.*`, `merge.*`, `required_mcp`; shipped defaults empty (0.1.66) |
| The example registry named real repos | `defaults/repos.example.json` uses `example-org` (0.1.67) |
| `install.sh` registered three fixed repos | `FOUNDRY_INSTALL_REPOS` / `foundry init --repo NAME=PATH` (0.1.60, 0.1.67) |
| Jira project key, which work gets a ticket, and the Atlassian site in dispatcher instructions | prefs `sync.jira.project`, `sync.jira.scope`, `sync.jira.site` (0.1.65) |
| Agent prompts named the org's repos, project and site | prompts point at the prefs keys (0.1.67) |
| Agent `tools:` lists and lead ask rules name the organization's MCP gateway tools | generic server names, mapped per install by prefs `mcp_servers` or a unique suffix match (0.1.76) |

### 3. One machine

| Assumption | Now |
| --- | --- |
| Worktree root and foreman directory under `~/work` | config `worktree_root`, `foreman_dir`; `foundry init` asks (0.1.60) |
| macOS `osascript` notifications | `cl.desktop_notify`: `FOUNDRY_NOTIFY_CMD`, macOS, Linux `notify-send`, else silent (0.1.65) |
| Legacy launchd-agent cleanup in `uninstall.sh` | unchanged; it only removes what an old version installed |

### 4. Locale and time

| Assumption | Now |
| --- | --- |
| A fixed UTC+9 clock and parsing zone | prefs `timezone`, any IANA zone, through `cl.zone()` (0.1.64); shipped default UTC (0.1.66) |
| Korean UI text in the board, pane, board.html and CLI | prefs `ui.language` (ko, ja, en) with catalogs in `defaults/i18n/` (0.1.61–0.1.63); what is still Korean is listed in `docs/oss-readiness.md` (Known limits) |
| Card language, outward comment languages and footers | prefs `language`, `sync.languages`, `sync.comment_footer`, `slack.footer`; shipped defaults English (0.1.66) |

### 5. Owner rules

| Assumption | Now |
| --- | --- |
| Release bump PRs approved by the owner's account | applies only to authors named in prefs `merge.bump_pr_authors` (empty by default) |
| The daily report and review reminder schedules | user prefs `schedules`; not in the shipped defaults (0.1.66) |

### 6. Tests

The suite used real Slack ids, people and repo names. It now uses `example-org` fixtures, and
`tests/fixtures/prefs-overlay.json` (through `FOUNDRY_PREFS_OVERLAY`) layers the fixture org values over the neutral
shipped defaults (0.1.66, 0.1.67). `tests/test_oss_denylist.py` keeps organization, people and machine data out of
the tree, with no exceptions (0.1.76).

## Design notes

- **Defaults ship empty of people and org.** An existing install keeps its values: on the first prefs load without
  the `prefs.materialized` marker, every key in `prefs.ORG_KEYS` that the user's prefs lack is copied from the last
  effective prefs (`prefs.valid.json`).
- **Per-repo facts live in `repos.json`**: release step, merge mode, Argo app, tracker. Cross-repo rules stay prefs
  globs.
- **Integrations default to off.** A user with only GitHub runs units, leads, hands, PRs, reviews and the board:
  tracker `none` per repo, no Slack channel (outbox items are listed, never sent), no notes target, no deploy steps.
