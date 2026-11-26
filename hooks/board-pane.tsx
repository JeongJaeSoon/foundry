import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Approval, BoardModel, BoardRow, Card, ColumnKey, Escalation, Handover, InventoryItem, Metrics, OutlineLine, PrSummary, PrTabRow, RepoBlock, ReviewerLoad, Tile, UnitView, View } from '../types'
import { GH_COLOR, GH_NERD, GH_REVIEW, NOTE_COLOR, PASTEL, repoAccent, SECTION_TONE, STATUS_TONE, type Tone } from './palette'
import { declareSlash, ownerRun, slashRun, stateDir } from './slash'
import { registerSpawn } from './spawn'
import { registerMappedAgents } from './agents'

// The pane's fixed words: board.py sends the `pane` catalog in prefs ui.language (defaults/i18n/<lang>/pane.json).
// A model from an older board.py, or a key its catalog lacks, falls back to these, the ko catalog's values.
export const PANE_KO = {
  allowed: '허용',
  answer_open: '(열림)',
  approval_queued: '(대기 만료 · 답하면 다음 재시도에 적용)',
  apr_card_head: '승인 요청 · tier {tier} (오너 전용: {why})',
  apr_evidence: '근거: {text}',
  apr_advisor: 'advisor: {text}',
  apr_auto_deny: '약 {n}분 뒤 자동 거부 + 대기열',
  band_hint: '[/foundry-board] 보드',
  band_idle: 'foundry · 대기 없음',
  intake_line: 'intake · 마지막 스캔 {at} · 제안 {proposed} · 상한 보류 {capped} · 다음 {next}',
  blocked_by: '선행 {unit}',
  btn_cancelled: '취소',
  btn_reopen: '다시 열기',
  btn_done: '완료로 표시',
  btn_parked: '보류',
  build_new: '새 버전 {version} → /reload-plugins',
  build_new_short: '새 버전 {version}',
  card_detail_cmd: '상세: foundry decide show {id}',
  chain_down: '뒤: {list}',
  chain_up: '앞: {list}',
  chat_answer: '채팅: {id} {key}',
  ci_failed: 'CI 실패',
  close: '/foundry-board close 닫기',
  collect_restart: '마지막 값 {at} · `foundry up` 으로 다시 시작',
  collect_stopped: '수집 중단 (foreman down)',
  conflict: '충돌',
  cost: '비용 누적 ${usd}',
  count_inbox: '받은 지시 {n}',
  count_steps: '단계 {n}',
  count_stopped: '멈춘 hand {n}',
  count_timeline: '타임라인 {n}',
  denied: '거부',
  fail_details: '자세한 내용: {path}',
  flag_mismatch: '상태 불일치 {text}',
  flag_no_wait_target: '대기 대상 없음',
  flag_stall: '막힘 {text}',
  pill_stall: '막힘 {n}',
  stall_banner: '⚠ 막힘 · {unit} · {text}',
  stall_recovered: '자동 복구: {action} · 두 번째 막힘이면 결정 카드',
  stall_escalated: '두 번째 막힘: 결정 카드로 올림',
  fold_less: '▾ 접기',
  foreman_restart: 'foreman 재시작 필요 (foreman.md 가 바뀜)',
  fold_more: '▸ 자세히',
  gh_closed: '닫힘',
  gh_draft: '초안',
  gh_merged: '머지됨',
  gh_open: '열림',
  h_approval_requests: '승인 요청',
  h_decision: '결정',
  h_dispatcher: 'dispatcher 요청',
  h_hands_running: '작업 중인 hand',
  h_hands_stopped: '멈춘 hand',
  h_inbox: '받은 지시',
  h_owner_todos: '오너 할 일',
  h_decisions_answered: '결정 (답함)',
  label_related_pr: '관련 PR',
  label_title_full: '원래 제목',
  h_steps: '단계',
  h_timeline: '타임라인',
  handover: '인계',
  handover_at: '{at} 기준',
  handover_changes: '이후 변경 {n}건',
  handover_live: '{n} (지금)',
  handover_title: '인계 · {meta}',
  keys_fold: 'ctrl+x f 접기',
  keys_full: 'ctrl+x f 접기 · ↑↓ 선택 · ⏎ 상세',
  keys_unfold: 'ctrl+x f 펼치기',
  kind_branch: '브랜치',
  kind_worktree: '워크트리',
  label_blocked: '막힌 것',
  label_caution: '주의',
  label_chain: '연결',
  label_held: '보류',
  label_session: '세션',
  label_status: '상태',
  label_ticket: '티켓',
  label_wait: '대기',
  launch_allowed: '허용',
  launch_held: '보류',
  leads: '동시 lead {live}/{cap} · 새 launch',
  level_danger: '위험',
  level_ok: 'ok',
  level_warn: '주의',
  metrics_loading: 'metrics.jsonl 읽는 중…',
  metrics_note: 'metrics.jsonl · {min}분 칸의 최악값 · 샘플 {n}',
  more_n: '외 {n}건',
  nearmiss_summary: 'nearmiss {n}건 · {unit} 외',
  no_pr: 'PR 없음',
  not_yet: '아직 아님',
  option_recommended: '{key} (추천)',
  own_decision: '결정',
  own_todo: '할 일',
  own_unit: '오너 확인',
  pc_load: 'PC 부하 · {level}',
  picked: '{key} 선택',
  pill_approvals: '승인 {n}',
  pill_archived: '보관 {n}',
  pill_decisions: '결정 {n}',
  pill_owner: '오너 대기 {n}',
  pill_run: '진행 {n}',
  pill_stale_foremen: '오래된 foreman {n}개',
  pill_stale_foremen_failed: '오래된 foreman {n}개 (중지 실패)',
  pr_tab_all: 'PR 탭에서 전체 보기',
  pr_tab_view: 'PR 탭에서 보기',
  prstate_approved: '승인 · 머지 대기',
  prstate_ci_failing: 'CI 실패',
  prstate_ci_running: 'CI 실행 중',
  prstate_deployed_dev: 'dev 배포됨 · prod 대기',
  prstate_prod_waiting: 'prod 오너 결정 대기',
  stage_ci_failing: 'CI 실패',
  stage_ci_running: 'CI 실행 중',
  stage_deployed_dev: 'dev 배포됨',
  stage_prod_waiting: 'prod 대기',
  prstate_draft: '초안',
  prstate_merged: '머지됨 · 릴리스 대기',
  prstate_review: '리뷰 대기',
  prtab_approved: '승인됨',
  prtab_draft: '초안',
  prtab_merge_wait: '머지 대기',
  prtab_release_wait: '릴리스 대기',
  prtab_review: '리뷰 대기',
  refresh: '새로고침',
  released: '릴리스됨',
  released_tag: '릴리스됨 {tag}',
  result_fail: '{what} → 실패{code}{detail}',
  result_ok: '{what} → {done} ✓',
  review_approved: '승인',
  review_changes: '수정 요청',
  review_pending: '리뷰 대기',
  reviewer_legend: '막대 = 내 의뢰 · 참고 = 다른 사람의 직접 요청',
  reviewer_load: '리뷰어 부하',
  reviewer_others: '참고 {n}',
  sample_age: '{n}s 전',
  sec_approvals: '승인 대기 ({n})',
  sec_decisions: '결정 필요 ({n})',
  sec_escalations: '에스컬레이션 ({n})',
  sec_inventory: '재고',
  sec_kaizen: '카이젠 · {at}',
  sec_next: '다음 작업',
  sec_releases: '릴리스 진행 ({n})',
  stage_approved: '머지 대기',
  stage_deployed: '배포됨',
  stage_draft: '초안',
  stage_merged: '릴리스 대기',
  stage_released: '배포 대기',
  stage_review: '리뷰 대기',
  stale_n: '오래 대기 {n}',
  status_blocked: '막힘',
  status_cancelled: '취소',
  status_done: '완료',
  status_in_progress: '진행 중',
  status_next: '{status} · 다음: {next} ({who})',
  status_parked: '보류',
  status_queue: '대기열',
  status_waiting_others: '팀 대기',
  status_waiting_owner: '오너 확인',
  tab_repo: '저장소별',
  tab_status: '상태별',
  th_age: '경과',
  th_choice: '선택',
  th_content: '내용',
  th_field: '항목',
  th_kind: '종류',
  th_next_action: '다음 행동 (누가)',
  th_priority: '우선순위',
  th_pros_cons: '장단점',
  th_question: '질문',
  th_request: '요청',
  th_reviewer: '리뷰어',
  th_stage: '단계',
  th_status: '상태',
  th_status_wait: '상태 · 대기',
  th_tag: '태그',
  th_target: '대상',
  th_title: '제목',
  th_unit: '단위',
  th_value: '값',
  tile_disk: 'disk',
  tile_docker: 'docker',
  tile_free_gb: '{n}G 여유',
  tile_free_pct: '{n}% 여유',
  tile_load: 'load/CPU',
  tile_memory: '메모리',
  tile_cache: '빌드 캐시',
  tile_cache_value: '{gb}G · 7일+ {old}G · 이미지 {images}',
  unfold_enter: '▸ ⏎ 펼치기',
  updated_legend: '갱신 {at} ([상태] 리드 세션, !n 승인 대기)',
  wait_answer: '답변 대기 · {who}',
  wait_until: '{at}까지',
  waitkind_ci: 'CI 대기',
  waitkind_deploy_window: '배포 대기',
  waitkind_jira: 'Jira 대기',
  waitkind_pr_merge: '머지 대기',
  waitkind_pr_review: '리뷰 대기',
  waitkind_team: '팀 대기',
  waitkind_time: '시간 대기',
  waiton_deploy: '배포 대기',
  waiton_ci: 'CI 대기',
  waiton_prod: 'prod 배포 대기',
  waiton_owner: '오너 대기',
  waiton_lead: '리드 대기',
  waiton_merge: '머지 대기',
  waiton_release: '릴리스 대기',
  waiton_reviewer: '리뷰 대기',
} as const
export type PaneKey = keyof typeof PANE_KO
export type Tr = (key: PaneKey, vars?: Record<string, string | number>) => string
type WithI18n = { i18n?: { lang?: string; pane?: Partial<Record<string, string>> } | null }
// `{name}` placeholders filled from vars; a function replacer, so a `$` in a value stays literal. `m` is a board
// model (types/ BoardModel does not declare `i18n`).
export const trOf = (m?: object | null): Tr => {
  const pane = (m as WithI18n | null | undefined)?.i18n?.pane
  return (key, vars) => {
    const s = pane?.[key] ?? PANE_KO[key] ?? key
    return vars ? s.replace(/\{(\w+)\}/g, (all, k: string) => (k in vars ? String(vars[k]) : all)) : s
  }
}
export const tr = (m: object | null | undefined, key: PaneKey, vars?: Record<string, string | number>) => trOf(m)(key, vars)
const KO_TR = trOf(null)
// An enum value's word (`<prefix>_<value>`, `-` as `_`), or undefined when the catalog names none, so the caller
// keeps its own fallback and never draws a key.
const wordOf = (t: Tr, prefix: string, v: string | null | undefined) => {
  const k = `${prefix}_${(v ?? '').replace(/-/g, '_')}`
  return v && k in PANE_KO ? t(k as PaneKey) : undefined
}

const PANE = 'foundry-board'
const TITLE = 'foundry board'
const board = atom({ plugin: 'foundry', key: 'board' } as const, null, { shape: 'board-3' })
const selected = atom({ plugin: 'foundry', key: 'selected' } as const, null)
const error = atom({ plugin: 'foundry', key: 'error' } as const, null)
const result = atom({ plugin: 'foundry', key: 'result' } as const, null)
// When `result` was set (epoch ms): a refresh a minute later clears it.
const resultAt = atom({ plugin: 'foundry', key: 'resultAt' } as const, null)
// The unit whose folded details (instructions, dispatcher requests, steps, full timeline) are open.
const detailOpen = atom({ plugin: 'foundry', key: 'detailOpen' } as const, null)
const viewAtom = atom({ plugin: 'foundry', key: 'view' } as const, 'status' as View)
const repoAtom = atom({ plugin: 'foundry', key: 'repoFilter' } as const, 'all')
const loadHours = atom({ plugin: 'foundry', key: 'pcHours' } as const, 3 as Hours)
const metricsAtom = atom({ plugin: 'foundry', key: 'metrics' } as const, null)
// The escalation shown in full (⏎ on its line), and whether the near-miss lines are unfolded.
const escOpen = atom({ plugin: 'foundry', key: 'escOpen' } as const, null)
const nearOpen = atom({ plugin: 'foundry', key: 'nearmissOpen' } as const, false)
// The decision (card id or ask key) shown in full: question, reason, options and the recommendation.
const cardOpen = atom({ plugin: 'foundry', key: 'cardOpen' } as const, null)
const foldedAtom = atom({ plugin: 'foundry', key: 'boardFolded' } as const, false)
const buildAtom = atom({ plugin: 'foundry', key: 'build' } as const, null)
// The timeline entry shown in full in the unit detail (`<unit>:<index>`); the rest stay one line each.
const timelineOpen = atom({ plugin: 'foundry', key: 'timelineOpen' } as const, null)
// Folded: the header, the pills and one line of counts; an inline pane asks for just that many rows (the dock
// keeps its full height and the body is simply short).
const FOLDED_ROWS = 4

// Escalations already toasted; null until the first refresh, which only records what is already open.
let seen: Set<string> | null = null

function toastNew($: EngineInterface, escalations: Escalation[], night: boolean) {
  if (seen && !night) {
    for (const x of escalations) {
      if (!seen.has(x.id)) $.ui.toast(`foundry ${x.kind} ${x.unit ?? ''}: ${x.text}`.slice(0, 200))
    }
  }
  seen = new Set([...(seen ?? []), ...escalations.map(x => x.id)])
}

async function py($: EngineInterface, script: string, args: string[]) {
  return $.process.run(['python3', `${$.plugin.root}/scripts/${script}`, ...args], { timeoutMs: 15000 })
}

async function refresh($: EngineInterface) {
  // A result older than RESULT_TTL_MS goes first, even when board.py then fails. One an older version left has no
  // resultAt (and may hold raw CLI output): it is stale already, so it goes on the first refresh.
  const at = await read($, resultAt)
  if ((await read($, result)) != null && (at == null || (await $.clock.now()) - at >= RESULT_TTL_MS)) {
    await update($, result, () => null)
    await update($, resultAt, () => null)
  }
  // The PC graph (always open) reloads alongside the board, so a refresh is one round of processes, not two in a
  // row; board.py answers an unchanged metrics.jsonl from its cache.
  const pc = loadMetrics($, await read($, loadHours))
  const r = await py($, 'board.py', ['json'])
  if (r.exitCode !== 0) {
    await update($, error, () => (r.stderr || 'board.py failed').slice(0, 300))
    await pc
    return
  }
  const model = JSON.parse(r.stdout) as BoardModel
  await update($, board, () => model)
  await update($, error, () => null)
  toastNew($, model.toasts ?? model.escalations, !!model.night)
  await pc
}

async function loadMetrics($: EngineInterface, hours: Hours) {
  const r = await py($, 'board.py', ['metrics', '--hours', String(hours)])
  if (r.exitCode === 0) await update($, metricsAtom, () => JSON.parse(r.stdout) as Metrics)
}

// Runs one foundry.py command for an owner button, shows its outcome, then refreshes. A press is the owner's
// own input, so it runs with an owner nonce: inside the foreman's process tree the CLI would refuse it otherwise.
// The line is short and human (`<what> → <done> ✓`, or `<what> → 실패 (code) <one sentence>`); the CLI's raw
// output never shows in it. A failure's full stdout and stderr go to FAIL_LOG in the state dir.
async function act($: EngineInterface, label: string, args: string[], doneOf: (t: Tr) => string) {
  const t = trOf(await read($, board))
  const done = doneOf(t)
  const r = await ownerRun($, args)
  const ok = r.exitCode === 0
  let out = ''
  if (!ok) {
    out = failText(r.stderr ?? '', r.stdout ?? '') || t('fail_details', { path: FAIL_LOG })
    await $.fs.write(`${await stateDir($)}/${FAIL_LOG}`,
      `${new Date(await $.clock.now()).toISOString()} foundry ${args.join(' ')} -> exit ${r.exitCode}\n--- stderr\n${r.stderr ?? ''}\n--- stdout\n${r.stdout ?? ''}\n`)
      .catch(() => undefined)
  }
  await update($, result, () => resultLine(label, ok, done, r.exitCode, out, t))
  const now = await $.clock.now()
  await update($, resultAt, () => now)
  await refresh($)
}

const answer = ($: EngineInterface, verdict: 'allow' | 'deny', rid: string) =>
  act($, rid, ['approve', verdict, rid, '--by', 'owner'], t => t(verdict === 'allow' ? 'allowed' : 'denied'))
const pick = ($: EngineInterface, id: string, key: string) =>
  act($, id, ['decide', 'answer', id, key, '--by', 'owner'], t => t('picked', { key }))
const setStatus = ($: EngineInterface, uid: string, status: string) =>
  act($, uid, ['unit', 'set', uid, '--status', status, '--by', 'owner'], t => statusWord(t, status))

type Hours = 3 | 6 | 24
// A pressure level as a bar color (mid-tones, readable on light and dark) and a word (`level_<lv>`).
const LEVEL_TONE: Record<string, Tone> = { ok: 'run', warn: 'caution', danger: 'alarm' }
const levelColor = (lv?: string | null) => PASTEL[LEVEL_TONE[lv ?? ''] ?? 'others'].bar
const hhmm = (ts?: string | null) => (ts ? ts.slice(11, 16) : '-')
const tileLabel = (t: Tr, name: string) => wordOf(t, 'tile', ['load', 'memory', 'disk', 'docker', 'cache'].includes(name) ? name : null) ?? name
function tileValue(name: string, v: number | null | undefined, t: Tr = KO_TR, extra?: Record<string, number | null> | null) {
  if (v == null) return '-'
  if (name === 'cache') return t('tile_cache_value', { gb: v, old: extra?.docker_cache_old_gb ?? '-', images: extra?.docker_images ?? '-' })
  if (name === 'memory') return t('tile_free_pct', { n: v })
  if (name === 'disk') return t('tile_free_gb', { n: Math.round(v) })
  if (name === 'docker') return `${v}% CPU`
  return String(v)
}
// Consecutive buckets of one level drawn as one colored run.
function runs(glyphs: string, levels: (string | null)[]) {
  const out: { lv: string | null; s: string }[] = []
  ;[...glyphs].forEach((g, i) => {
    const lv = levels[i] ?? null
    const last = out[out.length - 1]
    if (last && last.lv === lv) last.s += g
    else out.push({ lv, s: g })
  })
  return out
}
// PC 부하 tiles: a fixed width (the 24-bucket sparkline fits inside) laid side by side, as many as the pane holds.
export const TILE_W = 26
export const TILE_GAP = 2
export const tileColumns = (width: number, n = 4) =>
  Math.max(1, Math.min(n, 4, Math.floor((width + TILE_GAP) / (TILE_W + TILE_GAP))))
const chunk = <T,>(xs: T[], k: number) => Array.from({ length: Math.ceil(xs.length / k) }, (_, i) => xs.slice(i * k, i * k + k))
// A bar on a visible scale: the largest count fills `width` cells, any count at least one.
export const bar = (n: number, max: number, width: number) =>
  '█'.repeat(n > 0 ? Math.max(1, Math.round((n / Math.max(max, 1)) * width)) : 0)
// 재고 icons, per prefs `glyphs` as board.py resolved it: emoji (two columns, every terminal) or Nerd Font
// octicons (one column; private-use code points a terminal draws only with a Nerd Font, so one space follows).
export type Glyphs = 'nerd' | 'emoji'
export const ICONS = {
  emoji: {
    pr: { draft: '📝', review: '🔀', approved: '🔀', merged: '🟣', closed: '⛔' } as Record<string, string>,
    prDefault: '🔀', branch: '🌿', worktree: '📁', warn: '⚠',
    state: { draft: '📝', review: '👀', approved: '✅', merged: '🟣' } as Record<string, string>,
  },
  nerd: {
    pr: { draft: '\uf4dd', review: '\uf407', approved: '\uf407', merged: '\uf419', closed: '\uf4dc' } as Record<string, string>,
    prDefault: '\uf407', branch: '\uf418', worktree: '\uf413', warn: '\uf421',
    state: { draft: '\uf4dd', review: '\uf441', approved: '\uf42e', merged: '\uf419' } as Record<string, string>,
  },
}
// Git icons beside a PR, a repo and a branch: Nerd Font octicons only when the owner said their terminal draws
// them (prefs glyphs = nerd, asked once by `foundry setup`); otherwise nothing, and the column header says "PR".
export const GIT_ICON = { pr: '\uf407', repo: '\uf401', branch: '\uf418', github: '\uf408' }
export const gitIcon = (g: Glyphs, k: keyof typeof GIT_ICON) => (g === 'nerd' ? `${GIT_ICON[k]} ` : '')
// 재고 kinds (`kind_<kind>`), 오너 대기 kinds (`own_<kind>`), PR stages (`prstate_<stage>`), worktree waits
// (`waiton_<what>`): catalog words.
export function invKind(g: Glyphs, kind: string, stage?: string | null, t: Tr = KO_TR) {
  const ic = ICONS[g]
  if (kind === 'pr') return `${ic.pr[stage ?? ''] ?? ic.prDefault} PR`
  const k = kind as 'branch' | 'worktree'
  const word = wordOf(t, 'kind', kind)
  return word ? `${ic[k]} ${word}` : kind
}
export const prState = (g: Glyphs, stage?: string | null, t: Tr = KO_TR) => {
  const word = wordOf(t, 'prstate', stage)
  return word ? `${ICONS[g].state[stage!]} ${word}` : (stage ?? '—')
}
// Unit status enums as the pane says them (`status_<status>`); the colors are the ones the state column always had.
const STATUSES = ['queue', 'in_progress', 'waiting_others', 'waiting_owner', 'done', 'parked', 'blocked', 'cancelled'] as const
export const STATUS_KO: Record<string, string> = Object.fromEntries(STATUSES.map(s => [s, PANE_KO[`status_${s}`]]))
const statusWord = (t: Tr, s: string) => wordOf(t, 'status', STATUSES.includes(s as any) ? s : null) ?? s
const STATUS_COLOR: Record<string, string> = {
  done: 'success', in_progress: 'suggestion', blocked: 'error', waiting_owner: 'warning', waiting_others: 'warning',
  released: 'success', release_wait: 'warning',
}
// Hours as the shortest readable age; a missing one is a dash, never `-h`.
export function fmtAge(h: number | null | undefined) {
  if (h == null || !Number.isFinite(h)) return '—'
  if (h < 1) return `${Math.max(0, Math.round(h * 60))}m`
  return h < 48 ? `${Math.round(h)}h` : `${Math.round(h / 24)}d`
}
// 경과's colour: fresh (under 4h) dim, over a day the warning colour, between plain (views.age_tone).
export const ageCell = (h: number | null | undefined): Exclude<Cell, string> =>
  h == null || h < 4 ? { t: fmtAge(h), dim: true } : h > 24 ? { t: fmtAge(h), color: 'warning' } : { t: fmtAge(h) }
// A PR ref in `w` columns: the repo name gives way (…), the number after `#` never does.
export function fitRef(ref: string, w: number) {
  const i = ref.lastIndexOf('#')
  if (cols(ref) <= w || i < 0) return ref
  const num = ref.slice(i)
  const room = Math.max(0, w - cols(num) - 1)
  return (room > 0 ? ref.slice(0, room) + '…' : '') + num
}
// Only an https page opens from a press; anything else is drawn, never run.
export const openable = (url?: string | null) => !!url && /^https:\/\/\S+$/.test(url)
// The repos the model knows, as owner/repo slugs: only these get a GitHub page built for a bare PR ref.
export function knownRepos(m: Pick<BoardModel, 'repo_names' | 'repo_accents' | 'inventory' | 'pr_tab'>): string[] {
  const xs = [...(m.repo_names ?? []), ...Object.keys(m.repo_accents ?? {}), ...Object.keys(m.inventory?.repos ?? {}),
    ...(m.pr_tab ?? []).map(p => p.ref.split('#')[0]!)]
  return [...new Set(xs.filter(x => /^[\w.-]+\/[\w.-]+$/.test(x)))]
}
// A PR ref's page: the model's url when it has one, else github.com/<owner>/<repo>/pull/<n> for a repo the model
// knows (`repo#n` resolved against `home`, the unit's repo, first). Anything else stays text.
export function prHref(ref: string | null | undefined, repos: string[], url?: string | null, home?: string | null): string | undefined {
  if (openable(url)) return url!
  const m = /^(?:([\w.-]+)\/)?([\w.-]+)#(\d+)$/.exec((ref ?? '').trim())
  if (!m) return undefined
  const [, owner, name, num] = m
  const slug = owner ? (repos.includes(`${owner}/${name}`) ? `${owner}/${name}` : undefined)
    : [home, ...repos].find(r => !!r && r.includes('/') && r.split('/').pop() === name && repos.includes(r))
  return slug ? `https://github.com/${slug}/pull/${num}` : undefined
}
export const PR_REF_RE = /(?<![\w/#.-])(?:[\w.-]+\/)?[\w.-]+#\d+\b/g
// An owner-button outcome as one short line: `<what> → <done> ✓`, or the failure with its error text. It stays a
// minute (RESULT_TTL_MS), then the next refresh clears it.
export const RESULT_TTL_MS = 60_000
export const resultLine = (label: string, ok: boolean, done: string, code?: number | null, out?: string, t: Tr = KO_TR) =>
  ok ? t('result_ok', { what: label, done })
    : t('result_fail', { what: label, code: code != null ? ` (${code})` : '', detail: out ? ' ' + out : '' })
export const resultOk = (res: string) => res.endsWith(' ✓')
export const FAIL_LOG = 'pane-last-failure.log'
// One human sentence from a failed CLI run: the last line of a traceback, else the first stderr line that is not
// JSON, else a JSON `error` field; never raw output. At most 80 columns.
export function failText(stderr: string, stdout: string): string {
  const lines = stderr.split('\n').map(x => x.trim()).filter(Boolean)
  let t = (/^Traceback/.test(lines[0] ?? '') ? lines.at(-1) : lines.find(x => !/^[{[\]}"]/.test(x))) ?? ''
  for (const s of [stderr, stdout]) {
    if (t) break
    try {
      const o = JSON.parse(s)
      t = typeof o?.error === 'string' ? o.error : ''
    } catch {
      /* not JSON */
    }
  }
  t = t.replace(/\s+/g, ' ').trim()
  return cols(t) > 80 ? cutCols(t, 79)[0] + '…' : t
}
// A unit id in a table's 단위 column: at most `w` columns, cut in the middle, keeping a ticket key (PROJ-794) or a PR
// number (#12) and the id's end; the full id is in the detail (and board.html's title).
export function shortUnit(id: string, w = UNIT_CELL): string {
  if (cols(id) <= w) return id
  const m = id.match(/[A-Z][A-Z0-9]+-\d+|#\d+/)
  const keep = (a: string, b: string) => {
    const room = w - 1 - cols(a) - cols(b)
    return room >= 0 ? [a, b, room] as const : null
  }
  if (m) {
    const at = m.index!
    const head = id.slice(0, at + m[0].length)
    const tail = id.slice(at + m[0].length)
    if (cols(head) <= w - 2) {  // the key in the head: head … end of the id
      const r = keep(head, '')!
      return head + '…' + [...tail].slice(-Math.max(1, r[2])).join('').replace(/^-/, '')
    }
    if (cols(m[0]) + cols(tail) <= w - 2) {  // the key near the end: start … key and what follows
      const r = keep('', id.slice(at))!
      return cutCols(id, Math.max(1, r[2]))[0] + '…' + id.slice(at)
    }
  }
  const right = Math.ceil((w - 1) / 2)
  const end = [...id]
  let tail = ''
  for (let i = end.length - 1; i >= 0 && cols(end[i]! + tail) <= right; i--) tail = end[i]! + tail
  return cutCols(id, w - 1 - cols(tail))[0] + '…' + tail
}
export const UNIT_CELL = 14

// ---------- bordered tables ----------
// A table cell is a string (wrapped) or lines of styled segments; a segment with `href` is an OSC 8 link and never
// splits across lines.
export type Seg = { t: string; href?: string; color?: string; bold?: boolean; dim?: boolean }
export type TCell = string | Seg[][]
const BOX = { tl: '┌', tm: '┬', tr: '┐', ml: '├', mm: '┼', mr: '┤', bl: '└', bm: '┴', br: '┘', h: '─', v: '│' }
// The first `w` columns of `x` and the rest (wide characters counted as two).
export function cutCols(x: string, w: number): [string, string] {
  let n = 0
  let i = 0
  const cs = [...x]
  for (; i < cs.length; i++) {
    const c = cols(cs[i]!)
    if (n + c > w) break
    n += c
  }
  return [cs.slice(0, Math.max(i, 1)).join(''), cs.slice(Math.max(i, 1)).join('')]
}
const segCols = (xs: Seg[]) => xs.reduce((n, x) => n + cols(x.t), 0)
const sameStyle = (a: Seg, b: Seg) => !a.href && !b.href && a.color === b.color && a.bold === b.bold && a.dim === b.dim
// One line of segments as lines at most `w` columns: words stay whole where they fit, a link is cut with … only
// when it alone is wider than the cell.
export function wrapSegs(line: Seg[], w: number): Seg[][] {
  const out: Seg[][] = [[]]
  let used = 0
  const cur = () => out[out.length - 1]!
  const push = (x: Seg) => {
    const last = cur()[cur().length - 1]
    if (last && sameStyle(last, x)) last.t += x.t
    else cur().push({ ...x })
    used += cols(x.t)
  }
  const brk = () => {
    const last = cur()[cur().length - 1]
    if (last && !last.href) last.t = last.t.trimEnd()
    out.push([])
    used = 0
  }
  for (const seg of line) {
    const toks = seg.href ? [seg.t] : seg.t.match(/\S+\s*|\s+/g) ?? []
    for (let tok of toks) {
      if (used > 0 && used + cols(tok.trimEnd()) > w) brk()
      if (used === 0 && !seg.href) tok = tok.replace(/^\s+/, '')
      if (!tok) continue
      if (seg.href && cols(tok) > w) tok = cutCols(tok, w - 1)[0] + '…'
      while (!seg.href && cols(tok.trimEnd()) > w - used) {
        const [head, rest] = cutCols(tok, w - used)
        push({ ...seg, t: head })
        brk()
        tok = rest.replace(/^\s+/, '')
      }
      if (tok) push({ ...seg, t: tok })
    }
  }
  const last = cur()[cur().length - 1]
  if (last && !last.href) last.t = last.t.trimEnd()
  return out.length > 1 && cur().length === 0 ? out.slice(0, -1) : out
}
const cellSegs = (c: TCell, bold = false): Seg[][] => (typeof c === 'string' ? c.split('\n').map(t => [{ t, bold }]) : c)
// Column widths for `width` columns of table (borders included): every column but `flex` gets its natural width up
// to a third of the room; `flex` takes the rest.
export function tableWidths(width: number, head: string[] | null, rows: TCell[][], flex: number) {
  const n = Math.max(head?.length ?? 0, ...rows.map(r => r.length))
  const room = Math.max(n * 4, width - (3 * n + 1))
  const nat = Array.from({ length: n }, (_, i) =>
    Math.max(head ? cols(head[i] ?? '') : 1, ...rows.map(r => Math.max(0, ...cellSegs(r[i] ?? '').map(segCols)))))
  const ws = nat.map((x, i) => (i === flex ? 0 : Math.max(2, Math.min(x, Math.floor(room / 3)))))
  ws[flex] = Math.max(6, room - ws.reduce((a, b) => a + b, 0))
  return ws
}
// A bordered table as lines of segments (│ cell │ cell │, ┌─┬─┐ rules, a ├─┼─┤ under the header and between rows
// once any row wraps, unless `sep` says otherwise). `row` is the body row a line belongs to (-1 for rules and the
// header), `first` its first line.
export function tableLines(ws: number[], head: string[] | null, rows: TCell[][], sep = true) {
  const rule = (l: string, m: string, r: string, dim = true): Seg[] => [{ t: l + ws.map(w => BOX.h.repeat(w + 2)).join(m) + r, dim }]
  const lay = (cells: TCell[], bold: boolean) => {
    const ls = ws.map((w, i) => cellSegs(cells[i] ?? '', bold).flatMap(l => wrapSegs(l, w)))
    return Array.from({ length: Math.max(1, ...ls.map(l => l.length)) }, (_, k) => {
      const segs: Seg[] = [{ t: BOX.v + ' ', dim: true }]
      ls.forEach((l, i) => {
        const ln = l[k] ?? []
        segs.push(...ln, { t: ' '.repeat(Math.max(0, ws[i]! - segCols(ln))) + ' ' + BOX.v + (i < ws.length - 1 ? ' ' : ''), dim: true })
      })
      return segs
    })
  }
  const body = rows.map(r => lay(r, false))
  const between = sep
  const out: { segs: Seg[]; row: number; first: boolean }[] = [{ segs: rule(BOX.tl, BOX.tm, BOX.tr), row: -1, first: false }]
  if (head) {
    out.push(...lay(head, true).map(segs => ({ segs, row: -1, first: false })))
    out.push({ segs: rule(BOX.ml, BOX.mm, BOX.mr, false), row: -1, first: false })  // the header rule: full weight
  }
  body.forEach((b, i) => {
    // Between rows: a thin muted rule, so a ruled table does not read heavy.
    if (i && between) out.push({ segs: [{ t: BOX.ml + ws.map(w => BOX.h.repeat(w + 2)).join(BOX.mm) + BOX.mr, dim: true, color: 'inactive' }], row: -1, first: false })
    out.push(...b.map((segs, k) => ({ segs, row: i, first: k === 0 })))
  })
  out.push({ segs: rule(BOX.bl, BOX.bm, BOX.br), row: -1, first: false })
  return out
}
// The detail's status as one line: 상태, then 다음 only when it says something the status does not, and the blocked
// items no status, next or wait already names.
export function statusSummary(v: UnitView, t: Tr = KO_TR) {
  const norm = (x: string) => x.replace(/대기|[\s()·:,→\-]/g, '').toLowerCase()
  const base = norm(v.status_line)
  const next = v.next && v.next !== '-' && !base.includes(norm(v.next)) ? v.next : null
  const said = [base, next ? norm(next) : '', ...v.waits.map(w => norm(w.text))].join('|')
  return { line: next ? t('status_next', { status: v.status_line, next, who: v.who_ko }) : v.status_line,
    // A blocker the status already says (its PR ref aside: the PR rows carry it) is not repeated.
    blocked: v.blocked.filter(b => { const r = norm(b.replace(PR_REF_RE, '')); return !(r ? said.includes(r) : said.includes(norm(b))) }) }
}
// A PR as a one-liner's state: GitHub's word, the review mark, and for an open PR only what is wrong (CI failing,
// a conflict); a merged or closed PR says nothing about head, CI or mergeable.
const CI_BAD = ['failure', 'fail', 'error', 'cancelled', 'timed_out']
export function prStateSegs(p: { gh?: string | null; gh_review?: string | null; ci?: string | null; merge_state?: string | null; state?: string | null }, t: Tr = KO_TR): Seg[] {
  const gh = p.gh && GH_COLOR[p.gh] ? p.gh : 'open'
  const rv = p.gh_review ? GH_REVIEW[p.gh_review] : undefined
  const out: Seg[] = [{ t: wordOf(t, 'gh', gh) ?? gh, color: GH_COLOR[gh] }]
  if (rv) out.push({ t: ` ${rv.mark} ${wordOf(t, 'review', p.gh_review)}`, color: rv.color })
  if (gh === 'open' || gh === 'draft') {
    if (CI_BAD.includes(String(p.ci ?? '').toLowerCase())) out.push({ t: ` · ${t('ci_failed')}`, color: 'error' })
    if (['CONFLICTING', 'DIRTY'].includes(String(p.merge_state ?? '').toUpperCase())) out.push({ t: ` · ${t('conflict')}`, color: 'error' })
  }
  return out
}
export const shortRef = (ref: string) => ref.split('/').pop()!
// 재고's worktree state: its unit's status in the status tones, or a finished unit's reclaim state; null for a
// model from before 0.1.31, which keeps the old wait word.
const wtState = (it: InventoryItem, t: Tr = KO_TR): Exclude<Cell, string> | null => {
  if (it.reclaim) return it.reclaim_kept ? { t: it.reclaim, color: 'warning' } : { t: it.reclaim, dim: true }
  if (!it.status) return null
  const tone = PASTEL[STATUS_TONE[it.status] ?? 'others']
  return { t: ` ${statusWord(t, it.status)} `, bg: tone.bg, color: tone.fg }
}
// The PR counts line and stage buckets: board.py's pr_summary, or the same buckets counted here for an older model
// or a repo filter.
const PR_BUCKETS: [string, PaneKey, string[]][] = [
  ['review', 'prtab_review', ['review']], ['merge', 'prtab_merge_wait', ['approved', 'merge-wait']],
  ['release', 'prtab_release_wait', ['release-wait']], ['draft', 'prtab_draft', ['draft']],
]
export function prSummaryOf(m: BoardModel, rows: PrTabRow[], t: Tr = KO_TR): PrSummary {
  if (m.pr_summary && rows.length === m.pr_summary.total) return m.pr_summary
  const buckets = PR_BUCKETS.map(([k, label, sts]) => ({ key: k, label: t(label), n: rows.filter(r => sts.includes(r.state)).length })).filter(b => b.n)
  return { total: rows.length, stale: rows.filter(r => r.stale).length, buckets,
           text: [`PR ${rows.length}`, ...buckets.map(b => `${b.label} ${b.n}`)].join(' · ') }
}
// Repo blocks: board.py's repo_blocks, or counted here for an older model.
export function repoBlocksOf(m: BoardModel, prs: PrTabRow[], t: Tr = KO_TR): RepoBlock[] {
  if (m.repo_blocks) return m.repo_blocks
  const names = [...new Set([...Object.keys(m.inventory?.repos ?? {}), ...prs.map(p => p.ref.slice(0, p.ref.lastIndexOf('#')))])].filter(Boolean).sort()
  return names.map(repo => {
    const its = m.inventory?.repos?.[repo] ?? []
    const pr = prs.filter(p => p.ref.startsWith(`${repo}#`)).length
    const branch = its.filter(i => i.kind === 'branch').length
    const worktree = its.filter(i => i.kind === 'worktree').length
    const counts = ([['PR', pr], [t('kind_branch'), branch], [t('kind_worktree'), worktree]] as const).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(' · ')
    return { repo, short: repo.split('/').pop()!, accent: repoAccent(repo, m.repo_accents), counts, pr, branch, worktree }
  })
}
const hoursSince = (ts: string | null | undefined, now: string) =>
  ts && Date.parse(ts) && Date.parse(now) ? (Date.parse(now) - Date.parse(ts)) / 3600000 : null

// Tables: fixed columns sized for the pane's width, the one without a width takes the slack; when the slack
// falls under `min`, columns with a `drop` rank leave, highest first. Two-column gutters.
export type Col = { k: string; h: string; w?: number; drop?: number }
export type Cell = string | { t: string; color?: string; bg?: string; bold?: boolean; dim?: boolean; sub?: string; subColor?: string; href?: string }
export const GUTTER = 2
// A PR's state in GitHub's colours: the state word (with its octicon under a Nerd Font when `icon`), then the review
// accent as a trailing mark in its own colour. Unknown states read as open, GitHub's default.
// `compact` (the PR tab, whose state word already says where the review stands) keeps the mark alone.
export function ghCell(g: Glyphs, gh?: string | null, review?: string | null, label?: string, compact = false, t: Tr = KO_TR): Exclude<Cell, string> {
  const k = gh && GH_COLOR[gh] ? gh : 'open'
  const rv = review ? GH_REVIEW[review] : undefined
  return {
    t: `${!compact && g === 'nerd' ? GH_NERD[k] + ' ' : ''}${label ?? wordOf(t, 'gh', k)}`, color: GH_COLOR[k],
    sub: rv ? (compact ? rv.mark : `${rv.mark} ${wordOf(t, 'review', review)}`) : '', subColor: rv?.color,
  }
}
export function layout(cs: Col[], width: number, min = 12): (Col & { w: number })[] {
  const fixed = (xs: Col[]) => xs.reduce((n, c) => n + (c.w ?? 0), 0) + GUTTER * (xs.length - 1)
  let keep = cs
  while (width - fixed(keep) < min && keep.some(c => c.drop)) {
    const d = keep.filter(c => c.drop).reduce((a, b) => (b.drop! > a.drop! ? b : a))
    keep = keep.filter(c => c !== d)
  }
  return keep.map(c => ({ ...c, w: c.w ?? Math.max(min, width - fixed(keep)) }))
}
const UNIT_COLS = (t: Tr): Col[] => [
  { k: 'id', h: t('th_unit'), w: 11 }, { k: 'title', h: t('th_title') }, { k: 'pr', h: 'PR', w: 7, drop: 1 },
  { k: 'st', h: t('th_status'), w: 20 }, { k: 'age', h: t('th_age'), w: 4, drop: 2 },
]
const ESC_COLS = (t: Tr): Col[] => [{ k: 'kind', h: t('th_kind'), w: 9 }, { k: 'unit', h: t('th_unit'), w: 10 }, { k: 'text', h: t('th_content') }]
const DEC_COLS = (t: Tr): Col[] => [{ k: 'unit', h: t('th_unit'), w: 11 }, { k: 'q', h: t('th_question') }, { k: 'age', h: t('th_age'), w: 4, drop: 1 }]
const OWN_COLS = (t: Tr): Col[] => [{ k: 'unit', h: t('th_unit'), w: 11 }, { k: 'q', h: t('th_status') }]
const APR_COLS = (t: Tr): Col[] => [
  { k: 'unit', h: t('th_unit'), w: 11 }, { k: 'tier', h: 'tier', w: 4 }, { k: 'req', h: t('th_request') }, { k: 'age', h: t('th_age'), w: 4, drop: 1 },
]
// The [allow] [deny] buttons after an approval row's cells.
const APR_BUTTONS = 16
const NEXT_COLS = (t: Tr): Col[] => [
  { k: 'unit', h: t('th_unit'), w: 11 }, { k: 'repo', h: 'repo', w: 10, drop: 2 }, { k: 'title', h: t('th_title') },
  { k: 'pri', h: t('th_priority'), w: 8, drop: 1 }, { k: 'age', h: t('th_age'), w: 4, drop: 3 },
]
const PR_COLS = (t: Tr): Col[] => [
  { k: 'ref', h: 'PR', w: 14 }, { k: 'title', h: t('th_title'), drop: 1 }, { k: 'unit', h: 'unit', w: 10, drop: 4 }, { k: 'st', h: t('th_status'), w: 12 },
  { k: 'rev', h: t('th_reviewer'), w: 14, drop: 3 }, { k: 'age', h: t('th_age'), w: 4, drop: 2 }, { k: 'act', h: t('th_next_action'), w: 16 },
]
const INV_COLS = (t: Tr): Col[] => [
  { k: 'kind', h: t('th_kind'), w: 11 }, { k: 'ref', h: t('th_target') }, { k: 'age', h: t('th_age'), w: 4 },
  { k: 'st', h: t('th_status_wait'), w: 24, drop: 1 },
]

// The band under the prompt: background pills. Only what needs a look is drawn; a zero is
// left out. ANDON and a down foreman come first, rose; the text alone carries the meaning, the pastel tone ranks it.
type Pill = { key: string; text: string; tone: Tone }

// Counts come from board.py's summary(), the same numbers the text/HTML band and the board headers show.
function bandPills(m: BoardModel, pc = false): Pill[] {
  const t = trOf(m)
  const count = (key: string) => m.summary?.counts[key as ColumnKey] ?? m.sections.find(s => s.key === key)?.count ?? 0
  const decisions = m.summary?.decisions ?? m.cards_open ?? 0
  const out: Pill[] = []
  if (m.halts?.length) out.push({ key: 'andon', text: `ANDON ${m.halts.length}`, tone: 'alarm' })
  if (m.foreman_down) out.push({ key: 'foreman', text: 'foreman down', tone: 'alarm' })
  if (m.stale_foremen) out.push({ key: 'stale-foremen', text: t(m.stale_foremen_failed ? 'pill_stale_foremen_failed' : 'pill_stale_foremen', { n: m.stale_foremen }), tone: 'others' })
  const stalled = m.sections.flatMap(s => s.rows).filter(r => r.stall).length
  if (stalled) out.push({ key: 'stall', text: t('pill_stall', { n: stalled }), tone: 'alarm' })
  const run = count('in_progress')
  if (run) out.push({ key: 'run', text: t('pill_run', { n: run }), tone: 'run' })
  const owner = m.summary?.owner_wait ?? count('waiting_owner')
  if (owner) out.push({ key: 'owner', text: t('pill_owner', { n: owner }), tone: 'owner' })
  if (m.approvals.length) out.push({ key: 'approvals', text: t('pill_approvals', { n: m.approvals.length }), tone: 'approve' })
  if (decisions) out.push({ key: 'decisions', text: t('pill_decisions', { n: decisions }), tone: 'decide' })
  const lv = m.pressure.level
  if (lv !== 'ok' || pc) out.push({ key: 'pressure', text: `PC ${lv}`, tone: lv === 'ok' ? 'run' : lv === 'warn' ? 'caution' : 'alarm' })
  return out
}

function Band({ m, Text, Box, hint, pc = false }: { m: BoardModel; Text: any; Box: any; hint: boolean; pc?: boolean }) {
  const pills = bandPills(m, pc)
  const t = trOf(m)
  return (
    <Box flexDirection="row" gap={1}>
      {pills.length > 0 && (
        <Box key="pills" flexDirection="row">
          {pills.map(p => (
            <Text key={`band:${p.key}`} backgroundColor={PASTEL[p.tone].bg} color={PASTEL[p.tone].fg} bold>
              {` ${p.text} `}
            </Text>
          ))}
        </Box>
      )}
      {pills.length === 0 && <Text dimColor>{t('band_idle')}</Text>}
      {hint && <Text dimColor>{t('band_hint')}</Text>}
    </Box>
  )
}

// Terminal columns: Hangul and other East Asian wide characters take two, so do emoji drawn as emoji; joiners and
// variation selectors take none.
const wide = (c: string) =>
  /[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6]|\p{Emoji_Presentation}/u.test(c)
const zero = (c: string) => /[\u200d\ufe0e\ufe0f]/.test(c)
export const cols = (x: string) => [...x].reduce((n, c) => n + (zero(c) ? 0 : wide(c) ? 2 : 1), 0)
export function pad(x: string, n: number) {
  if (cols(x) <= n) return x + ' '.repeat(n - cols(x))
  let out = ''
  for (const c of x) {
    if (cols(out + c) > n - 1) break
    out += c
  }
  return out + '…' + ' '.repeat(n - 1 - cols(out))
}
// The build this session runs: the loaded copy's plugin.json, and the newest the marketplace it came from lists
// (an installed plugin's root is <plugins>/cache/<marketplace>/<name>/<version>). Either may be unreadable.
async function loadBuild($: any) {
  const json = async (path: string) => JSON.parse(await $.fs.read(path))
  let version: string | null = null
  let newest: string | null = null
  try {
    version = (await json(`${$.plugin.root}/.claude-plugin/plugin.json`)).version ?? null
  } catch {}
  try {
    const m = /^(.*)\/cache\/([^/]+)\/[^/]+\/[^/]+\/?$/.exec($.plugin.root)
    const loc = m ? (await json(`${m[1]}/known_marketplaces.json`))[m[2]!]?.installLocation : null
    if (loc) {
      const listed = (await json(`${loc}/.claude-plugin/marketplace.json`)).plugins ?? []
      newest = listed.find((x: { name?: string }) => x.name === 'foundry')?.version ?? null
    }
  } catch {}
  await update($, buildAtom, () => ({ version, newest }))
}
const semver = (v: string) => v.split(/[.-]/).slice(0, 3).map(x => Number(x) || 0)
export const isNewer = (a: string, b: string) => {
  const [x, y] = [semver(a), semver(b)]
  const i = x.findIndex((n, k) => n !== y[k])
  return i >= 0 && x[i]! > y[i]!
}
export function buildText(b: { version: string | null; newest: string | null } | null, t: Tr = KO_TR) {
  if (!b?.version) return null
  const stale = b.newest && isNewer(b.newest, b.version) ? t('build_new', { version: b.newest }) : null
  return { version: `v${b.version}`, stale, short: stale && t('build_new_short', { version: b.newest! }) }
}

// Header fitting. The pane's × sits at its top-right corner: two columns stay free on the right.
export const RIGHT_MARGIN = 2
// An open decision's outline lines shown before ▸ 자세히.
export const DEC_LINES = 6
export const CLOSE_LABEL = PANE_KO.close
// The longest key hint that fits beside the title and the close button, or none.
export function fitKeys(avail: number, left: number, folded: boolean, t: Tr = KO_TR) {
  const choices = folded ? [t('keys_unfold')] : [t('keys_full'), t('keys_fold')]
  return choices.find(k => left + 2 + cols(`${k} · `) + cols(t('close')) <= avail) ?? null
}
// Repo tabs: without the owner when every repo has the same one; as many as fit after the view tabs, the selected
// one always, and "+N" for the rest.
export const repoLabel = (names: string[]) => {
  const owners = new Set(names.map(n => (n.includes('/') ? n.split('/')[0] : null)))
  return (n: string) => (owners.size === 1 && !owners.has(null) ? n.split('/').pop()! : n)
}
export function fitTabs(names: string[], selected: string, avail: number, prTab = '', t: Tr = KO_TR) {
  const label = repoLabel(names)
  const lead = cols(`${t('tab_status')} ${t('tab_repo')} ${prTab ? prTab + ' ' : ''}|`)
  const all = ['all', ...names]
  const used = (xs: string[], more: number) =>
    lead + xs.reduce((n, x) => n + 1 + cols(label(x)), 0) + (more ? 1 + cols(`+${more}`) : 0)
  let shown = all
  while (used(shown, all.length - shown.length) > avail && shown.length > 1) {
    const drop = [...shown].reverse().find(x => x !== 'all' && x !== selected)
    if (!drop) break
    shown = shown.filter(x => x !== drop)
  }
  return { shown, more: all.length - shown.length, label }
}
// Bars: a label, a right-aligned count, then the bar, on the tables' column system, so every bar starts at one
// column and every count sits in one.
const BAR_COLS: Col[] = [{ k: 'label', h: '', w: 11 }, { k: 'n', h: '', w: 3 }, { k: 'bar', h: '' }]
export const BAR_MAX = 20

const prOf = (r: BoardRow) => {
  const p = r.prs[0]
  return p ? `#${p.split('#').pop()}` : '—'
}
// What a unit waits on, in plain words (`waitkind_<kind>`): a flag first, else its first open wait (who or what it
// names), else its PRs. Tool approvals, hands and the lead session's own state are the detail view's (and the
// approvals table's).
const ENV_SHORT: Record<string, string> = { development: 'dev', staging: 'stg', production: 'prod' }
const NOT_WHO = new Set(['ci', 'review', 'the', 'a', 'an', 'next', 'merge', 'approval', 'release', 'deploy'])
const whoOf = (note?: string | null) => {
  const m = /\b[Ww]aiting (?:for|on) ([A-Za-z][\w-]*)/.exec(note ?? '')
  return m && !NOT_WHO.has(m[1]!.toLowerCase()) ? m[1]! : null
}
export function waitText(w: BoardRow['waits'][number], t: Tr = KO_TR): string {
  const label = wordOf(t, 'waitkind', w.kind) ?? w.kind
  const num = w.ref && /#\d+$/.test(w.ref) ? `#${w.ref.split('#').pop()}` : null
  const who = whoOf(w.note)
  if (w.kind === 'pr-review') return who ? `${label} · ${who}` : num ? `${label} ${num}` : label
  if (w.kind === 'pr-merge' || w.kind === 'ci') return num ? `${label} ${num}` : label
  if (w.kind === 'team') return who ? t('wait_answer', { who }) : label
  if (w.kind === 'deploy-window') {
    const env = (w.ref ?? '').split('@')[1]
    return env ? `${label} ${ENV_SHORT[env] ?? env}` : label
  }
  if (w.kind === 'time' && w.until) return t('wait_until', { at: `${w.until.slice(5, 10).replace('-', '/')} ${w.until.slice(11, 16)}` })
  return label
}
function detailOf(r: BoardRow, t: Tr): { t: string; color?: string } | null {
  const flags = rowFlags(r, t)
  if (flags.length) return { t: flags[0]!, color: 'warning' }
  const w = r.waits.find(x => x.kind !== 'time') ?? r.waits[0]
  if (w) return { t: waitText(w, t) }
  const prs = (r.pr_items ?? []).filter(x => x && typeof x === 'object')
  if (prs.some(x => x.state === 'OPEN')) return { t: t('prtab_review') }
  if (prs.length && prs.every(x => x.state === 'MERGED')) {
    const tag = prs.map(x => x.released).find(x => x && !/^\d{4}-/.test(x))
    return { t: prs.every(x => x.released) ? (tag ? t('released_tag', { tag }) : t('released')) : t('prstate_merged') }
  }
  return null
}
// The unit's status in words, then the detail. Under a status heading that already names the status, the detail
// stands alone (a row whose status differs from its section, such as 막힘 under 팀 대기, keeps both).
function stateOf(r: BoardRow, t: Tr, section?: string): Cell {
  // A closed unit's cell is board.py's unit_state alone: no flag, wait, phase or deadline from when it ran.
  if (r.closed && r.state) {
    const color = STATUS_COLOR[r.state.group]
    if (section === r.state.group) return { t: r.state.detail || r.state.label, color, dim: !r.state.detail }
    return { t: r.state.label, color, sub: r.state.detail ?? '' }
  }
  const word = statusWord(t, r.status)
  const d = detailOf(r, t)
  if (section === r.status && d) return { t: d.t, color: d.color ?? STATUS_COLOR[r.status] }
  return { t: word, color: STATUS_COLOR[r.status], sub: d?.t ?? '', subColor: d?.color }
}

function rowFlags(r: BoardRow, t: Tr = KO_TR) {
  const out: string[] = []
  if (r.stall) out.push(t('flag_stall', { text: r.stall.text || r.stall.kind }))
  if (r.sync_text) out.push(r.sync_text)
  if (r.mismatch) out.push(t('flag_mismatch', { text: String(r.mismatch) }))
  if (r.mode === 'plan') out.push('plan')
  if (r.unwatched) out.push(r.unwatched)
  else if (r.status === 'waiting_others' && r.waits.length === 0) out.push(t('flag_no_wait_target'))
  return out
}

// The unit detail as board.py's views.py computed it; a model from an older board.py falls back to the raw row.
export function viewOf(r: BoardRow, t: Tr = KO_TR): UnitView {
  const ch = r.chain && !Array.isArray(r.chain) ? r.chain : null
  const chain = r.view?.chain ?? (ch && ((ch.upstream ?? []).length || (ch.downstream ?? []).length)
    ? [ch.upstream?.length ? t('chain_up', { list: ch.upstream.join(', ') }) : '', ch.downstream?.length ? t('chain_down', { list: ch.downstream.join(', ') }) : '']
        .filter(Boolean).join(' → ')
    : Array.isArray(r.chain) && r.chain.length ? r.chain.join(' → ') : null)
  const hands = r.hands ?? []
  const running = hands.filter(h => !['stopped', 'done', 'finished', 'cancelled'].includes(h.state ?? ''))
  const base: UnitView = {
    status_line: statusWord(t, r.status), next: '-', who: 'foundry', who_ko: 'foundry',
    blocked: [...(r.stall ? [t('flag_stall', { text: r.stall.text || r.stall.kind })] : []), ...(r.blocked_by ?? []).map(b => t('blocked_by', { unit: b }))],
    tracker: r.tracker ?? (r.tickets.join(', ') || null), chain,
    worktree: r.worktree?.path ?? null, branch: r.worktree?.branch ?? null,
    prs: [], waits: r.waits.map(w => ({ kind: w.kind, ref: w.ref, text: waitText(w, t), note: w.note, age_h: null, stale: false, stale_text: null })),
    approvals: r.approvals.map(a => ({ rid: a.rid, tier: a.tier, command: (a.summary ?? a.tool ?? '').replace(/^(\w+): \1: /, '$1: '), ready: true, why: null })),
    owner_todos: [], dispatcher: [],
    timeline: r.notes.slice(-8).reverse().map(n => ({ ts: n.ts, gist: n.text.split(/(?<=[.!?。])\s/)[0]!.slice(0, 90), text: n.text })),
    hands_running: running, hands_stopped: hands.length - running.length,
  }
  return r.view ? { ...base, ...r.view, chain } : base
}

// A timeline note's leading kind tag (`[report] …`, `[lead] …`, `intake: …`), split off so it can carry a color.
export function noteTag(x: string): { tag: string | null; kind: string; rest: string } {
  const m = /^\[([\w-]+)\]\s*/.exec(x) ?? /^(intake)\b:?\s*/i.exec(x)
  if (!m) return { tag: null, kind: '', rest: x }
  const kind = m[1]!.toLowerCase()
  return { tag: m[0].startsWith('[') ? `[${kind}]` : kind, kind, rest: x.slice(m[0].length) }
}
// One line per entry: a body that runs past 70 columns, or has more after it, ends in … (⏎ unfolds it).
export function gistOf(gist: string, text: string) {
  const cut = cols(gist) > 70 ? pad(gist, 70).trimEnd() : gist
  return cut === gist && text.trim() !== gist.trim() ? `${cut} …` : cut
}

// PR tab rows: board.py's pr_tab, or the inventory's PR items from an older model (`prtab_<state>`, else
// `stage_<stage>`).
// Stages foundry tracks, in GitHub's lifecycle words (an older model carries no `gh`).
export const ghOfStage = (stage?: string | null) =>
  stage === 'draft' ? 'draft' : ['merged', 'release-wait', 'released', 'deployed-dev', 'prod-waiting', 'deployed'].includes(stage ?? '') ? 'merged' : 'open'
// A stale PR keeps its GitHub colour; the 「· stale」 mark trails it in the warning colour.
const staleCell = (c: Exclude<Cell, string>, stale?: boolean): Cell =>
  stale ? { ...c, sub: `${c.sub ? c.sub + ' ' : ''}· stale`, subColor: c.sub ? c.subColor : 'warning' } : c
export function prTabOf(m: BoardModel, t: Tr = KO_TR): PrTabRow[] {
  if (m.pr_tab) return m.pr_tab
  return Object.values(m.inventory?.repos ?? {}).flat().filter(it => it.kind === 'pr').map(it => ({
    ref: it.ref, title: it.title ?? '', unit: it.unit ?? null, state: it.stage === 'merged' ? 'release-wait' : (it.stage ?? '-'),
    state_ko: wordOf(t, 'prtab', it.stage) ?? wordOf(t, 'stage', it.stage) ?? (it.stage ?? '-'),
    reviewer: (it.reviewers ?? []).join(', ') || (it.approvers ?? []).map(x => `✓${x}`).join(', ') || '-', age_h: it.age_h, stale: it.stale, action: '-', who: 'foundry', who_ko: 'foundry',
  }))
}

// Paths read from the home folder (`~/...`); a long one keeps its last two parts behind `…/`, so a line is cut
// before it wraps rather than wrapping on a path.
const HOME_RE = /\/(?:Users|home)\/[^/\s]+/g
export function shortPath(p: string, max = 36) {
  const s = p.replace(HOME_RE, '~')
  return s.length <= max ? s : '…/' + s.split('/').slice(-2).join('/')
}
export const shortPaths = (text: string) => text.replace(/(?<![\w:/.#-])[~/][^\s]*\/[^\s]+/g, m => shortPath(m))

const reposOf = (r: BoardRow) => (r.repos?.length ? r.repos : r.repo ? [r.repo] : [])
const joined = (x: string | string[] | null | undefined) => (Array.isArray(x) ? x.join('; ') : (x ?? ''))

export const register: Register = (on, options) => {
  registerSpawn(on, options)

  on('session.start', async ($, e, next) => {
    await registerMappedAgents($)
    await $.command.register({ name: 'foundry-board', description: 'Open the foundry board, then fold/expand it; `close` closes it' })
    await declareSlash($)
    $.clock.every(30000, () => void refresh($))
    void refresh($)
    void loadBuild($)
    // The foreman opens the board beside the chat by itself; the engine seats an unasked pane only on a wide
    // terminal (144 columns, 110 once the owner has opened it) and keeps it waiting below that.
    if ((await $.env.get('FOUNDRY_ROLE')) === 'foreman') await $.ui.open({ id: PANE, title: TITLE, columns: 48 })

    return next(e)
  })

  // Closed: open it, expanded. Open: fold or expand it. `close` closes it.
  on('command.run', { command: 'foundry-board' }, async ($, e) => {
    const isOpen = (await $.ui.panes()).some(p => p.id === PANE)
    if (e.args.trim() === 'close') {
      if (!isOpen) return { text: 'foundry board pane is not open.' }
      await $.ui.close({ id: PANE })
      return { text: 'foundry board pane closed.' }
    }
    if (isOpen) {
      const fold = !(await read($, foldedAtom))
      await update($, foldedAtom, () => fold)
      await $.ui.open({ id: PANE, title: TITLE, ...(fold ? { rows: FOLDED_ROWS } : {}) })
      return { text: fold ? 'foundry board folded; /foundry-board expands it.' : 'foundry board expanded; /foundry-board folds it.' }
    }
    await update($, foldedAtom, () => false)
    const opened = await $.ui.open({ id: PANE, title: TITLE })
    await refresh($)
    if (!opened.isPlaced) {
      return { text: 'foundry board pane is open but this surface has no room for it yet; `foundry board` prints it in chat.' }
    }

    return { text: 'foundry board pane opened; /foundry-board folds it, /foundry-board close closes it.' }
  })

  on('command.run', { command: 'foundry' }, async ($, e) => {
    const r = await slashRun($, e.args, e.origin?.kind)
    if (!r.isTextDisplayOnly) await refresh($)
    return r
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const m = await read($, board)
    if (e.props.hasSurvey || !m) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    // Until the pane is seated (a narrow terminal keeps the foreman's auto-open waiting), the band names the
    // command that opens it.
    const placed = (await $.ui.panes()).some(p => p.id === PANE && p.isPlaced)

    return <Band m={m} Text={Text} Box={Box} hint={!placed} />
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Link } = $.ui.resolve(e)
    const model = await read($, board)
    const sel = await read($, selected)
    const err = await read($, error)
    const res = await read($, result)
    const view = await read($, viewAtom)
    const repo = await read($, repoAtom)
    const hours = await read($, loadHours)
    const escSel = await read($, escOpen)
    const cardSel = await read($, cardOpen)
    const nearUnfold = await read($, nearOpen)
    const met = await read($, metricsAtom)
    const folded = await read($, foldedAtom)
    const t = trOf(model)
    const build = buildText(await read($, buildAtom), t)
    if (!model) return err ? <Text color="error">{err}</Text> : <Text dimColor>Loading foundry state...</Text>
    // One title row: the name on the left, the keys dim on the right, a thin rule under it. No printable hotkey: one
    // only fires while the pane holds the keyboard, and from the prompt the letter is typed into the chat. ctrl+x f
    // is the owner's Chat binding for /foundry-board.
    const avail = (e.props.bodyColumns ?? 100) - RIGHT_MARGIN
    const titleRow = (fold: boolean) => {
      // A newer build's note shortens before it pushes the close button off the row.
      const leftOf = (note: string | null | undefined) =>
        cols('foundry board') + (build ? 1 + cols(build.version) + (note ? 3 + cols(note) : 0) : 0)
      const stale = [build?.stale, build?.short].find(n => n && leftOf(n) + 2 + cols(t('close')) <= avail) ?? null
      const left = leftOf(stale)
      const keys = fitKeys(avail, left, fold, t)
      return (
        <Box key="title" flexDirection="column">
          <Box flexDirection="row" justifyContent="space-between" paddingRight={RIGHT_MARGIN}>
            <Box flexDirection="row" gap={1}>
              <Text bold>foundry board</Text>
              {build && <Text key="version" dimColor>{build.version}</Text>}
              {stale && <Text key="stale" color="warning">· {stale}</Text>}
            </Box>
            <Box flexDirection="row" gap={1}>
              {keys && <Text dimColor>{keys} ·</Text>}
              <Button key="close" plain label={t('close')} onPress={() => $.ui.close({ id: PANE })} />
            </Box>
          </Box>
          <Text key="rule" dimColor>{'─'.repeat(Math.max(10, avail))}</Text>
        </Box>
      )
    }
    if (folded) {
      return (
        <Box flexDirection="column">
          {titleRow(true)}
          <Band m={model} Text={Text} Box={Box} hint={false} pc />
          <Box key="fold-counts">
            <Text dimColor>{[...model.sections.map(s => `${s.label} ${s.count}`), ...(model.archived_count ? [t('pill_archived', { n: model.archived_count })] : [])].join(' · ')}</Text>
          </Box>
        </Box>
      )
    }

    const approvalRow = (a: Approval) => (
      <Box key={`a-${a.rid}`} flexDirection="row" gap={1}>
        <Text color={(a.tier ?? 0) >= 3 ? 'error' : 'warning'}>
          T{a.tier ?? '?'} {a.unit ?? '-'} {a.tool ?? ''}: {(a.summary ?? '').slice(0, 60)}
        </Text>
        <Button key={`allow:${a.rid}`} label="allow" onPress={() => answer($, 'allow', a.rid)} />
        <Button key={`deny:${a.rid}`} label="deny" onPress={() => answer($, 'deny', a.rid)} />
      </Box>
    )
    const status = (
      <Box flexDirection="column">
        {err && <Text color="error">{err}</Text>}
        {res && <Box key="result"><Text color={resultOk(res) ? 'success' : 'error'}>{res}</Text></Box>}
      </Box>
    )
    const andon = (model.halts ?? []).map((halt, i) => (
      <Box key={`halt-${i}`} backgroundColor={PASTEL.alarm.bg}>
        <Text bold color={PASTEL.alarm.fg}>
          ANDON {halt.scope ?? '-'}: {halt.reason ?? ''} ({halt.by ?? '-'})
        </Text>
      </Box>
    ))

    // A stalled lead at the top, above everything: what the detector saw, and what recovery did or that it escalated.
    const stallBanner = model.sections.flatMap(s => s.rows).filter(r => r.stall).map(r => (
      <Box key={`stall-${r.id}`} flexDirection="column">
        <Box backgroundColor={PASTEL.alarm.bg} alignSelf="flex-start">
          <Text bold color={PASTEL.alarm.fg}>{t('stall_banner', { unit: r.id, text: r.stall!.text || r.stall!.kind })}</Text>
        </Box>
        {r.stall!.escalated ? <Text dimColor>{'│ '}{t('stall_escalated')}</Text>
          : r.stall!.action ? <Text dimColor>{'│ '}{t('stall_recovered', { action: r.stall!.action })}</Text> : null}
      </Box>
    ))

    const Hang = ({ head, depth = 1, children }: { head?: any; depth?: number; children?: any }) => (
      <Box flexDirection="row" paddingLeft={2 * depth}>
        {head && <Box flexShrink={0}>{head}</Box>}
        <Box flexDirection="column" flexGrow={1} flexShrink={1}>
          {children}
        </Box>
      </Box>
    )
    const glyphs: Glyphs = model.glyphs === 'nerd' ? 'nerd' : 'emoji'
    const rows = model.sections.flatMap(s => s.rows)
    const repos = knownRepos(model)
    // A bordered table: link segments are OSC 8 links; a row with a press is drawn as buttons (one per line, the
    // first keyed by `keys[row]`), whose text keeps the links' labels.
    const table = (k: string, w: number, head: string[] | null, trs: TCell[][], flex: number,
      press?: { keys: string[]; on: (i: number) => unknown }, sep?: boolean) => (
      <Box key={`tbl-${k}`} flexDirection="column">
        {tableLines(tableWidths(w, head, trs, flex), head, trs, sep).map((ln, j) => {
          const segs = ln.segs.map((x, n) =>
            x.href && !(press && ln.row >= 0)
              ? <Link key={`l${n}`} href={x.href} label={x.t} />
              : <Text key={`t${n}`} color={x.color} bold={x.bold} dimColor={x.dim}>{x.t}</Text>)
          return press && ln.row >= 0 ? (
            <Button key={ln.first ? press.keys[ln.row] : `${press.keys[ln.row]}:${j}`} plain onPress={() => press.on(ln.row)}>{segs}</Button>
          ) : (
            <Text key={`tbl-${k}-${j}`}>{segs}</Text>
          )
        })}
      </Box>
    )
    const prSeg = (ref: string, url?: string | null, home?: string | null): Seg => {
      const href = prHref(ref, repos, url, home)
      return { t: shortRef(ref), ...(href ? { href } : {}), bold: true }
    }
    const row: BoardRow | undefined = sel ? rows.find(r => r.id === sel) : undefined
    if (row) {
      const v = viewOf(row, t)
      const tlSel = await read($, timelineOpen)
      const more = (await read($, detailOpen)) === row.id
      const tone = PASTEL[STATUS_TONE[row.status] ?? 'others']
      // The same room as every other table: one more column and the right border wraps under the header rule.
      const dw = (e.props.bodyColumns ?? 100) - 3 - RIGHT_MARGIN
      const home = reposOf(row)[0] ?? null
      const H = ({ t }: { t: string }) => (
        <Box key={`h-${t}`} marginTop={1}>
          <Text bold>{t}</Text>
        </Box>
      )
      const sum = statusSummary(v, t)
      // Waiting on another team: the stopped hands are history, not something to act on.
      const othersWait = row.status === 'waiting_others' && v.waits.some(w => w.kind === 'team' || !!w.unit)
      // What it waits on: the unit carrying the work (its status and PRs, each a link), else the wait itself.
      const waitLines = v.waits.map((w): Seg[][] => {
        const lines: Seg[][] = []
        if (w.unit) {
          lines.push([{ t: w.unit, bold: true }, { t: ` (${w.unit_status_ko ?? w.unit_status ?? '-'})`, dim: true }])
          for (const p of w.unit_prs ?? []) lines.push([{ t: '· ', dim: true }, prSeg(p.ref, p.url, home), { t: ' ' }, ...prStateSegs(p, t)])
        } else {
          const ref = w.ref && /#\d+$/.test(w.ref) ? w.ref : null
          lines.push(ref ? [{ t: `${w.text.replace(w.ref!, '').trim()} `, color: w.stale ? 'warning' : undefined }, prSeg(ref, null, home)]
            : [{ t: w.text, color: w.stale ? 'warning' : undefined }])
        }
        lines[0]!.push({ t: ` · ${fmtAge(w.age_h)}`, dim: true })
        if (w.note) lines.push([{ t: w.note, dim: true }])
        if (w.stale) lines.push([{ t: `stale: ${w.stale_text ?? ''}`, color: 'warning' }])
        return lines
      })
      const prList = v.prs.length ? v.prs : (row.prs ?? []).map(ref => ({ ref, url: null }) as UnitView['prs'][number])
      const facts: [string, TCell][] = [
        [t('label_status'), [[{ t: sum.line, bold: true, color: STATUS_COLOR[row.status] }]]],
        ...(waitLines.length ? [[t('label_wait'), waitLines.flat()] as [string, TCell]] : []),
        ...(sum.blocked.length ? [[t('label_blocked'), [[{ t: sum.blocked.join(' / '), color: 'warning' }]]] as [string, TCell]] : []),
        ['repo', reposOf(row).length ? gitIcon(glyphs, 'repo') + reposOf(row).join(', ') : '-'],
        [t('label_ticket'), v.tracker ?? '-'],
        ...(v.prs.length ? [] : prList.length || !v.related_prs?.length
          ? [['PR', prList.length ? [prList.flatMap((p, i) => [...(i ? [{ t: ', ' }] : []), prSeg(p.ref, p.url, home)])] : '-'] as [string, TCell]]
          : [[t('label_related_pr'), [v.related_prs.flatMap((p, i) => [...(i ? [{ t: ', ' }] : []), prSeg(p.ref, p.url, home)])]] as [string, TCell]]),
        ...(row.session ? [[t('label_session'), `${row.session.name} (${row.session.state})`] as [string, TCell]] : []),
        ...(v.branch || v.worktree
          ? [['branch', `${gitIcon(glyphs, 'branch')}${v.branch ?? '-'}${v.worktree ? ` · ${shortPath(v.worktree, 60)}` : ''}`] as [string, TCell]] : []),
        ...(v.worktree_note ? [['worktree', [[{ t: v.worktree_note, color: v.worktree_kept ? 'warning' : undefined }]]] as [string, TCell]] : []),
        ...(v.chain ? [[t('label_chain'), v.chain] as [string, TCell]] : []),
        ...(row.held ? [[t('label_held'), [[{ t: row.held.reason ?? '-', color: 'warning' }]]] as [string, TCell]] : []),
        ...(rowFlags(row, t).length ? [[t('label_caution'), [rowFlags(row, t).map(f => ({ t: f + ' ', color: 'warning' }))]] as [string, TCell]] : []),
      ]
      // One row per PR: the ref (a link), GitHub's state and review mark (CI / conflict only when an open PR has
      // trouble), the title.
      const prTable = v.prs.map((p): TCell[] => [[[prSeg(p.ref, p.url, home)]], [prStateSegs(p, t)], p.title ?? ''])
      const failure = res && !resultOk(res) ? res : null
      const stopped = othersWait ? [] : v.hands_stopped_list ?? []
      // The stale state tail is stripped from the title; the original sits under ▸ 자세히.
      const origTitle = row.title_full && row.title_full !== row.title ? row.title_full : null
      const counts = [
        row.inbox.length && t('count_inbox', { n: row.inbox.length }), v.dispatcher.length && `dispatcher ${v.dispatcher.length}`,
        row.todos.length && t('count_steps', { n: row.todos.length }), v.timeline.length && t('count_timeline', { n: v.timeline.length }),
        stopped.length && t('count_stopped', { n: stopped.length }),
        (v.decisions ?? []).some(d => d.open && d.outline.length > DEC_LINES) && t('h_decision'),
        origTitle && t('label_title_full'),
      ].filter(Boolean)
      return (
        <Box flexDirection="column">
          {andon}
          <Box key="detail-head" flexDirection="row" gap={1} backgroundColor={tone.bg} paddingX={1}>
            <Button key="back" plain onPress={() => update($, selected, () => null)}>
              <Text color={tone.fg}>{'< back'}</Text>
            </Button>
            <Text bold color={tone.fg}>{row.id}</Text>
            <Box key="badge" backgroundColor={tone.fg} paddingX={1}>
              <Text bold color={tone.bg}>{statusWord(t, row.status)}</Text>
            </Box>
          </Box>
          <Text key="detail-title" bold>{row.title}</Text>
          {table('facts', dw, [t('th_field'), t('th_value')], facts.map(([k, val]) => [k, val]), 1)}
          {v.prs.length > 0 && <H t={`PR (${v.prs.length})`} />}
          {v.prs.length > 0 && table('prs', dw, ['PR', t('th_status'), t('th_title')], prTable, 2)}
          {v.approvals.length > 0 && <H t={t('h_approval_requests')} />}
          {v.approvals.map(a => (
            <Box key={`a-${a.rid}`} flexDirection="column" paddingLeft={2}>
              <Text color={(a.tier ?? 0) >= 3 ? 'error' : 'warning'}>T{a.tier ?? '?'} {a.command}</Text>
              <Box flexDirection="row" gap={1}>
                {a.ready ? (
                  <Button key={`allow:${a.rid}`} label="allow" onPress={() => answer($, 'allow', a.rid)} />
                ) : (
                  <Text key={`notyet:${a.rid}`} color="warning">{a.why ?? t('not_yet')}</Text>
                )}
                <Button key={`deny:${a.rid}`} label="deny" onPress={() => answer($, 'deny', a.rid)} />
              </Box>
            </Box>
          ))}
          {v.owner_todos.length > 0 && <H t={t('h_owner_todos')} />}
          {v.owner_todos.map((x, i) => (
            <Text key={`todo-${i}`}>  - {x}</Text>
          ))}
          {v.decisions ? (() => {
            // Open asks as an outline (the first lines, the rest under ▸ 자세히); answered ones as one line each.
            const open = v.decisions.filter(d => d.open)
            const done = v.decisions.filter(d => !d.open)
            return (
              <Box key="decisions" flexDirection="column">
                {open.length > 0 && <H t={t('h_decision')} />}
                {open.map(d => (
                  <Box key={`dec-${d.key}`} flexDirection="column" paddingLeft={2}>
                    <Text bold color="warning">{d.key}</Text>
                    {(more ? d.outline : d.outline.slice(0, DEC_LINES)).map((x, i) => (
                      <Text key={`o${i}`}>{'  '.repeat(x.depth + 1)}- {x.text}</Text>
                    ))}
                    {!more && d.outline.length > DEC_LINES && <Text dimColor>  {t('fold_more')}</Text>}
                  </Box>
                ))}
                {done.length > 0 && <H t={t('h_decisions_answered')} />}
                {done.map(d => (
                  <Text key={`dec-${d.key}`} dimColor>  {d.key} → {d.answer ?? '-'}{d.answered ? ` · ${d.answered}` : ''}</Text>
                ))}
              </Box>
            )
          })() : (
            <Box key="decisions" flexDirection="column">
              {row.decisions.length > 0 && <H t={t('h_decision')} />}
              {row.decisions.map(d => (
                <Text key={`dec-${d.key}`} color={d.answer === null ? 'warning' : undefined}>
                  {'  '}
                  {d.key}: {d.question ?? ''} → {d.answer ?? t('answer_open')}
                </Text>
              ))}
            </Box>
          )}
          {v.hands_running.length > 0 && <H t={t('h_hands_running')} />}
          {v.hands_running.map(hand => (
            <Text key={`hand-${hand.name}`}>
              {'  '}
              {hand.name ?? '?'} ({hand.role ?? '-'})
            </Text>
          ))}
          {failure && <Box key="result"><Text color="error">{failure}</Text></Box>}
          <Box key="actions" flexDirection="row" gap={2} marginTop={1}>
            {v.closed ? (
              <Button key="set:queue" label={t('btn_reopen')} variant="secondary" onPress={() => setStatus($, row.id, 'queue')} />
            ) : (
              <>
                <Button key="set:done" label={t('btn_done')} variant="primary" onPress={() => setStatus($, row.id, 'done')} />
                <Button key="set:parked" label={t('btn_parked')} variant="secondary" onPress={() => setStatus($, row.id, 'parked')} />
                <Button key="set:cancelled" label={t('btn_cancelled')} variant="secondary" onPress={() => setStatus($, row.id, 'cancelled')} />
              </>
            )}
          </Box>
          {counts.length > 0 && (
            <Box key="details-toggle" marginTop={1}>
              <Button key="details" plain onPress={() => update($, detailOpen, x => (x === row.id ? null : row.id))}>
                <Text dimColor>{more ? t('fold_less') : t('fold_more')} · {counts.join(' · ')}</Text>
              </Button>
            </Box>
          )}
          {more && origTitle && <Text key="title-full" dimColor>  {t('label_title_full')}: {origTitle}</Text>}
          {more && row.inbox.length > 0 && <H t={t('h_inbox')} />}
          {more && row.inbox.map((i, n) => (
            <Hang key={`inbox-${n}`} head={<Text dimColor>{(i.ts ?? '').slice(5, 16).replace('T', ' ')} </Text>}>
              <Text>{shortPaths(i.text)}</Text>
            </Hang>
          ))}
          {more && v.dispatcher.length > 0 && <H t={t('h_dispatcher')} />}
          {more && v.dispatcher.map((x, i) => (
            <Text key={`disp-${i}`}>  - {x}</Text>
          ))}
          {more && row.todos.length > 0 && <H t={t('h_steps')} />}
          {more && row.todos.map((x, i) => (
            <Text key={`step-${i}`} dimColor={x.status === 'completed'}>
              {'  '}[{x.status}] {x.subject ?? ''}
            </Text>
          ))}
          {more && stopped.length > 0 && <H t={t('h_hands_stopped')} />}
          {more && stopped.map((hd, i) => (
            <Text key={`stopped-${i}`} dimColor>
              {'  '}
              {hd.name ?? '?'} ({hd.role ?? '-'}) · {hd.state ?? 'stopped'} {(hd.ts ?? '').slice(5, 16).replace('T', ' ')}
            </Text>
          ))}
          {more && v.timeline.length > 0 && <H t={t('h_timeline')} />}
          {more && v.timeline.slice(0, 12).map((tl, i) => {
            const k = `${row.id}:${i}`
            return (
              <Box key={`tl-${i}`} flexDirection="column">
                <Button key={`tl:${i}`} plain onPress={() => update($, timelineOpen, x => (x === k ? null : k))}>
                  {'  '}
                  <Text dimColor>{(tl.ts ?? '').slice(5, 16).replace('T', ' ')}</Text>
                  {' '}
                  {noteTag(tl.gist).tag && <Text bold color={NOTE_COLOR[noteTag(tl.gist).kind] ?? 'subtle'}>{noteTag(tl.gist).tag}</Text>}
                  {noteTag(tl.gist).tag ? ' ' : ''}
                  {gistOf(noteTag(tl.gist).rest, tl.text)}
                </Button>
                {tlSel === k && tl.text !== tl.gist && <Hang depth={2}><Text dimColor>{tl.text}</Text></Hang>}
              </Box>
            )
          })}
        </Box>
      )
    }

    // A section: a mid-tone bar down its left edge, its heading as a pastel chip, its items indented one step.
    // Item lines hang: a fixed-width head (kind, unit) and a body whose wrapped rows stay under the body.
    const Section = ({ tone, title, children }: { tone: Tone; title: string; children?: any }) => (
      <Box key={`sec-${title}`} flexDirection="row" marginTop={1}>
        <Box width={1} flexShrink={0} backgroundColor={PASTEL[tone].bar} />
        <Box flexDirection="column" flexGrow={1} flexShrink={1} paddingLeft={1}>
          <Box alignSelf="flex-start" backgroundColor={PASTEL[tone].bg} paddingX={1}>
            <Text bold color={PASTEL[tone].fg}>{title}</Text>
          </Box>
          {children}
        </Box>
      </Box>
    )

    // The section's bar and indent take two columns and the row's leading space one; the right margin stays free.
    const width = (e.props.bodyColumns ?? 100) - 3 - RIGHT_MARGIN
    const unitCols = layout(UNIT_COLS(t), width)
    const escCols = layout(ESC_COLS(t), width, 16)
    const invCols = layout(INV_COLS(t), width)
    const decCols = layout(DEC_COLS(t), width)
    const ownCols = layout(OWN_COLS(t), width)
    const aprCols = layout(APR_COLS(t), width - APR_BUTTONS)
    const nextCols = layout(NEXT_COLS(t), width)
    const ageOf = (ts?: string | null) => ({ t: fmtAge(hoursSince(ts, model.generated)), dim: true })
    const oneLine = (x?: string | null) => shortPaths(x ?? '').replace(/\s+/g, ' ').trim()
    const Head = ({ cs, k }: { cs: (Col & { w: number })[]; k: string }) => (
      <Box key={`head-${k}`}>
        <Text dimColor>
          {' '}
          {cs.map(c => pad(c.h, c.w)).join(' '.repeat(GUTTER)).trimEnd()}
        </Text>
      </Box>
    )
    const cells = (cs: (Col & { w: number })[], vals: Record<string, Cell | undefined>) =>
      cs.flatMap((c, i) => {
        const raw = vals[c.k]
        const v = typeof raw === 'object' ? raw : { t: raw ?? '' }
        const padded = pad(v.sub ? `${v.t} ${v.sub}` : v.t, c.w)
        const head = padded.startsWith(v.t) ? v.t : padded
        const tail = padded.slice(head.length)
        const drawn = <Text color={v.color} backgroundColor={v.bg} bold={v.bold} dimColor={v.dim}>{head}</Text>
        return [
          i ? ' '.repeat(GUTTER) : '',
          v.href ? <Link href={v.href} label={head} /> : drawn,
          tail ? <Text color={v.subColor} dimColor={!v.subColor}>{i === cs.length - 1 ? tail.trimEnd() : tail}</Text> : '',
        ]
      })
    // A press on a PR row opens its page in the browser; the ref cell is also an OSC 8 link (cmd+click).
    const openUrl = (url?: string | null) => (openable(url) ? $.process.run(['open', url!], { timeoutMs: 5000 }) : undefined)
    // A table line that opens `url`: cells with href are OSC 8 links (cmd+click); the first other cell after the
    // first is a button whose Enter or click runs `open`. A Button holds only Text, so links sit in the Text around it.
    const linkedLine = (key: string, cs: (Col & { w: number })[], vals: Record<string, Cell | undefined>, url?: string, openKey = `open:${key}`) => {
      const parts = cells(cs, vals)
      const at = url ? cs.findIndex((c, i) => i > 0 && !(typeof vals[c.k] === 'object' && (vals[c.k] as any).href)) : -1
      const [before, press, after] = at < 0 ? [parts, [], []] : [parts.slice(0, at * 3 + 1), parts.slice(at * 3 + 1, at * 3 + 3), parts.slice(at * 3 + 3)]
      return (
        <Box key={key} flexDirection="row">
          <Text>{' '}{before}</Text>
          {press.length > 0 && <Button key={openKey} plain onPress={() => openUrl(url)}>{press}</Button>}
          {after.length > 0 && <Text>{after}</Text>}
        </Box>
      )
    }
    // A repo block's header: the GitHub mark (Nerd Font), the name bold on the repo's accent, then its counts.
    const repoHead = (b: RepoBlock, k: string) => (
      <Box key={`repohead-${k}-${b.repo}`} flexDirection="row" gap={1} marginTop={1}>
        <Text> </Text>
        <Box backgroundColor={b.accent.bg} paddingX={1}>
          <Text bold color={b.accent.fg}>{glyphs === 'nerd' ? `${GIT_ICON.github} ` : ''}{b.short}</Text>
        </Box>
        <Text dimColor>{b.counts}</Text>
      </Box>
    )
    const keep = (r: BoardRow) => repo === 'all' || reposOf(r).includes(repo)
    const repoNames = model.repo_names ?? [...new Set(rows.flatMap(reposOf))].sort()
    const prAll = prTabOf(model, t).filter(p => repo === 'all' || p.ref.startsWith(`${repo}#`))
    const tabs = fitTabs(repoNames, repo, avail, `PR (${prAll.length})`, t)
    const decisionsOpen = model.summary?.decisions ?? model.cards_open ?? (model.cards ?? []).length
    // A unit row: its title (the branch is foundry/<KEY>, the key again, so it stays in the detail view).
    const boardRow = (r: BoardRow, section?: string) => (
      <Button key={r.id} plain onPress={() => update($, selected, () => r.id)}>
        {' '}
        {cells(unitCols, {
          id: r.id, title: r.title || r.id, pr: { t: prOf(r), dim: true }, st: stateOf(r, t, section),
          age: { t: fmtAge(hoursSince(r.updated, model.generated)), dim: true },
        })}
      </Button>
    )
    const unitTable = (k: string, rs: BoardRow[], section?: string) =>
      [<Head key={`h-${k}`} k={k} cs={unitCols} />, ...rs.map(r => boardRow(r, section))]
    const units =
      view === 'repo'
        ? (repo === 'all' ? [...repoNames, '-'] : [repo]).map(name => {
            const rs = rows.filter(r => (name === '-' ? reposOf(r).length === 0 : reposOf(r).includes(name)))
            return rs.length === 0 ? null : (
              <Section key={`g-${name}`} tone="others" title={`${name} (${rs.length})`}>
                {unitTable(`g-${name}`, rs)}
              </Section>
            )
          })
        : model.sections.map(s => {
            // 오너 대기 lists summary.owner_wait's own union (board.py owner_items): one row per card, ask, owner
            // to-do or bare waiting_owner unit, so its header always equals the band's 오너 대기 pill.
            if (s.items)
              return (
                <Section key={`s-${s.key}`} tone={SECTION_TONE[s.key] ?? 'others'} title={`${s.label} (${s.items.length})`}>
                  {s.items.length === 0 ? (
                    <Text dimColor> -</Text>
                  ) : (
                    table(`own-${s.key}`, width, [t('th_unit'), t('th_kind'), t('th_content')], s.items.map((x): TCell[] => {
                      // state is `<kind label>: <what>` in ui.language, or the bare owner-check label for a unit
                      const what = x.kind === 'unit' ? '' : x.state.replace(/^[^:]*:\s*/, '')
                      return [x.unit ? shortUnit(x.unit) : '-', [[{ t: wordOf(t, 'own', x.kind) ?? x.kind, bold: true }]],
                        oneLine([what, x.title].filter(Boolean).join(' · ') || x.state)]
                    }), 2)
                  )}
                </Section>
              )
            const rs = s.rows.filter(keep)
            return (
              <Section key={`s-${s.key}`} tone={SECTION_TONE[s.key] ?? 'others'} title={`${s.label} (${rs.length})`}>
                {rs.length === 0 ? <Text dimColor> -</Text> : unitTable(`s-${s.key}`, rs, s.key)}
              </Section>
            )
          })

    // PR refs a decision's text names, as links (a ref the model cannot place stays text).
    const prRefsLine = (text: string, home: string | null | undefined, k: string) => {
      const refs = [...new Set(text.match(PR_REF_RE) ?? [])]
      return refs.length === 0 ? null : (
        <Text key={`prrefs-${k}`}>
          <Text dimColor>PR </Text>
          {refs.flatMap((r, i) => {
            const href = prHref(r, repos, null, home)
            return [i ? <Text key={`c${i}`}>, </Text> : null, href ? <Link key={`p${i}`} href={href} label={shortRef(r)} /> : <Text key={`p${i}`}>{shortRef(r)}</Text>]
          })}
        </Text>
      )
    }
    // A decision's text as bullets, one line each, items indented under their section; one line stays plain text.
    const outlineOf = (ol: OutlineLine[] | undefined, text: string, id: string) =>
      (ol ?? []).length <= 1 ? (
        <Text>{shortPaths(text)}</Text>
      ) : (
        <Box key={`outline-${id}`} flexDirection="column">
          {(ol ?? []).map((x, i) => (
            <Hang key={`ol-${id}-${i}`} depth={x.depth} head={<Text dimColor>{x.depth ? '◦ ' : '• '}</Text>}>
              <Text bold={!x.depth && !x.text.includes(':')}>{shortPaths(x.text)}</Text>
            </Hang>
          ))}
        </Box>
      )
    const cardDetail = (c: Card) => (
      <Box key={`card-detail-${c.id}`} flexDirection="column" paddingLeft={2}>
        <Text bold>
          {c.id}
          {c.repo ? ` (${c.repo})` : ''}
        </Text>
        {outlineOf(c.outline, c.question ?? '', `card-${c.id}`)}
        {prRefsLine(`${c.question ?? ''} ${c.context ?? ''}`, c.repo, `card-${c.id}`)}
        {c.reason && <Text dimColor>{c.reason}</Text>}
        <Text dimColor>{t('card_detail_cmd', { id: c.id })}</Text>
        {(c.options ?? []).length > 0 && table(`opt-${c.id}`, width - 2, [t('th_choice'), t('th_content'), t('th_pros_cons')], (c.options ?? []).map((o): TCell[] => [
          [[{ t: o.key === c.recommended ? t('option_recommended', { key: o.key }) : o.key, bold: o.key === c.recommended }]],
          [[{ t: o.label ?? '', bold: o.key === c.recommended }], ...(o.description ? [[{ t: o.description, dim: true }]] : []),
            ...(o.preview ? [[{ t: shortPaths(o.preview), dim: true }]] : [])],
          [...(o.pros ? [[{ t: '+ ', color: 'success' }, { t: joined(o.pros) }]] : []),
            ...(o.cons ? [[{ t: '- ', color: 'error' }, { t: joined(o.cons) }]] : [])],
        ]), 1)}
        {(c.options ?? []).length > 0 && (
          <Box key={`picks-${c.id}`} flexDirection="row" gap={1}>
            {(c.options ?? []).map(o => (
              <Button key={`pick:${c.id}:${o.key}`} label={o.key === c.recommended ? t('option_recommended', { key: o.key }) : o.key} onPress={() => pick($, c.id, o.key)} />
            ))}
          </Box>
        )}
        {(c.options ?? []).length > 0 && (
          <Text dimColor>
            {'  '}
            {t('chat_answer', { id: c.id, key: c.recommended ?? c.options?.[0]?.key ?? '' })}
          </Text>
        )}
      </Box>
    )

    const askDetail = (d: NonNullable<BoardModel['asks']>[number]) => (
      <Box key={`ask-detail-${d.key}`} flexDirection="column" paddingLeft={2}>
        <Text bold>{d.key}</Text>
        {outlineOf(d.outline, d.question, `ask-${d.key}`)}
        {prRefsLine(d.question, reposOf(rows.find(r => r.id === d.unit) ?? ({} as BoardRow))[0], `ask-${d.key}`)}
        <Text dimColor>/foundry decide answer {d.key} "…"</Text>
      </Box>
    )
    // 결정 필요: cards then asks, one bordered table; ⏎ on a row opens it under the table.
    const decItems = [
      ...(model.cards ?? []).filter(c => repo === 'all' || !c.repo || c.repo === repo)
        .map(c => ({ id: c.id, unit: c.unit, q: c.headline ?? c.question ?? '', ts: c.opened, detail: () => cardDetail(c) })),
      ...(model.asks ?? []).map(d => ({ id: d.key, unit: d.unit, q: d.headline ?? d.question, ts: d.asked, detail: () => askDetail(d) })),
    ]
    const decOpen = decItems.find(x => x.id === cardSel)
    // The request: the tool, then the summary unless the summary already names the tool.
    const readyOf = (rid: string) => rows.flatMap(r => r.view?.approvals ?? []).find(x => x.rid === rid)?.ready ?? true
    const reqOf = (a: Approval) =>
      !a.tool ? (a.summary ?? '') : !a.summary ? a.tool : a.summary.startsWith(`${a.tool}:`) ? a.summary : `${a.tool}: ${a.summary}`
    // An approval: unit, tier, the request on one line, its age, then [allow] [deny].
    const aprRow = (a: Approval) => (
      <Box key={`apr-${a.rid}`} flexDirection="row" gap={1}>
        <Text>
          {' '}
          {cells(aprCols, {
            unit: a.unit ?? '-', tier: { t: `T${a.tier ?? '?'}`, color: (a.tier ?? 0) >= 3 ? 'error' : 'warning', bold: true },
            req: oneLine(reqOf(a)), age: ageOf(a.asked),
          })}
          {a.queued ? <Text dimColor>{` ${t('approval_queued')}`}</Text> : ''}
        </Text>
        {readyOf(a.rid) ? (
          <Button key={`allow:${a.rid}`} label="allow" onPress={() => answer($, 'allow', a.rid)} />
        ) : (
          <Text key={`notyet:${a.rid}`} color="warning">{t('not_yet')}</Text>
        )}
        <Button key={`deny:${a.rid}`} label="deny" onPress={() => answer($, 'deny', a.rid)} />
      </Box>
    )
    // A tier-3 request as a card: why it is the owner's, the unit and the exact request, the evidence and the
    // advisor's line when someone attached them, the buttons and when the waiting hook denies and queues it.
    const aprCard = (a: Approval) => {
      const mins = a.auto_deny && !a.queued
        ? Math.max(0, Math.ceil((Date.parse(a.auto_deny) - Date.parse(model.generated)) / 60000)) : null
      return (
        <Box key={`aprc-${a.rid}`} flexDirection="column" borderStyle="round" borderColor={PASTEL.alarm.bar} paddingX={1}>
          <Text bold color={PASTEL.alarm.fg}>{t('apr_card_head', { tier: a.tier ?? 3, why: a.why ?? '-' })}</Text>
          <Text><Text bold>{a.unit ?? '-'}</Text>{'  '}{oneLine(reqOf(a))}</Text>
          {a.evidence ? <Text dimColor>{t('apr_evidence', { text: oneLine(a.evidence) })}</Text> : null}
          {a.advisor ? <Text>{t('apr_advisor', { text: oneLine(a.advisor) })}</Text> : null}
          <Box flexDirection="row" gap={1}>
            {readyOf(a.rid) ? (
              <Button key={`allow:${a.rid}`} label="allow" onPress={() => answer($, 'allow', a.rid)} />
            ) : (
              <Text key={`notyet:${a.rid}`} color="warning">{t('not_yet')}</Text>
            )}
            <Button key={`deny:${a.rid}`} label="deny" onPress={() => answer($, 'deny', a.rid)} />
            {a.queued ? <Text dimColor>{t('approval_queued')}</Text>
              : mins != null ? <Text dimColor>{t('apr_auto_deny', { n: mins })}</Text> : null}
          </Box>
        </Box>
      )
    }
    const nextRow = (n: NonNullable<BoardModel['next']>[number]) => (
      <Box key={`next-${n.unit}`}>
        <Text>
          {' '}
          {cells(nextCols, {
            unit: n.unit, repo: { t: (n.repo ?? '-').split('/').pop()!, dim: true }, title: oneLine(n.title || n.unit),
            pri: n.priority == null ? { t: '—', dim: true } : `P${n.priority}`, age: ageOf(n.created),
          })}
        </Text>
      </Box>
    )

    const inv = model.inventory
    const invAll = Object.entries(inv?.repos ?? {}).filter(([name]) => repo === 'all' || name === repo)
    const invRepos = invAll.map(([n, its]) => [n, its.filter(it => it.kind !== 'pr')] as const).filter(([, its]) => its.length > 0)
    const prRows = prAll
    const revMax = Math.max(0, ...(inv?.reviewers ?? []).map(x => x.mine ?? x.pending))
    const barCols = layout(BAR_COLS, width, 6)
    // Two columns stay for a reviewer's ⚠ after the bar.
    const barW = Math.min(BAR_MAX, barCols[2]!.w - 2)
    const barRow = (key: string, label: string, n: number, max: number, color: string, warn = false) => (
      <Box key={`bar-${key}`}>
        <Text>
          {' '}
          {cells(barCols, {
            label, n: { t: String(n).padStart(3), bold: warn },
            bar: { t: bar(n, max, barW), color, sub: warn ? ICONS[glyphs].warn : '', subColor: color },
          })}
        </Text>
      </Box>
    )
    // 내 의뢰 (the owner's PRs asking this person) is the bar; 참고 (their other direct requests) trails it, dim.
    const revW = Math.max(4, barW - 9)
    const revRow = (x: ReviewerLoad) => {
      const color = x.warn ? levelColor('warn') : PASTEL.stock.bar
      return (
        <Box key={`bar-rev-${x.login}`}>
          <Text>
            {' '}
            {cells(barCols, {
              label: x.login, n: { t: String(x.mine ?? x.pending).padStart(3), bold: x.warn },
              bar: { t: bar(x.mine ?? x.pending, revMax, revW), color,
                     sub: `${x.warn ? ICONS[glyphs].warn + ' ' : ''}${t('reviewer_others', { n: x.others ?? '-' })}`,
                     subColor: x.warn ? color : undefined },
            })}
          </Text>
        </Box>
      )
    }
    const invRow = (name: string) => (it: InventoryItem, i: number) => {
      const num = it.kind === 'pr' ? it.ref.split('#').pop() : null
      const base = it.ref.split('/').filter(Boolean).pop()
      const ref = it.kind === 'pr' ? `#${num} ${it.title ?? it.unit ?? ''}` :
        it.kind === 'worktree' && it.unit && base === it.unit ? it.unit : shortPath(it.ref)
      const st: Cell = it.kind === 'pr'
        ? staleCell(ghCell(glyphs, it.gh ?? ghOfStage(it.stage), it.gh_review, wordOf(t, 'prstate', it.stage), false, t), it.stale)
        : it.kind === 'branch'
          ? it.gh ? ghCell(glyphs, it.gh, it.gh_review, `${wordOf(t, 'gh', it.gh) ?? it.gh} ${it.pr ? '#' + it.pr.split('#').pop() : ''}`.trim(), false, t) : { t: t('no_pr'), dim: true }
          : wtState(it, t) ?? { t: wordOf(t, 'waiton', it.waits_on) ?? (it.waits_on ?? '—') }
      if (it.kind === 'branch' && typeof st === 'object' && it.ahead) st.sub = `${st.sub ? st.sub + ' ' : ''}+${it.ahead}`
      // A branch's (or worktree's) PR opens from the row: its state cell links to it, the row's press opens it.
      const url = it.kind !== 'pr' && it.pr ? prHref(it.pr, repos, it.url, name) : undefined
      // The ref cell is the link (the state cell keeps GitHub's colours); a worktree also names its PR's number.
      const stc: Cell = url && it.kind === 'worktree' && typeof st === 'object'
        ? { ...st, sub: `${st.sub ? st.sub + ' ' : ''}#${it.pr!.split('#').pop()}` } : st
      return linkedLine(`inv-${it.ref}-${i}`, invCols, { kind: invKind(glyphs, it.kind, it.stage, t), ref: url ? { t: ref, href: url } : ref, age: ageCell(it.age_h), st: stc }, url)
    }
    const escRow = (x: Escalation) => (
      <Box key={`esc-${x.id}`} flexDirection="column">
        <Button key={`esc:${x.id}`} plain onPress={() => update($, escOpen, v => (v === x.id ? null : x.id))}>
          {' '}
          {cells(escCols, { kind: { t: x.kind, bold: true }, unit: x.unit ?? '-', text: shortPaths(x.text).replace(/\s+/g, ' ') })}
        </Button>
        {escSel === x.id && <Hang depth={1}><Text dimColor>{shortPaths(x.text)}</Text></Hang>}
      </Box>
    )
    // 인계: the 08:00 snapshot as sections of `unit — one line`; 결정 대기 is board.py's live count, never the
    // snapshot's. A line opens its unit's detail, where the full text lives; the raw markdown is never drawn.
    const handover = (ho: Handover) => {
      const meta = [ho.at && t('handover_at', { at: ho.at }), ho.changes ? t('handover_changes', { n: ho.changes }) : null].filter(Boolean).join(' · ')
      return (
        <Section key="handover" tone="others" title={meta ? t('handover_title', { meta }) : t('handover')}>
          {(ho.sections ?? []).map(sec => (
            <Box key={`ho-${sec.title}`} flexDirection="column">
              <Text bold>
                {' '}
                {sec.title} <Text dimColor>{sec.live ? t('handover_live', { n: sec.count }) : String(sec.count)}</Text>
              </Text>
              {sec.items.map((x, i) => (
                <Button key={`ho:${sec.title}:${i}`} plain onPress={() => x.unit && update($, selected, () => x.unit)}>
                  {'   '}
                  {x.unit ? <Text bold>{x.unit}</Text> : null}
                  {x.unit ? <Text dimColor>{' — '}</Text> : null}
                  {oneLine(x.line)}
                </Button>
              ))}
              {sec.link === 'pr' && (
                <Button key={`ho:${sec.title}:more`} plain onPress={() => update($, viewAtom, () => 'pr' as View)}>
                  {'   '}
                  {sec.more ? <Text dimColor>{t('more_n', { n: sec.more })} · </Text> : null}
                  <Text color="suggestion">{t('pr_tab_all')}</Text>
                </Button>
              )}
            </Box>
          ))}
        </Section>
      )
    }
    const near = model.escalations.filter(x => x.kind === 'nearmiss')
    const loud = model.escalations.filter(x => x.kind !== 'nearmiss')
    const prCols = layout(PR_COLS(t), width)
    const prSum = prSummaryOf(model, prRows, t)
    const blocks = repoBlocksOf(model, prAll, t).filter(x => repo === 'all' || x.repo === repo)
    const bucketMax = Math.max(0, ...prSum.buckets.map(x => x.n))
    // A PR row: the ref and the unit are OSC 8 links (cmd+click opens GitHub / Jira); the title is a button whose
    // Enter or click opens the PR with `open`. A Button holds only Text, so the links sit in the Text around it.
    const prRow = (p: PrTabRow) => {
      const refW = prCols.find(c => c.k === 'ref')?.w ?? 16
      const icon = gitIcon(glyphs, 'pr')
      const short = p.short ?? p.ref.split('/').pop()!
      const url = prHref(p.ref, repos, p.url)
      const vals: Record<string, Exclude<Cell, string>> = {
        ref: { t: icon + fitRef(short, refW - cols(icon)), href: url },
        title: { t: oneLine(p.title || p.unit || '') },
        unit: { t: p.unit ?? '-', dim: !p.unit_url, href: openable(p.unit_url) ? p.unit_url! : undefined },
        st: staleCell(ghCell(glyphs, p.gh ?? ghOfStage(p.state), p.gh_review, p.state_ko, true, t), p.stale),
        rev: { t: p.reviewer, bold: p.reviewer !== '-', sub: (p.reviewer_auto ?? []).join(', ') },
        age: ageCell(p.age_h),
        act: { t: p.action, sub: `(${p.who_ko})` },
      }
      return linkedLine(`pr-${p.ref}`, prCols, vals, url, `open:${p.ref}`)
    }
    const prTab = (
      <Section key="pr-tab" tone="stock" title={`PR (${prRows.length})`}>
        <Box key="pr-sum"><Text bold> {prSum.text}{prSum.stale ? <Text color="warning"> · {t('stale_n', { n: prSum.stale })}</Text> : null}</Text></Box>
        {prSum.buckets.map(x => barRow(`stage-${x.key}`, x.label, x.n, bucketMax, PASTEL.stock.bar))}
        {(inv?.reviewers ?? []).length > 0 && <Text key="rev-head" bold> {t('reviewer_load')}</Text>}
        {(inv?.reviewers ?? []).length > 0 && <Text key="rev-legend" dimColor>   {t('reviewer_legend')}</Text>}
        {(inv?.reviewers ?? []).map(revRow)}
        {prRows.length === 0 && <Text dimColor> -</Text>}
        {blocks.filter(x => prRows.some(p => p.ref.startsWith(`${x.repo}#`))).map(x => (
          <Box key={`prblock-${x.repo}`} flexDirection="column">
            {repoHead(x, 'pr')}
            <Head k={`pr-${x.repo}`} cs={prCols} />
            {prRows.filter(p => p.ref.startsWith(`${x.repo}#`)).map(prRow)}
          </Box>
        ))}
      </Section>
    )
    const mon = model.monitor
    const tiles: Tile[] = met?.tiles ?? []
    const setHours = async (hrs: Hours) => {
      await update($, loadHours, () => hrs)
      await loadMetrics($, hrs)
    }

    return (
      <Box flexDirection="column">
        {andon}
        {stallBanner}
        {titleRow(false)}
        <Box key="tabs" flexDirection="row" gap={1}>
          <Button key="view:status" plain onPress={() => update($, viewAtom, () => 'status' as View)}>
            <Text bold={view === 'status'} dimColor={view !== 'status'}>{t('tab_status')}</Text>
          </Button>
          <Button key="view:repo" plain onPress={() => update($, viewAtom, () => 'repo' as View)}>
            <Text bold={view === 'repo'} dimColor={view !== 'repo'}>{t('tab_repo')}</Text>
          </Button>
          <Button key="view:pr" plain onPress={() => update($, viewAtom, () => 'pr' as View)}>
            <Text bold={view === 'pr'} dimColor={view !== 'pr'}>PR ({prRows.length})</Text>
          </Button>
          <Text dimColor>|</Text>
          {tabs.shown.map(n => (
            <Button key={`repo:${n}`} plain onPress={() => update($, repoAtom, () => n)}>
              {n === 'all'
                ? <Text bold={repo === n} dimColor={repo !== n}>{tabs.label(n)}</Text>
                : <Text bold={repo === n} color={repoAccent(n, model.repo_accents).fg} backgroundColor={repoAccent(n, model.repo_accents).bg}>{repo === n ? `[${tabs.label(n)}]` : tabs.label(n)}</Text>}
            </Button>
          ))}
          {tabs.more > 0 && <Text key="tabs-more" dimColor>+{tabs.more}</Text>}
        </Box>
        {view === 'pr' && prTab}
        {view !== 'pr' && mon && (
          <Section tone={LEVEL_TONE[mon.status] ?? 'alarm'} title={t('pc_load', { level: wordOf(t, 'level', mon.status) ?? mon.status })}>
            <Box key="pc-body" flexDirection="column" paddingLeft={1}>
            <Box flexDirection="row" gap={1}>
              {([3, 6, 24] as const).map(x => (
                  <Button key={`hours:${x}`} plain onPress={() => setHours(x)}>
                    <Text bold={hours === x} dimColor={hours !== x}>{hours === x ? `[${x}h]` : `${x}h`}</Text>
                  </Button>
                ))}
              {mon.last_sample_age_s != null && <Text dimColor>{t('sample_age', { n: mon.last_sample_age_s })}</Text>}
            </Box>
            {tiles.length === 0 && <Text dimColor>{t('metrics_loading')}</Text>}
            {// The section's bar, indent and the rows' leading column take three of the pane's columns.
              chunk(tiles, tileColumns((e.props.bodyColumns ?? 0) - 3 - RIGHT_MARGIN, tiles.length)).map((line, li) => (
                <Box key={`tiles-${li}`} flexDirection="row" gap={TILE_GAP}>
                  {line.map(tile => (
                    <Box key={`tile-${tile.name}`} flexDirection="column" width={TILE_W} flexShrink={0}>
                      <Text>
                        {pad(tileLabel(t, tile.name), 9)} {pad(tileValue(tile.name, tile.value, t, tile.extra), 10)}{' '}
                        <Text bold color={levelColor(tile.status)}>
                          {wordOf(t, 'level', tile.status) ?? '-'}
                        </Text>
                      </Text>
                      <Text dimColor>
                        {tile.peak != null ? `${tile.worse === 'low' ? 'low' : 'peak'} ${tile.peak} ${hhmm(tile.peak_ts)}` : ' '}
                      </Text>
                      <Text>
                        {runs(tile.glyphs ?? '', tile.levels ?? []).map(r => (
                          <Text color={r.lv ? levelColor(r.lv) : undefined} dimColor={!r.lv}>
                            {r.s}
                          </Text>
                        ))}
                      </Text>
                    </Box>
                  ))}
                </Box>
              ))}
            {mon.leads && (
              <Text>
                {t('leads', { live: mon.leads.live, cap: mon.leads.cap })}{' '}
                <Text bold color={mon.leads.launch ? levelColor('ok') : levelColor('danger')}>
                  {mon.leads.launch ? t('launch_allowed') : t('launch_held')}
                </Text>
              </Text>
            )}
            {mon.collecting ? (
              met && <Text dimColor>{t('metrics_note', { min: met.bucket_min, n: met.samples })}</Text>
            ) : (
              <Box flexDirection="column">
                <Text bold color="error">{t('collect_stopped')}</Text>
                <Text dimColor>{t('collect_restart', { at: hhmm(mon.last_ts) })}</Text>
              </Box>
            )}
            </Box>
          </Section>
        )}
        {view !== 'pr' && (
        <Box key="main-tab" flexDirection="column">
        {decisionsOpen > 0 && (
          <Section tone="decide" title={t('sec_decisions', { n: decisionsOpen })}>
            {table('dec', width, [t('th_unit'), t('th_question'), t('th_age')], decItems.map((x): TCell[] => [x.unit ? shortUnit(x.unit) : '-', oneLine(x.q), ageOf(x.ts).t]), 1,
              { keys: decItems.map(x => `dec:${x.id}`), on: i => update($, cardOpen, v => (v === decItems[i]!.id ? null : decItems[i]!.id)) })}
            {decOpen && decOpen.detail()}
          </Section>
        )}
        {(model.releases ?? []).length > 0 && (
          <Section tone="run" title={t('sec_releases', { n: model.releases!.length })}>
            {table('rel', width, [t('th_unit'), t('th_stage'), 'PR', t('th_tag')], model.releases!.map((x): TCell[] =>
              [shortUnit(x.unit), x.stage_ko, String(x.prs.length), x.tag ?? '-']), 1)}
          </Section>
        )}
        {model.approvals.length > 0 && (
          <Section tone="approve" title={t('sec_approvals', { n: model.approvals.length })}>
            {model.approvals.filter(a => (a.tier ?? 0) >= 3).map(aprCard)}
            {model.approvals.some(a => (a.tier ?? 0) < 3) && <Head k="apr" cs={aprCols} />}
            {model.approvals.filter(a => (a.tier ?? 0) < 3).map(aprRow)}
          </Section>
        )}
        {status}
        {(model.next ?? []).length > 0 && (
          <Section tone="next" title={t('sec_next')}>
            <Head k="next" cs={nextCols} />
            {(model.next ?? []).map(nextRow)}
          </Section>
        )}
        {model.escalations.length > 0 && (
          <Section tone="caution" title={t('sec_escalations', { n: model.escalations.length })}>
            <Head k="esc" cs={escCols} />
            {loud.slice(0, 8).map(escRow)}
            {near.length > 1 && (
              <Button key="esc:nearmiss" plain onPress={() => update($, nearOpen, v => !v)}>
                {' '}
                {cells(escCols, {
                  kind: { t: 'nearmiss', bold: true }, unit: near[0]!.unit ?? '-',
                  text: { t: t('nearmiss_summary', { n: near.length, unit: near[0]!.unit ?? '-' }), sub: nearUnfold ? t('fold_less') : t('unfold_enter') },
                })}
              </Button>
            )}
            {(near.length === 1 || nearUnfold) && near.map(escRow)}
          </Section>
        )}
        {units}
        {(invRepos.length > 0 || prRows.length > 0) && (
          <Section tone="stock" title={t('sec_inventory')}>
            {prRows.length > 0 && (
              <Button key="pr-pointer" plain onPress={() => update($, viewAtom, () => 'pr' as View)}>
                {' '}{prSum.text} · <Text color="suggestion">{t('pr_tab_view')}</Text>
              </Button>
            )}
            {invRepos.map(([name, items]) => (
              <Box key={`inv-${name}`} flexDirection="column">
                {repoHead(blocks.find(x => x.repo === name) ?? repoBlocksOf({ ...model, repo_blocks: undefined }, [], t).find(x => x.repo === name)!, 'inv')}
                <Head k={`inv-${name}`} cs={invCols} />
                {items.map(invRow(name))}
              </Box>
            ))}
          </Section>
        )}
        {model.handover?.text && handover(model.handover)}
        {model.kaizen && (
          <Section key="kaizen" tone="done" title={t('sec_kaizen', { at: model.kaizen.at ?? '-' })}>
            {model.kaizen.lines.map((x, i) =>
              x.head ? <Text key={`kz-${i}`} bold>{' '}{x.text}</Text> : <Text key={`kz-${i}`}>{'   • '}{oneLine(x.text)}</Text>)}
          </Section>
        )}
        {(model.cost_usd ?? 0) > 0 && <Text dimColor>{t('cost', { usd: model.cost_usd!.toFixed(2) })}</Text>}
        {model.intake_status && <Text dimColor>{t('intake_line', { ...model.intake_status })}</Text>}
        </Box>
        )}
        <Box flexDirection="row" gap={1} marginTop={1}>
          {model.foreman_restart && <Text color="warning">{t('foreman_restart')}</Text>}
          <Text dimColor>{t('updated_legend', { at: model.generated.slice(11, 19) })}</Text>
          <Button key="refresh" plain dimColor onPress={() => refresh($)}>
            {t('refresh')}
          </Button>
        </Box>
      </Box>
    )
  })
}
