type Spec = { name: string; description: string; prompt: string; tools: string[]; [k: string]: unknown }

// Agent files name generic MCP servers (`mcp__slack__…`); `foundry mcp specs` maps them to this install's servers
// and returns the agents whose tool lists changed. Registering them under the same name replaces the file agent's
// definition for subagents and the tool list of an `--agent` main session. Any failure keeps the file agents.
// Called from board-pane.tsx's session.start (an event takes one unmatched hook per module).
export async function registerMappedAgents($: any): Promise<number> {
  let n = 0
  try {
    const env = await $.process.hookEnv().catch(() => ({}) as Record<string, string>)
    const r = await $.process.run(['python3', `${$.plugin.root}/scripts/foundry.py`, 'mcp', 'specs'], {
      timeoutMs: 10000,
      env,
    })
    if (r.exitCode !== 0) return 0
    for (const spec of (JSON.parse(r.stdout) as { agents?: Spec[] }).agents ?? []) {
      await $.agent.register(spec)
      n++
    }
  } catch {
    // keep the file agents
  }
  return n
}
