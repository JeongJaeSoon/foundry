"""Handoff: a compact restart document rendered from foundry state, never from a transcript.

The same text is (a) the prompt a lead is re-spawned with, (b) what a foundry session
reloads after /clear or compaction, and (c) the file left in handoffs/<unit>.md at
every save point (PreCompact, SessionEnd, SubagentStop). One renderer, one source:
the journal. Agents add only what state cannot know — goal, next step, file
pointers — through `foundry handoff write`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

ORCH = "_foreman"
MAX_NOTES = 6
MAX_LINE = 240


def _clip(text):
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_LINE else text[:MAX_LINE - 1] + "…"


def _h(u):
    return u.get("handoff") or {"goal": None, "next": None, "files": [], "ts": None}


def render(snap, uid):
    if uid == ORCH:
        return render_foreman(snap)
    u = snap["units"][uid]
    h = _h(u)
    todos = list(u["todos"].values())
    out = [f"UNIT: {uid}", f"# Handoff: {u['title']}",
           f"Kind: {u['kind']}  Lead: {u.get('lead') or '-'}  Repo: {u.get('repo') or '-'}  Status: {u['status']}"]

    out.append("\n## Goal")
    out.append(_clip(h["goal"] or u["title"]))

    done = [t for t in todos if t["status"] == "completed"]
    notes = u["notes"][-MAX_NOTES:]
    out.append("\n## Done")
    out += [f"- {_clip(t['subject'])}" for t in done[-10:]] or ["- (nothing recorded)"]
    if notes:
        out.append("Checkpoints (newest last):")
        out += [f"- {n['ts'][5:16]} {_clip(n['text'])}" for n in notes]

    out.append("\n## In progress")
    doing = [t for t in todos if t["status"] not in ("completed", "deleted")]
    out += [f"- [{t['status']}] {_clip(t['subject'])}" for t in doing] or ["- (none)"]

    out.append("\n## Waits")
    out += [f"- {w['wid']}: {w['kind']} {w.get('ref') or w.get('until') or ''} {_clip(w.get('note') or '')}".rstrip()
            for w in u["waits"]] or ["- (none)"]
    ev = [e for e in snap["events"] if e["unit"] == uid and not e["consumed"]]
    if ev:
        out.append("Events that arrived:")
        out += [f"- {e['eid']} {_clip(e['summary'])}" for e in ev]

    dec = [d for d in snap["decisions"].values() if d.get("unit") == uid]
    out.append("\n## Decisions")
    out += [f"- {d['key']}: " + (f"ANSWERED: {_clip(d['answer'])}" if d["answer"] is not None
                                 else f"OPEN (asked {str(d.get('asked'))[5:16]}; do not re-ask): {_clip(d['question'])}")
            for d in dec] or ["- (none)"]

    out.append("\n## Next step")
    out.append(_clip(h["next"]) if h["next"] else "- (not written: read In progress and Waits)")

    out.append("\n## Pointers")
    keys = [f"{k}: {', '.join(v)}" for k, v in u["keys"].items() if v]
    out += [f"- {k}" for k in keys]
    out += [f"- file: {f}" for f in h["files"]]
    if not keys and not h["files"]:
        out.append("- (none)")

    pending = [i for i in u["inbox"] if not i["delivered"]]
    if pending:
        out.append(f"\n({len(pending)} queued owner instruction(s) are injected when the lead starts.)")
    out.append("\nThis handoff replaces any earlier context. Follow the foundry-lead-loop skill.")
    return "\n".join(out)


def render_foreman(snap):
    u = snap["units"].get(ORCH) or {"todos": {}, "notes": [], "handoff": None}
    h = _h(u)
    out = ["# Handoff: foundry foreman"]
    out.append("\n## Goal")
    out.append(_clip(h["goal"]) if h["goal"] else "Route owner work through leads; keep this session small.")
    by = {}
    for x in snap["units"].values():
        if x["kind"] != "foreman":
            by.setdefault(x["status"], []).append(x)
    labels = (("waiting_owner", "Waiting on owner"), ("in_progress", "In progress"),
              ("waiting_others", "Waiting on others"), ("queue", "Queue"))
    out.append("\n## Units")
    for key, label in labels:
        xs = by.get(key, [])
        if xs:
            out.append(f"{label}:")
            for x in xs:
                live = cl.lead_live(snap, x)
                last = _clip(x["notes"][-1]["text"]) if x["notes"] else ""
                out.append(f"- {x['id']} ({x['title']}){' [lead session live]' if live else ''} {last}".rstrip())
    out.append(f"Done: {len(by.get('done', []))} unit(s) (`foundry board` for the list)")

    dec = [d for d in snap["decisions"].values() if d["answer"] is None]
    out.append("\n## Open owner decisions (relayed; do not re-ask)")
    out += [f"- {d['key']} ({d.get('unit')}): {_clip(d['question'])}" for d in dec] or ["- (none)"]

    ev = [e for e in snap["events"] if not e["consumed"]]
    out.append("\n## Unhandled watcher events")
    out += [f"- {e['eid']} {e['unit']}: {_clip(e['summary'])}" for e in ev] or ["- (none)"]

    doing = [t for t in u["todos"].values() if t["status"] not in ("completed", "deleted")]
    out.append("\n## In progress (this session's own steps)")
    out += [f"- [{t['status']}] {_clip(t['subject'])}" for t in doing] or ["- (none)"]

    out.append("\n## Next step")
    out.append(_clip(h["next"]) if h["next"] else "- Re-arm `foundry events wait`, then handle the lists above.")
    if h["files"]:
        out.append("\n## Pointers")
        out += [f"- file: {f}" for f in h["files"]]
    out.append("\nThis handoff replaces the earlier conversation. Owner words that are not in foundry state were not carried over: "
               "if the owner refers to something missing here, ask once.")
    return "\n".join(out)


def save(snap, uid, trigger):
    """Write handoffs/<uid>.md and journal that a snapshot was taken. Returns the path."""
    bad = None if uid == ORCH else cl.unit_id_error(uid)
    if bad:
        raise ValueError(bad)
    d = cl.path("handoffs")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{uid}.md")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        f.write(render(snap, uid) + "\n")
    os.replace(tmp, p)
    cl.append("handoff.snapshot", id=uid, trigger=trigger)
    return p
