"""Unit dependency graph, readiness, `foundry next`, the verify gate, role-scoped `unit set`, the andon
halt, locks, and lead<->boss notes.

A unit's `blocked_by` holds its unresolved refs; resolved facts live in STATE `refs`, so readiness is a
pure function of the snapshot. `resolve(snap, now)` turns satisfied refs into `ref.resolved` /
`unit.ready` records (the watcher calls it; `foundry graph resolve` appends them by hand). The
upstream/downstream graph is derived from `unit:` refs on demand and never stored.

Ref kinds:
  unit:<id>                 that unit is done
  pr:<repo>#<n>:merged      op pr.merged / pr.state (state merged), or a unit's `prs` entry marked merged
  deploy:<app>@<env>        op deploy.done {app, env} from `foundry unit deployed`
  ticket:<key>:<status>     op ticket.state {key, status} from `foundry sync done --ticket-status`
  time:<ISO>                now >= time
  lock:<name>               the unit holds the lock (resolve takes a free lock for the oldest waiter)
  decision:<id>             decide.answer on that card

Caller identity for `unit set` / `halt` / `resume-line`: `--by` names the role. When FOUNDRY_ROLE is in
the environment (lead sessions get it through the launcher's `--settings env`), `--by` must agree with
it; a lead's own unit is FOUNDRY_UNIT, else the unit whose live session has the lead's role. Without
FOUNDRY_ROLE, `--by` defaults to owner, which foundrylib.require_owner accepts only outside every foundry
session (a terminal, the board pane).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

KINDS = ("unit", "pr", "deploy", "ticket", "time", "lock", "decision")
LIVE = ("launching", "running", "idle")
CLOSED = ("done", "cancelled")
ROLES = ("owner", "foreman", "lead", "shop-boss", "hand", "dispatcher", "sweeper-judge")
ENV_ROLE = {"line-lead": "lead", "shop-boss": "shop-boss", "foreman": "foreman"}
SESSION_ROLE = {"lead": "line-lead", "shop-boss": "shop-boss"}
FOREMAN_STATUSES = ("queue", "parked", "blocked")
LEAD_STATUSES = ("parked", "blocked", "in_progress", "waiting_others", "waiting_owner", "done")
LAUNCHABLE = ("queue", "in_progress", "waiting_others", "waiting_owner")
LAUNCH_WORDS = ("launch", "start", "run", "resume", "restart", "reopen", "open")
# Who records verify.pass. A lead or shop-boss checks its own work, so its word is a report, not a verification:
# done comes from facts foundry records itself (a merged PR, a passed release gate) or from the foreman's check.
VERIFIERS = ("owner", "foreman")
EXIT_REFUSED = 2

STATE = {"refs": {}, "locks": {}, "halts": []}
UNIT_FIELDS = {"blocked_by": [], "resolved": [], "ready": None, "priority": None,
               "verify": {"passed": False, "evidence": None, "by": None, "ts": None}, "changed": None}


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def fail(msg, code=EXIT_REFUSED):
    print(msg, file=sys.stderr)
    return code


# ---------- refs ----------

def parse_ref(ref):
    """(kind, body) or None. Bodies: unit id, (repo#n, 'merged'), app@env, (key, status), ISO, name, card id."""
    kind, _, rest = str(ref).partition(":")
    if kind not in KINDS or not rest:
        return None
    if kind == "pr":
        body, _, state = rest.rpartition(":")
        return (kind, (body, state)) if state == "merged" and "#" in body else None
    if kind == "ticket":
        key, _, status = rest.rpartition(":")
        return (kind, (key, status)) if key and status else None
    if kind == "deploy" and "@" not in rest:
        return None
    if kind == "time" and cl.parse_ts(rest) is None:
        return None
    return kind, rest


def _pr_key(r):
    if r.get("repo") and r.get("number") is not None:
        return f"pr:{r['repo']}#{r['number']}"
    if r.get("pr") and "#" in str(r["pr"]):
        return f"pr:{r['pr']}"
    return None


def _units(s):
    return s.get("units") or {}


def _live(snap, u):
    """foundrylib.lead_live, tolerant of a partial snapshot (board fixtures)."""
    return cl.session_live((snap.get("sessions") or {}).get(u.get("session") or ""))


# ---------- reducers ----------

def _fact(key, value):
    def red(s, r, ts):
        k = key(r)
        if k:
            s["refs"][k] = {"value": value(r), "ts": ts, "via": r.get("op")}
    return red


def _changed(s, r, ts):
    u = _units(s).get(r.get("unit") or r.get("id"))
    if u is not None:
        u["changed"] = ts


def _op_block(s, r, ts):
    u = _units(s).get(r["unit"])
    if u is None:
        return
    for ref in r["refs"]:
        if ref not in u["blocked_by"]:
            u["blocked_by"].append(ref)
    u["ready"] = None
    u["updated"] = ts


def _op_unblock(s, r, ts):
    u = _units(s).get(r["unit"])
    if u is not None and r["ref"] in u["blocked_by"]:
        u["blocked_by"].remove(r["ref"])
        u["updated"] = ts


def _op_resolved(s, r, ts):
    u = _units(s).get(r["unit"])
    if u is not None and r["ref"] in u["blocked_by"]:
        u["blocked_by"].remove(r["ref"])
        u["resolved"] = (u["resolved"] + [{"ref": r["ref"], "ts": ts}])[-20:]
        u["updated"] = ts


def _op_ready(s, r, ts):
    u = _units(s).get(r["unit"])
    if u is None:
        return
    u["ready"] = ts
    s["events"].append({"eid": r.get("eid"), "unit": r["unit"], "wid": None, "kind": "unit.ready", "ref": None,
                        "summary": f"unit {r['unit']} is ready: its last blocker resolved", "ts": ts, "consumed": None})


def _op_verify(s, r, ts):
    u = _units(s).get(r["unit"])
    if u is not None:
        u["verify"] = {"passed": True, "evidence": r.get("evidence"), "by": r.get("by"), "ts": ts}


def _op_set(s, r, ts):
    u = _units(s).get(r["id"])
    if u is None:
        return
    if r.get("repo"):
        u["repo"] = r["repo"]
    if r.get("status"):
        u["status"] = r["status"]
    if "priority" in r and r["priority"] is not None:
        u["priority"] = r["priority"]
    if r.get("note"):
        u["notes"].append({"ts": ts, "text": f"[{r.get('by') or '?'}] {r['note']}"})
    u["updated"] = ts


def _op_halt(s, r, ts):
    scope = r.get("repo") or "global"
    s["halts"] = [h for h in s["halts"] if h["scope"] != scope] + [
        {"scope": scope, "reason": r.get("reason"), "by": r.get("by"), "ts": ts}]


def _op_resume(s, r, ts):
    scope = r.get("repo") or "global"
    s["halts"] = [h for h in s["halts"] if h["scope"] != scope]
    for u in s["units"].values():
        if " halted by " in str((u.get("held") or {}).get("reason") or "") and (scope == "global" or u.get("repo") == scope):
            u["held"] = None


def _op_lock_take(s, r, ts):
    s["locks"][r["name"]] = {"unit": r["unit"], "ts": ts}


def _op_lock_release(s, r, ts):
    cur = s["locks"].get(r["name"])
    if cur and (not r.get("unit") or cur["unit"] == r["unit"]):
        s["locks"].pop(r["name"])


def _noop(s, r, ts):
    pass


OPS = {
    "unit.block": _op_block,
    "unit.unblock": _op_unblock,
    "ref.resolved": _op_resolved,
    "unit.ready": _op_ready,
    "verify.pass": _op_verify,
    "unit.set": _op_set,
    "owner.override": _noop,   # audit only: the change itself is the unit.set beside it
    "unit.note": _noop,        # audit only: the note reaches the lead through the inbox.add beside it
    "repo.main_moved": _noop,
    "halt": _op_halt,
    "line.resume": _op_resume,
    "lock.take": _op_lock_take,
    "lock.release": _op_lock_release,
    # Facts other modules own; graph only observes them (foundrylib runs every module's reducer).
    "pr.merged": _fact(_pr_key, lambda r: "merged"),
    "pr.state": _fact(_pr_key, lambda r: str(r.get("state") or "").lower()),
    "pr.opened": _changed,
    "branch.pushed": _changed,
    "deploy.done": _fact(lambda r: f"deploy:{r['app']}@{cl.norm_env(r['env'])}" if r.get("app") and r.get("env") else None,
                         lambda r: "done"),
    "ticket.state": _fact(lambda r: f"ticket:{r['key']}" if r.get("key") else None,
                          lambda r: str(r.get("status") or "")),
}


# ---------- derived view ----------

def lock_holder(snap, name):
    """The unit holding a lock, or None when free (a done/cancelled holder frees it)."""
    cur = (snap.get("locks") or {}).get(name)
    if not cur:
        return None
    u = _units(snap).get(cur["unit"])
    return cur["unit"] if u and u["status"] not in CLOSED else None


def edges(snap):
    """{uid: [upstream unit ids]} from unresolved unit: refs. Derived, never stored."""
    out = {}
    for uid, u in _units(snap).items():
        out[uid] = [p[1] for p in map(parse_ref, u.get("blocked_by") or []) if p and p[0] == "unit"]
    return out


def _walk(start, nxt):
    seen, order, stack = set(), [], list(nxt(start))
    while stack:
        x = stack.pop(0)
        if x in seen or x == start:
            continue
        seen.add(x)
        order.append(x)
        stack.extend(nxt(x))
    return order


def chain(snap, uid):
    """{up: units this one waits on (transitively), down: units waiting on it}."""
    up = edges(snap)
    down = {}
    for a, bs in up.items():
        for b in bs:
            down.setdefault(b, []).append(a)
    return {"up": _walk(uid, lambda x: up.get(x, [])), "down": _walk(uid, lambda x: down.get(x, []))}


def find_cycle(snap, uid, new_refs):
    """The cycle path [uid, ..., uid] that blocking uid on new_refs would close, else None."""
    up = edges(snap)
    up[uid] = up.get(uid, []) + [p[1] for p in map(parse_ref, new_refs) if p and p[0] == "unit"]

    def dfs(node, path):
        for nxt in up.get(node, []):
            if nxt == uid:
                return path + [nxt]
            if nxt not in path:
                hit = dfs(nxt, path + [nxt])
                if hit:
                    return hit
        return None
    return dfs(uid, [uid])


def satisfied(snap, ref, now, answered=None):
    """Whether a non-lock ref holds now. Lock refs are decided in resolve (they need the waiter)."""
    p = parse_ref(ref)
    if not p:
        return False
    kind, body = p
    facts = snap.get("refs") or {}
    if kind == "unit":
        u = _units(snap).get(body)
        return bool(u) and u["status"] == "done"
    if kind == "pr":
        f = facts.get(f"pr:{body[0]}")
        if f and f["value"] == "merged":
            return True
        repo, _, num = body[0].rpartition("#")
        return any(str(x.get("repo")) == repo and str(x.get("number")) == num
                   and (str(x.get("state") or "").lower() == "merged" or x.get("merged"))
                   for u in _units(snap).values() for x in (u.get("prs") or []) if isinstance(x, dict))
    if kind == "deploy":
        app, _, env = str(body).rpartition("@")
        return f"deploy:{app}@{cl.norm_env(env)}" in facts
    if kind == "ticket":
        f = facts.get(f"ticket:{body[0]}")
        return bool(f) and f["value"].lower() == body[1].lower()
    if kind == "time":
        return now >= cl.parse_ts(body)
    if kind == "decision":
        if answered is not None:
            return body in answered
        return ((snap.get("cards") or {}).get(body) or {}).get("answer") is not None
    return False


def resolve(snap, now=None):
    """Records to append: deadline defaults, lock grants, ref.resolved, and unit.ready on the last blocker."""
    now = now or cl.now()
    recs = []
    cards = snap.get("cards") or {}
    answered = {cid for cid, c in cards.items() if c.get("answer") is not None}
    for cid in sorted(cards):
        c = cards[cid]
        dl = cl.parse_ts(c.get("deadline")) if c.get("deadline") else None
        if c.get("answer") is None and dl and c.get("default") and now >= dl:
            recs.append(("decide.answer", {"id": cid, "key": c["default"], "by": "deadline"}))
            answered.add(cid)
    held = {name: lock_holder(snap, name) for name in (snap.get("locks") or {})}
    units = sorted(_units(snap).values(), key=lambda u: (u.get("created") or "", u["id"]))
    for u in units:
        refs = u.get("blocked_by") or []
        if not refs or u["status"] in CLOSED:
            continue
        done = [r for r in refs if not r.startswith("lock:") and satisfied(snap, r, now, answered)]
        locks = [r for r in refs if r.startswith("lock:")]
        if len(done) + len(locks) == len(refs):
            # Only a unit with nothing else outstanding takes a lock: a waiter never sits on one.
            for r in locks:
                name = r[5:]
                if not held.get(name):
                    held[name] = u["id"]
                    recs.append(("lock.take", {"name": name, "unit": u["id"], "by": "resolve"}))
                if held[name] == u["id"]:
                    done.append(r)
        recs += [("ref.resolved", {"unit": u["id"], "ref": r}) for r in done]
        if done and len(done) == len(refs):
            recs.append(("unit.ready", {"unit": u["id"], "eid": cl.new_id("ev", u["id"], "ready")}))
    return recs


def apply_resolve():
    """Resolve under the lock and append; returns the records written."""
    return cl.transact(lambda snap: (lambda recs: (recs, recs))(resolve(snap)))[0]


def halt_of(snap, repo):
    hs = snap.get("halts") or []
    return next((h for h in hs if h["scope"] == "global"), None) or next(
        (h for h in hs if repo and h["scope"] == repo), None)


def launch_block(snap, uid):
    """Why the unit may not be launched now (halt, status, blockers), or None."""
    u = _units(snap).get(uid)
    if not u:
        return None
    h = halt_of(snap, u.get("repo"))
    if h:
        where = "line" if h["scope"] == "global" else f"repo {h['scope']}"
        return f"{where} halted by {h.get('by')}: {h.get('reason')} (lift: foundry resume-line)"
    if u["status"] in ("done", "cancelled", "parked", "blocked"):
        return f"status {u['status']}: launch takes {', '.join(LAUNCHABLE)}; {launch_path(uid, u['status'])}"
    refs = u.get("blocked_by") or []
    if refs:
        locked = [f"{r} (held by {lock_holder(snap, r[5:])})" for r in refs
                  if r.startswith("lock:") and lock_holder(snap, r[5:]) not in (None, uid)]
        return "blocked by " + ", ".join(locked or refs)
    return None


def _age_h(u, now):
    t = cl.parse_ts(u.get("created") or "")
    return round((now - t).total_seconds() / 3600, 1) if t else 0.0


def queued_ready(snap):
    return [u for u in _units(snap).values()
            if u.get("kind") in ("project", "repo") and u["status"] == "queue" and not u.get("blocked_by")
            and not _live(snap, u) and launch_block(snap, u["id"]) is None]


def has_repo(u):
    """A unit with no repo has no worktree for `foundry unit launch` to make (it refuses without --cwd)."""
    return bool(u.get("repo"))


def repoless(snap):
    """Queued, otherwise launchable units with no repo: never auto-launched, escalated once."""
    return [u for u in queued_ready(snap) if not has_repo(u)]


def next_units(snap, p=None, now=None):
    """Launchable units, best first, cut by the concurrency room and PC pressure."""
    if p is None:
        import prefs
        p = prefs.load()
    now = now or cl.now()
    ready = [u for u in queued_ready(snap) if has_repo(u)]
    rows = []
    for u in ready:
        down = len(chain(snap, u["id"])["down"])
        pr = u.get("priority")
        why = [f"priority {pr}" if pr is not None else "no priority"]
        if down:
            why.append(f"unblocks {down}")
        why.append(f"age {_age_h(u, now)}h")
        rows.append({"unit": u["id"], "title": u["title"], "repo": u.get("repo"), "why": ", ".join(why),
                     "priority": pr, "created": u.get("created"),
                     "score": [pr, -down, u.get("created") or ""]})
    rows.sort(key=lambda r: (r["score"][0] is None, r["score"][0] or 0, r["score"][1], r["score"][2], r["unit"]))
    live = len(cl.slot_holders(snap, now))
    room = max(0, int(p.get("concurrency_max") or 0) - live)
    pressure = cl.pressure_room(snap, now)  # the launcher's held_reason applies the same rule
    if pressure is not None:
        room = min(room, pressure)
    return rows[:room]


def mismatches(snap):
    """[{unit, text}] where the recorded status disagrees with the unit's session or blockers."""
    import i18n
    out = []
    for u in _units(snap).values():
        if u.get("kind") == "foreman":
            continue
        sess = (snap.get("sessions") or {}).get(u.get("session") or "") or {}
        running = sess.get("state") in ("launching", "running")
        agent = ((snap.get("agents") or {}).get(u.get("agent") or "") or {}).get("status") == "running"
        if u["status"] == "done" and running:
            text = i18n.t("board.mismatch_done_running")
        elif u["status"] == "in_progress" and not (_live(snap, u) or agent):
            text = i18n.t("board.mismatch_no_session")
        elif u.get("ready") and u.get("blocked_by"):
            text = i18n.t("board.mismatch_ready_blocked")
        else:
            continue
        out.append({"unit": u["id"], "text": text})
    return out


def _short(ref):
    return ref if len(ref) <= 32 else ref[:31] + "…"


def board(snap, m):
    mis = {x["unit"]: x["text"] for x in mismatches(snap)}
    for sec in m.get("sections") or []:
        for r in sec["rows"]:
            u = _units(snap).get(r["id"]) or {}
            r["blocked_by"] = [_short(x) for x in u.get("blocked_by") or []]
            r["mismatch"] = mis.get(r["id"])
            r["chain"] = chain(snap, r["id"])
            r["priority"] = u.get("priority")
    m["next"] = [{k: x[k] for k in ("unit", "title", "repo", "why", "priority", "created")} for x in next_units(snap)]
    m["halts"] = list(snap.get("halts") or [])


# ---------- caller identity ----------

def default_by():
    env = os.environ.get("FOUNDRY_ROLE")
    return ENV_ROLE.get(env, env) if env else "owner"


def check_by(by, action="this change"):
    """The role this call acts as, or (None, refusal) when --by disagrees with FOUNDRY_ROLE.

    An absent role means "outside foundry"; acting as the owner still has to pass require_owner, so an
    emptied or unset FOUNDRY_ROLE inside a foundry session is not the owner."""
    env = os.environ.get("FOUNDRY_ROLE")
    if by in (None, "owner") and cl.owner_attested():
        return "owner", None  # the mod ran this on the owner's own input, inside the foreman's process tree
    by = by or default_by()
    if env and ENV_ROLE.get(env, env) != by:
        return None, f"refused: --by {by} but this session runs as FOUNDRY_ROLE={env}"
    if by == "owner":
        err = cl.require_owner(f"{action} as the owner")
        if err:
            return None, err
    return by, None


def own_unit(snap, by, uid):
    """Whether a lead/shop-boss caller owns uid: FOUNDRY_UNIT, else the unit's live session has its role."""
    env_unit = os.environ.get("FOUNDRY_UNIT")
    if env_unit:
        return env_unit == uid
    u = _units(snap).get(uid) or {}
    sess = (snap.get("sessions") or {}).get(u.get("session") or "") or {}
    return _live(snap, u) and sess.get("role") == SESSION_ROLE.get(by)


# ---------- unit set (and the `unit status <id> <status>` alias) ----------

def launch_path(uid, status):
    """The exact commands that take a unit in `status` to a launch."""
    then = f"then `foundry unit launch {uid}`"
    if status in ("parked", "blocked"):
        return f"`foundry unit status {uid} queue`, {then}"
    if status in CLOSED:
        return (f"reopening is the owner's call: `foundry unit status {uid} queue --quote \"<the owner's exact "
                f"words>\"`, {then}")
    return f"`foundry unit launch {uid}`"


def merged_since_change(u):
    """The unit's own PRs as evidence nobody types: one merged (the merge hook let it through only approved, CLEAN
    and at its head, or the github stream saw it merged), none still open, the last merge after the last change."""
    prs = u.get("prs") or []
    if any(str(p.get("state") or "").upper() == "OPEN" for p in prs):
        return False
    merged = [t for t in (cl.parse_ts(p.get("merged") or "") for p in prs
                          if str(p.get("state") or "").upper() == "MERGED") if t]
    if not merged:
        return False
    changed = cl.parse_ts(u.get("changed") or "")
    return changed is None or max(merged) >= changed


def verified(u):
    v = u.get("verify") or {}
    changed = cl.parse_ts(u.get("changed") or "")
    if v.get("passed") and (changed is None or (cl.parse_ts(v.get("ts") or "") or changed) >= changed):
        return True
    return merged_since_change(u)


def done_evidence_refusal(uid):
    return (f"refused: done needs evidence foundry records itself, and {uid} has none since its last code change: "
            f"a merged PR of the unit (recorded by the merge hook and the github stream), a passed release gate, "
            f"or the foreman's verify. With no PR, report what you ran and saw (`foundry unit report {uid} --text "
            f"'<what you ran and saw>'`); the foreman checks it and records `foundry unit verify {uid} --evidence "
            f"'...' --by foreman`, and its inbox note tells you to set done. A lead never verifies its own unit.")


def scope_refusal(snap, by, u, status, priority, quote=None):
    """None when `by` may make this change, else the refusal text. The foreman relaying the owner's exact words
    (`--quote`) has the owner's scope: 종료해줘 / 정리해줘 is the owner's call, carried out and journaled."""
    uid = u["id"]
    if by == "owner" or cl.relay_quote(by, quote):
        return None
    if by == "foreman":
        if priority is not None or status in FOREMAN_STATUSES or status is None:
            return None
        if status == "done" and release_gate_ok(snap, u):
            return None  # a release unit whose gate passed: closing it is foundry's act, not the owner's
        if status == "cancelled" and u.get("intake") and u["status"] == "queue":
            return None
        if status == "cancelled":
            return ("refused: on its own the foreman cancels only its own queued intake units; on the owner's "
                    "word add --quote \"<the owner's exact words>\"")
        return (f"refused: on its own the foreman sets only {', '.join(FOREMAN_STATUSES)} (done goes through the "
                "lead's verify gate); on the owner's word add --quote \"<the owner's exact words>\"")
    if by in ("lead", "shop-boss"):
        if not own_unit(snap, by, uid):
            return f"refused: a {by} changes only its own unit (FOUNDRY_UNIT), not {uid}"
        if priority is not None:
            return "refused: priority is the owner's (or the foreman's) call"
        if status not in LEAD_STATUSES:
            return f"refused: a {by} sets only {', '.join(LEAD_STATUSES)}"
        return None
    return f"refused: {by} never changes unit status; report to your lead instead"


def release_gate_ok(snap, u):
    """A release unit whose done gate holds: its tag and dev records are the verify evidence."""
    if not (u.get("release") or {}).get("prs"):
        return False
    import release
    return release.done_refusal(snap, u) is None


def set_status(uid, status=None, by=None, note=None, priority=None, repo=None, quote=None):
    by, err = check_by(by)
    if err:
        return fail(err)
    if repo:
        import launcher
        repos = launcher.load_repos()
        if launcher.repo_name(repo, repos) not in repos:
            return fail(f"refused: repo {repo!r} is not in the registry (`foundry repo list`; add it with "
                        f"`foundry repo add {repo} --path <main checkout>`)")
    if by not in ROLES:
        return fail(f"refused: unknown role {by}")
    if status is not None and status not in cl.STATUSES:
        msg = f"status {status!r} is not one of: {', '.join(cl.STATUSES)}"
        if str(status).lower() in LAUNCH_WORDS:
            cur = (_units(cl.load()).get(uid) or {}).get("status")
            if cur:
                msg += f". {uid} is {cur}; to launch it: {launch_path(uid, cur)}"
        return fail(msg)

    if status == "done":  # read GitHub/git outside the journal lock; a tag cut before the unit left no record
        import release
        try:
            fill = release.backfill(cl.load(), uid)
        except Exception:
            fill = []
        if fill:
            cl.append_many(fill)

    def decide(snap):
        u = _units(snap).get(uid)
        if not u:
            return cl.unknown_unit(uid, snap), []
        err = scope_refusal(snap, by, u, status, priority, quote)
        if err:
            return err, []
        relay = cl.relay_quote(by, quote)
        owner = by == "owner" or bool(relay)
        if status == "done" and not owner and not verified(u) and not release_gate_ok(snap, u):
            return done_evidence_refusal(uid), []
        if status == "done" and not owner:
            open_kids = [k for k in u.get("children") or [] if (_units(snap).get(k) or {}).get("status") not in CLOSED]
            if open_kids:
                return (f"refused: child unit(s) {', '.join(open_kids)} of the same ticket are still open; "
                        "done waits for them"), []
            import release
            why = release.done_refusal(snap, u)
            if why:
                return f"refused: {why}", []
        rec = {"id": uid, "status": status, "priority": priority, "note": note or (cl.RELAY_NOTE if relay else None),
               "by": by}
        if repo:
            rec["repo"] = repo
        if relay:
            rec["quote"] = relay
        recs = [("unit.set", rec)]
        if owner:
            recs.append(("owner.override", {k: v for k, v in rec.items() if k != "by"} | ({"by": by} if relay else {})))
            if _live(snap, u) and u["status"] not in CLOSED:
                what = ", ".join(x for x in (f"status {status}" if status else "",
                                             f"priority {priority}" if priority is not None else "", note or "") if x)
                said = f" (owner: \"{relay}\")" if relay else ""
                recs.append(("inbox.add", {"id": uid, "iid": cl.new_id("in", uid, "override", what),
                                           "text": f"Owner override: {what}{said}", "from": "owner"}))
        return None, recs

    err, snap = cl.transact(decide)
    if err:
        return fail(err)
    recs = apply_resolve()
    return emit({"unit": uid, "status": snap["units"][uid]["status"], "by": by,
                 **({"quote": cl.relay_quote(by, quote)} if cl.relay_quote(by, quote) else {}),
                 "resolved": [r[1] for r in recs]}) or 0


# ---------- commands ----------

def cmd_set(a):
    return set_status(a.id, a.status, a.by, a.note, a.priority, a.repo, a.quote)


def cmd_verify(a):
    """The foreman's (or the owner's) check of a unit's reported evidence; never the lead's word on its own work."""
    snap = cl.load()
    if a.id not in snap["units"]:
        return fail(cl.unknown_unit(a.id, snap))
    by, err = check_by(a.by)
    if err:
        return fail(err)
    if by not in VERIFIERS:
        return fail(f"refused: a {by} does not verify its own work. Record what you ran and saw with "
                    f"`foundry unit report {a.id} --text '<what you ran and saw>'`; done then follows from the unit's "
                    "merged PR, or from the foreman's verify after it reads that report.")
    recs = [("verify.pass", {"unit": a.id, "evidence": a.evidence, "by": by})]
    u = snap["units"][a.id]
    if _live(snap, u) and u.get("status") not in CLOSED:
        text = f"Verified by the {by}: {a.evidence}. Set done: foundry unit status {a.id} done"
        recs.append(("inbox.add", {"id": a.id, "iid": cl.new_id("in", a.id, "verify", a.evidence), "text": text,
                                   "from": by}))
    cl.append_many(recs)
    return emit({"verified": a.id, "by": by}) or 0


def cmd_deployed(a):
    """The `deploy.done` fact a lead records after verifying a deploy it was told to do; the deploy is not run here."""
    snap = cl.load()
    if a.id not in snap["units"]:
        return fail(cl.unknown_unit(a.id, snap))
    by, err = check_by(a.by, "recording a deploy")
    if err:
        return fail(err)
    if by not in ("owner", "lead") or (by == "lead" and not own_unit(snap, by, a.id)):
        return fail(f"refused: only the owner or {a.id}'s own lead records its deploy")
    cl.append("deploy.done", unit=a.id, app=a.app, env=a.target_env, tag=a.tag, evidence=a.evidence, by=by)
    recs = apply_resolve()
    return emit({"deployed": f"deploy:{a.app}@{a.target_env}", "unit": a.id, "resolved": [r[1] for r in recs]}) or 0


def cmd_block(a):
    bad = [r for r in a.on if not parse_ref(r)]
    if bad:
        return fail(f"unknown ref(s) {bad}: use one of {', '.join(k + ':' for k in KINDS)}")

    def decide(snap):
        if a.id not in snap["units"]:
            return cl.unknown_unit(a.id, snap), []
        missing = [b for k, b in map(parse_ref, a.on) if k == "unit" and b not in snap["units"]]
        if missing:
            return f"unknown unit(s) {missing}", []
        cyc = find_cycle(snap, a.id, a.on)
        if cyc:
            return "refused: cycle " + " -> ".join(cyc), []
        return None, [("unit.block", {"unit": a.id, "refs": a.on})]

    err, _ = cl.transact(decide)
    if err:
        return fail(err)
    recs = apply_resolve()
    return emit({"blocked": a.id, "on": a.on, "resolved": [r[1] for r in recs]}) or 0


def cmd_unblock(a):
    if str(a.ref).startswith("decision:"):
        # A decision card is resolved by its answer (`foundry decide answer|chat`); lifting it by hand is the owner's.
        err = cl.require_owner(f"unblocking {a.id} from {a.ref} without an answer")
        if err:
            return fail(err + f" To answer the card: foundry decide answer {a.ref[9:]} <option>.")
    u = cl.load()["units"].get(a.id)
    if not u or a.ref not in u.get("blocked_by", []):
        return fail(f"unit {a.id} is not blocked by {a.ref}")
    recs = [("unit.unblock", {"unit": a.id, "ref": a.ref})]
    if u["blocked_by"] == [a.ref]:
        recs.append(("unit.ready", {"unit": a.id, "eid": cl.new_id("ev", a.id, "ready")}))
    cl.append_many(recs)
    return emit({"unblocked": a.id, "ref": a.ref, "ready": len(recs) > 1}) or 0


def note_records(uid, text, by):
    return [("unit.note", {"id": uid, "text": text, "by": by}),
            ("inbox.add", {"id": uid, "iid": cl.new_id("in", uid, text), "text": text, "from": by})]


def cmd_note(a):
    snap = cl.load()
    if a.id not in snap["units"]:
        return fail(cl.unknown_unit(a.id, snap))
    by, err = check_by(a.by)
    if err:
        return fail(err)
    cl.append_many(note_records(a.id, a.text, by))
    return emit({"noted": a.id}) or 0


def cmd_next(a):
    rows = next_units(cl.load())
    if a.json:
        return emit(rows) or 0
    for r in rows:
        print(f"{r['unit']:<24} {r['repo'] or '-':<16} {r['why']}")
    if not rows:
        print("nothing ready")
    return 0


def cmd_resolve(a):
    return emit({"appended": [{"op": op, **f} for op, f in apply_resolve()]}) or 0


def lead_repo(snap, by, unit):
    """The repo a lead/shop-boss caller owns, from FOUNDRY_UNIT or --unit."""
    uid = os.environ.get("FOUNDRY_UNIT") or unit
    u = _units(snap).get(uid or "")
    return u.get("repo") if u and own_unit(snap, by, uid) else None


def cmd_halt(a):
    by, err = check_by(a.by)
    if err:
        return fail(err)
    if by not in ("owner", "lead", "shop-boss", "foreman"):
        return fail(f"refused: {by} does not pull the andon; report to your lead")
    snap = cl.load()
    if a.repo and by in ("lead", "shop-boss") and lead_repo(snap, by, a.unit) != a.repo:
        return fail(f"refused: a {by} halts only its own unit's repo")
    cl.append("halt", repo=a.repo, reason=a.reason, by=by)
    return emit({"halted": a.repo or "global", "by": by}) or 0


def cmd_resume_line(a):
    by, err = check_by(a.by)
    if err:
        return fail(err)
    snap = cl.load()
    if (a.repo or "global") not in {h["scope"] for h in snap["halts"]}:
        return fail(f"no halt on {a.repo or 'the line'}")
    if not a.repo and by != "owner":
        return fail("refused: only the owner lifts a global halt")
    if a.repo and by != "owner" and not (by in ("lead", "shop-boss") and lead_repo(snap, by, a.unit) == a.repo):
        return fail(f"refused: a repo halt is lifted by the owner or {a.repo}'s lead")
    cl.append("line.resume", repo=a.repo, by=by)
    return emit({"resumed": a.repo or "global", "by": by}) or 0


def lock_caller_refusal(snap, uid, act="releasing"):
    """Locks are taken and released by the unit's own lead (or the owner); either act for another unit is the owner's."""
    env = os.environ.get("FOUNDRY_ROLE") or ""
    by = ENV_ROLE.get(env, env)
    if by in ("lead", "shop-boss") and own_unit(snap, by, uid):
        return None
    return cl.require_owner(f"{act} a lock for unit {uid}", snap)


def cmd_lock(a):
    err = lock_caller_refusal(cl.load(), a.unit, "taking" if a.sub == "take" else "releasing")
    if err:
        return fail(err)

    def decide(snap):
        if a.unit not in snap["units"]:
            return cl.unknown_unit(a.unit, snap), []
        holder = lock_holder(snap, a.name)
        if a.sub == "take":
            if holder and holder != a.unit:
                return f"refused: lock {a.name} is held by {holder}", []
            return None, ([] if holder == a.unit else [("lock.take", {"name": a.name, "unit": a.unit})])
        if holder != a.unit:
            return f"refused: lock {a.name} is held by {holder or 'nobody'}, not {a.unit}", []
        return None, [("lock.release", {"name": a.name, "unit": a.unit})]

    err, _ = cl.transact(decide)
    if err:
        return fail(err)
    recs = apply_resolve()
    return emit({a.sub: a.name, "unit": a.unit, "resolved": [r[1] for r in recs]}) or 0


def cmd_main_moved(a):
    snap = cl.load()
    live = [u["id"] for u in _units(snap).values()
            if u.get("repo") == a.repo and u["status"] not in CLOSED and _live(snap, u)]
    text = f"{a.repo} main moved to {a.sha}: rebase or re-check before you push"
    recs = [("repo.main_moved", {"repo": a.repo, "sha": a.sha})]
    for uid in live:
        recs += note_records(uid, text, "shop-boss")[1:]
    cl.append_many(recs)
    return emit({"repo": a.repo, "sha": a.sha, "noted": live}) or 0


def add_unit_cli(u):
    st = u.add_parser("set")
    st.add_argument("id"); st.add_argument("--status"); st.add_argument("--priority", type=int)
    st.add_argument("--by", choices=ROLES); st.add_argument("--note")
    st.add_argument("--repo", help="registry name: where `unit launch` makes the unit's worktree")
    st.add_argument("--quote", help="the foreman acting on the owner's word: the owner's exact chat words")
    st.set_defaults(func=cmd_set)
    v = u.add_parser("verify")
    v.add_argument("id"); v.add_argument("--evidence", required=True, help="what the lead reported and you checked")
    v.add_argument("--by", choices=ROLES)
    v.set_defaults(func=cmd_verify)
    dp = u.add_parser("deployed", help="record a verified deploy (resolves deploy:<app>@<env> refs)")
    dp.add_argument("id"); dp.add_argument("--app", required=True)
    dp.add_argument("--env", dest="target_env", required=True)
    dp.add_argument("--tag"); dp.add_argument("--evidence", required=True); dp.add_argument("--by", choices=ROLES)
    dp.set_defaults(func=cmd_deployed)
    b = u.add_parser("block")
    b.add_argument("id"); b.add_argument("--on", action="append", required=True, metavar="REF")
    b.set_defaults(func=cmd_block)
    ub = u.add_parser("unblock")
    ub.add_argument("id"); ub.add_argument("--ref", required=True)
    ub.set_defaults(func=cmd_unblock)
    n = u.add_parser("note")
    n.add_argument("id"); n.add_argument("--text", required=True); n.add_argument("--by", choices=ROLES)
    n.set_defaults(func=cmd_note)


def add_cli(sp):
    import argparse
    nx = sp.add_parser("next", help="ready units, best first, cut by concurrency and pressure")
    nx.add_argument("--json", action="store_true")
    nx.set_defaults(func=cmd_next)
    g = sp.add_parser("graph").add_subparsers(dest="sub", required=True)
    g.add_parser("resolve").set_defaults(func=cmd_resolve)
    h = sp.add_parser("halt", help="andon: hold every launch on the line or one repo")
    h.add_argument("--repo"); h.add_argument("--reason", required=True)
    h.add_argument("--by", choices=ROLES); h.add_argument("--unit", help="the caller's unit when FOUNDRY_UNIT is unset")
    h.set_defaults(func=cmd_halt)
    rl = sp.add_parser("resume-line")
    rl.add_argument("--repo"); rl.add_argument("--by", choices=ROLES); rl.add_argument("--unit")
    rl.set_defaults(func=cmd_resume_line)
    lk = sp.add_parser("lock").add_subparsers(dest="sub", required=True)
    for name in ("take", "release"):
        x = lk.add_parser(name)
        x.add_argument("name"); x.add_argument("--unit", required=True)
        x.set_defaults(func=cmd_lock)
    if "repo" in sp.choices:  # launcher's `foundry repo` group
        repo = next(x for x in sp.choices["repo"]._actions if isinstance(x, argparse._SubParsersAction))
    else:
        repo = sp.add_parser("repo").add_subparsers(dest="sub", required=True)
    mm = repo.add_parser("main-moved")
    mm.add_argument("repo"); mm.add_argument("--sha", required=True)
    mm.set_defaults(func=cmd_main_moved)
