"""Regenerate docs/snapshots.md: text snapshots of the board pane, a unit's detail, a decision card, the PR tab and the
handover, rendered from a throwaway state filled with example-org data. Nothing real is read or written.

    python3 tests/doc_snapshots.py          (from foundry/; needs `claude` for the pane renders)

The pane is mounted with `claude plugin test` on a temporary copy of hooks/, fed the board model `board.py json`
builds from the example state, and its UI tree is laid out as plain text (no colours, CJK width ignored).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
NOW = "2026-11-20T10:00:00+00:00"

RENDER_TEST = r"""import { test } from 'claude-code/testing'
import MODEL from './snapshot-model'

const PANE = { plugin: 'foundry', component: 'Pane', requestId: 'foundry-board',
  props: { title: 'foundry board', isFocused: false, bodyColumns: 100, placement: 'dock', scroll: { offset: 0, bodyRows: 200 }, view: {} } } as const

const ok = (stdout: string) => ({ value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } })

function harness(on: any) {
  on('clock.now', () => ({ value: Date.parse('__NOW__') }))
  on('command.register', (_: unknown, e: { name: string }) => ({ value: { command: e.name } }))
  on('env.get', () => ({ value: undefined }))
  on('fs.write', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.panes', () => ({ value: [] }))
  on('clock.every', () => ({ value: null }))
  on('ui.toast', () => ({ value: undefined }))
  on('process.hookEnv', () => ({ value: {} }))
  on('process.run', (_: unknown, e: { argv: string[] }) => {
    if (e.argv[2] === 'metrics') {
      const tiles = ['load', 'memory', 'disk', 'docker'].map(name => ({ name, value: 30, unit: name === 'load' ? '/cpu' : '%',
        status: 'ok', spark: [20, 30], glyphs: '▃▄', levels: ['ok', 'ok'] }))
      return ok(JSON.stringify({ hours: 3, bucket_min: 5, samples: 2, tiles }))
    }
    if (e.argv[1]?.endsWith('board.py')) return ok(JSON.stringify(MODEL))
    return { value: { exitCode: 1, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('session.start', (_: unknown, e: { cwd: string }) => ({ cwd: e.cwd }))
}

const flat = (xs: any[]): any[] => xs.flatMap((c: any) => (Array.isArray(c) ? flat(c) : c == null || c === false ? []
  : typeof c === 'object' && !c.type ? flat(c.children ?? []) : [c]))
const textOf = (n: any): string =>
  typeof n === 'string' || typeof n === 'number' ? String(n) : Array.isArray(n) ? flat(n).map(textOf).join('')
    : !n ? '' : n.type === 'Link' ? String(n.props?.label ?? '') : flat(n.children ?? []).map(textOf).join('')

function lines(n: any): string[] {
  if (n == null || n === false) return []
  if (typeof n === 'string' || typeof n === 'number') return String(n).split('\n')
  if (Array.isArray(n)) return flat(n).flatMap(lines)
  const p = n.props ?? {}
  const kids = flat(n.children ?? [])
  let out: string[]
  if (n.type === 'Text' || n.type === 'Link') out = textOf(n).split('\n')
  else if (n.type === 'Button') out = [kids.length ? textOf(n) : `[${p.label ?? ''}]`]
  else if (p.flexDirection === 'row') {
    const cols = kids.map(lines).filter((c: string[]) => c.length)
    const gap = ' '.repeat(p.gap ?? (p.justifyContent === 'space-between' ? 2 : 0))
    const h = Math.max(0, ...cols.map((c: string[]) => c.length))
    out = []
    for (let i = 0; i < h; i++) {
      out.push(cols.map((c: string[], j: number) => (j === cols.length - 1 ? c[i] ?? '' : (c[i] ?? '').padEnd(Math.max(...c.map(s => s.length))))).join(gap).trimEnd())
    }
  } else out = kids.flatMap(lines)
  if (!kids.length && p.backgroundColor && p.width) out = ['▌']
  const pad = ' '.repeat((p.paddingLeft ?? 0) + (p.paddingX ?? 0))
  if (pad) out = out.map(l => (l ? pad + l : l))
  if (p.marginTop) out = [...Array(p.marginTop).fill(''), ...out]
  return out
}

const dump = async (name: string, ui: any) => {
  const tree = typeof ui.drawn === 'function' ? await ui.drawn() : ui.drawn
  console.log(`===SNAP ${name}===\n${lines(tree).join('\n').replace(/\n{3,}/g, '\n\n')}\n===END===`)
}

test('doc snapshots', async ($, on) => {
  harness(on)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui: any = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await dump('board', ui)
  await ui.press({ key: '__DETAIL__' })
  await dump('detail', ui)
  await ui.press({ key: 'back' })
  await ui.press({ key: 'view:pr' })
  await dump('pr', ui)
  await ui.unmount()
})
"""


def run(args, env, ok=(0,)):
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry.py"), *args], env=env, capture_output=True,
                       text=True)
    if r.returncode not in ok:
        raise SystemExit(f"foundry {' '.join(args)}: {r.returncode}\n{r.stderr}{r.stdout}")
    return r.stdout


def build_state(d):
    env = {**os.environ, "FOUNDRY_STATE_DIR": d, "FOUNDRY_NOW": NOW, "FOUNDRY_CLAUDE": "false", "FOUNDRY_DOCKER": "false",
           "FOUNDRY_WORKTREE_DIRS": "", "FOUNDRY_MCP_SERVERS": "[]",
           "FOUNDRY_GH": f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_gh.py')}",
           "FAKE_GH_DATA": os.path.join(d, "gh.json"), "FOUNDRY_PRESSURE_FAKE": json.dumps(
               {"disk_free_gb": {"/": 300}, "mem_free_pct": 55, "load_per_cpu": 0.3})}
    for k in ("FOUNDRY_PREFS_OVERLAY", "FOUNDRY_SESSION", "FOUNDRY_ROLE", "FOUNDRY_UNIT"):
        env.pop(k, None)
    with open(env["FAKE_GH_DATA"], "w") as f:
        json.dump({}, f)
    with open(os.path.join(d, "prefs.json"), "w") as f:
        json.dump({"ui": {"language": "en"}, "language": "en", "timezone": "UTC"}, f)
    run(["unit", "new", "--id", "login-fix", "--title", "Fix the login redirect loop", "--repo", "example-org/app",
         "--ticket", "PROJ-12", "--pr", "example-org/app#41", "--status", "in_progress"], env)
    run(["unit", "new", "--id", "export-limit", "--title", "Rate-limit the export API", "--repo", "example-org/api",
         "--ticket", "PROJ-15", "--status", "in_progress"], env)
    run(["unit", "new", "--id", "faq", "--title", "Update the FAQ page", "--repo", "example-org/docs",
         "--ticket", "PROJ-9", "--pr", "example-org/docs#7", "--status", "done"], env)
    run(["unit", "new", "--id", "deps", "--title", "Bump test dependencies", "--kind", "repo", "--repo",
         "example-org/app"], env)
    sys.path.insert(0, SCRIPTS)
    os.environ.update({k: env[k] for k in ("FOUNDRY_STATE_DIR", "FOUNDRY_NOW")})
    import foundrylib as cl
    cl.append_many([
        ("session.launch", {"name": "lead-login-fix", "unit": "login-fix", "role": "line-lead", "bg_id": "b1",
                            "cwd": "/work/wt/app/login-fix", "model": "opus"}),
        ("session.launch", {"name": "lead-export-limit", "unit": "export-limit", "role": "line-lead", "bg_id": "b2",
                            "cwd": "/work/wt/api/export-limit", "model": "opus"}),
        ("session.state", {"name": "lead-export-limit", "state": "idle"}),
        ("unit.worktree", {"id": "login-fix", "repo": "example-org/app", "path": "/work/wt/app/login-fix",
                           "branch": "foundry/login-fix"}),
        ("unit.worktree", {"id": "export-limit", "repo": "example-org/api", "path": "/work/wt/api/export-limit",
                           "branch": "foundry/export-limit"}),
        ("unit.checkpoint", {"id": "login-fix", "note": "Redirect fixed behind a cookie check; tests added"}),
        ("pr.state", {"unit": "login-fix", "repo": "example-org/app", "number": 41, "state": "OPEN",
                      "title": "Fix the login redirect loop", "review": "REVIEW_REQUIRED"}),
        ("wait.add", {"id": "login-fix", "wid": "w1", "kind": "pr-review", "ref": "example-org/app#41", "until": None,
                      "note": "reviewer: alex"}),
        ("pr.state", {"unit": "faq", "repo": "example-org/docs", "number": 7, "state": "MERGED",
                      "title": "Update the FAQ page"}),
        ("unit.checkpoint", {"id": "export-limit", "note": "Token bucket per API key drafted; needs a limit value"}),
        ("approval.request", {"rid": "ap-1", "key": "k1", "session": "S-login", "unit": "login-fix",
                              "role": "line-lead", "tool": "Bash", "summary": "git push origin login-fix", "tier": 2}),
    ])
    run(["decide", "open", "--unit", "export-limit", "--key", "export-limit:limit", "--asked-by", "line-lead",
         "--question", "Which default limit should the export API ship with?",
         "--option", "A:60 requests a minute per key", "--option", "B:600 requests an hour per key",
         "--recommend", "A", "--reason", "Matches the current peak with room to spare"], env)
    return env


def pane_snaps(model, tmp):
    plug = os.path.join(tmp, "plugin")
    shutil.copytree(os.path.join(ROOT, ".claude-plugin"), os.path.join(plug, ".claude-plugin"))
    shutil.copytree(os.path.join(ROOT, "hooks"), os.path.join(plug, "hooks"),
                    ignore=shutil.ignore_patterns("*.test.ts", "*.test.tsx"))
    shutil.copytree(os.path.join(ROOT, "types"), os.path.join(plug, "types"))
    with open(os.path.join(plug, "hooks", "snapshot-model.ts"), "w") as f:
        f.write("export default " + json.dumps(model, ensure_ascii=False) + "\n")
    with open(os.path.join(plug, "hooks", "snapshots.test.tsx"), "w") as f:
        f.write(RENDER_TEST.replace("__NOW__", NOW).replace("__DETAIL__", "export-limit"))
    r = subprocess.run(["claude", "plugin", "test", plug], capture_output=True, text=True, timeout=300)
    if os.environ.get("SNAP_DEBUG"):
        print((r.stdout + r.stderr)[-4000:])
    snaps = dict(re.findall(r"===SNAP (\w+)===\n(.*?)\n===END===", r.stdout + r.stderr, re.S))
    if not snaps:
        raise SystemExit("pane render failed:\n" + (r.stdout + r.stderr)[-3000:])
    return snaps


def main():
    tmp = tempfile.mkdtemp(prefix="foundry-snap-")
    try:
        state = os.path.join(tmp, "state")
        os.makedirs(state)
        env = build_state(state)
        model = json.loads(subprocess.run([sys.executable, os.path.join(SCRIPTS, "board.py"), "json"], env=env,
                                          capture_output=True, text=True, check=True).stdout)
        snaps = pane_snaps(model, tmp)
        card = run(["decide", "show", "d-0001"], env)
        board_text = run(["board"], env)
        handover = run(["report", "handover"], env)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    block = lambda t: "```text\n" + t.rstrip() + "\n```\n"  # noqa: E731
    doc = [
        "# Snapshots",
        "",
        "Text renders of foundry's screens with example data (example-org, made-up units). Generated by",
        "`python3 tests/doc_snapshots.py` from a throwaway state; the pane renders come from the board-pane module",
        "mounted in `claude plugin test`, laid out as plain text (colours and pills dropped). Regenerate after UI",
        "changes. See [concepts](concepts.md) for what each part means.",
        "",
        "## Board pane",
        "",
        "Sections by state, an approval waiting on the owner, and the per-repo filter.",
        "", block(snaps.get("board", "")),
        "## Unit detail",
        "",
        "A unit waiting on a decision: the facts table, what blocks it, its session and branch, and the status buttons.",
        "", block(snaps.get("detail", "")),
        "## PR tab",
        "", block(snaps.get("pr", "")),
        "## Decision card (`foundry decide show`)",
        "", block(card),
        "## Text board (`foundry board`)",
        "", block(board_text),
        "## Handover (`foundry report handover`)",
        "", block(handover),
    ]
    with open(os.path.join(ROOT, "docs", "snapshots.md"), "w") as f:
        f.write("\n".join(doc))
    print("wrote docs/snapshots.md")


if __name__ == "__main__":
    main()
