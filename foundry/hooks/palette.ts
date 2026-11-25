// Pastel tones for the band pills and the board's section chips: a soft background with text in a darker shade of
// the same hue (`bg`/`fg`, every pair ≥ 5:1, WCAG AA asks 4.5:1), and a mid-tone
// `bar` for a section's left edge, ≥ 3:1 against both black and white so it reads on light and dark themes. A pill
// carries its own background, so its text keeps that contrast on either theme. Fixed hex: no theme key is pastel.
// Without truecolor (tmux without RGB) the engine rounds each channel to round(x / 51) of the 256-color cube: the
// soft backgrounds merge there, but every text color lands on its own cell, so the tones still differ.
export const PASTEL = {
  alarm: { bg: '#F8E1DE', fg: '#A8291F', bar: '#D2575A' }, // ANDON, foreman down, PC high
  run: { bg: '#DEF0E4', fg: '#24704A', bar: '#2E9A7E' }, // 진행
  owner: { bg: '#F6ECD2', fg: '#8A5A0C', bar: '#C7792E' }, // 오너 대기 / 오너 확인
  approve: { bg: '#F6E0F0', fg: '#9A2E7A', bar: '#C25A9C' }, // 승인
  decide: { bg: '#E6E0F8', fg: '#5A40A8', bar: '#8670D0' }, // 결정
  caution: { bg: '#F4F0C4', fg: '#6E5A06', bar: '#A58A12' }, // PC warn, escalations
  next: { bg: '#DFE9F6', fg: '#2B5E9E', bar: '#3F88C9' }, // 다음 작업
  done: { bg: '#E4F2CF', fg: '#3E7010', bar: '#5E9A3A' }, // 완료
  others: { bg: '#E4E7EA', fg: '#475260', bar: '#7A8798' }, // 타인 대기, repo groups
  queue: { bg: '#EEE5D3', fg: '#74552B', bar: '#A08458' }, // queue
  stock: { bg: '#D8EFF2', fg: '#14646F', bar: '#2F95AD' }, // 재고
} as const

export type Tone = keyof typeof PASTEL

// Board section key → tone. Unknown keys fall back to `others`.
export const SECTION_TONE: Record<string, Tone> = {
  done: 'done',
  in_progress: 'run',
  waiting_others: 'others',
  waiting_owner: 'owner',
  queue: 'queue',
}

// Unit status → tone: the detail header's badge and the board's status cells. Done green, running blue,
// waiting yellow, blocked red; the rest grey.
export const STATUS_TONE: Record<string, Tone> = {
  done: 'done',
  in_progress: 'next',
  waiting_owner: 'owner',
  waiting_others: 'owner',
  blocked: 'alarm',
  queue: 'queue',
  parked: 'others',
  cancelled: 'others',
}

// Timeline kinds: a note's leading `[report]` / `[lead]` / `intake` tag. Drawn on the terminal's own background,
// so theme keys, which the light and dark themes each resolve to a readable shade, never a fixed pastel.
export const NOTE_COLOR: Record<string, string> = {
  report: 'success', lead: 'suggestion', intake: 'permission', owner: 'warning', foreman: 'merged',
}

const channel = (x: number) => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)
const luminance = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map(i => channel(parseInt(hex.slice(i, i + 2), 16) / 255))
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi! + 0.05) / (lo! + 0.05)
}

// GitHub's PR colours, everywhere a PR or a branch's PR shows (재고, PR tab, unit detail): open green, draft grey,
// merged purple, closed red, and the review accent (approved green ✓, changes requested red ✗, pending yellow ●).
// Theme keys, so the person's light or dark theme resolves each to a readable shade; board.html carries the same
// map as Primer hex values with a contrast test (board.py GH_LIGHT / GH_DARK). The words are the pane catalog's
// (`gh_<state>`, `review_<state>`).
export const GH_COLOR: Record<string, string> = { open: 'success', draft: 'inactive', merged: 'merged', closed: 'error' }
export const GH_REVIEW: Record<string, { color: string; mark: string }> = {
  approved: { color: 'success', mark: '✓' },
  changes: { color: 'error', mark: '✗' },
  pending: { color: 'warning', mark: '●' },
}
// nf-oct-git_pull_request / git_pull_request_draft / git_merge / git_pull_request_closed
export const GH_NERD: Record<string, string> = { open: '', draft: '', merged: '', closed: '' }

// A repo's accent: the same list and hash as views.py REPO_ACCENT / repo_hash, so a repo has one colour in the
// pane's block headers and filter tabs and in board.html. Soft background, darker text of its hue, every pair ≥ 4.5:1.
export const REPO_ACCENT: readonly { bg: string; fg: string }[] = [
  { bg: '#DFE9F6', fg: '#2B5E9E' }, { bg: '#DEF0E4', fg: '#24704A' }, { bg: '#E6E0F8', fg: '#5A40A8' },
  { bg: '#F6E0F0', fg: '#9A2E7A' }, { bg: '#D8EFF2', fg: '#14646F' }, { bg: '#EEE5D3', fg: '#74552B' },
  { bg: '#F4F0C4', fg: '#6E5A06' }, { bg: '#FBE3D0', fg: '#8F4A12' },
]
// djb2 over UTF-16 code units, unsigned 32-bit.
export function repoHash(name: string): number {
  let h = 5381
  for (let i = 0; i < name.length; i++) h = (Math.imul(h, 33) + name.charCodeAt(i)) >>> 0
  return h
}
export type Accent = { bg: string; fg: string }
// A registered repo's accent comes from the model (views.accent_map: registration order, config override); the hash
// is the fallback for a repo the registry does not name.
export const repoAccent = (name: string, accents?: Record<string, Accent>): Accent =>
  accents?.[name] ?? accents?.[name.split('/').pop()!] ?? REPO_ACCENT[repoHash(name) % REPO_ACCENT.length]!
