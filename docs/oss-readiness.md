# OSS readiness (G6)

Status as of 0.1.76. The owner decided the open questions (below); nothing has been published and no remote was
created.

## Done

- **Org data out of the tree.** Shipped defaults carry no people, organization or service values (0.1.66); agent
  prompts, skills, scripts, the install script and the example registry use prefs keys or `example-org`; the test
  suite uses `example-org` fixtures. `tests/test_oss_denylist.py` fails on organization names, people, real Slack
  ids and machine paths anywhere in the tree (the marketplace root's files included), with no exceptions.
- **Integrations off by default.** Trackers default to none, Slack sends need a configured channel, notes and deploy
  steps apply only when configured. `tests/test_oss_github_only.py` runs `foundry init`, a repo, a unit, a launch
  dry run, a sync drain and the board (text, html, three UI languages) on the shipped defaults alone.
- **Docs.** English `README.md` with a quickstart through `foundry init`, `docs/architecture.md`,
  `CONTRIBUTING.md`, and `CHANGELOG.md` built from the release commits. `docs/generalization.md` records each
  former assumption and where it lives now.
- **Scans.**
  - Secrets: gitleaks 8.30.1 over the tree and over the git history (133 commits scanned): no findings.
  - Personal and organization data in history: about 870 added lines in past commits carry organization names,
    people's logins or real Slack ids; 7 commit messages name the organization; every commit's author email is a
    work address. The current tree is clean, the history is not.

## Known limits

- **Korean that `ui.language` does not cover.** Text written to the journal stays in the language it was written
  in, and some of it is still written in Korean: auto-close reasons (`decide.py`, `intake.py`), reviewer pick
  reasons (`review.py`), the tracker event lines `sync.py` queues (`상태 →`, `PR 오픈`, `보고:`, which the dispatcher
  rewrites into the target's `sync.languages`), and the shipped `schedules` an existing install materialized. The
  board, the daily report, release and review gaps, the sync status line and the prod card's options follow
  `ui.language` since 0.1.94; the board pane keeps a Korean fallback table only for a model from an older
  `board.py`, and a test holds it equal to the ko catalog.
- **Materialized values are pinned.** An install that materialized (0.1.65/0.1.66) keeps its own copy of
  `schedules`, `required_mcp`, `trackers` and the other `prefs.ORG_KEYS`; later changes to those shipped defaults
  do not reach it.
- **Upgrade path.** Materialize copies from the last effective prefs, so an install that skipped a version adding
  an ORG key gets the neutral default for that key (`ui.language` came in 0.1.61, `sync.jira` in 0.1.65).

## Owner decisions

1. **License: MIT** (0.1.74). `LICENSE` (copyright JeongJaeSoon) and `license` in `plugin.json`.
2. **Where: the owner's personal account**, `JeongJaeSoon/foundry`, under the name foundry. Publishing and pushing
   are separate, later steps.
3. **History: squashed.** Each publication is one parentless commit of the current tree (git commit-tree), authored with
   the owner's GitHub noreply address; the procedure is `docs/publishing.md` (0.1.74).
4. **Trackers: jira, linear, github or none** (0.1.75). GitHub Issues go through `gh` (comment, labels from prefs
   `sync.github.labels`, close and reopen with the unit); the default stays none.
5. **MCP gateway tool names: removed from the tree** (0.1.76). Agent `tools:` lists name generic servers
   (`atlassian`, `slack`, `datadog`, `kubernetes-stg`, `kubernetes-prd`, `obsidian`, `linear`). An install whose servers
   carry other names is mapped: prefs `mcp_servers` ({generic: [server]}), else the one installed server whose name
   ends in `-<generic>` or `_<generic>` (user MCP config and enabled plugins' `.mcp.json`). The board-pane module
   re-registers the affected agents with the mapped names at session start (`foundry mcp specs`), the launcher adds
   them to the lead ask rules, and preflight `mcp_names` fails when a name is ambiguous (`foundry mcp map`).
6. **Design notes: not published.** The Korean design record and overview page live in the owner's notes, outside
   the repository, and are updated there (0.1.74). Release history is `CHANGELOG.md`.
7. **Dev artifacts.** The reviewed autoMode rules moved to `docs/recommended-automode.md`; the worktree-trust patch
   proposal was dropped (0.1.74).
