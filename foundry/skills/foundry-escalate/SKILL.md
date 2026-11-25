---
name: foundry-escalate
description: How a foundry lead, shop-boss or dispatcher asks the owner for a decision exactly once and keeps working on everything else, and how the foreman relays and records the answer. Use whenever a decision only the owner can make blocks part of a unit.
---

# Escalate to the owner

`foundry` is the plugin's `bin/foundry`, on the Bash tool's PATH while the plugin is enabled; type it as the first word of the command.

## Lead side

0. Is it really the owner's call? Consult `foundry:advisor` with the facts and your draft; owner-only acts (prod, secrets/logins, settings/permissions, payments, external publishing) always are. A configuration value foundry can discover (jira_base, Slack ids) never is: the foreman sets it. An order the owner already gave in chat is recorded from the quote, never asked again. An owner to-do carries `--why-owner`, one line on why only the owner can do it.
1. Name the decision with a stable key: `<unit>:<topic>`, e.g. `alpha:prod-merge-window`. The same question always gets the same key; the key is what stops a second ask.
2. `foundry decision ask --key <key> --unit <unit> --question "<질문 한 줄, 그 아래 개조식 줄>"`. Write it 개조식 (short bullets, one fact per line), never one paragraph: a one-line question first, then `- 확인됨` / `- 확인 불가` / `- 참고` with their facts indented as `  - ` items, `- 옵션 A: ...` and `- 옵션 B: ...` one per line, `- 추천: A (why)` on its own line and `- 막는 것: ...` last, each on its own line. Multi-line text never goes inline: Write it to a file in your worktree and pass `--question-file <file>` (`foundry decide open` also takes `--context-file`). The board, board.html, the pane and `foundry decide show` draw those lines as bullets (an old one-paragraph card is cut at the same labels). The owner reads the card, so write it in Korean (prefs `language`, default `ko`): question, options, 추천 and 막는 것; code ids, PR refs and shas stay as they are. `foundry decision ask` and `foundry decide open` refuse a card with no Hangul. A card never hands the owner a shell command (no `gh pr merge ...`, no `foundry unit ...`, no "run this"): merges are your own act through the merge gate, and when an owner act is truly needed (a tier-3 approval) the card names the board's [allow] button or `/foundry approve allow <rid>` only. The same commands refuse a card that names a shell command.
   - exit 0, `answered`: the owner already decided. Use the answer. Do not ask.
   - exit 3, `pending`: already asked. Do not ask again. Continue the unblocked work; if nothing else is left, checkpoint and report.
   - exit 10, `escalate`: new. Go on.
3. Record it in the journal first, then message: `foundry unit report <unit> --text "[decision] <key>: <question, options, recommendation, what is blocked>"`, and `SendMessage(to: "foreman")` with the same text. The journal record is what reaches a foreman that is down.
4. Keep going on everything the decision does not block. If only blocked work is left, checkpoint and let the session idle; the answer reaches you as a message (live session) or through the inbox at the next launch.

## Foreman side

1. Relay the question to the owner in chat, once, with the key, in Korean, never with a shell command for the owner to type (merges go through the merge gate; an owner act is the board's [allow] or `/foundry approve allow <rid>`). Call `PushNotification` with a one-line summary (under 200 characters): `foundry: <unit> needs a decision: <short question>`. The board pane also toasts it.
2. When the owner answers: `foundry decision answer --key <key> --answer "<the answer>" --quote "<the owner's exact words>"` (the foreman's relay is refused without `--quote`). This also queues the answer in the unit's inbox.
3. If the unit's session is live, SendMessage `lead-<unit>` the answer; otherwise it is applied at the next launch or resume.
4. Never answer for the owner, and never re-ask a key `foundry decision list` shows as answered.
