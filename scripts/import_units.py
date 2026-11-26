"""One-time import of open work into foundry units, from a JSON list the owner exports or writes by hand:
  [{"id": "...", "title": "...", "status": "in_progress", "kind": "project|repo", "repo": "owner/name",
    "ticket": ["PROJ-1"], "pr": ["owner/repo#1"], "topic": ["..."], "notes": ["..."], "lead": "foundry:line-lead"}]
Units that already exist (same id, or same ticket/PR) are skipped and reported, so a re-run is harmless.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
from foundry import LEAD_FOR_KIND, find_units  # noqa: E402

STATUS_MAP = {"active": "in_progress", "running": "in_progress", "blocked": "waiting_owner", "waiting": "waiting_others",
              "review": "waiting_others", "closed": "done", "merged": "done", "todo": "queue", "open": "queue"}


def main(argv=None):
    """`foundry unit import <file> [--dry-run] [--source S]` (the CLI verb is the way in; the hook refuses this
    script run by path, like every raw script under scripts/)."""
    ap = argparse.ArgumentParser(prog="foundry unit import")
    ap.add_argument("file")
    ap.add_argument("--source", default="import")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        with open(os.path.expanduser(a.file)) as f:
            items = json.load(f)
    except (OSError, ValueError) as e:
        print(f"cannot read {a.file}: {e}", file=sys.stderr)
        return 2
    if isinstance(items, dict):
        items = items.get("units", items)
        items = list(items.values()) if isinstance(items, dict) else items
    if not isinstance(items, list) or not all(isinstance(x, dict) and x.get("id") for x in items):
        print("expected a JSON list of units, each with an id (or {\"units\": [...]})", file=sys.stderr)
        return 2
    snap = cl.load()
    imported, skipped = [], []
    for it in items:
        uid = it["id"]
        bad = cl.unit_id_error(uid)
        if bad:
            skipped.append({"id": uid, "error": bad})
            continue
        dup = [u["id"] for u in find_units(snap, "", it.get("ticket", []), it.get("pr", []))]
        if uid in snap["units"] or uid in imported or dup:  # imported: a dry run never writes, so dedupe here too
            skipped.append({"id": uid, "existing": dup or [uid]})
            continue
        kind = it.get("kind", "project") if it.get("kind") in LEAD_FOR_KIND else "project"
        status = STATUS_MAP.get(it.get("status", "queue"), it.get("status", "queue"))
        if status not in cl.STATUSES:
            status = "queue"
        recs = [("unit.new", {"id": uid, "kind": kind, "title": it.get("title", uid), "repo": it.get("repo"),
                              "lead": it.get("lead") or LEAD_FOR_KIND[kind], "ticket": it.get("ticket", []),
                              "pr": it.get("pr", []), "topic": it.get("topic", [])})]
        # An imported unit has no live lead: in_progress work shows up as "re-spawn needed" at the next session start.
        recs.append(("unit.status", {"id": uid, "status": status, "note": f"imported from {a.source}"}))
        notes = it.get("notes", [])
        recs += [("unit.checkpoint", {"id": uid, "note": n if isinstance(n, str) else json.dumps(n, ensure_ascii=False)})
                 for n in ([notes] if isinstance(notes, str) else notes)]
        if not a.dry_run:
            snap = cl.append_many(recs)
        imported.append(uid)
    print(json.dumps({"imported": imported, "skipped": skipped, "dry_run": a.dry_run}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
