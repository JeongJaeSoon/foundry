import { expect, test } from 'claude-code/testing'
import { registerMappedAgents } from './agents'

// A stand-in $: the test harness does not offer $.agent.register (the engine does; checked with `claude -p`).
function fake(reply: { exitCode: number; stdout: string } | 'throw') {
  const registered: { name: string; tools: string[] }[] = []
  const runs: string[][] = []
  const $ = {
    plugin: { root: '/p' },
    process: {
      hookEnv: async () => ({}),
      run: async (argv: string[]) => {
        runs.push(argv)
        if (reply === 'throw') throw new Error('timed out')
        return reply
      },
    },
    agent: { register: async (s: { name: string; tools: string[] }) => void registered.push({ name: s.name, tools: s.tools }) },
  }
  return { $, registered, runs }
}

test('mapped agent specs are registered under their own names', async () => {
  const spec = { name: 'dispatcher', description: 'd', prompt: 'p', tools: ['Bash', 'mcp__slack__x', 'mcp__gw_slack__x'] }
  const h = fake({ exitCode: 0, stdout: JSON.stringify({ agents: [spec] }) })
  expect(await registerMappedAgents(h.$)).toBe(1)
  expect(h.runs[0]).toEqual(['python3', '/p/scripts/foundry.py', 'mcp', 'specs'])
  expect(h.registered).toEqual([{ name: 'dispatcher', tools: spec.tools }])
})

test('a failing or crashing specs call registers nothing', async () => {
  for (const reply of [{ exitCode: 1, stdout: '' }, 'throw' as const]) {
    const h = fake(reply)
    expect(await registerMappedAgents(h.$)).toBe(0)
    expect(h.registered).toEqual([])
  }
})
