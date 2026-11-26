---
name: model-selection
description: Pick the model tier (and effort) for a foundry role or a hand task — decisions up, execution cheap — and update the tier table when a new Claude model ships. Use before spawning any foundry agent, when a hand fails on its tier, or when a new model is announced.
---

# Model selection

Rule of thumb: **decisions up, execution cheap.** The model that decides what to do sits high; the models that type, search and collect sit as low as the task allows.

## Current models (checked 2026-11-18)

Source: https://platform.claude.com/docs/en/about-claude/models/overview and .../models/choosing-a-model

- **Fable 5.2** (`fable`, `claude-fable-5-2`): "For demanding reasoning and long-horizon agentic work". $8 / $40 per MTok. Default effort `high`.
- **Opus 5.5** (`opus`, `claude-opus-5-5`): "For long-running agentic coding and knowledge work". $4 / $20. Default effort `medium`. The docs' default starting point: "If you're unsure which model to use, start with Claude Opus 5.5."
- **Sonnet 5.5** (`sonnet`, `claude-sonnet-5-5`): "The best combination of speed and intelligence". $2 / $10. Default effort `high`; `medium` for well-specified agentic coding.
- **Haiku 5.5** (`haiku`, `claude-haiku-5-5`): "For high-volume, latency-sensitive tasks such as classification, extraction, and routing". From $0.10 / $0.50. Default effort `medium`; `low` for simple high-volume work.

All four: 1M context, 128K output.

## Foundry tiers

- **foreman** (`foundry:foreman`, its own session): Opus, effort medium (config `foreman_effort`, passed as `--effort` on the start line), consults the Fable advisor at judgment points.
- **line-lead** (`foundry:line-lead`, a `--bg` session started by `foundry unit launch`): Opus, effort medium (config `lead_model`/`lead_effort`), consults the Fable advisor at judgment points. There is no separate hard-lead tier: hard units get the advisor, not a pricier lead.
- **advisor** (`foundry:advisor`): Fable, effort high. Read-only consultant called by the foreman and leads before escalating, choosing a design, giving a review verdict, approving a risky cleanup or a tier-2 permission request.
- **shop-boss** (`foundry:shop-boss`, a `--bg` session): Sonnet, effort medium (`boss_model`/`boss_effort`). Loose tasks are small by definition.
- **dispatcher** (`foundry:dispatcher`): Sonnet, effort medium. Writes for humans; Haiku's prose was not good enough for outward text.
- **hand, light** (`foundry:hand-light`): Haiku. Lookups, collection, renames, formatting, mechanical edits.
- **hand** (`foundry:hand`): Sonnet. Normal coding, tests, docs, PR updates.
- **hand, heavy** (`foundry:hand-heavy`): Opus. Wide refactors, subtle bugs, migrations, concurrency, or any task a Sonnet hand already failed once.
- **watch, sweep and collection**: scripts (`watch.py`, `housekeep.py scan`). No model.
- **sweeper judge** (`foundry:sweeper-judge`): Haiku, effort low; runs only when the watchers found candidates.

## Choosing a hand tier

1. Can a script do it? Then a script does it (no agent at all).
2. Is the answer a list of facts with sources? `hand-light`.
3. Is the task well specified, in one repo, with tests to run? `hand`.
4. Does it need judgment about a design, touch many files, or did `hand` fail at it? `hand-heavy`.
5. Before moving a task up a tier, try more effort on the same tier: "Tuning effort is often a better lever than switching models" (choosing-a-model).

## How the tier is applied

Each tier is its own agent with `model:` pinned in frontmatter, so picking the agent picks the model. Measured on Claude Code 2.1.326 (2026-11-18): the Agent tool's inline `model` parameter **is** honored, both in this interactive session and under `claude -p`, and it overrides the frontmatter (precedence per the sub-agents docs: inline parameter, then the definition's `model`, then `CLAUDE_CODE_SUBAGENT_MODEL`, then the parent's model). It is ignored only when `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` is set or a mod rewrites the spawn. So: pick the agent by tier and **do not** pass `model` inline unless you mean to override the tier for one run.

To check what actually ran: the subagent transcript (`~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl`) has `message.model` on every assistant turn.

## When a new model ships

1. Read the models overview and choosing-a-model pages (and the effort page) for the new model's description, price, default effort and recommended use. Quote them; do not paraphrase from memory.
2. Decide which tier it replaces, using the docs' own recommendations, not the version number: a new Haiku replaces `hand-light` and the sweeper judge; a new Sonnet replaces `hand`, the shop-boss and the dispatcher; a new Opus replaces `hand-heavy`, the line-lead and the foreman; a new top model replaces the advisor.
3. If the plugin pins aliases (`haiku`, `sonnet`, `opus`, `fable`), the alias moves by itself; only change `effort:` if the new model's default differs. If it pins a full model id, change the `model:` line in each affected `agents/*.md`. Lead sessions take their model from `~/.local/state/foundry/config.json` (`lead_model`, `boss_model`), not frontmatter: update those too.
4. Update the "Current models" list and the date above.
5. Run one representative task per changed tier and confirm `message.model` in the transcript.
6. Commit to the plugin's local git with a message naming the model and the tiers moved.

Prompting note from the prompt-engineering overview: "Not every success criterion or failing eval is best solved by prompt engineering. For example, you can sometimes improve latency and cost more easily by choosing a different model." Define what success looks like for a role before moving it between tiers.
