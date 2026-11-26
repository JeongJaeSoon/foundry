export type ColumnKey = 'in_progress' | 'waiting_owner' | 'waiting_others' | 'queue' | 'release_wait' | 'released' | 'done' | 'cancelled'

export type Approval = {
  rid: string
  unit: string | null
  role: string | null
  tool: string | null
  summary: string | null
  tier: number | null
  asked: string | null
  session: string | null
  /** The asking hook gave up and denied; an answer still applies once on the session's next identical request. */
  queued?: boolean
  /** Tier 3: why it is the owner's (the owner-only rule's class, or "foreman session"). */
  why?: string | null
  /** `foundry approve note`: facts the request rests on, and the foreman's or advisor's one line. */
  evidence?: string | null
  advisor?: string | null
  /** When the waiting hook denies and queues it (absent once queued). */
  auto_deny?: string | null
}

export type BoardRow = {
  id: string
  title: string
  /** the original title when `title` dropped a trailing state the unit's status has overtaken */
  title_full?: string
  kind: string
  status: string
  /** board.py unit_state: the group the row sits in and its state cell (a closed unit's names only its PRs) */
  group?: ColumnKey
  state?: { group: ColumnKey; label: string; detail: string | null }
  closed?: boolean
  repo: string | null
  lead: string | null
  summary: string
  updated: string | null
  held: { reason: string | null; ts: string | null } | null
  session: { name: string; state: string; role: string | null; bg_id: string | null; model: string | null; updated: string | null } | null
  agent: { id: string | null; status: string | null }
  tracker: string | null
  worktree: { repo: string | null; path: string | null; branch: string | null } | null
  keys: { ticket: string[]; pr: string[]; topic: string[] }
  tickets: string[]
  prs: string[]
  note: string
  waits: { wid: string; kind: string; ref: string | null; until: string | null; note: string | null }[]
  inbox: { iid: string; ts: string; text: string }[]
  decisions: { key: string; question: string | null; answer: string | null }[]
  open_decisions: { key: string; question: string | null }[]
  approvals: Approval[]
  todos: { subject: string | null; status: string }[]
  notes: { ts: string; text: string }[]
  repos?: string[]
  blocked_by?: string[]
  chain?: { upstream?: string[]; downstream?: string[] } | string[] | null
  stall?: { kind: string; text: string; action?: string | null; escalated?: boolean } | null
  sync_lag_min?: number | null
  sync_text?: string | null
  cost_usd?: number | null
  mode?: string
  mismatch?: string | null
  /** A waiting_* unit nothing will move: 대기 대상 없음, 감시 안 되는 대기, 결정 대상 없음. */
  unwatched?: string | null
  hands?: Hand[]
  pr_items?: { repo?: string; number?: number | string; url?: string; state?: string; released?: string | null }[]
  /** The detail view's owner-facing summary (scripts/views.py); absent from an older board.py. */
  view?: UnitView
}

export type UnitView = {
  status_line: string
  next: string
  who: string
  who_ko: string
  blocked: string[]
  tracker: string | null
  chain: string | null
  worktree: string | null
  branch: string | null
  worktree_note?: string | null
  /** this unit's decision asks: open ones as an outline, answered ones as `key → answer (MM-DD HH:MM)` */
  decisions?: { key: string; open: boolean; answer: string | null; answered: string | null; outline: { depth: number; text: string }[] }[]
  /** done or cancelled: only open asks show, and the status buttons give way to one reopen button */
  closed?: boolean
  /** PRs named in the unit's notes and questions when none is recorded */
  related_prs?: { ref: string; url: string | null }[]
  /** the worktree stays (worktree_note says why), in any ui.language */
  worktree_kept?: boolean
  prs: { ref: string; url: string | null; title: string | null; state: string | null; head: string | null; ci: string | null;
    review: string | null; merge_state: string | null; reviewers: string[]; pick?: string | null;
    gh?: string | null; gh_review?: string | null }[]
  waits: { kind: string; ref: string | null; text: string; note: string | null; age_h: number | null; stale: boolean; stale_text: string | null
    /** the open unit a team wait is waiting on (views.wait_unit), with its status and PRs */
    unit?: string | null; unit_status?: string | null; unit_status_ko?: string | null; unit_prs?: PrBrief[] }[]
  approvals: { rid: string; tier: number | null; command: string; ready: boolean; why: string | null }[]
  owner_todos: string[]
  dispatcher: string[]
  timeline: { ts: string | null; gist: string; text: string }[]
  hands_running: Hand[]
  hands_stopped: number
  hands_stopped_list?: { name: string | null; role: string | null; state: string | null; ts: string | null }[]
}

/** A PR as a one-liner: ref, page, GitHub's state words, and CI / mergeable only to flag trouble. */
export type PrBrief = { ref: string; url: string | null; title: string | null; gh?: string | null; gh_review?: string | null
  ci?: string | null; merge_state?: string | null }

/** release.progress: an open release unit's stage (태그 → dev → prod), its PRs and newest deploy tag */
export type ReleaseProgress = { unit: string; repo: string | null; prs: string[]; released: number; stage: string
  stage_ko: string; tag: string | null; dev: string | null; prod: string | null; status: string }

export type PrTabRow = {
  ref: string
  /** a merged PR waiting on a release: the carrying release unit's stage and id, or 릴리스 unit 없음 */
  release?: string | null
  title: string
  unit: string | null
  state: string
  state_ko: string
  reviewer: string
  age_h: number | null
  stale: boolean
  action: string
  who: string
  who_ko: string
  /** review.pick's reason, e.g. 선정: CODEOWNERS + 최근 리뷰 4건 · 내 의뢰 1 */
  pick?: string | null
  gh?: string | null
  gh_review?: string | null
  /** CODEOWNERS / team / bot requests, drawn dim beside the asked reviewers, never counted */
  reviewer_auto?: string[]
  /** the PR's GitHub page */
  url?: string | null
  repo?: string
  /** `repo#number`, the owner dropped */
  short?: string
  /** the unit's Jira page when config jira_base is set */
  unit_url?: string | null
}

/** views.pr_counts: the PR counts every surface shows */
export type PrSummary = { total: number; stale: number; buckets: { key: string; label: string; n: number }[]; text: string }
/** views.repo_blocks: one block per repo, its accent and 「PR 3 · 브랜치 5 · 워크트리 6」 */
export type RepoBlock = {
  repo: string; short: string; accent: { bg: string; fg: string }; counts: string; pr: number; branch: number; worktree: number
}

export type Hand = { name: string | null; role: string | null; state: string | null }

export type CardOption = {
  key: string
  label?: string | null
  description?: string | null
  pros?: string | string[] | null
  cons?: string | string[] | null
  preview?: string | null
}

// A decision's text as bullets (개조식), built by views.outline: depth 0 is a section line, 1+ its items.
export type OutlineLine = { depth: number; text: string }

export type Card = {
  id: string
  unit: string | null
  repo: string | null
  question: string | null
  options: CardOption[] | null
  recommended: string | null
  reason: string | null
  opened?: string | null
  headline?: string | null
  context?: string | null
  outline?: OutlineLine[]
}

export type InventoryItem = {
  kind: string
  ref: string
  unit?: string | null
  title?: string | null
  age_h: number | null
  waits_on: string | null
  stage: string | null
  stale: boolean
  reviewers?: string[]
  approvers?: string[]
  /** GitHub's lifecycle word (open/draft/merged/closed); on a branch, its PR's, null when it has none */
  gh?: string | null
  /** approved / changes / pending, or null */
  gh_review?: string | null
  /** a branch's (or a worktree's branch's) PR ref */
  pr?: string | null
  /** that PR's GitHub page */
  url?: string | null
  /** a branch's commits ahead of the default branch */
  ahead?: number | null
  /** a worktree's unit status */
  status?: string | null
  /** a finished unit's worktree: 회수 대기, or 남김: <why> (in ui.language) */
  reclaim?: string | null
  /** reclaim says the worktree stays, in any ui.language */
  reclaim_kept?: boolean
}

export type Tile = {
  name: string
  value: number | null
  unit: string | null
  worse?: string
  status?: string
  spark: (number | null)[]
  // board.py metrics only: fixed-scale glyphs, each bucket's level, and the window's worst value and its time.
  key?: string
  glyphs?: string
  levels?: (string | null)[]
  peak?: number | null
  peak_ts?: string | null
  warn?: number | null
  danger?: number | null
  // the cache tile: the newest sample's other docker values
  extra?: Record<string, number | null> | null
}

export type Monitor = {
  status: string
  last_sample_age_s?: number | null
  collecting: boolean
  tiles: Tile[]
  footer?: string | null
  timeline?: { ts: string | null; level: string | null }[]
  last_ts?: string | null
  leads?: { live: number; cap: number; launch: boolean } | null
}

export type Metrics = { hours: number; bucket_min: number; samples: number; tiles: Tile[] }

export type Escalation = { id: string; kind: string; unit: string | null; text: string; ts: string | null }

/** One 오너 대기 row (board.py owner_items): a card or ask, an owner to-do, or a waiting_owner unit with neither. */
export type OwnerItem = { kind: 'decision' | 'todo' | 'unit'; ref: string; unit: string | null; title: string; state: string; text: string }

export type BoardModel = {
  generated: string
  sections: { key: ColumnKey; label: string; count: number; rows: BoardRow[]; items?: OwnerItem[] }[]
  counts: Record<ColumnKey, number>
  approvals: Approval[]
  escalations: Escalation[]
  /** Everything new worth a toast (decisions, tier-3 approvals, reports, escalations); escalations is the section. */
  toasts?: Escalation[]
  pressure: { level: string; reasons: string[] }
  sessions: { name: string; unit: string | null; role: string | null; state: string }[]
  proposals: { cls: string; target: string; reason: string | null }[]
  events: { eid: string; unit: string; summary: string }[]
  next?: { unit: string; title?: string | null; repo?: string | null; why?: string | null; priority?: number | null; created?: string | null }[]
  cards?: Card[]
  cards_open?: number
  /** units in the archive view (`foundry board --archived`), left off this board */
  archived_count?: number
  archived_view?: boolean
  /** foreman sessions foundry started that a prune found stale and has not stopped yet */
  stale_foremen?: number
  stale_foremen_failed?: number
  asks?: { key: string; unit: string | null; question: string; asked: string | null; headline?: string | null; outline?: OutlineLine[] }[]
  summary?: { counts: Record<ColumnKey, number>; decisions: number; approvals: number; attention: boolean; owner_wait?: number }
  inventory?: { repos: Record<string, InventoryItem[]>; reviewers: ReviewerLoad[]; warn_at?: number | null }
  monitor?: Monitor | null
  halts?: { scope: string | null; reason: string | null; by: string | null; ts: string | null }[]
  foreman?: { alive: boolean; ts: string | null } | null
  foreman_down?: boolean
  foreman_restart?: boolean
  handover?: Handover | null
  night?: boolean
  glyphs?: 'nerd' | 'emoji'
  repo_names?: string[]
  pr_tab?: PrTabRow[]
  pr_count?: number
  releases?: ReleaseProgress[]
  /** the latest `foundry report kaizen` (weekly cron), its lines with ## headings marked */
  kaizen?: { at: string | null; lines: { head: boolean; text: string }[] } | null
  pr_summary?: PrSummary
  repo_blocks?: RepoBlock[]
  /** views.accent_map: registered repos' accents by registry name, slug and short name */
  repo_accents?: Record<string, { bg: string; fg: string }>
  cost_usd?: number
  /** the GitHub intake scan: last run, its proposed / capped counts, the next scheduled run (MM-DD HH:MM) */
  intake_status?: { at: string; proposed: number; capped: number; next: string } | null
}

export type View = 'status' | 'repo' | 'pr'


declare module 'claude-code' {
  interface PluginState {
    foundry: {
      board: Shaped<BoardModel | null>
      selected: string | null
      error: string | null
      result: string | null
      resultAt: number | null
      detailOpen: string | null
      view: View
      repoFilter: string
      pcHours: 3 | 6 | 24
      metrics: Metrics | null
      escOpen: string | null
      cardOpen: string | null
      nearmissOpen: boolean
      boardFolded: boolean
      build: { version: string | null; newest: string | null } | null
      timelineOpen: string | null
    }
  }
}

/** review.load_rows: mine = the owner's open PRs requesting this person directly; others = their other direct requests in tracked repos (bots excluded). */
export interface ReviewerLoad { login: string; pending: number; warn: boolean; mine?: number; others?: number | null; total?: number; text?: string }

/** The morning handover as the board draws it (factory.handover_view): 결정 대기 is read live at render time. */
export type Handover = {
  text: string
  ts?: string | null
  /** the snapshot's time, MM-DD HH:MM JST */
  at?: string | null
  /** units, cards and decisions that changed after the snapshot */
  changes?: number
  sections?: {
    title: string; count: number; live: boolean; items: { unit: string | null; id: string | null; line: string; url?: string | null }[]
    /** items past the cap; `link: 'pr'` points at the PR tab */
    more?: number; link?: string | null
  }[]
}
