import { expect, test } from 'claude-code/testing'
import {
  ageCell, bar, buildText, cols, fitRef, ghCell, gitIcon, ICONS, invKind, openable, prState, fitKeys, fitTabs, fmtAge, isNewer, pad, RIGHT_MARGIN, shortPath, shortPaths, STATUS_KO, tileColumns, TILE_W,
  failText, FAIL_LOG, knownRepos, prHref, resultLine, RESULT_TTL_MS, shortUnit, tableLines, tableWidths, wrapSegs,
} from './board-pane'
import type { TCell } from './board-pane'
import { contrast, PASTEL, REPO_ACCENT, repoAccent, repoHash, SECTION_TONE } from './palette'
import { slashArgv, splitArgs } from './slash'

const PANE = {
  plugin: 'foundry',
  component: 'Pane',
  requestId: 'foundry-board',
  props: {
    title: 'foundry board',
    isFocused: false,
    bodyColumns: 100,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 60 },
    view: {},
  },
} as const

const APPROVAL = { rid: 'r1', unit: 'alpha', role: 'line-lead', tool: 'Bash', summary: 'git push', tier: 2, asked: '2026-11-18T10:00:00+09:00', session: 'lead-alpha' }

const row = (id: string, title: string, summary: string) => ({
  id, title, summary, kind: 'project', status: 'in_progress', repo: 'example-org/x', lead: 'foundry:line-lead', updated: '2026-11-18T10:00:00+09:00',
  held: null,
  session: { name: `lead-${id}`, state: 'running', role: 'line-lead', bg_id: 'b1', model: null, updated: null },
  agent: { id: 'a1', status: 'running' }, tracker: 'jira',
  worktree: { repo: 'example-org/x', path: '/wt/x-alpha', branch: 'alpha' },
  keys: { ticket: ['PROJ-1'], pr: [], topic: [] }, tickets: ['PROJ-1'], prs: [], note: 'pushed',
  waits: [{ wid: 'w', kind: 'pr-review', ref: 'example-org/x#1', until: null, note: 'alice' }],
  inbox: [], decisions: [{ key: 'k1', question: 'merge now?', answer: null }],
  open_decisions: [{ key: 'k1', question: 'merge now?' }],
  approvals: [APPROVAL],
  todos: [{ subject: 'write tests', status: 'in_progress' }], notes: [{ ts: '2026-11-18T10:00:00+09:00', text: 'pushed' }],
})

const model = (escalations: { id: string; kind: string; unit: string | null; text: string; ts: string | null }[] = []) => ({
  generated: '2026-11-18T10:00:00+09:00',
  sections: [
    { key: 'done', label: '완료', count: 0, rows: [] },
    { key: 'in_progress', label: '진행 중', count: 1, rows: [row('alpha', 'Alpha project', 'pushed')] },
    { key: 'waiting_others', label: '타인 대기', count: 0, rows: [] },
    { key: 'waiting_owner', label: '오너 확인', count: 0, rows: [] },
    { key: 'queue', label: 'queue', count: 0, rows: [] },
  ],
  counts: { done: 0, in_progress: 1, waiting_others: 0, waiting_owner: 0, queue: 0 },
  approvals: [APPROVAL],
  escalations,
  pressure: { level: 'high', reasons: ['8 sessions'] },
  sessions: [{ name: 'lead-alpha', unit: 'alpha', role: 'line-lead', state: 'running' }],
  proposals: [],
  events: [],
})

const ESC = (id: string) => ({ id, kind: 'decision', unit: 'alpha', text: `question ${id}`, ts: null })

function harness(on: any, panes: { id: string; title: string; isPlaced: boolean }[] = [], role?: string) {
  const runs: string[][] = []
  const envs: Record<string, string>[] = []
  const writes: { path: string; text: string }[] = []
  const opens: any[] = []
  const closes: any[] = []
  const toasts: string[] = []
  // `run` lets a test answer one process itself (return undefined for the default).
  const state = { model: model() as any, now: 1_800_000_000_000, run: undefined as undefined | ((argv: string[]) => any) }
  on('clock.now', () => ({ value: state.now }))
  on('command.register', (_: unknown, e: { name: string }) => ({ value: { command: e.name } }))
  on('env.get', (_: unknown, e: { name: string }) => ({
    value: e.name === 'HOME' ? '/home/o' : e.name === 'FOUNDRY_ROLE' ? role : undefined,
  }))
  on('fs.write', (_: unknown, e: { path: string; text: string }) => {
    writes.push({ path: e.path, text: e.text })
    return { value: undefined }
  })
  on('ui.open', (_: unknown, e: any) => {
    opens.push(e)
    return { value: { isPlaced: true } }
  })
  on('ui.panes', () => ({ value: panes }))
  on('clock.every', () => ({ value: null }))
  on('ui.toast', (_: unknown, e: { text: string }) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('process.run', (_: unknown, e: { argv: string[]; init?: { env?: Record<string, string> } }) => {
    runs.push([...e.argv])
    envs.push({ ...(e.init?.env ?? {}) })
    const own = state.run?.(e.argv)
    if (own) return { value: { stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false, ...own } }
    if (e.argv[2] === 'metrics') {
      const hours = Number(e.argv[4])
      const tiles = [{ name: 'load', value: hours, unit: '/cpu', worse: 'high', status: 'warn', spark: [0.2, 0.3, hours],
        glyphs: '▁▁█', levels: ['ok', 'ok', 'danger'], peak: hours, peak_ts: '2026-11-18T13:59:00+09:00' },
        ...['memory', 'disk', 'docker'].map(name => ({ name, value: 40, unit: '%', status: 'ok', spark: [40], glyphs: '▄', levels: ['ok'] }))]
      return { value: { exitCode: 0, stdout: JSON.stringify({ hours, bucket_min: 5, samples: 3, tiles }), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    }
    if (e.argv[2] === 'unit') return { value: { exitCode: 2, stdout: '', stderr: "invalid choice: 'set'", isStdoutTruncated: false, isStderrTruncated: false } }
    if (e.argv[1]?.endsWith('board.py')) return { value: { exitCode: 0, stdout: JSON.stringify(state.model), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
    return { value: { exitCode: 0, stdout: 'answered', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
  return { runs, envs, writes, opens, closes, toasts, state }
}

for (const surface of ['terminal', 'desktop'] as const) {
  test(`rows open their detail and go back (${surface})`, async ($, on) => {
    harness(on)
    await $.session.start({ cwd: '/', surface, isInteractive: true })

    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /진행 중 \(1\)/ }))?.text).toBe('진행 중 (1)')
    await ui.press({ key: 'alpha' })
    expect((await ui.find({ type: 'Text', text: /merge now/ }))?.text).toContain('k1: merge now? → (열림)')
    // The facts are a bordered 항목 | 값 table; the status is one line (no 다음 when it adds nothing).
    expect((await ui.find({ type: 'Text', text: /^│ branch / }))?.text).toContain('alpha · /wt/x-alpha')
    expect((await ui.find({ type: 'Text', text: /^│ 세션 / }))?.text).toContain('lead-alpha (running)')
    expect((await ui.find({ type: 'Text', text: /^│ 상태 / }))?.text).toMatch(/^│ 상태 +│ 진행 중 +│$/)
    expect((await ui.find({ type: 'Text', text: /^│ 항목 / }))?.text).toMatch(/^│ 항목 +│ 값 +│$/)
    expect(await ui.find({ type: 'Text', text: /foundry:line-lead/ })).toBeUndefined()
    await ui.press({ key: 'back' })
    expect(await ui.find({ type: 'Text', text: /merge now/ })).toBeUndefined()
    await ui.unmount()
  })
}

test('allow and deny run the approve CLI and show the result', async ($, on) => {
  const h = harness(on)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })

  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'allow:r1' })
  const approve = h.runs.find(a => a.includes('approve'))!
  expect(approve.slice(1)).toEqual([expect.stringMatching(/scripts\/foundry\.py$/), 'approve', 'allow', 'r1', '--by', 'owner'])
  expect((await ui.find({ key: 'result' }))?.text).toBe('r1 → 허용 ✓')

  await ui.press({ key: 'alpha' })
  await ui.press({ key: 'deny:r1' })
  expect(h.runs.filter(a => a.includes('approve')).at(-1)!.slice(2)).toEqual(['approve', 'deny', 'r1', '--by', 'owner'])
  await ui.unmount()
})

test('a new escalation toasts once', async ($, on) => {
  const h = harness(on)
  h.state.model = model([ESC('decision:old')])
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'refresh' })
  expect(h.toasts).toEqual([])  // open before the mod saw the board: no toast

  h.state.model = model([ESC('decision:old'), ESC('decision:new')])
  await ui.press({ key: 'refresh' })
  await ui.press({ key: 'refresh' })
  expect(h.toasts).toEqual(['foundry decision alpha: question decision:new'])
  await ui.unmount()
})

const BAND = {
  plugin: 'foundry',
  surface: 'terminal',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isCollapsed: false, isWorking: false, maxRows: 20, scroll: { offset: 0, bodyRows: 20 } } as any,
} as const

const pill = async (ui: any, text: string) => (await ui.find({ type: 'Text', text: new RegExp(`^ ${text} $`) }))?.props

test('the band shows background pills, zero counts hidden', async ($, on) => {
  harness(on)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount(BAND)
  expect(await pill(band, '진행 1')).toMatchObject({ backgroundColor: PASTEL.run.bg, color: PASTEL.run.fg, bold: true })
  expect(await pill(band, '승인 1')).toMatchObject({ backgroundColor: PASTEL.approve.bg, color: PASTEL.approve.fg })
  expect(await pill(band, 'PC high')).toMatchObject({ backgroundColor: PASTEL.alarm.bg })
  for (const gone of [/오너 대기/, /ANDON/, /결정/, /완료/, /foreman down/]) {
    expect(await band.find({ type: 'Text', text: gone })).toBeUndefined()
  }
  await band.unmount()
})

test('the band hints at /foundry-board until the pane is open, and ANDON is a red pill first', async ($, on) => {
  const h = harness(on)
  h.state.model = { ...model(), halts: [{ unit: 'alpha' }] } as any
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount(BAND)
  expect((await band.find({ type: 'Text', text: /^\[\/foundry-board\] 보드$/ }))?.props).toMatchObject({ dimColor: true })
  expect(await pill(band, 'ANDON 1')).toMatchObject({ backgroundColor: PASTEL.alarm.bg, color: PASTEL.alarm.fg, bold: true })
  const texts = (await band.findAll({ type: 'Text' })).map((t: any) => t.text)
  expect(texts[0]).toBe(' ANDON 1 ')
  await band.unmount()
})

test('band pills read board.py summary(): an open decision.ask turns the band to attention', async ($, on) => {
  const h = harness(on)
  const m = model()
  const counts = { done: 1, in_progress: 1, waiting_others: 9, waiting_owner: 3, queue: 7 }
  h.state.model = { ...m, cards: [], cards_open: 0, summary: { counts, decisions: 1, approvals: 0, attention: true },
    asks: [{ key: 'PROJ-779:data-security-check', unit: 'PROJ-779', question: 'ok?', asked: null }] } as any
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount(BAND)
  expect(await pill(band, '진행 1')).toBeTruthy()
  expect(await pill(band, '오너 대기 3')).toMatchObject({ backgroundColor: PASTEL.owner.bg })
  expect(await pill(band, '결정 1')).toMatchObject({ backgroundColor: PASTEL.decide.bg })
  await band.unmount()
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /^결정 필요/ }))?.text).toBe('결정 필요 (1)')
  // An ask is a table row (단위 · 질문 · 경과); ⏎ shows its key and the answer command.
  expect((await ui.find({ type: 'Button', key: 'dec:PROJ-779:data-security-check' }))?.text).toMatch(/^│ PROJ-779 │ ok\? +│ — +│$/)
  await ui.press({ key: 'dec:PROJ-779:data-security-check' })
  expect((await ui.find({ type: 'Text', text: /decide answer PROJ-779:data-security-check/ }))).toBeDefined()
  await ui.unmount()
})

test('no band hint once the board pane is open', async ($, on) => {
  harness(on, [{ id: 'foundry-board', title: 'foundry board', isPlaced: true }])
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount(BAND)
  expect(await band.find({ type: 'Text', text: /foundry-board\]/ })).toBeFalsy()
  await band.unmount()
})

test('pastel tones: same-hue darker text on a soft background, ≥ 4.5:1, one hue per meaning', () => {
  for (const [k, t] of Object.entries(PASTEL)) {
    expect([k, contrast(t.bg, t.fg) >= 4.5]).toEqual([k, true])
    // The section bar sits on the terminal's own background, light or dark: ≥ 3:1 against both.
    expect([k, contrast(t.bar, '#000000') >= 3, contrast(t.bar, '#FFFFFF') >= 3]).toEqual([k, true, true])
  }
  const bgs = Object.values(PASTEL).map(t => t.bg)
  expect(new Set(bgs).size).toBe(bgs.length)
  // Without truecolor each channel rounds to round(x / 51) of the 256-color cube; the text colors must still differ.
  const cell = (hex: string) => [1, 3, 5].map(i => Math.round(parseInt(hex.slice(i, i + 2), 16) / 51)).join()
  const fgs = Object.values(PASTEL).map(t => t.fg)
  expect(new Set(fgs.map(cell)).size).toBe(fgs.length)
  // Same hue: the text is the background's hue, darker (not near-black), as in the mock's band.
  const hue = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255) as [number, number, number]
    const mx = Math.max(r, g, b), d = mx - Math.min(r, g, b)
    if (d === 0) return 0
    const x = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4
    return (x * 60 + 360) % 360
  }
  for (const [k, t] of Object.entries(PASTEL)) {
    const dh = Math.abs(hue(t.bg) - hue(t.fg))
    expect([k, Math.min(dh, 360 - dh) <= 30, contrast(t.fg, '#000000') >= 2]).toEqual([k, true, true])
  }
  expect(Math.round(contrast('#FFFFFF', '#000000'))).toBe(21)
})

test('the 오너 대기 pill is summary().owner_wait, not the waiting_owner column alone (0.1.44)', async ($, on) => {
  const h = harness(on)
  const m = model()
  const counts = { done: 1, in_progress: 0, waiting_others: 0, waiting_owner: 2, queue: 0 }
  h.state.model = { ...m, summary: { counts, decisions: 1, approvals: 0, attention: true, owner_wait: 8 } } as any
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount(BAND)
  expect(await pill(band, '오너 대기 8')).toMatchObject({ backgroundColor: PASTEL.owner.bg })
  await band.unmount()
})

test('band pills carry a pastel tone per state', async ($, on) => {
  const h = harness(on)
  const m = model()
  m.sections[3]!.count = 2  // waiting_owner
  h.state.model = { ...m, cards_open: 1, pressure: { level: 'warn', reasons: [] }, foreman_down: true }
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount(BAND)
  expect(await pill(band, '오너 대기 2')).toMatchObject({ backgroundColor: PASTEL.owner.bg, color: PASTEL.owner.fg })
  expect(await pill(band, '결정 1')).toMatchObject({ backgroundColor: PASTEL.decide.bg, color: PASTEL.decide.fg })
  expect(await pill(band, 'PC warn')).toMatchObject({ backgroundColor: PASTEL.caution.bg })
  expect(await pill(band, 'foreman down')).toMatchObject({ backgroundColor: PASTEL.alarm.bg })
  // The pills touch, as in the mock: no gap between them.
  expect((await band.find({ key: 'pills' }))?.props.gap).toBeUndefined()
  await band.unmount()

  h.state.model = { ...model(), sections: model().sections.map(x => ({ ...x, count: 0, rows: [] })), approvals: [], pressure: { level: 'ok', reasons: [] } }
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const calm = await $.ui.mount(BAND)
  expect((await calm.find({ type: 'Text', text: /대기 없음/ }))?.props).toMatchObject({ dimColor: true })
  expect(await calm.find({ type: 'Text', text: /PC|승인|진행/ })).toBeUndefined()
  await calm.unmount()
})

test('status rows are a table: 단위 · 제목 · PR · 상태 · 경과, and [b] closes the pane', async ($, on) => {
  const h = harness(on)
  on('ui.close', (_: unknown, e: any) => {
    h.closes.push(e)
    return { value: undefined }
  })
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // One title row: the name bold on the left, the keys dim on the right, a rule under it; no separate load line.
  const title = await ui.find({ key: 'title' })
  expect(title!.text).toMatch(/^foundry boardctrl\+x f 접기 · ↑↓ 선택 · ⏎ 상세 ·\/foundry-board close 닫기─+$/)  // .text joins elements without their gaps
  expect((await ui.find({ type: 'Text', text: /^foundry board$/ }))?.props).toMatchObject({ bold: true })
  expect((await ui.find({ type: 'Text', text: /^ctrl\+x f 접기 · ↑↓ 선택 · ⏎ 상세 ·$/ }))?.props).toMatchObject({ dimColor: true })
  expect((await ui.find({ type: 'Text', text: /^─{20,}$/ }))?.props).toMatchObject({ dimColor: true })
  expect(await ui.find({ type: 'Text', text: /8 sessions/ })).toBeUndefined()
  expect((await ui.find({ key: 'tabs' }))!.text).toMatch(/^상태별저장소별PR \(\d+\)\|all/)
  const line = (await ui.find({ type: 'Button', key: 'alpha' }))!.text
  // Under the 진행 중 heading the status word would repeat it; the wait, in plain Korean, stands in its place.
  // A tool approval is the approvals table's, not this column's.
  expect(line).toMatch(/^ alpha {8}Alpha project +— {8}리뷰 대기 #1 {10}0m$/)
  const head = (await ui.find({ key: 'head-s-in_progress' }))!.text
  expect(head).toMatch(/^ 단위 {9}제목 +PR {7}상태 {18}경과$/)
  expect(cols(line.split('리뷰 대기')[0]!)).toBe(cols(head.split('상태')[0]!))  // the columns line up under the header
  // No ctrl+x x and no printable hotkey that the prompt would type: the header names ctrl+x f and the close command.
  const close = await ui.find({ type: 'Button', key: 'close' })
  expect(close?.props).toMatchObject({ label: '/foundry-board close 닫기' })
  expect(close?.props.action).toBeUndefined()
  expect((await ui.findAll({ type: 'Button' })).filter((b: any) => b.props.hotkey)).toEqual([])
  await ui.press({ key: 'close' })
  expect(h.closes).toEqual([expect.objectContaining({ id: 'foundry-board' })])
  await ui.unmount()
})

for (const [role, opened] of [['foreman', true], ['line-lead', false], [undefined, false]] as const) {
  test(`the board pane opens by itself only in the foreman (${role ?? 'no role'})`, async ($, on) => {
    const h = harness(on, [], role)
    await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
    expect(h.opens.length > 0).toBe(opened)
    if (opened) expect(h.opens[0]).toMatchObject({ id: 'foundry-board', columns: 48 })
    const band = await $.ui.mount(BAND)
    await band.unmount()
  })
}

test('splitArgs splits like a shell without expanding anything', async () => {
  expect(splitArgs('resume-line --by owner')).toEqual(['resume-line', '--by', 'owner'])
  expect(splitArgs(`unit new 'a b' "c \\"d\\"" e\\ f`)).toEqual(['unit', 'new', 'a b', 'c "d"', 'e f'])
  expect(splitArgs(`x '' "" $HOME;rm`)).toEqual(['x', '', '', '$HOME;rm'])
  expect(splitArgs(`a 'b\\n'`)).toEqual(['a', 'b\\n'])
  expect(splitArgs('  ')).toEqual([])
  expect(splitArgs(`a 'b`)).toBeNull()
  expect(splitArgs('a "b')).toBeNull()
})

test('slashArgv keeps the owner verbs and answers usage for the rest', async () => {
  for (const v of ['up', 'resume-line', 'halt', 'approve', 'decide', 'prefs', 'unit', 'board', 'report']) {
    expect(slashArgv(`${v} x`)).toEqual({ argv: [v, 'x'] })
  }
  for (const bad of ['', 'sweep', 'hand spawn', 'rm -rf /', '--by owner up']) {
    expect('usage' in slashArgv(bad)).toBe(true)
  }
  expect((slashArgv(`decide 'open`) as any).usage).toMatch(/^unbalanced quote/)
  // A lead's question is answered by its decision key and free text; a card by its id and option key.
  expect(slashArgv('decide answer PROJ-779:data-security-check "yes, go"')).toEqual({
    argv: ['decide', 'answer', 'PROJ-779:data-security-check', 'yes, go'],
  })
  expect((slashArgv('nope') as any).usage).toContain('decide answer <card id d-0001 | decision key> <option key | "answer text">')
})

test('/foundry from the owner runs the CLI with a one-use nonce', async ($, on) => {
  const h = harness(on, [], 'foreman')
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const before = h.runs.length
  const r: any = await $.command.run({ command: 'foundry', args: `resume-line`, origin: { kind: 'composer' } } as any)
  const run = h.runs.slice(before).find(a => a[1]?.endsWith('foundry.py'))!
  expect(run.slice(2)).toEqual(['resume-line'])
  const env = h.envs[h.runs.indexOf(run)]!
  expect(env.FOUNDRY_OWNER_NONCE).toMatch(/^[0-9a-f]{32}$/)
  expect(h.writes.at(-1)).toEqual({ path: `/home/o/.local/state/foundry/owner-nonce/${env.FOUNDRY_OWNER_NONCE}`, text: env.FOUNDRY_OWNER_NONCE })
  expect(r.text).toMatch(/^foundry resume-line: ok/)
  expect(r.context?.[0]).toMatch(/do not run it again/)
})

test('/foundry runs nothing for a bad verb or an origin that is not the owner', async ($, on) => {
  const h = harness(on)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const before = h.runs.length
  const bad: any = await $.command.run({ command: 'foundry', args: 'sweep', origin: { kind: 'composer' } } as any)
  expect(bad.text).toMatch(/^usage: \/foundry/)
  for (const kind of ['plugin', 'sdk', 'scheduled-trigger', 'peer']) {
    const r: any = await $.command.run({ command: 'foundry', args: 'resume-line', origin: { kind, name: 'x' } } as any)
    expect(r.text).toMatch(/nothing was run/)
  }
  expect(h.runs.slice(before).filter(a => a[1]?.endsWith('foundry.py'))).toEqual([])
  expect(h.writes).toEqual([])
})

test('/foundry-board: closed opens expanded, open folds and expands, close closes', async ($, on) => {
  const closed: string[] = []
  const panes: { id: string; title: string; isPlaced: boolean }[] = []
  const h = harness(on, panes)
  on('ui.close', (_, e) => {
    closed.push(e.id)
    return { value: undefined }
  })
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const board = (args = '') => $.command.run({ command: 'foundry-board', args } as any).then(r => JSON.stringify(r))
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal', props: { ...PANE.props, placement: 'inline' } } as any)
  const isFolded = async () => !!(await ui.find({ key: 'fold-counts' }))

  h.state.model = { ...model(), pressure: { level: 'ok', reasons: [] } }
  // closed -> open, expanded
  expect(await board('close')).toContain('not open')
  expect(await board()).toContain('opened')
  expect(h.opens.at(-1)).toMatchObject({ id: 'foundry-board' })
  expect(h.opens.at(-1).rows).toBeUndefined()
  expect(await isFolded()).toBe(false)
  panes.push({ id: 'foundry-board', title: 'foundry board', isPlaced: true })

  // open -> folded: pills (PC level included even when ok) and one line of counts; an inline pane asks for few rows
  expect(await board()).toContain('folded')
  expect(h.opens.at(-1).rows).toBe(4)
  expect(await isFolded()).toBe(true)
  expect((await ui.find({ key: 'fold-counts' }))!.text).toBe('완료 0 · 진행 중 1 · 타인 대기 0 · 오너 확인 0 · queue 0')
  expect(await ui.find({ type: 'Text', text: /PC ok/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /승인 1/ })).toBeTruthy()
  expect(await ui.find({ key: 'view:status' })).toBeFalsy()

  // folded -> expanded
  expect(await board()).toContain('expanded')
  expect(h.opens.at(-1).rows).toBeUndefined()
  expect(await isFolded()).toBe(false)
  expect(await ui.find({ key: 'view:status' })).toBeTruthy()

  // close, and reopening starts expanded again
  expect(await board('close')).toContain('closed')
  expect(closed).toEqual(['foundry-board'])
})

const full = () => {
  const m = model() as any
  const beta = { ...row('beta', 'Beta project', 'waiting'), repo: 'example-org/y', repos: ['example-org/y', 'example-org/x'], approvals: [] }
  m.sections[1].rows.push(beta)
  m.sections[1].rows[0].repos = ['example-org/x']
  Object.assign(m.sections[1].rows[0], {
    stall: { kind: 'idle', text: '40분 무응답' }, sync_text: '동기화 지연 45분', mismatch: 'PR merged',
    mode: 'plan', blocked_by: ['decision:d-0123'], chain: { upstream: ['beta'], downstream: [] },
    hands: [{ name: 'fixer', role: 'hand', state: 'running' }],
  })
  return Object.assign(m, {
    repo_names: ['example-org/x', 'example-org/y'],
    cards: [{ id: 'd-0123', unit: 'alpha', repo: 'example-org/x', question: 'merge now?', reason: 'CI green', recommended: 'B',
      options: [{ key: 'A', label: 'wait', description: 'hold', pros: ['safe'], cons: ['slow'] },
        { key: 'B', label: 'merge', description: 'ship', preview: 'gh pr merge 1' }] }],
    cards_open: 1,
    next: [{ unit: 'beta', title: 'Beta', repo: 'example-org/y', why: 'unblocked' }],
    inventory: { repos: { 'example-org/x': [{ kind: 'pr', ref: 'example-org/x#1', age_h: 30, waits_on: 'alice', stage: 'review', stale: true }] },
      reviewers: [{ login: 'alice', pending: 4, warn: true }] },
    monitor: { status: 'warn', collecting: false, last_sample_age_s: 5, footer: '수집 중단 (foreman down)',
      tiles: [{ name: 'load', value: 1, unit: '/cpu', spark: [0, 1] }, { name: 'memory', value: 42, unit: '%', spark: [] }],
      timeline: [], last_ts: '2026-11-18T03:12:00+09:00', leads: { live: 3, cap: 8, launch: true } },
    foreman: { alive: false, ts: null }, foreman_down: true,
    halts: [{ scope: 'all', reason: 'prod incident', by: 'owner', ts: null }],
    handover: { text: 'morning: 2 PRs merged' },
  })
}

test('view switch and repo filter', async ($, on) => {
  const h = harness(on)
  h.state.model = full()
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /진행 중 \(2\)/ }))?.text).toBe('진행 중 (2)')
  await ui.press({ key: 'view:repo' })
  expect((await ui.find({ type: 'Text', text: /^example-org\/y \(/ }))?.text).toBe('example-org/y (1)')
  expect((await ui.find({ type: 'Text', text: /^example-org\/x \(/ }))?.text).toBe('example-org/x (2)')
  await ui.press({ key: 'repo:example-org/y' })
  expect(await ui.find({ type: 'Text', text: /^example-org\/x \(/ })).toBeUndefined()
  await ui.press({ key: 'view:status' })
  expect((await ui.find({ type: 'Text', text: /진행 중 \(/ }))?.text).toBe('진행 중 (1)')
  await ui.unmount()
  const again = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await again.find({ type: 'Text', text: /진행 중 \(/ }))?.text).toBe('진행 중 (1)')  // remembered
  await again.unmount()
})

test('a row shows its title, not the branch key; its state in plain Korean, never internal words', async ($, on) => {
  const h = harness(on)
  const m = model() as any
  m.sections[2].rows.push({ ...row('PROJ-831', 'Fix the release wait', 'x'), status: 'waiting_others', approvals: [],
    worktree: { repo: 'example-org/x', path: '/wt/x/PROJ-831', branch: 'foundry/PROJ-831' }, updated: '2026-11-18T07:00:00+09:00' })
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const line = (await ui.find({ type: 'Button', key: 'PROJ-831' }))!.text
  expect(line).toMatch(/^ PROJ-831 {5}Fix the release wait +— {8}리뷰 대기 #1 {10}3h$/)
  expect(line.match(/PROJ-831/g)).toHaveLength(1)
  expect([STATUS_KO.waiting_others, STATUS_KO.waiting_owner, STATUS_KO.queue]).toEqual(['타인 대기', '오너 확인', '대기열'])
  await ui.unmount()
  // The first open wait, with who or what it names; else the PRs; a waiting unit with no wait is flagged. Tool
  // approvals, hands and the lead session's state stay out: no done / hand ×n / running / 승인 n tails.
  const st = async (patch: any, sec = 2, key = 'PROJ-831') => {
    const mm = model() as any
    mm.sections[sec].rows.push({ ...row(key, 'T', 'x'), status: mm.sections[sec].key, waits: [], ...patch })
    h.state.model = mm
    const u = await $.ui.mount({ ...PANE, surface: 'terminal' })
    const text = (await u.find({ type: 'Button', key }))!.text
    await u.unmount()
    return text.replace(/^ \S+ +T +\S+ {2,}/, '').replace(/ +\S+$/, '').trim()
  }
  const w = (kind: string, ref: string | null, note: string | null = null, until: string | null = null) =>
    ({ waits: [{ wid: 'w', kind, ref, until, note }] })
  expect(await st(w('pr-review', 'example-org/f#1632', 'waiting for carol (carol-gh) approval'))).toBe('리뷰 대기 · carol')
  expect(await st(w('pr-review', 'example-org/b#710', 'PR #710 open; waiting on CI + review'))).toBe('리뷰 대기 #710')
  expect(await st(w('deploy-window', 'deploy:webapp-app@development', 'x'))).toBe('배포 대기 dev')
  expect(await st(w('team', 'slack:C1/2', 'Waiting for alice (requester) to reply'))).toBe('답변 대기 · alice')
  expect(await st(w('time', 'review-reminder', null, '2026-11-19T14:00:00+09:00'))).toBe('11/19 14:00까지')
  expect(await st({ pr_items: [{ state: 'MERGED', released: null }] }, 0)).toBe('머지됨 · 릴리스 대기')
  expect(await st({ pr_items: [{ state: 'MERGED', released: 'v1.1.36' }] }, 0)).toBe('릴리스됨 v1.1.36')
  expect(await st({ pr_items: [{ state: 'MERGED', released: null }] })).toBe('대기 대상 없음')
  // board.py's unwatched: waits no watcher resolves, or waiting on the owner with nothing open
  expect(await st({ waits: [{ wid: 'w', kind: 'team', ref: 'x', until: null, note: null }], unwatched: '감시 안 되는 대기' })).toBe('감시 안 되는 대기')
  expect(await st({ unwatched: '결정 대상 없음' }, 1)).toBe('결정 대상 없음')
  expect(await st({ status: 'blocked', session: null })).toBe('막힘')
  for (const patch of [{ session: { ...row('x', 'x', 'x').session, state: 'done' } }, { hands: [{ name: 'h', role: 'hand', state: 'running' }] },
    { approvals: [APPROVAL] }]) {
    for (const sec of [0, 1]) {
      const got = await st({ ...patch, waits: [] }, sec)
      for (const bad of ['done', 'hand', 'running', '승인', '작업자', '세션']) expect(got).not.toContain(bad)
    }
  }
  // A narrow pane drops 경과, then PR, before the title gets too short to read.
  const narrow = await $.ui.mount({ ...PANE, props: { ...PANE.props, bodyColumns: 48 }, surface: 'terminal' })
  expect((await narrow.find({ key: 'head-s-in_progress' }))!.text).toMatch(/^ 단위 {9}제목 +상태$/)
  await narrow.unmount()
})

test('widths: Hangul and emoji take two columns; ages and bars', () => {
  expect([cols('🟣 머지됨'), cols('📁 워크트리'), cols('⚠'), cols('✅')]).toEqual([9, 11, 1, 2])
  expect(pad('🔀 PR', 6)).toBe('🔀 PR ')
  expect(cols(pad('👀 리뷰 대기 · alice, bob', 12))).toBe(12)
  expect([fmtAge(null), fmtAge(0.4), fmtAge(30), fmtAge(72)]).toEqual(['—', '24m', '30h', '3d'])
  expect([bar(0, 4, 20), bar(1, 100, 20), bar(1, 10, 20), bar(4, 4, 20).length]).toEqual(['', '█', '██', 20])
})

test('shortPath: home as ~, a long path keeps its last two parts', () => {
  expect(shortPath('/Users/someone/work/wt/webapp/PROJ-831')).toBe('~/work/wt/webapp/PROJ-831')
  expect(shortPath('/Users/someone/work/wt/claude-plugins/foundry-019/foundry/PROJ-831')).toBe('…/foundry/PROJ-831')
  expect(shortPaths('worktree /home/o/work/wt/x/PROJ-831 2.4h - waits lead; example-org/x#5 https://github.com/a/b'))
    .toBe('worktree ~/work/wt/x/PROJ-831 2.4h - waits lead; example-org/x#5 https://github.com/a/b')
})

test('each section has a pastel heading chip and a bar; item lines hang under their text', async ($, on) => {
  const h = harness(on)
  const m = full()
  m.inventory.repos['example-org/x'].push({ kind: 'worktree', ref: '/Users/someone/work/wt/webapp/PROJ-831', age_h: 2.4,
    stage: '-', waits_on: 'lead', stale: false })
  m.escalations = [{ id: 'e1', kind: 'stall', unit: 'alpha', text: 'idle 40m in /Users/someone/work/wt/webapp/PROJ-831', ts: null }]
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const chip = async (title: RegExp) => {
    const t = await ui.find({ type: 'Text', text: title })
    return t?.props
  }
  expect(await chip(/^결정 필요 \(1\)$/)).toMatchObject({ bold: true, color: PASTEL.decide.fg })
  expect(await chip(/^진행 중 \(2\)$/)).toMatchObject({ color: PASTEL[SECTION_TONE.in_progress!].fg })
  expect(await chip(/^다음 작업$/)).toMatchObject({ color: PASTEL.next.fg })
  expect(await chip(/^에스컬레이션 \(1\)$/)).toMatchObject({ color: PASTEL.caution.fg })
  expect(await chip(/^재고$/)).toMatchObject({ color: PASTEL.stock.fg })
  const boxes = (await ui.findAll({ type: 'Box' })).map((b: any) => b.props.backgroundColor).filter(Boolean)
  for (const tone of ['decide', 'next', 'caution', 'stock', 'run'] as const) {
    expect(boxes).toContain(PASTEL[tone].bar)
    expect(boxes).toContain(PASTEL[tone].bg)
  }
  // The worktree line: an emoji kind, the path shortened, an age, the wait in Korean, all on table columns.
  const wt = (await ui.find({ key: 'inv-/Users/someone/work/wt/webapp/PROJ-831-0' }))!.text
  expect(wt).toMatch(/^ 📁 워크트리  ~\/work\/wt\/webapp\/PROJ-831 +2h {4}리드 대기$/)
  expect((await ui.find({ type: 'Button', key: 'esc:e1' }))?.text).toMatch(/^ stall {6}alpha {7}idle 40m in ~\/work\/wt\/webapp\/PROJ-831$/)
  await ui.unmount()
})

test('decision cards pick an option, andon and sections show', async ($, on) => {
  const h = harness(on)
  h.state.model = full()
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /ANDON all/ }))?.text).toContain('prod incident')
  expect((await ui.find({ type: 'Text', text: /결정 필요 \(/ }))?.text).toBe('결정 필요 (1)')
  // One table row per card; options show only once ⏎ opens it.
  expect((await ui.find({ type: 'Button', key: 'dec:d-0123' }))?.text).toMatch(/^│ alpha │ merge now\? +│ — +│$/)
  expect(await ui.find({ type: 'Button', key: 'pick:d-0123:B' })).toBeUndefined()
  await ui.press({ key: 'dec:d-0123' })
  expect((await ui.find({ type: 'Text', text: /채팅:/ }))?.text).toContain('d-0123 B')
  expect(await ui.find({ key: 'pr-pointer' })).toBeTruthy()
  for (const want of [/다음 작업/, /재고/, /인계/, /수집 중단/, /safe/, /gh pr merge 1/, /CI green/]) {
    expect(await ui.find({ type: 'Text', text: want })).toBeDefined()
  }
  await ui.press({ key: 'pick:d-0123:B' })
  expect(h.runs.find(a => a.includes('decide'))!.slice(2)).toEqual(['decide', 'answer', 'd-0123', 'B', '--by', 'owner'])
  await ui.unmount()
})

test('override buttons run unit set and report a failure', async ($, on) => {
  const h = harness(on)
  h.state.model = full()
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'alpha' })
  expect((await ui.find({ type: 'Text', text: /막힘/ }))?.text).toContain('40분 무응답')
  expect((await ui.find({ type: 'Text', text: /^│ 연결 / }))?.text).toContain('앞: beta')
  expect(await ui.find({ type: 'Text', text: /\(this\)/ })).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /fixer/ }))?.text).toContain('fixer (hand)')
  for (const [k, st] of [['set:done', 'done'], ['set:parked', 'parked'], ['set:cancelled', 'cancelled']]) {
    await ui.press({ key: k! })
    expect(h.runs.filter(a => a[2] === 'unit').at(-1)!.slice(2)).toEqual(['unit', 'set', 'alpha', '--status', st, '--by', 'owner'])
  }
  // A failure stays in the detail with its error text; a success never shows there.
  expect((await ui.find({ key: 'result' }))?.text).toMatch(/^alpha → 실패 \(2\) invalid choice/)
  await ui.unmount()
})

test('PC load is always open, first under the filters (A6): tiles, a level-colored graph, peak, leads; 3h/6h/24h', async ($, on) => {
  const h = harness(on)
  h.state.model = full()
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'hours:3' })
  const row = async () => (await ui.find({ type: 'Text', text: /^load\/CPU/ }))?.text
  expect(await row()).toMatch(/^load\/CPU +3 +주의$/)
  expect((await ui.find({ type: 'Text', text: /^peak / }))?.text).toBe('peak 3 13:59')
  // The graph: one run per level, colored by that level's bar tone.
  const exact = async (x: string) => (await ui.findAll({ type: 'Text' })).filter((t: any) => t.text === x).map((t: any) => t.props.color)
  expect(await exact('▁▁')).toEqual([PASTEL.run.bar])
  expect(await exact('█')).toContain(PASTEL.alarm.bar)
  expect((await ui.find({ type: 'Text', text: /^동시 lead/ }))?.text).toBe('동시 lead 3/8 · 새 launch 허용')
  expect((await ui.find({ type: 'Text', text: /^수집 중단/ }))?.text).toBe('수집 중단 (foreman down)')
  expect((await ui.find({ type: 'Text', text: /^마지막 값/ }))?.text).toBe('마지막 값 03:12 · `foundry up` 으로 다시 시작')
  await ui.press({ key: 'hours:24' })
  expect(await row()).toMatch(/^load\/CPU +24 /)
  const hrs = h.runs.filter(a => a[2] === 'metrics').map(a => a[4])
  expect([hrs.includes('3'), hrs.at(-1)]).toEqual([true, '24'])
  expect(await ui.find({ type: 'Button', key: 'load' })).toBeUndefined()  // no fold
  const texts = (await ui.findAll({ type: 'Text' })).map((t: any) => t.text)
  const at = (re: RegExp) => texts.findIndex((t: string) => re.test(t))
  expect(at(/^PC 부하/)).toBeGreaterThan(at(/^저장소별$/))
  expect(at(/^PC 부하/)).toBeLessThan(at(/^결정 필요/))
  await ui.unmount()
})

test('PC load tiles sit side by side, as many as the pane width holds (1..4)', async ($, on) => {
  expect([20, 26, 53, 54, 82, 110, 200].map(w => tileColumns(w))).toEqual([1, 1, 1, 2, 3, 4, 4])
  const h = harness(on)
  h.state.model = full()
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const across = async (bodyColumns: number) => {
    const ui = await $.ui.mount({ ...PANE, props: { ...PANE.props, bodyColumns }, surface: 'terminal' })
    await ui.press({ key: 'hours:3' })
    const lines: number[] = []
    for (let i = 0; i < 4; i++) {
      const line = await ui.find({ key: `tiles-${i}` })
      if (line) lines.push(line.children.length)
    }
    const tile = await ui.find({ key: 'tile-load' })
    await ui.unmount()
    return { lines, width: tile?.props.width }
  }
  expect(await across(120)).toEqual({ lines: [4], width: TILE_W })
  expect(await across(70)).toEqual({ lines: [2, 2], width: TILE_W })
  expect(await across(40)).toEqual({ lines: [1, 1, 1, 1], width: TILE_W })
})

test('PR tab owns the PR counts by stage and the reviewer load with its warning; 재고 keeps one count line', async ($, on) => {
  const h = harness(on)
  h.state.model = full()
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // No internal captions in the main view: no "재고 = …" legend, no "(최대 n = m칸)".
  expect(await ui.find({ type: 'Text', text: /재고 = |칸\)/ })).toBeUndefined()
  // The main tab lists no PR and draws no reviewer bars.
  expect(await ui.find({ key: 'bar-rev-alice' })).toBeUndefined()
  expect(await ui.find({ key: 'bar-stage-review' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^ 리뷰어 부하$/ })).toBeUndefined()
  await ui.press({ key: 'view:pr' })
  expect((await ui.find({ key: 'pr-sum' }))?.text).toBe(' PR 1 · 리뷰 대기 1 · 오래 대기 1')
  expect((await ui.find({ type: 'Text', text: /^ 리뷰어 부하$/ }))?.props.bold).toBe(true)
  expect((await ui.find({ key: 'bar-rev-alice' }))!.text).toMatch(/^ alice {10}4  █{11} ⚠ 참고 -$/)
  const exact = async (x: string) => (await ui.findAll({ type: 'Text' })).filter((t: any) => t.text === x).map((t: any) => t.props)
  expect((await exact('█'.repeat(11))).map((p: any) => p.color)).toContain(PASTEL.caution.bar)
  expect((await exact('  4'))[0]?.bold).toBe(true)
  expect((await ui.find({ key: 'bar-stage-review' }))!.text).toMatch(/^ 리뷰 대기 {6}1  █{20}$/)
  await ui.unmount()
})

test('재고 rows: emoji kinds, #n and title, a key for a worktree path, Korean waits; one group per repo', async ($, on) => {
  const h = harness(on)
  const m = full()
  m.inventory.repos['example-org/x'] = [
    { kind: 'pr', ref: 'example-org/x#1639', unit: 'PROJ-831', title: 'Release wait fix', age_h: 5, waits_on: 'release', stage: 'merged', stale: false },
    { kind: 'pr', ref: 'example-org/x#1640', unit: 'PROJ-832', age_h: null, waits_on: 'alice', reviewers: ['alice'], stage: 'review', stale: false },
    { kind: 'worktree', ref: '/Users/someone/work/wt/x/PROJ-831', unit: 'PROJ-831', age_h: 3, waits_on: 'lead', stage: null, stale: false },
  ] as any
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const text = async (k: string) => (await ui.find({ key: k }))!.text
  const head = await text('head-inv-example-org/x')
  expect(head).toMatch(/^ 종류 {9}대상 +경과  상태 · 대기$/)
  // PRs are the PR tab's: the main tab keeps a count and a pointer.
  expect(await ui.find({ key: 'inv-example-org/x#1639-0' })).toBeUndefined()
  expect(await text('pr-pointer')).toBe(' PR 2 · 리뷰 대기 1 · 릴리스 대기 1 · PR 탭에서 보기')
  const wt = await text('inv-/Users/someone/work/wt/x/PROJ-831-0')
  expect(wt).toMatch(/^ 📁 워크트리  PROJ-831 +3h {4}리드 대기$/)
  // Emoji are two columns wide: every row's columns start where the header's do.
  expect(cols(wt.split('PROJ-831')[0]!)).toBe(cols(head.split('대상')[0]!))
  expect(cols(wt.split('리드 대기')[0]!)).toBe(cols(head.split('상태')[0]!))
  await ui.press({ key: 'pr-pointer' })
  expect(await text('head-pr-example-org/x')).toMatch(/^ PR +제목 +unit +상태 +리뷰어 +경과 +다음 행동 \(누가\)$/)
  expect(await text('pr-example-org/x#1639')).toMatch(/^ x#1639 +Release wait… +PROJ-831 +릴리스 대기 +- +5h +- \(foundry\)$/)
  expect(await text('pr-example-org/x#1640')).toMatch(/^ x#1640 +PROJ-832 +PROJ-832 +리뷰 대기 +alice /)
  await ui.unmount()
})

test('glyphs=nerd: octicons, one column each and a space after, on the same grid as emoji', async ($, on) => {
  // Nerd Font code points are private-use: one column, so the kind column keeps its width either way.
  expect([cols('\uf407'), cols(invKind('nerd', 'pr', 'merged')), cols(invKind('nerd', 'worktree'))]).toEqual([1, 4, 10])
  expect(invKind('nerd', 'pr', 'merged')).toBe('\uf419 PR')
  expect(invKind('emoji', 'branch')).toBe('🌿 브랜치')
  expect([prState('nerd', 'review'), prState('emoji', 'approved'), prState('nerd', 'gone')])
    .toEqual(['\uf441 리뷰 대기', '✅ 승인 · 머지 대기', 'gone'])
  for (const g of ['nerd', 'emoji'] as const) {
    for (const v of [...Object.values(ICONS[g].pr), ...Object.values(ICONS[g].state)]) expect(cols(v)).toBe(g === 'nerd' ? 1 : 2)
  }
  const h = harness(on)
  const m = full()
  m.glyphs = 'nerd'
  m.inventory.repos['example-org/x'] = [
    { kind: 'pr', ref: 'example-org/x#1639', unit: 'PROJ-831', title: 'Release wait fix', age_h: 5, waits_on: 'release', stage: 'merged', stale: false },
    { kind: 'worktree', ref: '/Users/someone/work/wt/x/PROJ-831', unit: 'PROJ-831', age_h: 3, waits_on: 'lead', stage: null, stale: false },
  ] as any
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const text = async (k: string) => (await ui.find({ key: k }))!.text
  const head = await text('head-inv-example-org/x')
  const wt = await text('inv-/Users/someone/work/wt/x/PROJ-831-0')
  expect(wt).toMatch(/^ \uf413 워크트리 {3}PROJ-831 /)
  expect(cols(wt.split('PROJ-831')[0]!)).toBe(cols(head.split('대상')[0]!))
  expect(cols(wt.split('리드 대기')[0]!)).toBe(cols(head.split('상태')[0]!))
  // The reviewer warning is the octicon alert in nerd mode (reviewer load is the PR tab's).
  await ui.press({ key: 'view:pr' })
  expect((await ui.find({ key: 'bar-rev-alice' }))!.text).toMatch(/^ alice {10}4  █{11} \uf421 참고 -$/)
  await ui.unmount()
})

test('git icons on PR rows and the repo/branch facts only with a Nerd Font; the pick reason under the PR', async ($, on) => {
  expect([gitIcon('nerd', 'pr'), gitIcon('emoji', 'pr'), gitIcon('emoji', 'branch')]).toEqual(['\uf407 ', '', ''])
  const h = harness(on)
  for (const g of ['nerd', 'emoji'] as const) {
    const m = full() as any
    m.glyphs = g
    m.pr_tab = [{ ref: 'example-org/app#5', title: 'api', unit: 'alpha', state: 'review', state_ko: '리뷰 대기',
      reviewer: 'carol', age_h: 2, stale: false, action: '리뷰', who: 'reviewer', who_ko: '리뷰어',
      pick: '선정: CODEOWNERS · 내 의뢰 0' }]
    h.state.model = m
    await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
    const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await ui.press({ key: 'view:pr' })
    const row = (await ui.find({ key: 'pr-example-org/app#5' }))!.text
    expect(row.startsWith(g === 'nerd' ? ' \uf407 app#5' : ' app#5')).toBe(true)
    await ui.unmount()
  }
})

test('인계: styled sections of unit — one line, 결정 대기 counted live, the snapshot time and changes since; a line opens its unit', async ($, on) => {
  const h = harness(on)
  const m = full() as any
  m.handover = {
    text: '# 인수인계 2026-11-19 08:00\n## 완료 (1)\n- alpha: Alpha project', at: '11-19 08:00', changes: 5,
    sections: [
      { title: '완료', count: 1, live: false, items: [{ unit: 'alpha', id: null, line: 'Alpha project' }] },
      { title: '결정 대기', count: 1, live: true, items: [{ unit: 'alpha', id: 'k1', line: 'merge now?' }] },
      { title: '정체', count: 0, live: false, items: [] },
    ],
  }
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /^인계/ }))?.text).toBe('인계 · 11-19 08:00 기준 · 이후 변경 5건')
  expect(await ui.find({ type: 'Text', text: /^#|^##/ })).toBeUndefined()  // no literal markdown
  expect((await ui.find({ type: 'Text', text: /결정 대기/ }))?.text).toBe(' 결정 대기 1 (지금)')
  expect((await ui.find({ key: 'ho:완료:0' }))?.text).toBe('   alpha — Alpha project')
  await ui.press({ key: 'ho:완료:0' })
  expect((await ui.find({ type: 'Text', text: /merge now/ }))?.text).toContain('k1: merge now? → (열림)')
  await ui.unmount()
})

test('PR and branch states in GitHub colours: open green, draft grey, merged purple, closed red, review accent', async ($, on) => {
  expect(['open', 'draft', 'merged', 'closed'].map(k => ghCell('emoji', k).color)).toEqual(['success', 'inactive', 'merged', 'error'])
  expect(ghCell('nerd', 'merged', null).t).toBe(' 머지됨')
  expect(ghCell('nerd', 'closed').t).toBe(' 닫힘')
  expect(ghCell('emoji', 'draft').t).toBe('초안')  // text fallback without a Nerd Font
  expect([ghCell('emoji', 'open', 'approved'), ghCell('emoji', 'open', 'changes'), ghCell('emoji', 'open', 'pending')]
    .map(c => [c.sub, c.subColor])).toEqual([['✓ 승인', 'success'], ['✗ 수정 요청', 'error'], ['● 리뷰 대기', 'warning']])
  const h = harness(on)
  const m = full() as any
  m.glyphs = 'nerd'
  m.inventory.repos['example-org/x'] = [
    { kind: 'branch', ref: 'feat/a', unit: 'alpha', age_h: 2, waits_on: 'lead', stage: 'merged', stale: false, gh: 'merged', gh_review: 'approved', pr: 'example-org/x#7' },
    { kind: 'branch', ref: 'feat/b', unit: 'alpha', age_h: 1, waits_on: 'lead', stage: null, stale: false, gh: null, gh_review: null, pr: null },
  ]
  m.pr_tab = [{ ref: 'example-org/x#8', title: 'x', unit: 'alpha', state: 'review', state_ko: '리뷰 대기', gh: 'open', gh_review: 'pending',
    reviewer: 'carol', age_h: 2, stale: false, action: '리뷰', who: 'reviewer', who_ko: '리뷰어' }]
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ key: 'inv-feat/a-0' }))!.text).toMatch(/ 머지됨 #7 ✓ 승인$/)
  expect((await ui.find({ type: 'Text', text: /머지됨 #7$/ }))?.props.color).toBe('merged')
  expect((await ui.find({ key: 'inv-feat/b-1' }))!.text).toMatch(/PR 없음$/)
  await ui.press({ key: 'view:pr' })
  expect((await ui.findAll({ type: 'Text', text: /^리뷰 대기$/ })).map((t: any) => t.props.color)).toContain('success')
  expect((await ui.find({ key: 'pr-example-org/x#8' }))!.text).toMatch(/ 리뷰 대기 ● +carol /)
  expect((await ui.findAll({ type: 'Text' })).filter((t: any) => t.text.trim() === '●').map((t: any) => t.props.color)).toEqual(['warning'])
  await ui.unmount()
})

test('escalations: one line each, ⏎ shows the full text; near-misses fold into one line', async ($, on) => {
  const h = harness(on)
  const long = 'stalled ' + 'x'.repeat(200)
  h.state.model = model([{ id: 'e1', kind: 'stall', unit: 'alpha', text: long, ts: null },
    ...['PROJ-828', 'PROJ-829', 'PROJ-830'].map((u, i) => ({ id: `n${i}`, kind: 'nearmiss', unit: u, text: `near ${u}`, ts: null }))])
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const e1 = (await ui.find({ type: 'Button', key: 'esc:e1' }))!.text
  expect(cols(e1)).toBeLessThanOrEqual(97)  // the row's leading space + 96 columns of cells
  expect(e1.endsWith('…')).toBe(true)
  expect(await ui.find({ type: 'Text', text: long })).toBeUndefined()
  await ui.press({ key: 'esc:e1' })
  expect(await ui.find({ type: 'Text', text: long })).toBeDefined()
  expect((await ui.find({ type: 'Button', key: 'esc:nearmiss' }))?.text).toContain('nearmiss 3건 · PROJ-828 외')
  expect(await ui.find({ type: 'Button', key: 'esc:n1' })).toBeUndefined()
  await ui.press({ key: 'esc:nearmiss' })
  expect((await ui.find({ type: 'Button', key: 'esc:n1' }))?.text).toContain('near PROJ-829')
  await ui.unmount()
})

test('night: no toasts for new escalations', async ($, on) => {
  const h = harness(on)
  h.state.model = { ...model([ESC('decision:old')]), night: true } as any
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'refresh' })
  h.state.model = { ...model([ESC('decision:old'), ESC('decision:new')]), night: true } as any
  await ui.press({ key: 'refresh' })
  expect(h.toasts).toEqual([])
  await ui.unmount()
})

test('결정 필요, pending approvals and 다음 작업 are tables on the same column system', async ($, on) => {
  const h = harness(on)
  const m = full()
  m.cards[0].opened = '2026-11-18T07:00:00+09:00'
  m.cards[0].question = 'merge now? ' + 'x'.repeat(200)
  m.approvals = [{ ...APPROVAL, tier: 2, summary: 'git push\n  origin foundry/alpha', asked: '2026-11-18T09:30:00+09:00' }]
  m.next = [{ unit: 'beta', title: 'Beta', repo: 'example-org/y', why: 'x', priority: 1, created: '2026-11-17T10:00:00+09:00' },
    { unit: 'gamma', title: 'Gamma', repo: null, why: 'x', priority: null, created: null }]
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // A bordered table: header, then a row whose long question wraps inside its cell; every line the same width.
  const lines = (await ui.findAll({ type: 'Text' })).map((t: any) => t.text).filter((x: string) => /^[┌│├└]/.test(x))
  expect(lines).toContain(lines.find((x: string) => /^│ 단위 +│ 질문 +│ 경과 │$/.test(x)))
  const dec = (await ui.find({ type: 'Button', key: 'dec:d-0123' }))!.text
  expect(dec).toMatch(/^│ alpha │ merge now\? +│ 3h +│$/)
  expect((await ui.findAll({ type: 'Button' })).some((el: any) => /^dec:d-0123:\d+$/.test(String(el['key'])))).toBe(true)  // its wrapped lines press too
  const decW = cols(dec)
  expect(lines.filter((x: string) => x.startsWith('│ alpha') || x.startsWith('│ 단위')).every((x: string) => cols(x) === decW)).toBe(true)
  expect(decW).toBeLessThanOrEqual(PANE.props.bodyColumns)
  expect((await ui.find({ key: 'head-apr' }))!.text).toMatch(/^ 단위 {9}tier {2}요청 +경과$/)
  const apr = (await ui.find({ key: 'apr-r1' }))!.text
  expect(apr).toMatch(/^ alpha {8}T2 {4}Bash: git push origin foundry\/alpha +30m ?allow ?deny$/)
  expect((await ui.find({ type: 'Text', text: /^T2$/ }))?.props).toMatchObject({ color: 'warning' })
  expect((await ui.find({ key: 'head-next' }))!.text).toMatch(/^ 단위 {9}repo {8}제목 +우선순위 {2}경과$/)
  expect((await ui.find({ key: 'next-beta' }))!.text).toMatch(/^ beta {9}y {11}Beta +P1 {8}24h$/)
  expect((await ui.find({ key: 'next-gamma' }))!.text).toMatch(/^ gamma {8}- {11}Gamma +— {9}—$/)
  await ui.press({ key: 'dec:d-0123' })
  expect((await ui.find({ type: 'Text', text: /^merge now\? x{200}$/ }))).toBeDefined()  // ⏎: the full question
  expect((await ui.find({ type: 'Button', key: 'pick:d-0123:B' }))?.props.label).toBe('B (추천)')
  await ui.press({ key: 'dec:d-0123' })
  expect(await ui.find({ type: 'Button', key: 'pick:d-0123:B' })).toBeUndefined()
  await ui.unmount()
  m.approvals = [{ ...APPROVAL, tier: 2, tool: 'Write', summary: 'Write: ~/x/msg.txt' }]
  h.state.model = { ...m }
  const ui2 = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui2.find({ key: 'apr-r1' }))!.text).toMatch(/ {2}Write: ~\/x\/msg\.txt +/)  // the tool once
  await ui2.unmount()
})

test('one grid at 60/80/120 columns: bars start on one column, counts end on one, no line past the width', async ($, on) => {
  const h = harness(on)
  const m = full() as any
  const pr = (n: number, stage: string) => ({ kind: 'pr', ref: `example-org/x#${n}`, age_h: 1, waits_on: 'alice', stage, stale: false })
  m.inventory.repos['example-org/x'] = [...[1, 2, 3, 4, 5].map(n => pr(n, 'review')), pr(6, 'merged')]
  m.inventory.reviewers = [{ login: 'alice', pending: 5, warn: true }, { login: 'carol-gh', pending: 1, warn: false }]
  m.repo_names = ['example-org/aws-infra', 'example-org/gateway', 'example-org/infra-ops', 'example-org/webapp', 'example-org/x', 'example-org/y']
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  for (const bodyColumns of [60, 80, 120]) {
    const ui = await $.ui.mount({ ...PANE, props: { ...PANE.props, bodyColumns }, surface: 'terminal' })
    const max = bodyColumns - RIGHT_MARGIN
    const barsOf = async () => (await ui.findAll({})).filter((x: any) => String(x.key ?? '').startsWith('bar-'))
    // Reviewer load and the PR stage bars are both the PR tab's; the main tab draws no bar.
    expect((await barsOf()).length).toBe(0)
    // Every table and bar line, inside the section's bar and indent (two columns), keeps the right margin.
    const lines = (await ui.findAll({})).filter((x: any) => /^(bar-|head-|inv-example-org\/x#|next-|apr-)/.test(String(x.key ?? '')) ||
      ['alpha', 'beta'].includes(x.key))
    for (const x of lines) expect([bodyColumns, x.key, 2 + cols(x.text) <= max]).toEqual([bodyColumns, x.key, true])
    // The tab line: its pieces and the one-column gaps between them.
    const tabs = await ui.find({ key: 'tabs' })
    const pieces = (await ui.findAll({})).filter((x: any) => /^(view:|repo:|tabs-more)/.test(String(x.key ?? ''))).length + 1
    expect([bodyColumns, cols(tabs!.text) + pieces - 1 <= max]).toEqual([bodyColumns, true])
    expect(tabs!.text).not.toContain('example-org/')
    if (bodyColumns === 60) expect(tabs!.text).toMatch(/\+\d$/)
    await ui.press({ key: 'view:pr' })
    const bars = await barsOf()
    expect(bars.length).toBe(4)
    const starts = bars.map((x: any) => cols(x.text.split('█')[0]!))
    const ends = bars.map((x: any) => cols(x.text.split('█')[0]!.trimEnd()))
    expect([bodyColumns, new Set(starts).size, new Set(ends).size]).toEqual([bodyColumns, 1, 1])
    const prLines = (await ui.findAll({})).filter((x: any) => /^(bar-stage|head-pr|pr-example-org)/.test(String(x.key ?? '')))
    expect(prLines.length).toBe(2 + 1 + 6)
    for (const x of prLines) expect([bodyColumns, x.key, 2 + cols(x.text) <= max]).toEqual([bodyColumns, x.key, true])
    await ui.press({ key: 'view:status' })
    // The title row: name and version on the left, keys and close on the right, at least two columns between.
    const title = (await ui.find({ key: 'title' }))!.text.replace(/─+$/, '')
    expect([bodyColumns, cols(title) + 4 <= max]).toEqual([bodyColumns, true])
    await ui.unmount()
  }
})

test('the title row names the loaded build, and a newer one in the marketplace', async ($, on) => {
  harness(on)
  on('fs.read', (_: unknown, e: { path: string }) =>
    e.path.endsWith('/.claude-plugin/plugin.json') ? { value: JSON.stringify({ name: 'foundry', version: '0.1.13' }) } : { value: '{}' })
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /^v0\.1\.13$/ }))?.props).toMatchObject({ dimColor: true })
  expect((await ui.find({ key: 'title' }))!.text).toMatch(/^foundry boardv0\.1\.13ctrl/)
  await ui.unmount()
  expect(buildText({ version: '0.1.12', newest: '0.1.13' })).toEqual({ version: 'v0.1.12', stale: '새 버전 0.1.13 → /reload-plugins', short: '새 버전 0.1.13' })
  expect(buildText({ version: '0.1.13', newest: '0.1.13' })?.stale).toBeNull()
  expect(buildText({ version: '0.1.10', newest: '0.1.9' })?.stale).toBeNull()
  expect(buildText({ version: null, newest: '0.1.13' })).toBeNull()
  expect([isNewer('0.1.13', '0.1.12'), isNewer('0.2.0', '0.1.13'), isNewer('0.1.12', '0.1.12')]).toEqual([true, true, false])
  // Narrow panes keep the close button and drop key hints first.
  expect(fitKeys(118, 21, false)).toBe('ctrl+x f 접기 · ↑↓ 선택 · ⏎ 상세')
  expect(fitKeys(58, 21, false)).toBeNull()
  const t = fitTabs(['example-org/a', 'example-org/b', 'example-org/c'], 'example-org/c', 24)
  expect([t.shown.includes('example-org/c'), t.more > 0, t.label('example-org/c')]).toEqual([true, true, 'c'])
  expect(fitTabs(['example-org/a', 'other/b'], 'all', 200).label('example-org/a')).toBe('example-org/a')
})

test('unit detail from the unit view: Korean summary, PR card, stale wait, gated approval, extracted lists, folded timeline', async ($, on) => {
  const h = harness(on)
  const m = model() as any
  const r = m.sections[1].rows[0]
  r.hands = [{ name: 'impl', role: 'hand', state: 'stopped' }, { name: 'advisor', role: 'advisor', state: 'stopped' }]
  r.view = {
    status_line: '리뷰 대기 (carol-gh, 15h)', next: '리뷰', who: 'reviewer', who_ko: '리뷰어', blocked: ['리뷰 대기 example-org/x#1646'],
    tracker: 'PROJ-787', chain: null, worktree: '/wt/x-alpha', branch: 'alpha',
    prs: [{ ref: 'example-org/x#1646', url: null, title: 't', state: 'OPEN', head: '6111840', ci: 'pending', review: 'REVIEW_REQUIRED',
      merge_state: 'BLOCKED', reviewers: ['carol-gh'] }],
    waits: [{ kind: 'ci', ref: 'example-org/x#1646', text: 'CI example-org/x#1646', note: 'head b42c0be', age_h: 3, stale: true,
      stale_text: '기록 sha b42c0be ≠ head 6111840 (github stream 이 다시 맞춤)' }],
    approvals: [{ rid: 'r1', tier: 3, command: 'Bash: gh pr merge 1646 -R example-org/x --squash --match-head-commit 6111840', ready: false,
      why: '아직 아님: CI pending, 리뷰 REVIEW_REQUIRED' }],
    owner_todos: ['owner must create facets api_key_group_id and api_key_client'],
    dispatcher: ['Datadog query still to be posted on Jira via dispatcher'],
    timeline: [{ ts: '2026-11-18T18:20:00+09:00', gist: '[report] review wait refused', text: '[report] review wait refused by the guard. Long text follows here.' }],
    hands_running: [], hands_stopped: 2,
    hands_stopped_list: [{ name: 'impl', role: 'hand', state: 'stopped', ts: '2026-11-18T18:00:00+09:00' },
      { name: 'advisor', role: 'advisor', state: 'stopped', ts: null }],
  }
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // The main approvals table follows the unit view too: no allow before the merge is ready.
  expect(await ui.find({ key: 'allow:r1' })).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /^아직 아님$/ }))?.props.color).toBe('warning')
  await ui.press({ key: 'alpha' })
  const t = async (re: RegExp) => (await ui.find({ type: 'Text', text: re }))?.text
  // One status line: 다음 only when it adds something; 막힌 것 only for what the status and waits do not say.
  expect(await t(/^│ 상태 /)).toMatch(/^│ 상태 +│ 리뷰 대기 \(carol-gh, 15h\) +│$/)  // 다음 '리뷰' adds nothing
  expect(await t(/^│ 막힌 것 /)).toBeUndefined()
  expect(await t(/^│ 티켓 /)).toContain('PROJ-787')
  expect(await t(/^│ PR +│ 상태 +│ 제목 +│$/)).toBeTruthy()  // the PR table; no duplicate PR fact row
  // The header: the unit id and a status badge in the status's tone.
  const head = await ui.find({ key: 'detail-head' })
  expect(head?.props.backgroundColor).toBe(PASTEL.next.bg)
  expect((await ui.find({ key: 'badge' }))?.props.backgroundColor).toBe(PASTEL.next.fg)
  expect((await ui.find({ type: 'Button', key: 'set:done' }))?.props.variant).toBe('primary')
  // One line per PR: state word, review mark; an open PR's CI shows only when failing; no head / mergeable words.
  expect(await t(/^│ x#1646 /)).toMatch(/^│ x#1646 │ 열림 +│ t +│$/)
  expect(await t(/mergeable/)).toBeUndefined()  // the head sha shows only in the stale-wait note below
  expect(await t(/stale:/)).toContain('b42c0be ≠ head 6111840')
  // The full command, not "Bash: Bash: … --match-head-" cut short; 아직 아님 instead of allow.
  expect(await t(/gh pr merge 1646/)).toBe('T3 Bash: gh pr merge 1646 -R example-org/x --squash --match-head-commit 6111840')
  expect(await ui.find({ key: 'allow:r1' })).toBeUndefined()
  expect(await t(/아직 아님/)).toContain('CI pending')
  expect(await ui.find({ key: 'deny:r1' })).toBeTruthy()
  expect(await t(/^오너 할 일$/)).toBeTruthy()
  expect(await t(/facets/)).toContain('api_key_group_id')
  // Instructions, dispatcher requests, stopped hands and the timeline are folded behind one line.
  expect(await t(/^dispatcher 요청$/)).toBeUndefined()
  expect((await ui.find({ type: 'Button', key: 'details' }))?.text).toBe('▸ 자세히 · dispatcher 1 · 단계 1 · 타임라인 1 · 멈춘 hand 2')
  expect(await ui.find({ type: 'Button', key: 'tl:0' })).toBeUndefined()
  await ui.press({ key: 'details' })
  expect(await t(/^dispatcher 요청$/)).toBeTruthy()
  // Stopped hands say which hand and how it ended; no `chain: (this)`.
  expect(await t(/^ +impl \(hand\)/)).toContain('stopped')
  expect(await ui.find({ type: 'Text', text: /chain/ })).toBeUndefined()
  // The timeline is one line per entry with no blank line between; ⏎ shows the full text.
  expect(await t(/Long text follows/)).toBeUndefined()
  const tag = await ui.find({ type: 'Text', text: /^\[report\]$/ })
  expect(tag?.props.color).toBe('success')
  expect((await ui.find({ type: 'Button', key: 'tl:0' }))?.text).toMatch(/\[report\] review wait refused …$/)
  expect((await ui.find({ key: 'tl-0' }))?.props.marginTop).toBeUndefined()
  await ui.press({ key: 'tl:0' })
  expect(await t(/Long text follows/)).toContain('refused by the guard')
  await ui.unmount()
})

test('PR tab: board.py rows with state, reviewer, age and the next action with its owner; decisions show their headline', async ($, on) => {
  const h = harness(on)
  const m = model() as any
  m.pr_tab = [{ ref: 'example-org/x#1646', title: 'Datadog facets', unit: 'PROJ-787', state: 'review', state_ko: '리뷰 대기',
    reviewer: 'carol-gh', age_h: 15, stale: false, action: '리뷰', who: 'reviewer', who_ko: '리뷰어' }]
  m.pr_count = 1
  m.asks = [{ key: 'k1', unit: 'alpha', question: 'merge now? a very long paragraph', asked: null,
    headline: 'merge now? · 답 필요 · 막는 것: alpha 진행' }]
  m.cards_open = 1
  m.cost_usd = 0
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Button', key: 'dec:k1' }))?.text).toContain('막는 것: alpha 진행')
  expect(await ui.find({ type: 'Text', text: /비용/ })).toBeUndefined()
  await ui.press({ key: 'view:pr' })
  expect((await ui.find({ key: 'pr-example-org/x#1646' }))!.text).toMatch(/^ x#1646 +Datadog face\S* +PROJ-787 +리뷰 대기 +carol-gh +15h +리뷰 \(리뷰어\)$/)
  expect(await ui.find({ type: 'Text', text: /진행 중 \(/ })).toBeUndefined()
  await ui.unmount()
})

test('PR tab: a reviewer comes from the PR, never a wait note; a long login and action stay on one line', async ($, on) => {
  const h = harness(on)
  const m = model() as any
  m.inventory = { repos: { 'example-org/x': [
    { kind: 'pr', ref: 'example-org/x#713', unit: 'docs', title: 'Docs', age_h: 3, stage: 'review', stale: false,
      waits_on: 'Dev-only PR. CI is green; waiting for carol to look', reviewers: ['cloud-infra-reviewer'] },
    { kind: 'pr', ref: 'example-org/x#1647', unit: 'beta', title: 'Facets', age_h: 2, stage: 'approved', stale: false,
      waits_on: 'CI', reviewers: [], approvers: ['bob'] },
  ] }, reviewers: [] }
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'view:pr' })
  const a = (await ui.find({ key: 'pr-example-org/x#713' }))!.text
  expect(a).toContain('cloud-infra-r')
  expect(a).not.toMatch(/Dev-only|carol to look/)
  expect(a).not.toContain('\n')
  expect((await ui.find({ key: 'pr-example-org/x#1647' }))!.text).toContain('✓bob')
  await ui.unmount()
})

test('0.1.31 repo accent: the hash matches views.py, every pair reads at 4.5:1, a repo keeps its colour', async () => {
  // views.repo_hash gives the same numbers (tests/test_lifecycle_0131.py pins them too).
  expect([repoHash('example-org/webapp'), repoHash('a'), repoHash('example-developers-auth'), repoHash('example-org/gateway')])
    .toEqual([1609613780, 177670, 222722518, 2253952167])
  for (const a of REPO_ACCENT) expect(contrast(a.bg, a.fg)).toBeGreaterThanOrEqual(4.5)
  expect(repoAccent('example-org/webapp')).toEqual(REPO_ACCENT[4])
  expect(fitRef('example-developers-auth#1234', 12)).toBe('exampl…#1234')
  expect(fitRef('webapp#1651', 14)).toBe('webapp#1651')
  expect([openable('https://github.com/o/r/pull/1'), openable('file:///etc/hosts'), openable('https://x y'), openable(null)])
    .toEqual([true, false, false, false])
  expect([ageCell(2).dim, ageCell(30).color, ageCell(10).color, ageCell(10).dim]).toEqual([true, 'warning', undefined, undefined])
})

test('0.1.31 PR tab: one block per repo with its accent header, a link on the ref and the unit, a press opens the PR', async ($, on) => {
  const h = harness(on)
  const m = full() as any
  m.glyphs = 'nerd'
  m.pr_tab = [
    { ref: 'example-org/app#5', short: 'app#5', repo: 'example-org/app', url: 'https://github.com/example-org/app/pull/5',
      unit_url: 'https://jira.example.com/browse/PROJ-5', title: 'api', unit: 'PROJ-5', state: 'review', state_ko: '리뷰 대기',
      reviewer: 'carol', reviewer_auto: ['team-bot'], age_h: 30, stale: true, action: '리뷰', who: 'reviewer', who_ko: '리뷰어' },
    { ref: 'example-org/web#12', short: 'web#12', repo: 'example-org/web', url: 'https://github.com/example-org/web/pull/12',
      unit_url: null, title: 'ui', unit: 'beta', state: 'release-wait', state_ko: '릴리스 대기',
      reviewer: '-', age_h: 2, stale: false, action: '릴리스', who: 'foundry', who_ko: 'foundry' },
  ]
  m.pr_summary = { total: 2, stale: 1, buckets: [{ key: 'review', label: '리뷰 대기', n: 1 }, { key: 'release', label: '릴리스 대기', n: 1 }],
    text: 'PR 2 · 리뷰 대기 1 · 릴리스 대기 1' }
  m.repo_blocks = ['example-org/app', 'example-org/web'].map(repo => ({ repo, short: repo.split('/')[1], accent: repoAccent(repo),
    counts: 'PR 1', pr: 1, branch: 0, worktree: 0 }))
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  // 재고 says the one counts line; the PR rows live on the PR tab only.
  expect((await ui.find({ key: 'pr-pointer' }))!.text).toBe(' PR 2 · 리뷰 대기 1 · 릴리스 대기 1 · PR 탭에서 보기')
  expect(await ui.find({ key: 'pr-example-org/app#5' })).toBeUndefined()
  await ui.press({ key: 'view:pr' })
  expect((await ui.find({ key: 'pr-sum' }))!.text).toBe(' PR 2 · 리뷰 대기 1 · 릴리스 대기 1 · 오래 대기 1')
  const head = await ui.find({ key: 'repohead-pr-example-org/app' })
  expect(head!.text).toContain(' app')
  expect(head!.text).toContain('PR 1')
  expect(head!.props.marginTop).toBe(1)
  const chip = (await ui.findAll({ type: 'Box' })).find((b: any) => b.props.backgroundColor === repoAccent('example-org/app').bg)
  expect(chip).toBeTruthy()
  const links = (await ui.findAll({ type: 'Link' })).map((l: any) => l.props.href)
  expect(links).toEqual(['https://github.com/example-org/app/pull/5', 'https://jira.example.com/browse/PROJ-5',
    'https://github.com/example-org/web/pull/12'])
  const row = (await ui.find({ key: 'pr-example-org/app#5' }))!.text
  expect(row).toMatch(/^  app#5 /)
  expect(row).toContain('carol team-bot')  // the auto reviewer rides along, dim
  expect((await ui.findAll({ type: 'Text' })).find((t: any) => t.text.trim() === 'team-bot')?.props.dimColor).toBe(true)
  await ui.press({ key: 'open:example-org/app#5' })
  expect(h.runs).toContainEqual(['open', 'https://github.com/example-org/app/pull/5'])
  const order = (await ui.findAll({})).map((x: any) => String(x.key ?? '')).filter((k: string) => k.startsWith('repohead-pr-'))
  expect(order).toEqual(['repohead-pr-example-org/app', 'repohead-pr-example-org/web'])
  await ui.unmount()
})

test('0.1.31 재고: worktrees say their unit status in its tone or the reclaim state, a branch its commits ahead, 경과 coloured', async ($, on) => {
  const h = harness(on)
  const m = full() as any
  m.inventory.repos['example-org/x'] = [
    { kind: 'worktree', ref: '/wt/x/run', unit: 'run', age_h: 2, waits_on: 'lead', stage: null, stale: false, status: 'in_progress', reclaim: null },
    { kind: 'worktree', ref: '/wt/x/old', unit: 'old', age_h: 50, waits_on: null, stage: null, stale: false, status: 'done', reclaim: '회수 대기' },
    { kind: 'worktree', ref: '/wt/x/kept', unit: 'kept', age_h: 30, waits_on: null, stage: null, stale: false, status: 'done', reclaim: '남김: uncommitted 2', reclaim_kept: true },
    { kind: 'branch', ref: 'feat/a', unit: 'run', age_h: 2, waits_on: 'lead', stage: null, stale: false, gh: null, pr: null, ahead: 3 },
  ]
  m.repo_blocks = [{ repo: 'example-org/x', short: 'x', accent: repoAccent('example-org/x'), counts: 'PR 1 · 브랜치 1 · 워크트리 3', pr: 1, branch: 1, worktree: 3 }]
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const text = async (k: string) => (await ui.find({ key: k }))!.text
  expect(await text('repohead-inv-example-org/x')).toContain('PR 1 · 브랜치 1 · 워크트리 3')
  expect(await text('inv-/wt/x/run-0')).toMatch(/ 진행 중 $/)
  const pill = (await ui.findAll({ type: 'Text' })).find((t: any) => t.text === ' 진행 중 ')
  expect(pill?.props).toMatchObject({ backgroundColor: PASTEL.next.bg, color: PASTEL.next.fg })
  expect(await text('inv-/wt/x/old-1')).toMatch(/회수 대기$/)
  expect((await ui.findAll({ type: 'Text' })).find((t: any) => t.text.startsWith('남김:'))?.props.color).toBe('warning')
  expect(await text('inv-feat/a-3')).toMatch(/PR 없음 \+3$/)
  expect(await ui.find({ type: 'Text', text: /리드 대기/ })).toBeUndefined()
  const ages = (await ui.findAll({ type: 'Text' })).filter((t: any) => /^(2h|2d|30h)\s*$/.test(t.text))
    .map((t: any) => [t.text.trim(), t.props.color ?? null, !!t.props.dimColor])
  expect(ages).toContainEqual(['2h', null, true])
  expect(ages).toContainEqual(['30h', 'warning', false])
  await ui.unmount()
})

test('0.1.31 인계 PR 변경: capped lines, 「외 N건」 and a button to the PR tab; the repo filter tabs wear the accent', async ($, on) => {
  const h = harness(on)
  const m = full() as any
  m.repo_names = ['example-org/x', 'example-org/y']
  m.handover = { text: 'x', at: '11-19 08:00', changes: 7, sections: [
    { title: 'PR 변경', count: 7, live: true, more: 2, link: 'pr',
      items: [1, 2, 3, 4, 5].map(n => ({ unit: 'alpha', id: null, line: `x#${n} 머지됨 — t${n}` })) },
  ] }
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ key: 'ho:PR 변경:more' }))!.text).toBe('   외 2건 · PR 탭에서 전체 보기')
  const tab = (await ui.findAll({ type: 'Text' })).find((t: any) => t.text === 'y')
  expect(tab?.props).toMatchObject({ color: repoAccent('example-org/y').fg, backgroundColor: repoAccent('example-org/y').bg })
  await ui.press({ key: 'ho:PR 변경:more' })
  expect(await ui.find({ key: 'pr-sum' })).toBeTruthy()
  await ui.unmount()
})

test('0.1.33 registered repos wear their allocated accent (views.accent_map); unknown names fall back to the hash', async ($, on) => {
  expect(repoAccent('example-org/y', { 'example-org/y': REPO_ACCENT[5] })).toEqual(REPO_ACCENT[5])
  expect(repoAccent('example-org/y', { y: REPO_ACCENT[6] })).toEqual(REPO_ACCENT[6])
  expect(repoAccent('example-org/z', { y: REPO_ACCENT[6] })).toEqual(repoAccent('example-org/z'))
  const h = harness(on)
  const m = full() as any
  m.repo_names = ['example-org/x', 'example-org/y']
  m.repo_accents = { 'example-org/y': REPO_ACCENT[3] }
  m.handover = { text: 'x', at: '11-19 08:00', changes: 1, sections: [] }
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const tab = (await ui.findAll({ type: 'Text' })).find((t: any) => t.text === 'y')
  expect(tab?.props).toMatchObject({ color: REPO_ACCENT[3].fg, backgroundColor: REPO_ACCENT[3].bg })
  await ui.unmount()
})

test('0.1.32 a queued tier-3 request keeps its [allow]/[deny] buttons and says an answer applies on the retry', async ($, on) => {
  const h = harness(on)
  const m = full() as any
  m.approvals = [{ rid: 'ap-db83b1aa', unit: 'PROJ-795', role: 'lead', tool: 'Bash', summary: 'Bash: gh pr merge 1647 -R example-org/app',
    tier: 3, asked: '2026-11-19T13:08:55+09:00', session: 's', queued: true }]
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ key: 'aprc-ap-db83b1aa' }))!.text).toContain('(대기 만료 · 답하면 다음 재시도에 적용)')
  expect(await ui.find({ key: 'deny:ap-db83b1aa' })).toBeTruthy()
  await ui.press({ key: 'allow:ap-db83b1aa' })
  expect(h.runs.some(r => r.slice(-5).join(' ') === 'approve allow ap-db83b1aa --by owner')).toBe(true)
  await ui.unmount()
})

test('오너 대기 lists summary.owner_wait rows: card, to-do or bare unit, header = pill (0.1.46)', async ($, on) => {
  const h = harness(on)
  const m = model()
  const items = [
    { kind: 'decision', ref: 'd-0001', unit: 'carded', title: 'CARDED', state: '결정: d-0001', text: 'Which DB?' },
    { kind: 'todo', ref: 'ot-1', unit: 'todo', title: 'TODO', state: '할 일: sops secret 등록', text: 'sops secret 등록' },
    { kind: 'unit', ref: 'bare', unit: 'bare', title: 'BARE', state: '오너 확인', text: '' },
  ]
  m.sections[3] = { key: 'waiting_owner', label: '오너 대기', count: 3, rows: [], items } as any
  const counts = { done: 0, in_progress: 1, waiting_others: 0, waiting_owner: 3, queue: 0 }
  h.state.model = { ...m, summary: { counts, decisions: 1, approvals: 1, attention: true, owner_wait: 3 } } as any
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount(BAND)
  expect(await pill(band, '오너 대기 3')).toBeTruthy()
  await band.unmount()
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /^오너 대기 \(/ }))?.text).toBe('오너 대기 (3)')
  expect((await ui.find({ type: 'Text', text: /^│ 단위 +│ 종류/ }))?.text).toMatch(/^│ 단위 +│ 종류 +│ 내용 +│$/)
  expect((await ui.find({ type: 'Text', text: /d-0001 · CARDED/ }))?.text).toMatch(/^│ carded │ 결정 +│ d-0001 · CARDED +│$/)
  expect((await ui.find({ type: 'Text', text: /sops secret/ }))?.text).toMatch(/^│ todo +│ 할 일 +│ sops secret 등록 · TODO +│$/)
  expect((await ui.find({ type: 'Text', text: /BARE/ }))?.text).toMatch(/^│ bare +│ 오너 확인 │ BARE +│$/)
  await ui.unmount()
})

test('a decision with an outline opens as bullets: sections, then their items one level in', async ($, on) => {
  const h = harness(on)
  const m = model()
  h.state.model = { ...m, cards: [], cards_open: 0, summary: { counts: {}, decisions: 1, approvals: 0, attention: true },
    asks: [{ key: 'u1:scope', unit: 'u1', question: 'scope. 확인됨: a, b', asked: null,
      outline: [{ depth: 0, text: 'scope' }, { depth: 0, text: '확인됨' }, { depth: 1, text: 'first fact' },
        { depth: 0, text: '추천: A' }] }] } as any
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'dec:u1:scope' })
  expect(await ui.find({ type: 'Text', text: /^first fact$/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^◦ $/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^추천: A$/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^확인됨$/ })).toBeDefined()
  await ui.unmount()
})

// ---------- 0.1.49: clickable PR refs, a transient result line, the unit detail redesign, bordered tables ----------

test('0.1.49 prHref: the recorded url first, else a GitHub url for a repo the model knows; anything else stays text', async () => {
  const repos = ['example-org/app', 'other-org/app', 'example-org/api']
  expect(prHref('example-org/app#7', repos, 'https://github.com/example-org/app/pull/7?x')).toBe('https://github.com/example-org/app/pull/7?x')
  expect(prHref('example-org/app#7', repos)).toBe('https://github.com/example-org/app/pull/7')
  expect(prHref('api#3', repos)).toBe('https://github.com/example-org/api/pull/3')
  expect(prHref('app#3', repos, null, 'other-org/app')).toBe('https://github.com/other-org/app/pull/3')  // the unit's repo first
  expect([prHref('unknown/z#1', repos), prHref('PROJ-1', repos), prHref(null, repos), prHref('example-org/app#7', repos, 'not a url')])
    .toEqual([undefined, undefined, undefined, 'https://github.com/example-org/app/pull/7'])
  expect(knownRepos({ repo_names: ['example-org/app'], inventory: { repos: { 'example-org/api': [] } } as any,
    pr_tab: [{ ref: 'other-org/app#1' }] as any, repo_accents: undefined } as any).sort())
    .toEqual(['example-org/api', 'example-org/app', 'other-org/app'])
})

test('0.1.49 result line: `X → 완료 ✓`; a failure keeps its exit code and error text', async () => {
  expect(resultLine('r1', true, '허용')).toBe('r1 → 허용 ✓')
  expect(resultLine('alpha', false, '완료', 2, "invalid choice: 'set'")).toBe("alpha → 실패 (2) invalid choice: 'set'")
  expect(resultLine('alpha', false, '완료')).toBe('alpha → 실패')
  expect(RESULT_TTL_MS).toBe(60_000)
})

test('0.1.49 bordered tables: every line the same width, inside the budget; a link never splits', async () => {
  const head = ['항목', '값']
  const rows: TCell[][] = [['상태', '리뷰 대기 '.repeat(12)], ['PR', [[{ t: 'see ' }, { t: 'example-org/app#12', href: 'https://example.test/12' }]]]]
  for (const w of [40, 60, 100]) {
    const ws = tableWidths(w, head, rows, 1)
    const lines = tableLines(ws, head, rows).map(l => l.segs.map(s => s.t).join(''))
    expect(lines[0]!.startsWith('┌')).toBe(true)
    expect(lines.at(-1)!.startsWith('└')).toBe(true)
    expect(new Set(lines.map(cols)).size).toBe(1)
    expect(cols(lines[0]!)).toBeLessThanOrEqual(w)
  }
  // The long value wraps, so rows get a ├─┼─┤ between them; the link stays one segment.
  const out = tableLines(tableWidths(40, head, rows, 1), head, rows)
  expect(out.filter(l => l.row === 0).length).toBeGreaterThan(1)
  expect(out.filter(l => l.row === 0)[0]!.first).toBe(true)
  expect(out.flatMap(l => l.segs).filter(s => s.href).map(s => s.t)).toEqual(['example-org/app#12'])
  for (const l of wrapSegs([{ t: 'aaa bbb ccc ddd' }], 7)) expect(cols(l.map(s => s.t).join(''))).toBeLessThanOrEqual(7)
})

test('0.1.49 an owner-button result stays a minute, then the next refresh clears it; a success never shows in the detail', async ($, on) => {
  const h = harness(on)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'allow:r1' })
  expect((await ui.find({ key: 'result' }))?.text).toBe('r1 → 허용 ✓')
  h.state.now += 30_000
  await ui.press({ key: 'refresh' })
  expect((await ui.find({ key: 'result' }))?.text).toBe('r1 → 허용 ✓')
  h.state.now += 31_000
  await ui.press({ key: 'refresh' })
  expect(await ui.find({ key: 'result' })).toBeUndefined()
  await ui.press({ key: 'alpha' })
  await ui.press({ key: 'deny:r1' })
  expect(await ui.find({ key: 'result' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /거부 ✓/ })).toBeUndefined()
  await ui.unmount()
})

test('0.1.50 a result an older version stored without resultAt clears on the first refresh, no button press', async ($, on) => {
  harness(on)
  let legacy = true
  const isResult = (e: any) => e.plugin === 'foundry' && e['key'] === 'result'
  on('state.get', async (_: unknown, e: any, next: any) =>
    legacy && isResult(e)
      ? { value: { value: 'gateway-slack-scheduled-empty -> done: ok {"ok":true}', version: 1 } }
      : next(e))
  on('state.set', async (_: unknown, e: any, next: any) => {
    if (isResult(e)) legacy = false
    return next(e)
  })
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(legacy).toBe(false)
  expect(await ui.find({ key: 'result' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /scheduled-empty/ })).toBeUndefined()
  await ui.unmount()
})

test('0.1.49 재고: a branch with a PR links its ref, a worktree names its PR; both open on GitHub', async ($, on) => {
  const h = harness(on)
  const m = full() as any
  m.repo_names = ['example-org/app']
  m.inventory.repos = { 'example-org/app': [
    { kind: 'branch', ref: 'feat/a', unit: 'alpha', age_h: 2, waits_on: 'lead', stage: null, stale: false, gh: 'open', gh_review: null, pr: 'example-org/app#7' },
    { kind: 'worktree', ref: '/wt/app/alpha', unit: 'alpha', age_h: 1, waits_on: 'lead', stage: null, stale: false, status: 'in_progress',
      pr: 'example-org/app#9', url: 'https://github.com/example-org/app/pull/9' },
  ] }
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const hrefs = (await ui.findAll({ type: 'Link' })).map((l: any) => l.props.href)
  expect(hrefs).toContain('https://github.com/example-org/app/pull/7')
  expect(hrefs).toContain('https://github.com/example-org/app/pull/9')
  expect((await ui.find({ key: 'inv-/wt/app/alpha-1' }))!.text).toContain('#9')
  await ui.unmount()
})

const viewOf794 = (over: Record<string, unknown> = {}) => ({
  status_line: '타 팀 대기', next: '-', who: 'others', who_ko: '타 팀', blocked: [],
  tracker: 'PROJ-794', chain: null, worktree: '/wt/app/PROJ-794', branch: 'PROJ-794',
  prs: [{ ref: 'example-org/app#12', url: null, title: 'collector', gh: 'merged', state: 'MERGED' },
    { ref: 'example-org/app#13', url: 'https://github.com/example-org/app/pull/13', title: 'facets', gh: 'open', ci: 'failure', state: 'OPEN' }],
  waits: [{ kind: 'team', ref: 'PROJ-794', text: 'sre 팀 대기', unit: 'PROJ-794-infra', unit_status: 'review', unit_status_ko: '리뷰 대기',
    unit_prs: [{ ref: 'example-org/infra#5', url: null, gh: 'open', gh_review: null }], age_h: 20, note: null, stale: false }],
  approvals: [], owner_todos: [], dispatcher: [], timeline: [
    { ts: '2026-11-18T10:00:00+09:00', gist: '[report] merged #12', text: '[report] merged #12' },
    { ts: '2026-11-18T11:00:00+09:00', gist: '[report] asked sre for #5', text: '[report] asked sre for #5' }],
  hands_running: [], hands_stopped: 3,
  hands_stopped_list: [{ name: 'impl', role: 'hand', state: 'stopped', ts: null }],
  ...over,
})

test('0.1.49 unit detail (PROJ-794 shape): the wait is the unit it waits on with its PR link; PRs are one-liners; stopped hands drop', async ($, on) => {
  const h = harness(on)
  const m = model() as any
  m.repo_names = ['example-org/app', 'example-org/infra']
  const r = m.sections[1].rows[0]
  Object.assign(r, { repo: 'example-org/app', status: 'waiting_others', approvals: [], view: viewOf794() })
  m.approvals = []
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'alpha' })
  const t = async (re: RegExp) => (await ui.find({ type: 'Text', text: re }))?.text
  const lines = (await ui.findAll({ type: 'Text' })).map((x: any) => x.text as string)
  expect(await t(/^│ 대기 /)).toMatch(/^│ 대기 +│ PROJ-794-infra \(리뷰 대기\) · 20h/)
  expect(lines.some(l => /^│ +│ · infra#5 열림 +│$/.test(l))).toBe(true)
  expect(await t(/^│ app#12 /)).toMatch(/^│ app#12 │ 머지됨 +│ collector +│$/)
  expect(await t(/^│ app#13 /)).toMatch(/^│ app#13 │ 열림 · CI 실패 │ facets +│$/)
  const hrefs = (await ui.findAll({ type: 'Link' })).map((l: any) => l.props.href)
  expect(hrefs).toEqual(expect.arrayContaining(['https://github.com/example-org/app/pull/12',
    'https://github.com/example-org/app/pull/13', 'https://github.com/example-org/infra/pull/5']))
  // 0.1.51: every fact row has its own rule (the wait's two lines stay one row), and no PR fact row next to the PR
  // table. findAll sees a one-part rule line twice (the Text and its part) and a row once: n rows give 2n rules.
  const facts = lines.slice(lines.findIndex(l => /^│ 항목 /.test(l)), lines.findIndex(l => /^└/.test(l)))
  expect(facts.filter(l => l.startsWith('├')).length).toBe(2 * (facts.filter(l => /^│ \S/.test(l)).length - 1))
  expect(facts.filter(l => l.startsWith('├')).length).toBeGreaterThan(2)
  expect(facts.some(l => /^│ PR +│ app#12/.test(l))).toBe(false)
  // Waiting on another team: the stopped hands are not listed, and the timeline has no blank lines.
  expect((await ui.find({ type: 'Button', key: 'details' }))?.text).toBe('▸ 자세히 · 단계 1 · 타임라인 2')
  await ui.press({ key: 'details' })
  expect(await t(/impl \(hand\)/)).toBeUndefined()
  expect((await ui.find({ key: 'tl-1' }))?.props.marginTop).toBeUndefined()
  // Every bordered line of the detail fits the pane.
  const bordered = (await ui.findAll({ type: 'Text' })).map((x: any) => x.text as string).filter(l => /^[┌│├└]/.test(l))
  expect(bordered.every(l => cols(l) <= PANE.props.bodyColumns)).toBe(true)
  await ui.unmount()
})

test('0.1.51 a failure is one human sentence, never raw output; the details go to the log', async ($, on) => {
  expect(failText('', '{"ok": false, "detail": {"x": 1}}')).toBe('')
  expect(failText('refused: only the owner answers\nmore', '{"ok": true}')).toBe('refused: only the owner answers')
  expect(failText('Traceback (most recent call last):\n  File "x.py", line 1\nKeyError: \'rid\'', '')).toBe("KeyError: 'rid'")
  expect(failText('', '{"error": "no such card d-9"}')).toBe('no such card d-9')
  expect(cols(failText('x'.repeat(200), ''))).toBe(80)
  const h = harness(on)
  h.state.run = argv => (argv[2] === 'approve' ? { exitCode: 3, stdout: '{"rid": "r1", "ok": false}' } : undefined)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'allow:r1' })
  const line = (await ui.find({ key: 'result' }))?.text as string
  expect(line).toBe(`r1 → 실패 (3) 자세한 내용: ${FAIL_LOG}`)
  expect(line).not.toContain('{')
  const log = h.writes.find(w => w.path.endsWith(`/${FAIL_LOG}`))
  expect(log?.text).toContain('"ok": false')
  await ui.unmount()
})

test('0.1.51 a legacy result clears even when board.py fails on that refresh', async ($, on) => {
  const h = harness(on)
  let legacy = true
  const isResult = (e: any) => e.plugin === 'foundry' && e['key'] === 'result'
  on('state.get', async (_: unknown, e: any, next: any) =>
    legacy && isResult(e) ? { value: { value: 'gateway-slack-scheduled-empty -> done: ok {"ok":true}', version: 1 } } : next(e))
  on('state.set', async (_: unknown, e: any, next: any) => {
    if (isResult(e)) legacy = false
    return next(e)
  })
  h.state.run = argv => (argv[1]?.endsWith('board.py') && argv[2] === 'json' ? { exitCode: 1, stderr: 'boom' } : undefined)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(legacy).toBe(false)
  expect(await ui.find({ type: 'Text', text: /scheduled-empty/ })).toBeUndefined()
  await ui.unmount()
})

test('0.1.51 a long unit id is cut in the middle to 14 columns, keeping its ticket or PR and its end', async () => {
  const cases: [string, string][] = [
    ['short-id', 'short-id'],
    ['release-gateway-sample-svc-ja-example-org-gateway-7', 'releas…teway-7'],
    ['PROJ-794-infra-facets-collector', 'PROJ-794…ector'],
    ['release-sample-svc-PROJ-12', 'releas…PROJ-12'],
    ['gateway-review-fix-for-#1655', 'gateway-…#1655'],
  ]
  for (const [id, want] of cases) {
    expect(shortUnit(id)).toBe(want)
    expect(cols(shortUnit(id))).toBeLessThanOrEqual(14)
  }
})

test('0.1.51 every row is ruled: a dim muted rule between rows, the header rule at full weight, no padding', async () => {
  const rows: TCell[][] = ['상태', 'repo', '티켓', 'PR', '세션', 'branch', '주의'].map(k => [k, 'v'])
  const out = tableLines(tableWidths(60, ['항목', '값'], rows, 1), ['항목', '값'], rows)
  const plain = tableLines(tableWidths(60, ['항목', '값'], rows, 1), ['항목', '값'], rows, false)
  expect(out.length - plain.length).toBe(6)  // 7 rows: six separators, nothing else
  const rules = out.filter(l => l.row === -1 && l.segs[0]!.t.startsWith('├'))
  expect(rules[0]!.segs[0]!.dim).toBe(false)  // under the header
  expect(rules.slice(1).every(l => l.segs[0]!.dim && l.segs[0]!.color === 'inactive')).toBe(true)
  expect(out.filter(l => l.row >= 0).length).toBe(7)  // one line per row where it fits
})

test('0.1.53 릴리스 진행: open release units as a ruled table with their stage', async ($, on) => {
  const h = harness(on)
  h.state.model.releases = [{ unit: 'release-gateway-sample-svc-ja-example-org-gateway-7', repo: 'example-org/gateway', prs: ['example-org/gateway#714'],
    released: 1, stage: 'prod', stage_ko: 'prod 배포 대기 (오너 머지)', tag: 'sample-svc-ja-rel-1', dev: 'sample-svc-ja-rel-1', prod: null, status: 'in_progress' }]
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /릴리스 진행 \(1\)/ }))?.text).toBe('릴리스 진행 (1)')
  expect((await ui.find({ type: 'Text', text: /^│ releas…teway-7 / }))?.text).toMatch(/prod 배포 대기 \(오너 머지\) +│ 1 +│ sample-svc-ja-rel-1/)
  await ui.unmount()
})

test('0.1.54 the latest weekly kaizen shows as its own section', async ($, on) => {
  const h = harness(on)
  h.state.model.kaizen = { at: '11-23 09:00', lines: [{ head: true, text: '아차 사고 (2)' }, { head: false, text: 'stall: 2' }] }
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /카이젠 · 11-23 09:00/ }))?.text).toBe('카이젠 · 11-23 09:00')
  expect((await ui.find({ type: 'Text', text: /stall: 2/ }))?.text).toContain('• stall: 2')
  await ui.unmount()
})

test('ui.language: the pane draws board.py\'s `pane` catalog; a key it lacks stays Korean', async ($, on) => {
  const h = harness(on)
  h.state.model = { ...model(), i18n: { lang: 'en', pane: { th_title: 'Title' } } }
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ key: 'head-s-in_progress' }))!.text).toMatch(/^ 단위 {9}Title +PR {7}상태 {18}경과$/)
  await ui.unmount()
})

test('0.1.72 a finished unit: open asks only, answered decisions one line, a reopen button, related PRs; 0.1.73 the stripped title, the original under ▸ 자세히', async ($, on) => {
  const h = harness(on)
  const m = model() as any
  const outline = Array.from({ length: 9 }, (_, i) => ({ depth: i ? 1 : 0, text: `line ${i}` }))
  const done = {
    ...row('PROJ-783', 'group keys', 'done'), status: 'done', prs: [], title_full: 'group keys — dev 検証待ち',
    view: {
      status_line: '완료', next: '-', who: 'foundry', who_ko: 'foundry', blocked: [], tracker: 'PROJ-783', chain: null,
      worktree: null, branch: null, prs: [], waits: [], approvals: [], owner_todos: [], dispatcher: [], timeline: [],
      hands_running: [], hands_stopped: 0, closed: true,
      decisions: [{ key: 'PROJ-783:scope', open: false, answer: 'A', answered: '11-19 10:00', outline: [] },
        { key: 'PROJ-783:next', open: true, answer: null, answered: null, outline }],
      related_prs: [{ ref: 'example-org/webapp#1627', url: 'https://github.com/example-org/webapp/pull/1627' }],
    },
  }
  m.sections[0].rows = [done]
  m.sections[0].count = 1
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'PROJ-783' })
  const t = async (re: RegExp) => (await ui.find({ type: 'Text', text: re }))?.text
  expect(await t(/^group keys$/)).toBeTruthy()
  expect(await t(/検証待ち/)).toBeUndefined()  // the original title only under ▸ 자세히
  expect(await ui.find({ type: 'Button', key: 'set:done' })).toBeUndefined()
  expect((await ui.find({ type: 'Button', key: 'set:queue' }))?.props.label).toBe('다시 열기')
  expect(await t(/^결정 \(답함\)$/)).toBeTruthy()
  expect(await t(/PROJ-783:scope → A · 11-19 10:00/)).toBeTruthy()
  expect(await t(/- line 5$/)).toBeTruthy()
  expect(await t(/- line 6$/)).toBeUndefined()  // the rest under ▸ 자세히
  expect(await t(/^│ 관련 PR /)).toContain('webapp#1627')
  expect(await t(/^▸ 자세히 · .*원래 제목/)).toBeTruthy()
  await ui.press({ key: 'details' })
  expect(await t(/^ *원래 제목: group keys — dev 検証待ち$/)).toBeTruthy()
  expect(await t(/- line 6$/)).toBeTruthy()
  for (const ln of (await ui.findAll({ type: 'Text', text: /^[┌├└│]/ })).map((x: any) => x.text as string))
    expect(cols(ln)).toBeLessThanOrEqual(PANE.props.bodyColumns - 3 - RIGHT_MARGIN)
  await ui.unmount()
})

test('0.1.106 a tier-3 request is a card: why the owner answers it, the unit and request, evidence, advisor, auto-deny', async ($, on) => {
  const h = harness(on)
  const m = full() as any
  m.approvals = [{ rid: 'ap-1', unit: 'PROJ-9', role: 'lead', tool: 'Bash', summary: 'Bash: argocd app sync app-prd',
    tier: 3, asked: '2026-11-18T09:58:00+09:00', session: 's', why: 'production', evidence: 'PR #12 merged · dev deployed 09:40',
    advisor: 'dev checks D1-D3 passed', auto_deny: '2026-11-18T10:07:00+09:00' }]
  m.generated = '2026-11-18T10:00:00+09:00'
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const card = (await ui.find({ key: 'aprc-ap-1' }))!
  expect(card.props).toMatchObject({ borderStyle: 'round' })
  for (const s of ['승인 요청 · tier 3 (오너 전용: production)', 'PROJ-9', 'Bash: argocd app sync app-prd',
    '근거: PR #12 merged · dev deployed 09:40', 'advisor: dev checks D1-D3 passed', '약 7분 뒤 자동 거부 + 대기열'])
    expect(card.text).toContain(s)
  await ui.press({ key: 'deny:ap-1' })
  expect(h.runs.some(r => r.slice(-5).join(' ') === 'approve deny ap-1 --by owner')).toBe(true)
  await ui.unmount()
})

test('0.1.106 a stalled lead: a red pill in the band and a banner at the top with what recovery did', async ($, on) => {
  const h = harness(on)
  const m = model() as any
  m.sections[1].rows[0].stall = { kind: 'api_loop', text: '재시도 루프 (4회)', action: 'relaunch', escalated: false }
  h.state.model = m
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const band = await $.ui.mount(BAND)
  expect(await pill(band, '막힘 1')).toMatchObject({ backgroundColor: PASTEL.alarm.bg })
  await band.unmount()
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const banner = (await ui.find({ key: 'stall-alpha' }))!.text
  expect(banner).toContain('⚠ 막힘 · alpha · 재시도 루프 (4회)')
  expect(banner).toContain('자동 복구: relaunch')
  await ui.unmount()
})
