import type { Register } from 'claude-code'

type Check = { deny?: string; model?: string }

// hooks.json takes one module, so board-pane.tsx calls this from its own register.
// The PreToolUse hook stays the enforcing fallback: any failure here passes the spawn through unchanged.
export const registerSpawn: Register = on => {
  on('agent.spawn', async ($, e, next) => {
    let res: Check = {}
    try {
      const req = {
        parentAgentId: e.parentAgentId,
        subagentType: e.subagentType,
        model: e.model,
        parentModel: e.parentModel,
        tool_use_id: e.tool_use_id,
        background: e.background,
        isTeammate: e.isTeammate ?? false,
        name: e.name,
        resume: e.resume?.agentId ?? null,
        prompt: e.prompt.slice(0, 200),
      }
      const env = await $.process.hookEnv().catch(() => ({}) as Record<string, string>)
      const r = await $.process.run(
        ['python3', `${$.plugin.root}/scripts/foundry.py`, 'spawn-check', '--json', JSON.stringify(req)],
        { timeoutMs: 10000, env },
      )
      if (r.exitCode === 0) res = JSON.parse(r.stdout) as Check
    } catch {
      res = {}
    }
    if (res.deny) return { deny: res.deny }
    if (res.model && !e.fork && !e.resume) return next({ ...e, model: res.model })

    return next(e)
  })
}
