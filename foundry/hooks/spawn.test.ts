import { expect, test } from 'claude-code/testing'

function harness(on: any, reply: { exitCode: number; stdout: string } | 'throw') {
  const runs: string[][] = []
  const spawned: { model?: string; subagentType: string }[] = []
  on('process.hookEnv', () => ({ value: { CLAUDE_CODE_SESSION_ID: 'S1' } }))
  on('process.run', (_: unknown, e: { argv: string[] }) => {
    runs.push([...e.argv])
    if (reply === 'throw') throw new Error('timed out')
    return { value: { ...reply, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('agent.spawn', (_: unknown, e: { model?: string; subagentType: string }) => {
    spawned.push({ model: e.model, subagentType: e.subagentType })
    return { model: e.model ?? 'inherit', agentId: 'A1' }
  })
  return { runs, spawned }
}

test('a deny from spawn-check refuses the spawn', async ($, on) => {
  const h = harness(on, { exitCode: 0, stdout: JSON.stringify({ deny: '[foundry] no', caller: 'foreman' }) })
  const r = await $.agent.spawn(<any>{ prompt: 'UNIT: alpha\ngo', subagentType: 'general-purpose' })
  expect(r).toEqual({ deny: '[foundry] no' })
  expect(h.spawned).toEqual([])
  const argv = h.runs[0]!
  expect(argv.slice(1, 3)).toEqual([expect.stringMatching(/scripts\/foundry\.py$/), 'spawn-check'])
  const req = JSON.parse(argv[4]!)
  expect(req.subagentType).toBe('general-purpose')
  expect(req.prompt).toBe('UNIT: alpha\ngo')
})

test('a pinned model rewrites the spawn', async ($, on) => {
  const h = harness(on, { exitCode: 0, stdout: JSON.stringify({ model: 'haiku', caller: 'lead' }) })
  await $.agent.spawn(<any>{ prompt: 'go', subagentType: 'general-purpose', model: 'opus' })
  expect(h.spawned).toEqual([{ model: 'haiku', subagentType: 'general-purpose' }])
})

test('a failing or crashing check passes the spawn through', async ($, on) => {
  const h = harness(on, { exitCode: 1, stdout: '' })
  await $.agent.spawn(<any>{ prompt: 'go', subagentType: 'general-purpose', model: 'opus' })
  expect(h.spawned).toEqual([{ model: 'opus', subagentType: 'general-purpose' }])
})

test('a thrown process error passes the spawn through', async ($, on) => {
  const h = harness(on, 'throw')
  await $.agent.spawn(<any>{ prompt: 'go', subagentType: 'general-purpose' })
  expect(h.spawned.length).toBe(1)
})
