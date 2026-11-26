import type { EngineInterface } from 'claude-code'

// The verbs the owner may run as `/foundry <verb> …`. Everything else in the CLI belongs to the foreman or the leads.
export const SLASH_VERBS = ['up', 'resume-line', 'halt', 'approve', 'decide', 'prefs', 'unit', 'board', 'report', 'adopt', 'lock'] as const

export const SLASH_USAGE =
  `usage: /foundry <verb> [args]   verbs: ${SLASH_VERBS.join(', ')}\n` +
  'Runs `foundry <verb> [args]` as the owner (no shell); verbs that take --by get --by owner.\n' +
  'decide: `decide list`, then `decide answer <card id d-0001 | decision key> <option key | "answer text">`.\n' +
  'lock: `lock list`, then `lock release <name> --unit <holder>`.'

// Where a command.run came from: only the person's own input may act as the owner.
const OWNER_ORIGINS = new Set(['composer', 'bridge'])

/** Shell-like words: whitespace splits, '…' is literal, "…" and \ escape; nothing is expanded. null on an open quote. */
export function splitArgs(s: string): string[] | null {
  const out: string[] = []
  let cur = ''
  let has = false
  let q: '"' | "'" | null = null
  for (let i = 0; i < s.length; i++) {
    const c = s.charAt(i)
    if (q === "'") {
      if (c === "'") q = null
      else cur += c
    } else if (c === '\\' && i + 1 < s.length && q !== null) {
      cur += s.charAt(++i)
    } else if (c === '\\' && i + 1 < s.length) {
      cur += s.charAt(++i)
      has = true
    } else if (q === '"') {
      if (c === '"') q = null
      else cur += c
    } else if (c === '"' || c === "'") {
      q = c
      has = true
    } else if (/\s/.test(c)) {
      if (has || cur) out.push(cur)
      cur = ''
      has = false
    } else {
      cur += c
      has = true
    }
  }
  if (q) return null
  if (has || cur) out.push(cur)
  return out
}

/** The foundry argv for `/foundry <args>`, or the usage text when the verb is not one the owner runs here. */
export function slashArgv(args: string): { argv: string[] } | { usage: string } {
  const words = splitArgs(args.trim())
  if (words === null) return { usage: `unbalanced quote\n${SLASH_USAGE}` }
  if (!words.length || !(SLASH_VERBS as readonly string[]).includes(words[0]!)) return { usage: SLASH_USAGE }
  return { argv: words }
}

export async function stateDir($: EngineInterface) {
  return (await $.env.get('FOUNDRY_STATE_DIR')) || `${await $.env.get('HOME')}/.local/state/foundry`
}

/** Runs foundry.py for the owner: a fresh one-use nonce file the CLI consumes, named in the child's env only. */
export async function ownerRun($: EngineInterface, args: string[], timeoutMs = 15000) {
  const nonce = crypto.randomUUID().replace(/-/g, '')
  await $.fs.write(`${await stateDir($)}/owner-nonce/${nonce}`, nonce)
  return $.process.run(['python3', `${$.plugin.root}/scripts/foundry.py`, ...args], {
    timeoutMs,
    env: { FOUNDRY_OWNER_NONCE: nonce },
  })
}

// Called from board-pane's session.start: one module may hook session.start only once.
export function declareSlash($: EngineInterface) {
  return $.command.register({
    name: 'foundry',
    description: 'Run a foundry command as the owner (up, resume-line, halt, approve, decide, prefs, unit, board, report, adopt, lock)',
    argumentHint: '<verb> [args]',
    immediate: true,
  })
}

/** `/foundry <args>`: the command's answer. Only the person's own input runs anything. */
export async function slashRun($: EngineInterface, args: string, origin: string | undefined) {
  if (!origin || !OWNER_ORIGINS.has(origin)) {
    return { text: '/foundry runs only from the owner typing it; nothing was run.', isTextDisplayOnly: true as const }
  }
  const p = slashArgv(args)
  if ('usage' in p) return { text: p.usage, isTextDisplayOnly: true as const }
  const r = await ownerRun($, p.argv, 120000)
  const out = (r.exitCode === 0 ? r.stdout : r.stderr || r.stdout).trim()
  const head = `foundry ${p.argv.join(' ')}: ${r.exitCode === 0 ? 'ok' : `failed (exit ${r.exitCode})`}`
  const note =
    p.argv[0] === 'up'
      ? 'The owner ran /foundry up. Arm what it returned now (the Monitor streams, the crons, the resumes), as you would after running it yourself.'
      : `The owner ran /foundry ${p.argv.join(' ')} themselves; it is done, do not run it again.`
  return { text: out ? `${head}\n${out.slice(0, 4000)}` : head, context: [`${note}\n${head}\n${out.slice(0, 12000)}`] }
}
