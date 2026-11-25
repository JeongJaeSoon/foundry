# Contributing

## Tests

From `foundry/`:

```sh
python3 -m unittest discover -s tests     # the Python suite (stdlib only)
claude plugin test .                      # the board pane
```

Run them with a `PATH` that does not contain an installed `foundry` shim, so the tests exercise this checkout.

## Rules

- Code, comments and commit messages in English. Comment only what the code cannot say.
- The minimum code that works: reuse a helper before adding one, the standard library before a dependency.
- Never weaken a hook, a permission tier, the merge gate or an approval check to make something pass.
- UI text goes through the catalogs (`defaults/i18n/<language>/<namespace>.json`): add the key to `en`, `ja` and
  `ko` with the same named placeholders; never build a sentence from translated fragments.
- No organization, people or machine data in the tree: `tests/test_oss_denylist.py` checks it. Fixtures use
  `example-org`; org-like prefs for tests live in `tests/fixtures/prefs-overlay.json`.
- Agent `tools:` lists name generic MCP servers (`mcp__slack__…`), never an organization's server names;
  `scripts/mcpmap.py` maps them per install.
- A new prefs key goes into `defaults/prefs.json` (validation rejects unknown keys) with a neutral default.
- Each release bumps the version in `foundry/.claude-plugin/plugin.json` and the marketplace manifest, adds a line to
  `CHANGELOG.md`, and adds a test file for what changed.
