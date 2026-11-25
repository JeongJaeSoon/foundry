---
marp: true
theme: default
paginate: true
---

# foundry

A Claude Code plugin that runs many units of work in parallel,
with one person in charge.

`docs/concepts.md` has the full picture.

---

## 1. The problem

- One person, many tickets, many repos, many Claude sessions.
- Sessions forget rules, retry forever, wait by polling, and ask the person everything.
- Production, secrets and pushes at night need a human; the rest should not.

foundry keeps the person on decisions and leaves the rest to sessions.

---

## 2. The chain

```
owner ──chat──> foreman ──launch/resume──> line-lead (one per unit)
                   │                   └─> shop-boss (one per repo)
                   │                          │
                   └─ dispatcher, judge       └─Agent─> hands (light / normal / heavy)
```

- The owner talks only to the foreman.
- Leads are separate `--bg` sessions. They never talk to each other.
- A call that skips a link (owner→lead, foreman→hand) is denied by a hook.

---

## 3. Rules live in hooks, not memory

- PreToolUse, PermissionRequest, SessionStart, Stop, ... hooks enforce the chain,
  git policy, Slack quiet hours and dedup, and the footer on outward text.
- A rule that only lives in a prompt is a wish; a hook is a guarantee.
- A denial carries the corrected command, so the model retries the right form once.

---

## 4. Approvals in tiers

```
tool call ─> auto mode classifier ─deny─> final (triage: alternative / retry / owner card)
                 │
                 └─ask rule─> tier 0 rules ─> tier 1 ledger ─> tier 2 foreman (+advisor)
                                   └─ owner-only ──────────────> tier 3 owner
```

- Owner-only: production, secrets and logins, settings, payments, publishing.
- Tier 3 shows as a card on the board: why, evidence, advisor note, allow / deny.

---

## 5. State is one journal

- Every fact is one line appended to `journal.jsonl`; a snapshot is rebuilt from it.
- Units, waits, decisions, approvals, PRs, deploys: all reducers over the same log.
- Nothing important lives only in a model's context: `/clear` loses nothing.

---

## 6. Waiting without a model

```
stream scripts (PR/CI, sessions, PC, sync, stalls) ──one line on change──> foreman (Monitor)
crons (handover, preflight, intake) ─────────────────────────────────────> foreman
```

- Scripts watch; the model wakes only when a line appears.
- Leads wait up to 30 min with Monitor, longer waits park the lead and resume it on the event.

---

## 7. The board

- A side pane (`/foundry-board`), the text board, and `board.html`.
- Units by state or repo, decision cards, approval cards, stalls, PR inventory,
  PC load, the morning handover.
- Every button runs a `foundry ... --by owner` command. The pane writes nothing itself.
- UI text in English, Japanese or Korean (`prefs ui.language`).

---

## 8. Night and morning

- Night window (default 22:00–08:00): no pushes, Slack sends wait, no toasts.
- Night work ends at a dev deploy and its verification.
- 08:00 handover: what finished, open decisions, stalls, PR changes, cost.
- Only an explicit "send now" from the owner lifts quiet hours, for one channel.

---

## 9. Safety

- Cleanup acts only on what it re-checks itself: merged or closed PR, clean worktree,
  nothing unpushed, no process inside. Anything else becomes a proposal.
- Production is the owner's: a prod deploy needs the owner's order on a card.
- Merges are pinned to the reviewed head commit.

---

## 10. Install and start

```sh
sh <marketplace>/foundry/install.sh
foundry init --repo app=~/src/app --tracker none
foundry foreman start        # refuses while a foreman already runs
claude attach foreman        # first prompt: foundry up
```

`foundry foreman line` prints the same start line for a start by hand.

---

## 11. Status and limits

- Used daily by one person on one machine; generalized for other users and machines.
- Trackers: Jira, Linear, GitHub Issues, or none.
- Text written to the journal stays in the language it was written in.
- Needs Claude Code with `--bg` sessions, Monitor and the mod API.

See `docs/oss-readiness.md` for the known limits.
