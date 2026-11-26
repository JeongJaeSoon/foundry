---
name: foundry-approve
description: How permission prompts of unattended foundry sessions (line-leads, shop-bosses, the foreman) are answered — the four approval tiers, how the foreman answers tier 2 with the advisor, why tier 3 is the owner's, and what a timeout means. Use when `foundry approve list` shows requests, an `approval` event arrives, or a tool call came back "denied and queued".
---

# Permission requests

`foundry` is the plugin's `bin/foundry`, on the Bash tool's PATH while the plugin is enabled; type it as the first word of the command.

A `PermissionRequest` hook answers every prompt a foundry session would otherwise show. Auto-mode classifier denials never reach it: never try to override one, by any route.

## Tiers

- **Tier 0, rules.** `foundry approve rules` prints the table: config `approval_rules` first (`{tool, pattern, action}`; regex on the input JSON), then the built-ins. Reads and edits inside the unit worktree are allowed. Owner-only patterns go to tier 3: production, secrets/logins, settings/permissions (`~/.claude/`, `.claude/settings*.json`, `sudo`, `chmod`/`chown` outside the worktree), payments, publishing beyond the rules, and MCP write tools (unless config `approval_mcp_allow` lists them).
- **Tier 1, ledger.** An answer given with `--remember` is reused for the same tool + input. A tier-3 request only reuses an answer the owner gave.
- **Tier 2, foreman.** The request is recorded and wakes `foundry events wait` (an `approval` event). The hook waits up to `approval_wait_seconds` (default 540). The foreman's own session never gets tier 2: it cannot answer while blocked, so its requests are tier 3.
- **Tier 3, owner.** The same, plus a macOS notification. The owner answers with the board pane's `[allow]`/`[deny]` buttons on the 승인 대기 row (queued ones included), `/foundry approve allow|deny <rid>` typed into the foreman session, or `~/.local/bin/foundry approve allow|deny <rid> --by owner` in a separate terminal tab. Never advise a shell escape inside a session: it inherits the session and the guard refuses it. Models never answer tier 3 (a hook blocks it): relay it to the owner. Relaying it, attach what the owner needs on the board's approval card instead of a chat paragraph: `foundry approve note <rid> --evidence "<facts, one line>" --advisor "<your or the advisor's one-line view>"` (words only; it answers nothing).

## Foreman: answering tier 2

1. `foundry approve list`, then `foundry approve show <rid>`.
2. Consult `foundry:advisor` with the request, the unit's goal and your draft answer.
3. `foundry approve allow|deny <rid> --reason "..."`. Add `--remember` only when the same exact call should pass (or fail) without asking next time.

Leads, bosses and hands never answer requests (blocked). Answering an answered request is a no-op.

## Timeouts

No answer in time → the tool call is denied with "denied and queued as <rid>" and the request stays open. Answer it anyway; the output says `queued: true`. The next identical call from that unit gets the late answer once. Then tell the lead: `SendMessage` to its session when it is live, otherwise `foundry inbox route --unit <unit> --text "..."` (the `next` field says which).
