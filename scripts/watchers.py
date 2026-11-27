"""Foreman Monitor streams: `foundry watch stream --kind github|sessions|pressure|sync|stalls`.

Each stream is a model-free loop the foreman runs under the Monitor tool. It prints one line per event and
nothing on a quiet cycle, journals what it sees, and exits after --max-minutes so the foreman re-arms it.
Every stream also journals a `foreman.state` heartbeat (at most once a minute): the board and the owner's
SessionStart note read it to tell whether anything is collecting at all.
"""

import datetime as dt
import fnmatch
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import graph  # noqa: E402
import i18n  # noqa: E402
import prefs  # noqa: E402
import roles  # noqa: E402
import views  # noqa: E402

KINDS = ("github", "sessions", "pressure", "sync", "stalls")
DEFAULT_INTERVAL = {"github": 120, "sessions": 120, "sync": 300, "stalls": 60}
FOREMAN_ALIVE_MIN = 35
HEARTBEAT_S = 60
LARGE_WRITE_BYTES = 40000
CRON_RECREATE_DAYS = 6  # CronCreate jobs expire after 7 days
WRITE_IN_PARTS = "write in parts of ≤250 lines"
STALL_TEXT = views.Labels({"api_loop": "stall_api_loop", "no_progress": "stall_no_progress",
                           "repeat_error": "stall_repeat_error", "auth": "stall_auth"}, "cli")
# A lead that died on the model gateway or its auth: the session ended with one of these as its last words.
AUTH_RE = re.compile(r"(?i)authentication_error|invalid (x-)?api[ -]?key|oauth token (has )?expired|"
                     r"please run /login|API Error: (401|403|407|502|503)\b|bad gateway|proxy authentication|"
                     r"permission_error|could not resolve authentication")


def auth_failure(entries):
    """The auth / gateway error a session ended on, or None: only its last few transcript entries count."""
    for e in list(entries)[-6:][::-1]:
        text = json.dumps(e, ensure_ascii=False)
        m = AUTH_RE.search(text)
        if m:
            return text[max(0, m.start() - 40):m.end() + 60]
    return None


def auth_step(missing):
    if missing:
        return (f"오너 할 일: 게이트웨이 환경변수 {', '.join(missing)} 를 셸이나 settings env 에 다시 설정 "
                "(값은 foundry 가 저장하지 않음). 설정되면 리드를 자동으로 다시 띄움.")
    return "오너 할 일: 게이트웨이 로그인 상태 확인 (/login). 환경이 맞으면 리드를 자동으로 다시 띄움."


# ---------- state ----------

def _stall(s, uid):
    return s["stalls"].setdefault(uid, {"active": False, "kind": None, "detail": None, "count": 0, "since": None,
                                        "episodes": 0, "actions": [], "escalated": False})


def _op_stalled(s, r, ts):
    st = _stall(s, r["unit"])
    if not st["active"]:
        st.update({"active": True, "since": ts, "actions": [], "escalated": False})
        st["episodes"] += 1
    st.update({"kind": r.get("kind"), "detail": r.get("detail"), "count": r.get("count") or 0})


def _op_recover(s, r, ts):
    _stall(s, r["unit"])["actions"].append({"action": r.get("action"), "ts": ts})


def _op_escalate(s, r, ts):
    _stall(s, r["unit"])["escalated"] = True


def _op_cleared(s, r, ts):
    st = _stall(s, r["unit"])
    st.update({"active": False, "cleared": ts})


def _op_foreman(s, r, ts):
    s["foreman"] = {"alive": bool(r.get("alive")), "ts": ts}


def _op_foreman_def(s, r, ts):
    s["foreman_def"] = {"hash": r.get("hash"), "session": r.get("session"), "ts": ts}


def _op_wait_monitor(s, r, ts):
    if r.get("unit") in s["units"]:
        s["units"][r["unit"]]["monitor_wait"] = {"what": r.get("what"), "until": r.get("until"), "ts": ts}


def _op_cron_created(s, r, ts):
    s["crons"][r.get("key") or r.get("id") or ts] = {"id": r.get("id"), "cron": r.get("cron"), "ts": ts}


def _op_cron_deleted(s, r, ts):
    for k, c in list(s["crons"].items()):
        if c.get("id") and c["id"] == r.get("id"):
            del s["crons"][k]


OPS = {"unit.stalled": _op_stalled, "stall.recover": _op_recover, "stall.escalate": _op_escalate,
       "stall.cleared": _op_cleared, "foreman.state": _op_foreman, "wait.monitor": _op_wait_monitor,
       "cron.created": _op_cron_created, "cron.deleted": _op_cron_deleted, "foreman.def": _op_foreman_def}
STATE = {"stalls": {}, "foreman": {"alive": False, "ts": None}, "crons": {}, "foreman_def": None}
UNIT_FIELDS = {"monitor_wait": None}


def _p():
    return prefs.load()


def foreman_alive(snap, now=None):
    """True when a foreman stream heartbeat landed within the last 35 minutes."""
    f = snap.get("foreman") or {}
    ts = cl.parse_ts(f.get("ts"))
    return bool(f.get("alive")) and ts is not None and (now or cl.now()) - ts <= dt.timedelta(minutes=FOREMAN_ALIVE_MIN)


FOREMAN_MD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents", "foreman.md")


def foreman_def_hash(path=FOREMAN_MD):
    """The installed foreman.md's content hash (12 hex), or None when it cannot be read."""
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except OSError:
        return None


def foreman_drift(snap):
    """(loaded, installed) when the foreman session runs a foreman.md other than the installed one, else None.
    `foreman.def` is journaled at the foreman's SessionStart (startup or resume: when the definition is loaded)."""
    loaded = (snap.get("foreman_def") or {}).get("hash")
    now = foreman_def_hash()
    return (loaded, now) if loaded and now and loaded != now else None


def foreman_drift_line(snap):
    """The "foreman restart needed" line, once per (loaded, installed) pair; None when nothing new to say."""
    d = foreman_drift(snap)
    if not d or _side("watch-foreman-def.json", []) == list(d):
        return None
    _side_write("watch-foreman-def.json", list(d))
    return (f"foreman restart needed: agents/foreman.md changed since the foreman session loaded it ({d[0]} -> {d[1]}); "
            f"restart it with the foundry-handoff skill's restart procedure (stop it with `{foreman_stop_cmd(snap)}`)")


def foreman_stop_cmd(snap):
    """`claude stop <short id>` for the current foreman: the name `foreman` is ambiguous once stale sessions
    share it."""
    sid = (snap.get("foreman_current") or {}).get("session")
    return f"claude stop {sid[:8]}" if sid else "claude stop foreman"


def heartbeat(now):
    """Journal `foreman.state` unless one landed in the last minute (several streams share it)."""
    def decide(snap):
        ts = cl.parse_ts((snap.get("foreman") or {}).get("ts"))
        if (snap.get("foreman") or {}).get("alive") and ts and now - ts < dt.timedelta(seconds=HEARTBEAT_S):
            return False, []
        return True, [("foreman.state", {"alive": True})]
    return cl.transact(decide)[0]


def _side(name, default):
    try:
        with open(cl.path(name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _side_write(name, data):
    cl.write_atomic(cl.path(name), json.dumps(data, ensure_ascii=False))


# ---------- transcripts ----------

def transcript_root():
    if os.environ.get("FOUNDRY_TRANSCRIPTS"):
        return os.environ["FOUNDRY_TRANSCRIPTS"]
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(base, "projects")


def transcript_path(sess):
    sid = sess.get("session_id")
    if not sid:
        return None
    root = transcript_root()
    if sess.get("cwd"):
        p = os.path.join(root, re.sub(r"[^A-Za-z0-9]", "-", sess["cwd"]), sid + ".jsonl")
        if os.path.exists(p):
            return p
    hits = glob.glob(os.path.join(root, "*", sid + ".jsonl"))
    return hits[0] if hits else None


def read_tail(p, limit=512_000):
    with open(p, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - limit))
        data = f.read().decode("utf-8", "replace")
    lines = data.splitlines()
    if size > limit:
        lines = lines[1:]  # the first line is cut mid-record
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _blocks(e):
    c = (e.get("message") or {}).get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return [b for b in c or [] if isinstance(b, dict)]


def _ts(e):
    t = e.get("timestamp") or e.get("ts")
    return cl.parse_ts(str(t).replace("Z", "+00:00")) if t else None


def _result_text(b):
    c = b.get("content")
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return re.sub(r"\s+", " ", str(c or "")).strip()[:200]


def classify(e):
    """One transcript entry as 'progress' | 'request' | 'result_error' | None (not counted)."""
    t = e.get("type")
    blocks = _blocks(e)
    if t == "user":
        results = [b for b in blocks if b.get("type") == "tool_result"]
        if not results:
            return None
        return "result_error" if all(b.get("is_error") for b in results) else "progress"
    if t == "assistant":
        if any(b.get("type") == "text" and (b.get("text") or "").strip() for b in blocks):
            return "progress"
        return "request"
    if t == "system" and "api" in str(e.get("subtype") or "").lower():
        return "request"
    return None


def monitor_active(entries):
    """A Monitor tool call with no tool_result yet: the lead is waiting on purpose."""
    done = {b.get("tool_use_id") for e in entries for b in _blocks(e) if b.get("type") == "tool_result"}
    return any(b.get("type") == "tool_use" and b.get("name") == "Monitor" and b.get("id") not in done
               for e in entries for b in _blocks(e))


def detect(entries, now, p):
    """(kind, detail, count) for a stalled transcript, else None.

    repeat_error: the last 3+ tool results are errors with the same text (a deny loop).
    api_loop: 2+ model requests (distinct message ids) since the last tool result or text output.
    no_progress: nothing but pending work for stall_warn_min (a finished turn is idle, not stalled)."""
    if monitor_active(entries):
        return None
    errs = []
    for e in reversed(entries):
        c = classify(e)
        if c == "result_error":
            errs.append(_result_text(next(b for b in _blocks(e) if b.get("type") == "tool_result")))
        elif c == "progress" and e.get("type") == "user":
            break
    if len(errs) >= 3 and len(set(errs[:3])) == 1:
        n = 0
        for t in errs:
            if t != errs[0]:
                break
            n += 1
        return "repeat_error", errs[0][:120], n
    run, last_req = [], None
    for e in reversed(entries):
        c = classify(e)
        if c in ("progress", "result_error"):
            break
        if c == "request":
            mid = (e.get("message") or {}).get("id") or e.get("requestId") or e.get("uuid") or id(e)
            if mid not in run:
                run.append(mid)
            last_req = last_req or _ts(e)
    if len(run) >= 2:
        return "api_loop", f"{len(run)} model requests with no tool result or text", len(run)
    counted = [e for e in entries if classify(e)]
    if not counted:
        return None
    last = counted[-1]
    if last.get("type") == "assistant" and classify(last) == "progress" \
            and not any(b.get("type") == "tool_use" for b in _blocks(last)):
        return None  # the turn ended with text: idle
    activity = [_ts(e) for e in counted if classify(e) in ("progress", "result_error") and _ts(e)]
    since = activity[-1] if activity else _ts(counted[0])
    if since is None:
        return None
    idle = int((now - since).total_seconds() // 60)
    if idle >= p["stall_warn_min"]:
        return "no_progress", f"no tool result or text for {idle} min", idle
    return None


def _parked(u, now):
    mw = u.get("monitor_wait") or {}
    until = cl.parse_ts(mw.get("until"))
    return u["status"] in ("waiting_others", "waiting_owner", "done") or (until is not None and until > now)


def _open_card(uid, st, kind, detail):
    try:
        import decide
    except ImportError:
        return False
    if not hasattr(decide, "open_card"):
        return False
    try:
        decide.open_card(unit=uid, key=f"stall:{uid}:{st.get('since') or ''}:{kind}",
                         question=f"{uid} stalled again ({kind}: {detail}). Relaunch, re-plan, or stop?",
                         options=[{"key": "relaunch", "label": "relaunch"}, {"key": "replan", "label": "re-plan"},
                                  {"key": "stop", "label": "stop"}],
                         asked_by="watchers")
        return True
    except Exception:  # a broken card must not hide the escalation: fall back to the journal
        return False


def _auth_recover(uid, done):
    """A lead dead on auth: the owner's one step as a to-do, and one resume as soon as every recorded gateway
    variable has a value again."""
    import launcher
    _, missing = launcher.gateway_env()
    recs = []
    if "owner_step" not in done:
        recs += [("stall.recover", {"unit": uid, "action": "owner_step", "note": auth_step(missing)}),
                 ("owner.todo", {"kind": "auth", "target": uid, "tid": f"auth:{uid}",
                                 "text": f"{uid}: 인증/게이트웨이 오류로 리드가 멈춤. {auth_step(missing)}"})]
    if missing or "resume" in done:
        return recs
    rc, out = launcher.resume(uid)
    if rc or "error" in out or "held" in out:
        why = out.get("error") or f"held: {(out.get('held') or {}).get('reason')}"
        return recs + [("stall.recover", {"unit": uid, "action": "resume_failed", "note": str(why)[:300]})]
    return recs + [("stall.recover", {"unit": uid, "action": "resume", "note": "gateway env present"})]


def respond(uid, found, st, now, p):
    """Records and printed lines for one stalled unit: the stall itself once per episode, then recovery once."""
    kind, detail, count = found
    recs, lines = [], []
    new = not st.get("active")
    if new or st.get("kind") != kind:
        recs.append(("unit.stalled", {"unit": uid, "kind": kind, "detail": detail, "count": count}))
        recs.append(("nearmiss", {"kind": "stall", "unit": uid, "detail": f"{kind}: {detail}"}))
        lines.append(f"stall {uid} {kind}: {detail}")
    if kind == "auth":  # never a stall card: the fix is the environment, then one resume
        got = _auth_recover(uid, [a["action"] for a in (st.get("actions") or [])] if not new else [])
        return recs + got, lines + [f"stall {uid} auth {r['action']}: {r.get('note')}" for op, r in got
                                    if op == "stall.recover"]
    episodes = (st.get("episodes") or 0) + (1 if new else 0)
    if episodes >= 2:
        if new:
            card = _open_card(uid, st, kind, detail)
            recs.append(("stall.escalate", {"unit": uid, "kind": kind, "detail": detail, "card": card}))
            lines.append(f"stall {uid} escalated: second stall, owner decides")
        return recs, lines
    done = [a["action"] for a in (st.get("actions") or [])] if not new else []
    if "relaunch_failed" in done:
        return recs, lines  # one failed relaunch per episode: the foreman already has the event
    since = cl.parse_ts(st.get("since")) or now
    if kind == "api_loop" and "relaunch" not in done:
        act = {"action": "relaunch", "note": WRITE_IN_PARTS}
    elif kind == "no_progress" and "nudge" not in done:
        act = {"action": "nudge", "note": "no progress: report where you are, or park and exit"}
    elif kind == "no_progress" and "relaunch" not in done and (count >= p["stall_kill_min"]
                                                             or now - since >= dt.timedelta(minutes=p["stall_kill_min"])):
        act = {"action": "relaunch", "note": "no progress past stall_kill_min"}
    elif kind == "repeat_error" and "triage" not in done:
        act = {"action": "triage", "note": "same error repeated: triage the denial or error"}
    else:
        return recs, lines
    if act["action"] == "relaunch":
        import launcher
        try:
            launcher.relaunch(uid, note=act["note"])
        except Exception as ex:  # journal only what happened, and wake the foreman
            why = f"{act['note']} (relaunch failed: {ex})"[:300]
            recs.append(("stall.recover", {"unit": uid, "action": "relaunch_failed", "note": why}))
            recs.append(("event", {"eid": cl.new_id("ev", uid, "relaunch_failed", why), "unit": uid, "wid": None,
                                   "kind": "stall", "ref": None, "summary": f"{uid}: stall relaunch failed: {why}"}))
            lines.append(f"event {uid} stall: relaunch failed: {why}")
            return recs, lines
    recs.append(("stall.recover", {"unit": uid, **act}))
    if act["action"] == "nudge":
        recs.append(("inbox.add", {"id": uid, "iid": cl.new_id("in", uid, "stall"), "from": "foundry",
                                   "text": f"[foundry] stall: {detail}. {act['note']}."}))
    lines.append(f"stall {uid} recover {act['action']}: {act['note']}")
    return recs, lines


STALL_HEALTH_CYCLES = 3  # consecutive cycles where no running lead's transcript parsed: say it once


def stall_health(running, read, failed):
    """Record this stalls cycle's summary (a side file, not the journal) and return the warning line, once per
    broken streak: running leads exist and none of their transcripts could be read and parsed, N cycles in a row."""
    h = _side("watch-stall-health.json", {})
    broken = running > 0 and read == 0
    streak = (h.get("streak") or 0) + 1 if broken else 0
    said = bool(h.get("said")) and broken
    line = None
    if streak >= STALL_HEALTH_CYCLES and not said:
        line = (f"stall detector may be broken: {streak} cycles in a row with {running} running lead(s) and no "
                f"transcript parsed ({failed} failed this cycle; see watch-stall-health.json)")
        said = True
    _side_write("watch-stall-health.json", {"ts": cl.iso(cl.now()), "running": running, "read": read,
                                            "failed": failed, "streak": streak, "said": said})
    return line


def _parse_tail(tp, now, p):
    """(ok, found): ok is False when the transcript is missing, unreadable, or yields no records."""
    if not tp:
        return False, None
    try:
        recs = read_tail(tp)
    except OSError:
        return False, None
    if not recs:
        return False, None
    try:
        return True, detect(recs, now, p)
    except Exception:  # a transcript shape the detector does not understand
        return False, None


def cycle_stalls(now, p):
    snap = cl.load()
    recs, lines = [], []
    running = read = failed = 0
    for sess in snap["sessions"].values():
        uid = sess.get("unit")
        u = snap["units"].get(uid or "")
        if not u:
            continue
        st = snap["stalls"].get(uid) or {}
        found = None
        if sess.get("state") in ("launching", "running") and not _parked(u, now):
            running += 1
            ok, found = _parse_tail(transcript_path(sess), now, p)
            read += ok
            failed += not ok
        elif sess.get("state") in ("done", "stopped", "lost") and u.get("session") == sess.get("name") \
                and u["status"] not in graph.CLOSED:
            tp = transcript_path(sess)
            try:
                why = auth_failure(read_tail(tp)) if tp else None
            except OSError:
                why = None
            if why:
                found = ("auth", why[:160], 1)
        if found:
            r, l_ = respond(uid, found, st, now, p)
            recs += r
            lines += l_
        elif st.get("active"):
            recs.append(("stall.cleared", {"unit": uid}))
            lines.append(f"stall {uid} cleared: progress resumed")
    warn = stall_health(running, read, failed)
    if warn:
        lines.append(warn)
    return recs, lines


# ---------- pressure ----------

def _docker_cpu():
    docker = shlex.split(os.environ.get("FOUNDRY_DOCKER") or "docker")
    try:
        r = subprocess.run([*docker, "stats", "--no-stream", "--format", "{{.CPUPerc}}"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    vals = [float(x.strip().rstrip("%")) for x in r.stdout.split() if re.match(r"^[\d.]+%?$", x.strip())]
    return round(sum(vals), 1)


DOCKER_DF_TIMEOUT_S = 5
DOCKER_CACHE_OLD_DAYS = 7
SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([kKMGT]?B)\s*$")
SIZE_GB = {"B": 1e-9, "kB": 1e-6, "KB": 1e-6, "MB": 1e-3, "GB": 1.0, "TB": 1e3}
AGE_RE = re.compile(r"(\d+|an?|About an?)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
AGE_DAYS = {"second": 0, "minute": 0, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}


def _gb(text):
    m = SIZE_RE.match(str(text or ""))
    return float(m.group(1)) * SIZE_GB.get(m.group(2), 0) if m else 0.0


def _age_days(text):
    """Days in Docker's "8 days ago" / "2 weeks ago" / "About an hour ago"; None when it says no age."""
    m = AGE_RE.search(str(text or ""))
    if not m:
        return None
    n = m.group(1)
    return (int(n) if n.isdigit() else 1) * AGE_DAYS[m.group(2).lower()]


def _docker_df():
    """`docker system df -v`: image count, build cache GB, and the cache last used DOCKER_CACHE_OLD_DAYS or more
    ago. Each value None when Docker does not answer within DOCKER_DF_TIMEOUT_S (Colima off, no daemon)."""
    none = {"docker_images": None, "docker_build_cache_gb": None, "docker_cache_old_gb": None}
    docker = shlex.split(os.environ.get("FOUNDRY_DOCKER") or "docker")
    try:
        r = subprocess.run([*docker, "system", "df", "-v", "--format", "{{json .}}"], capture_output=True, text=True,
                           timeout=DOCKER_DF_TIMEOUT_S)
        d = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        d = None
    if not isinstance(d, dict):
        return none
    cache = [c for c in d.get("BuildCache") or [] if isinstance(c, dict)]
    old = [c for c in cache if (_age_days(c.get("LastUsedSince")) if _age_days(c.get("LastUsedSince")) is not None
                                else _age_days(c.get("CreatedSince")) or 0) >= DOCKER_CACHE_OLD_DAYS]
    return {"docker_images": len(d.get("Images") or []),
            "docker_build_cache_gb": round(sum(_gb(c.get("Size")) for c in cache), 2),
            "docker_cache_old_gb": round(sum(_gb(c.get("Size")) for c in old), 2)}


def sample():
    """load per CPU, memory reclaimable %, lowest disk free GB, docker CPU %. FOUNDRY_SAMPLE_JSON: a file to read instead."""
    fake = os.environ.get("FOUNDRY_SAMPLE_JSON")
    if fake:
        with open(fake) as f:
            return json.load(f)
    import pressure
    m = pressure.measure()
    disks = list((m.get("disk_free_gb") or {}).values())
    return {"load_per_cpu": m.get("load_per_cpu"), "mem_reclaimable_pct": m.get("mem_free_pct"),
            "disk_free_gb": min(disks) if disks else None, "docker_cpu_pct": _docker_cpu(), **_docker_df()}


def level_of(key, v, pp):
    """One metric's own level against its `<key>_warn` / `<key>_danger` prefs; None without a value."""
    if not isinstance(v, (int, float)):
        return None
    high = WORSE[key] == "high"
    for lv in ("danger", "warn"):
        t = pp.get(f"{key}_{lv}")
        if t is not None and (v >= t if high else v <= t):
            return lv
    return "ok"


def band(s, pp):
    """('ok'|'warn'|'danger', reasons)."""
    rank, reasons = [0], []

    def hit(lv, text):
        reasons.append(text)
        rank[0] = max(rank[0], 2 if lv == "danger" else 1)

    load, mem, disk, dock = (s.get(k) for k in ("load_per_cpu", "mem_reclaimable_pct", "disk_free_gb", "docker_cpu_pct"))
    if load is not None and load >= pp["load_per_cpu_warn"]:
        hit("danger" if load >= pp["load_per_cpu_danger"] else "warn", f"load: {load} per CPU")
    if mem is not None and mem <= pp["mem_reclaimable_pct_warn"]:
        hit("danger" if mem <= pp["mem_reclaimable_pct_danger"] else "warn", f"memory: {mem}% reclaimable")
    if disk is not None and disk <= pp["disk_free_gb_warn"]:
        hit("danger" if disk <= pp["disk_free_gb_danger"] else "warn", f"disk: {disk} GB free")
    if dock is not None and dock >= pp["docker_cpu_pct_warn"]:
        hit("warn", f"docker: {dock}% CPU")
    return ("ok", "warn", "danger")[rank[0]], reasons


def _metric_rows(lines):
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def read_metrics(since=None):
    """Every sample, or with `since` (an aware datetime) only the tail from there: the file is read backwards a
    block at a time and stops at the first block that starts before `since`, so a short window reads a short tail."""
    try:
        with open(cl.path("metrics.jsonl"), "rb") as f:
            if since is None:
                return _metric_rows(f.read().decode("utf-8", "replace").splitlines())
            f.seek(0, os.SEEK_END)
            pos, buf = f.tell(), b""
            while pos > 0:
                step = min(65536, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
                parts = buf.split(b"\n", 2)
                first = parts[1] if pos and len(parts) > 1 else parts[0]  # the first whole line read so far
                t = cl.parse_ts((_metric_rows([first.decode("utf-8", "replace")]) or [{}])[0].get("ts"))
                if t and t < since:
                    break
    except OSError:
        return []
    rows = _metric_rows(buf.decode("utf-8", "replace").splitlines()[1 if pos else 0:])
    return [r for r in rows if (cl.parse_ts(r.get("ts")) or since) >= since]


def write_metric(rec, now, keep_hours):
    """Append one sample and drop the ones older than keep_hours (a ring by age)."""
    cut = now - dt.timedelta(hours=keep_hours)
    rows = [r for r in read_metrics() if (cl.parse_ts(r.get("ts")) or now) >= cut] + [rec]
    cl.write_atomic(cl.path("metrics.jsonl"), "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def cycle_pressure(now, p):
    s = sample()
    level, reasons = band(s, p["pressure"])
    write_metric({"ts": cl.iso(now), **s, "level": level}, now, p["pressure"]["keep_hours"])
    if level == cl.pressure_level(cl.load()):
        return [], []
    return [("pressure", {"level": level, "reasons": reasons, "sample": s})], \
           [f"pressure {level}: " + ("; ".join(reasons) or "back to normal")]


# ---------- github, sessions, sync ----------

def _pr_refs(snap):
    import aging
    refs = {}
    for u in sorted(snap["units"].values(), key=lambda u: u["status"] == "done"):
        if u["kind"] == "foreman":
            continue
        if u["status"] == "done":  # only until GitHub says the PR is closed or merged: then it leaves the board
            states = {f"{p.get('repo')}#{p.get('number')}": str(p.get("state") or "").upper() for p in u.get("prs") or []}
            for ref in list(u["keys"].get("pr") or []) + list(states):
                if not aging.is_dropped(snap, ref) and states.get(ref, "OPEN") == "OPEN":
                    refs.setdefault(ref, u["id"])
            continue
        for ref in u["keys"].get("pr") or []:
            refs.setdefault(ref, u["id"])
        for pr in u.get("prs") or []:
            if pr.get("repo") and pr.get("number") and (pr.get("state") or "open").lower() == "open":
                refs.setdefault(f"{pr['repo']}#{pr['number']}", u["id"])
    return refs


def _pr_record(snap, uid, repo, number):
    u = snap["units"].get(uid) or {}
    return next((p for p in u.get("prs") or [] if p.get("repo") == repo and p.get("number") == number), None)


def _merged_refs(snap, now, p):
    """Merged PRs waiting on a release (factory.pr_release_state: not released, a repo with a release step, merged
    within release.watch_days; older ones are left as they are)."""
    import factory
    out = {}
    for u in snap["units"].values():
        if u["kind"] == "foreman":
            continue
        for pr in u.get("prs") or []:
            if pr.get("repo") and pr.get("number") and factory.pr_release_state(pr, p, None, snap, now) == "release-wait":
                out.setdefault(f"{pr['repo']}#{pr['number']}", u["id"])
    return out


def _natural(name):
    return [(0, int(x), "") if x.isdigit() else (1, 0, x) for x in re.split(r"(\d+)", name)]


def release_check(now, p, snap):
    """pr.released for a merged PR once a release tag contains its merge commit (GitHub compare API, read-only).
    Per repo only the newest matching tag of each tag family (`v`, `<app>-rel-`) is compared, each tag once per
    PR, and a PR is looked at every release.check_minutes at most. A repo with no release step is never checked:
    the inventory drops its merged PRs by configuration alone."""
    import factory
    rp = p.get("release") or {}
    side = _side("watch-release.json", {})
    recs, lines, listed = [], [], {}
    for ref, uid in _merged_refs(snap, now, p).items():
        repo, num = ref.rsplit("#", 1)
        pattern = factory.release_tag(repo, p)
        ent = side.get(ref) or {}
        last = cl.parse_ts(ent.get("checked"))
        if pattern is None or (last and now - last < dt.timedelta(minutes=rp.get("check_minutes", 10))):
            continue
        side[ref] = ent
        ent["checked"] = cl.iso(now)
        if not ent.get("sha"):
            ent["sha"] = ((factory.gh("pr", "view", num, "-R", repo, "--json", "mergeCommit") or {}).get("mergeCommit") or {}).get("oid")
            if not ent["sha"]:
                continue
        if repo not in listed:
            listed[repo] = factory.gh("api", f"repos/{repo}/tags?per_page=100")
        tags = listed[repo]
        heads = {}
        for tag in tags if isinstance(tags, list) else []:
            name = str(tag.get("name") or "")
            fam = re.sub(r"\d.*", "", name)
            if fnmatch.fnmatch(name, pattern) and (fam not in heads or _natural(name) > _natural(heads[fam])):
                heads[fam] = name
        tried = set(ent.get("tags") or [])
        for tag in sorted(set(heads.values()) - tried):
            tried.add(tag)
            cmp = factory.gh("api", f"repos/{repo}/compare/{ent['sha']}...{tag}")
            if (cmp or {}).get("status") in ("ahead", "identical"):
                recs.append(("pr.released", {"unit": uid, "repo": repo, "number": int(num), "tag": tag}))
                lines.append(f"pr {ref} ({uid}): released in {tag}")
                break
        ent["tags"] = sorted(tried)
    AFTER_COMMIT.append(lambda: _side_write("watch-release.json", side))
    return recs, lines


STUCK_MERGE = {"DIRTY": "has merge conflicts with its base", "BEHIND": "is behind its base, which must be up to date"}


def conflict_iid(uid, ref, head):
    repo, num = ref.split("#", 1)
    return f"cfl-{uid}-{repo.rsplit('/', 1)[-1]}-{num}-{(head or '')[:7]}"


def conflict_records(snap, viewed):
    """(records, lines): an open PR whose merge state turned DIRTY (or BEHIND) wakes its unit's lead once per head
    with the fix (update, resolve, test, push, re-request review); waiting never clears a conflict. Once GitHub says
    the PR merges cleanly again, an unread note for it is retired so a resume never acts on it."""
    seen = {e.get("eid") for e in snap.get("events") or []}
    recs, lines = [], []
    for ref, (uid, pr) in sorted(viewed.items()):
        u = snap["units"].get(uid)
        if not u or u["status"] in graph.CLOSED or str(pr.get("state") or "").upper() != "OPEN":
            continue
        ms, head = str(pr.get("mergeStateStatus") or "").upper(), pr.get("headRefOid") or ""
        iid = conflict_iid(uid, ref, head)
        if ms not in STUCK_MERGE:
            stale = [i["iid"] for i in u.get("inbox") or [] if not i.get("delivered")
                     and str(i.get("iid") or "").startswith(conflict_iid(uid, ref, "")[:-1] + "-")]
            if stale and ms:
                recs.append(("inbox.supersede", {"id": uid, "iids": stale, "by": "foundry",
                                                 "reason": f"{ref} merge state is {ms} now"}))
            continue
        eid = f"ev-{iid[4:]}-{ms.lower()}"
        if not head or eid in seen:
            continue
        num = ref.split("#", 1)[1]
        repo = ref.split("#", 1)[0]
        say = (f"{ref} {STUCK_MERGE[ms]} (merge state {ms} at {head[:7]}); waiting will not fix it. In the unit "
               f"worktree: update the branch against the base (merge or rebase it"
               + (f", or `gh pr update-branch {num} -R {repo}`" if ms == "BEHIND" else "")
               + "), resolve the conflicts, run the tests, and push (a plain push, or --force-with-lease only on "
               "this unit's own branch). The push may dismiss the approval: re-request review from the previous "
               f"approver(s) (`foundry review assign {ref} --unit {uid}` or `gh pr edit {num} -R {repo} "
               "--add-reviewer <login>`; a Slack re-ask goes through `foundry outbox add --kind review-ask`), "
               "`foundry wait add --kind pr-review`, then merge with the new head pinned once it is approved, "
               "CLEAN and green. A semantic conflict you cannot resolve with confidence goes on a card "
               f"(`--ref pr:{ref}`), never as shell homework for the owner.")
        recs += [("event", {"eid": eid, "unit": uid, "wid": None, "kind": "pr-conflict", "ref": f"pr:{ref}",
                            "summary": say[:300]}),
                 ("inbox.add", {"id": uid, "iid": iid, "from": "foundry", "text": say})]
        lines.append(f"pr {ref} ({uid}): merge state {ms}: lead woken to update the branch")
    return recs, lines


def cycle_github(now, p):
    import watch
    recs, lines = [], []
    seen = _side("watch-github.json", {})
    snap = cl.load()
    viewed = {}
    for ref, uid in _pr_refs(snap).items():
        try:
            pr = watch.gh_pr(ref)
        except Exception as ex:  # one bad PR must not stop the rest
            cl.append_log("watch.log", {"stream": "github", "error": f"{ref}: {ex}"})
            continue
        viewed[ref] = (uid, pr)
        ci = watch.ci_bucket(pr.get("statusCheckRollup"))
        revs = [x.get("login") for x in pr.get("reviewRequests") or [] if x.get("login")]
        apps = sorted({(x.get("author") or {}).get("login") for x in pr.get("latestReviews") or []
                       if x.get("state") == "APPROVED"} - {None})
        fp = (f"{pr.get('state')}|{pr.get('reviewDecision')}|{ci}|{(pr.get('headRefOid') or '')[:7]}|"
              f"{pr.get('headRefName')}|{','.join(revs)}|{','.join(apps)}|v2")  # v2: re-read once for auto_reviewers
        repo, num = ref.split("#", 1)
        rec = _pr_record(snap, uid, repo, int(num))
        bare = bool(pr.get("title")) and not (rec or {}).get("title")
        if seen.get(ref) == fp and not bare:
            continue
        auto = None
        if revs and pr.get("state") == "OPEN":  # who asked them: CODEOWNERS / team / bot requests never count as load
            import review
            try:
                auto = review.read_auto(ref)
            except Exception as ex:
                cl.append_log("watch.log", {"stream": "github", "error": f"{ref} timeline: {ex}"})
        recs.append(("pr.state", {"unit": uid, "ref": ref, "repo": repo, "number": int(num), "state": pr.get("state"),
                                  "review": pr.get("reviewDecision"), "ci": ci, "head": pr.get("headRefOid"),
                                  "branch": pr.get("headRefName"), "title": pr.get("title"),
                                  "created": pr.get("createdAt"), "merge_state": pr.get("mergeStateStatus"),
                                  "reviewers": revs, "approvers": apps, "auto_reviewers": auto}))
        if ref in seen and seen[ref].removesuffix("|v2") != fp.removesuffix("|v2"):
            lines.append(f"pr {ref} ({uid}): {pr.get('state')} review={pr.get('reviewDecision') or '-'} ci={ci}")
        seen[ref] = fp
    try:  # waits that drifted from the PR: a missing review wait, a CI wait pinned to an old head
        import aging
        w_fix, w_lines = aging.wait_repairs(snap, viewed)
        recs += w_fix
        lines += w_lines
    except Exception as ex:
        cl.append_log("watch.log", {"stream": "github", "error": f"wait repair: {ex}"})
    try:
        c_recs, c_lines = conflict_records(snap, viewed)
        recs += c_recs
        lines += c_lines
    except Exception as ex:
        cl.append_log("watch.log", {"stream": "github", "error": f"conflicts: {ex}"})
    # Written only after run_cycle appended the pr.state records: a stream killed in between re-emits them.
    AFTER_COMMIT.append(lambda: _side_write("watch-github.json", seen))
    if any(op == "pr.state" for op, _ in recs):  # a merged PR satisfies pr: refs: resolve right after it lands
        import graph
        AFTER_COMMIT.append(graph.apply_resolve)
    import review
    said = _side("watch-review.json", [])
    try:  # the "참고" half of the reviewer load; throttled to review.LOAD_TTL_MIN inside
        review.refresh_others(review.pool_logins(p), now=now)
    except Exception:
        pass
    asks = review.reviewless_lines(snap, viewed, set(said), p)
    if asks:
        keep = sorted(set(said) | {k for k, _ in asks})[-500:]
        AFTER_COMMIT.append(lambda: _side_write("watch-review.json", keep))
        lines += [line for _, line in asks]
    r_recs, r_lines = release_check(now, p, snap)
    recs += r_recs
    lines += r_lines
    import release
    b_recs, b_lines = release.release_due(now, p, snap)
    recs += b_recs
    lines += b_lines
    g_recs, g_lines = release.gate_records(snap)
    recs += g_recs
    lines += g_lines
    try:  # waiting-PR lifecycle: reminders, owner cards, drops (own transaction)
        import aging
        lines += aging.pr_cycle(p, now)
    except Exception as ex:
        cl.append_log("watch.log", {"stream": "github", "error": f"pr aging: {ex}"})
    w_recs, events = watch.poll_waits(snap, now)
    recs += w_recs
    lines += [f"event {e['unit']} {e['kind']}: {e['summary']}" for e in events]
    return recs, lines


def cron_due(snap, now):
    """The crons foundry created that are CRON_RECREATE_DAYS old or more, oldest first."""
    old = [(cl.parse_ts(c.get("ts")), k) for k, c in (snap.get("crons") or {}).items()]
    return sorted((t, k) for t, k in old if t and now - t >= dt.timedelta(days=CRON_RECREATE_DAYS))


def cron_line(now):
    """One "recreate crons now" line per generation of crons, said once."""
    due = cron_due(cl.load(), now)
    said = _side("watch-crons.json", None)
    key = cl.iso(due[0][0]) if due else None
    if not due or said == key:
        return []
    AFTER_COMMIT.append(lambda: _side_write("watch-crons.json", key))
    return [f"crons recreate now: {len(due)} foundry cron(s) are >= {CRON_RECREATE_DAYS} days old and expire at 7: "
            "CronList; CronDelete them; CronCreate each entry of `foundry up`'s crons"]


def launch_lines(now, p, snap=None):
    """With prefs auto_launch on, one `launch <unit>` line per unit that newly heads `foundry next` (a free slot and
    no PC pressure): the foreman launches it in that order without asking the owner first."""
    if not p.get("auto_launch"):
        return []
    import graph
    rows = graph.next_units(snap or cl.load(), p, now)
    said = set(_side("watch-next.json", []))
    current = sorted(r["unit"] for r in rows)
    if current != sorted(said):
        AFTER_COMMIT.append(lambda: _side_write("watch-next.json", current))
    lines = [f"launch {r['unit']} ({r['why']}): a slot is free; run `foundry unit launch {r['unit']}` now, "
             "in `foundry next` order (prefs auto_launch; no owner start order needed)"
             for r in rows if r["unit"] not in said]
    told = set(_side("watch-repoless.json", []))
    bare = sorted(u["id"] for u in graph.repoless(snap or cl.load()))
    if bare != sorted(told):
        AFTER_COMMIT.append(lambda: _side_write("watch-repoless.json", bare))
    lines += [f"unit {uid} is queued with no repo, so it is not auto-launched: record its repo with "
              f"`foundry unit set {uid} --repo <registry name>` (it then launches in order), or ask the owner on a "
              "decision card if the repo is unknown" for uid in bare if uid not in told]
    return lines


def park_lines(p):
    """prefs park_waiting_leads: resume parked leads whose wait cleared (before new launches take the room), then
    park idle leads that only wait, so waiting never holds a slot. Each failure is said once."""
    if not p.get("park_waiting_leads"):
        return []
    import launcher
    said, now_said, lines = set(_side("watch-park.json", [])), set(), []
    for uid, why in launcher.unparkable(cl.load()):
        if why == launcher.IDLE_WAKE:
            launcher.stop_session(cl.load()["units"][uid]["session"], "owner approval answered: resume")
        rc, out = launcher.resume(uid)
        if "error" in out or "held" in out:
            key = f"resume:{uid}"
            now_said.add(key)
            if key not in said:
                lines.append(f"resume {uid} ({why}) not done: {out.get('error') or out['held']['reason']}; "
                             "retried every cycle")
        else:
            lines.append(f"resumed {uid} ({why}) as {out['resumed']}")
    for uid, name, src in launcher.parkable(cl.load()):
        res = launcher.park(uid, name, src)
        if "error" in res:
            key = f"park:{uid}"
            now_said.add(key)
            if key not in said:
                lines.append(f"park {uid} failed: {res['error']}")
        else:
            lines.append(f"parked {uid} ({src}): handoff written, session stopped, slot freed; "
                         "resumed automatically when the wait clears")
    if now_said != said:
        AFTER_COMMIT.append(lambda: _side_write("watch-park.json", sorted(now_said)))
    return lines


def cycle_sessions(now, p):
    import launcher
    changes = launcher.refresh()  # records its own session.state changes
    lines = [f"session {c['name']} ({c['unit']}): {c['from']} -> {c['to']}" for c in changes]
    lines += cron_line(now)
    import graph
    try:  # refs that move by themselves (time:, pr:) resolve without waiting for some other journal write
        for op, r in graph.apply_resolve():
            if op == "unit.ready":
                lines.append(f"unit {r.get('id') or r.get('unit')} ready: its last blocker cleared")
    except Exception as ex:
        cl.append_log("watch.log", {"stream": "sessions", "error": f"resolve: {ex}"})
    drift = foreman_drift_line(cl.load())
    if drift:
        lines.append(drift)
    lines += park_lines(p)
    lines += launch_lines(now, p)
    lines += stuck_lines()
    try:  # sessions ending leave worktrees and containers: announce new cleanup candidates here
        import housekeep
        import sweep
        ev = sweep.announce(housekeep.scan())
        if ev:
            lines.append(f"event _foreman housekeeping: {ev['summary']}")
    except Exception as ex:
        cl.append_log("watch.log", {"stream": "sessions", "error": f"housekeeping: {ex}"})
    return [], lines


def stuck_lines():
    """Once per unit and reason: a waiting unit nothing will move (watch.unwatched: no wait a watcher resolves, or no
    open question for the owner). The foreman resumes its lead or sets the real status; nothing changes here."""
    import watch
    snap = cl.load()
    seen = set(_side("watch-stuck.json", []))
    apps = watch.known_apps(snap)
    lines, current = [], set()
    for u in sorted(snap["units"].values(), key=lambda u: u["id"]):
        why = watch.unwatched(snap, u, apps)
        if not why:
            continue
        key = f"{u['id']}:{u['status']}"
        current.add(key)
        if key not in seen:
            lines.append(f"unit {u['id']} {u['status']} waits on nothing that will move it ({why}): resume its lead "
                         f"(`foundry unit resume {u['id']}`) or set its real status")
    _side_write("watch-stuck.json", sorted(current))
    return lines


def sync_lags(snap, now, p=None):
    """{unit: {target: lag minutes}} from the sync queue itself (sync.target_lags): the age of the oldest item still
    pending on an enabled target. A closed batch empties the queue and so clears the lag; nothing else counts."""
    import sync
    return sync.target_lags(snap, now, p)


def cycle_sync(now, p):
    warned = set(_side("watch-sync.json", []))
    lag_warn = p["sync"]["lag_warn_min"]
    lines, current = [], set()
    for uid, targets in sync_lags(cl.load(), now, p).items():
        for target, lag in targets.items():
            if lag < lag_warn:
                continue
            key = f"{uid}:{target}"
            current.add(key)
            if key not in warned:
                lines.append(f"sync {uid} {target}: {lag} min behind (> {lag_warn})")
    _side_write("watch-sync.json", sorted(current))
    return [], lines + review_batch_lines(now, p)


def review_batch_lines(now, p):
    """`outbox review-ask due ...` once per review-ask batch that turned due after it was filed on hold: the
    outbox event fired only at `outbox add`, so without this line a held batch would wait for the next one."""
    import outbox
    seen = set(_side("watch-outbox.json", []))
    lines, current = [], set()
    for b in outbox.batches(cl.load(), now, p):
        if not b["due"]:
            continue
        key = ",".join(b["oids"])
        current.add(key)
        if key not in seen:
            lines.append(f"outbox review-ask due: {b['count']} for {', '.join(b['to']) or '?'} ({b['send']}): send the "
                         "dispatcher with `foundry outbox batches`")
    _side_write("watch-outbox.json", sorted(current))
    return lines


CYCLES = {"github": cycle_github, "sessions": cycle_sessions, "pressure": cycle_pressure,
          "sync": cycle_sync, "stalls": cycle_stalls}


# ---------- the stream loop ----------

AFTER_COMMIT = []  # side-file writes a cycle defers until its records are in the journal


def run_cycle(kind, p):
    now = cl.now()
    # Only the foreman's own streams are a heartbeat; a one-off run by anyone else proves nothing.
    beat = heartbeat(now) if os.environ.get("FOUNDRY_ROLE") == "foreman" else False
    AFTER_COMMIT.clear()
    try:
        recs, lines = CYCLES[kind](now, p)
    except Exception as ex:  # a failing cycle is logged and the stream keeps running
        cl.append_log("watch.log", {"stream": kind, "error": str(ex)[:300]})
        recs, lines = [], []
        AFTER_COMMIT.clear()
    if recs:
        cl.append_many(recs)
    while AFTER_COMMIT:
        AFTER_COMMIT.pop(0)()
    if recs or beat or lines:
        import board
        board.write_all(cl.load())
    for line in lines:
        print(f"[foundry {kind}] {line}", flush=True)
    return lines


def stream(kind, once=False, interval=None, max_minutes=None):
    p = _p()
    cap = p["monitor_max_wait_min"]
    max_minutes = min(max_minutes or cap, cap, 30)
    interval = interval or (p["pressure"]["sample_seconds"] if kind == "pressure" else DEFAULT_INTERVAL[kind])
    deadline = time.time() + max_minutes * 60
    if not once:
        cl.claim_instance(kind)
    try:
        while True:
            if not once and not cl.owns_instance(kind):
                print(f"[foundry {kind}] superseded by a newer {kind} stream: exiting", flush=True)
                return 0
            run_cycle(kind, p)
            if once or time.time() + interval > deadline:
                return 0
            time.sleep(interval)
    finally:
        if not once:
            cl.release_instance(kind)


def cmd_watch(a):
    return stream(a.kind, a.once, a.interval, a.max_minutes)


# ---------- foundry up ----------

# Commands the foreman types into Monitor and cron prompts go through the plugin's bin/foundry, which Claude
# Code puts on the Bash tool's PATH for every enabled plugin. ${CLAUDE_PLUGIN_ROOT} is set only for hooks,
# and an absolute cache path dies on the next plugin update.
def stream_cmd(kind):
    return f"foundry watch stream --kind {kind}"


# Claude Code delays every recurring cron job by a fixed per-job offset: min(hash(id) * 0.5 * period, 30 min)
# (CLI 2.1.327 scheduler defaults recurringFrac 0.5, recurringCapMs 1800000). For a daily or weekly job that is
# 30 min for ~96% of ids, so a job is scheduled CRON_LEAD_MIN early and fires at, or a little before, the pref time.
CRON_LEAD_MIN = 30


def cron_lead(expr, lead=CRON_LEAD_MIN):
    """`expr` moved `lead` minutes earlier when minute and hour are plain numbers; else unchanged."""
    f = expr.split()
    if len(f) != 5 or not (f[0].isdigit() and f[1].isdigit()):
        return expr
    t = int(f[1]) * 60 + int(f[0]) - lead
    if t < 0:
        if f[2] != "*" or f[3] != "*":
            return expr  # a day-of-month job cannot move to the previous day safely
        t += 24 * 60
        if f[4] != "*":
            try:
                f[4] = ",".join(str((int(d) - 1) % 7) for d in f[4].split(","))
            except ValueError:
                return expr
    f[0], f[1] = str(t % 60), str(t // 60)
    return " ".join(f)


def cron_base_prompt(s):
    return f'Run `{s["run"]}`' if s["run"].startswith("foundry ") else s["run"]


# CronList shows long prompts cut short, so a cron is identified by a tag at the very start of its prompt:
# the schedule name and a hash of the spec prompt. A changed spec changes the hash.
CRON_TAG_RE = re.compile(r"^\[foundry-cron (\S+) ([0-9a-f]{8})\]")


def cron_tag(s):
    import hashlib
    return f"[foundry-cron {s['name']} {hashlib.sha256(cron_base_prompt(s).encode()).hexdigest()[:8]}]"


def cron_prompt(s):
    return f"{cron_tag(s)} {cron_base_prompt(s)}"


def cron_key(ti, cid):
    """The schedule a CronCreate is (prefs name), else its cron + prompt, else its id: two schedules that run the
    same prompt (preflight-night, preflight-morning) stay two entries, and recreating one replaces it."""
    m = CRON_TAG_RE.match(str(ti.get("prompt") or ""))
    if m:
        return m.group(1)
    try:
        import prefs
        for s in prefs.load().get("schedules") or []:  # a cron created before the tag (0.1.70)
            if ti.get("prompt") == cron_base_prompt(s) and ti.get("cron") in (cron_lead(s["cron"]), s["cron"]):
                return s["name"]
    except Exception:  # a broken prefs file must not lose the cron's age
        pass
    if ti.get("cron") or ti.get("prompt"):
        return f'{ti.get("cron") or ""}|{str(ti.get("prompt") or "")[:200]}'
    return cid


CATCH_UP_H = 24  # a schedule missed longer ago than this is skipped, not run late


def missed_schedules(snap, p, now=None):
    """prefs schedules that fired while the foreman was down (its crons with it): each one's latest fire time after
    the last foreman heartbeat, within CATCH_UP_H. Nothing while the foreman is alive or before it ever was."""
    import report
    now = now or cl.now()
    last = cl.parse_ts((snap.get("foreman") or {}).get("ts"))
    if not last or foreman_alive(snap, now):
        return []
    zone = report._tz(p)
    start = max(last, now - dt.timedelta(hours=CATCH_UP_H))
    out = []
    for sch in p.get("schedules") or []:
        t = now.astimezone(zone).replace(second=0, microsecond=0)
        while t > start:
            if report._cron_fires(sch["cron"], t):
                out.append({"name": sch["name"], "missed": cl.iso(t), "run": cron_base_prompt(sch)})
                break
            t -= dt.timedelta(minutes=1)
    return out


def up_plan(snap, p):
    import launcher
    timeout_ms = min(p["monitor_max_wait_min"], 30) * 60 * 1000
    streams = [{"tool": "Monitor", "description": f"foundry {k} stream", "command": stream_cmd(k),
                "timeout_ms": timeout_ms, "rearm": "on expiry or exit"} for k in KINDS]
    crons = [{"tool": "CronCreate", "name": s["name"], "cron": cron_lead(s["cron"]), "pref_cron": s["cron"],
              "recurring": True, "prompt": cron_prompt(s)} for s in p.get("schedules") or []]
    lost, blocked = [], []
    for u in snap["units"].values():
        if u["kind"] == "foreman" or u["status"] == "done":
            continue
        sess = snap["sessions"].get(u.get("session") or "")
        if sess and sess.get("state") not in launcher.LIVE and u["status"] == "in_progress":
            lost.append({"unit": u["id"], "session": sess["name"], "state": sess.get("state"),
                         "next": f"foundry unit resume {u['id']}"})
        if u.get("held") or u.get("blocked_by"):
            blocked.append({"unit": u["id"], "held": (u.get("held") or {}).get("reason"),
                            "blocked_by": u.get("blocked_by") or []})
    catch_up = missed_schedules(snap, p)
    return {"streams": streams, "crons": crons, "catch_up": catch_up,
            "steps": ["Arm every stream with the Monitor tool (timeout_ms as given); re-arm each one when it expires "
                      "or exits.",
                      *(["Run each catch_up entry once now, oldest first: its schedule fired while the foreman was "
                         "down, so its cron never ran it."] if catch_up else []),
                      "CronList; CronDelete foundry crons left from an earlier run; CronCreate each entry in crons "
                      f"exactly as given (`cron` is `pref_cron` minus {CRON_LEAD_MIN} min: the scheduler adds up to "
                      f"{CRON_LEAD_MIN} min to recurring jobs, so they fire at or just before the pref time). "
                      "Recurring crons expire after 7 days: the sessions stream says `crons recreate now` once the "
                      "oldest is 6 days old; rebuild them then and on every start.",
                      "Resume the lost units below and look at the blocked ones.",
                      ("Launch every unit in `next` now with `foundry unit launch <id>`, in that order (prefs "
                       "auto_launch: queued units start whenever a slot is free and the PC is not under pressure; "
                       "the sessions stream says `launch <unit>` for each one that becomes ready later)."
                       if p.get("auto_launch") else
                       "prefs auto_launch is off: launch queued units (`foundry next`) only on the owner's order."),
                      "No /loop, ScheduleWakeup, cloud routines or heartbeat crons: the streams are the heartbeat."],
            "foreman_alive": foreman_alive(snap), "lost": lost, "blocked": blocked,
            "next": [{"unit": r["unit"], "why": r["why"], "next": f"foundry unit launch {r['unit']}"}
                     for r in _next_rows(snap, p)]}


def _next_rows(snap, p):
    import graph
    try:
        return graph.next_units(snap, p) if p.get("auto_launch") else []
    except Exception:
        return []


def cron_drift(live, schedules):
    """Each prefs schedule against the live crons, by the tag at the start of the prompt (which survives CronList's
    truncation): ok (same tag and cron), recreate (the schedule's tag with another hash or cron, or an untagged job
    on its cron: delete it, create the spec), missing; plus extra foundry-looking jobs no schedule owns."""
    pool = [dict(x) for x in live if isinstance(x, dict)]
    out = {"ok": [], "recreate": [], "missing": [], "extra": []}

    def tag(x):
        m = CRON_TAG_RE.match(str(x.get("prompt") or ""))
        return m.groups() if m else None
    for s in schedules:
        want = {"name": s["name"], "cron": cron_lead(s["cron"]), "prompt": cron_prompt(s)}
        crons = (want["cron"], s["cron"])
        mine = cron_tag(s)[1:-1].split()[1:]
        hit = next((x for x in pool if tag(x) == tuple(mine) and x.get("cron") in crons), None)
        if hit:
            pool.remove(hit)
            out["ok"].append(s["name"])
            continue
        old = next((x for x in pool if (tag(x) or (None,))[0] == s["name"]), None) or \
            next((x for x in pool if not tag(x) and x.get("cron") in crons), None)
        if old:
            pool.remove(old)
            out["recreate"].append(want | {"delete": old.get("id"), "live_prompt": old.get("prompt")})
        else:
            out["missing"].append(want)
    out["extra"] = [{"id": x.get("id"), "cron": x.get("cron"), "prompt": x.get("prompt")} for x in pool
                    if tag(x) or "foundry" in str(x.get("prompt") or "")]
    return out


def _read_crons(spec):
    if spec == "-":
        text = sys.stdin.read()
    else:
        with open(spec, encoding="utf-8") as f:
            text = f.read()
    data = json.loads(text)
    return data.get("jobs") or data.get("crons") or [] if isinstance(data, dict) else data


SLEPT_MIN = 10  # a heartbeat gap this long means the PC slept (or the foreman stalled): check before acting
EVENTS_WAIT_CMD = "foundry events wait --timeout 3300"


def stream_health(snap, now=None):
    """{rearm: [...], slept_min}: every stream kind and the events wait with no live process (a PC sleep kills them
    all while the foreman session lives on), each with the command that re-arms it at the current version; a kind
    that still runs is left alone (one per machine). slept_min: minutes since the last foreman heartbeat when that
    gap says the machine slept."""
    now = now or cl.now()
    live = {k for k, v in cl.instances().items() if v["running"]}
    p = _p()
    timeout_ms = min(p["monitor_max_wait_min"], 30) * 60 * 1000
    rearm = [{"kind": k, "line": f"re-arm: {k}", "tool": "Monitor", "command": stream_cmd(k), "timeout_ms": timeout_ms}
             for k in KINDS if k not in live]
    if "events-wait" not in live:
        rearm.append({"kind": "events-wait", "line": "re-arm: events-wait", "tool": "Bash (run_in_background)",
                      "command": EVENTS_WAIT_CMD})
    beat = cl.parse_ts((snap.get("foreman") or {}).get("ts"))
    gap = int((now - beat).total_seconds() // 60) if beat else None
    return {"rearm": rearm, "slept_min": gap if gap is not None and gap >= SLEPT_MIN else None}


def cmd_up(a):
    import launcher
    if getattr(a, "check", False) and not a.crons:
        res = stream_health(cl.load())
        if res["slept_min"]:
            res["first"] = (f"the machine slept or stalled for {res['slept_min']} min: re-arm, then read `foundry "
                            "events pending` and the board before acting on anything older")
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 1 if res["rearm"] else 0
    if getattr(a, "check", False):
        try:
            live = _read_crons(a.crons)
        except (OSError, ValueError) as e:
            print(f"refused: cannot read --crons: {e}", file=sys.stderr)
            return 2
        res = cron_drift(live, _p().get("schedules") or [])
        res.update(stream_health(cl.load()))
        res["action"] = ("CronDelete each recreate.delete, then CronCreate each recreate and missing entry exactly as "
                         "given (recurring); CronDelete the extra ones only if no schedule should own them")
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 1 if res["recreate"] or res["missing"] else 0  # the cron answer; rearm rides along
    prefs.ensure_materialized()
    launcher.refresh()
    pruned = launcher.prune_foremen(by="up")
    snap = cl.load()
    plan = up_plan(snap, _p())
    plan["foreman_prune"] = pruned
    names = launcher.record_gateway_env()  # names only: every spawn re-reads the values
    if names:
        plan["gateway_env"] = names
    cl.append("foundry.up", streams=[s["description"] for s in plan["streams"]], crons=len(plan["crons"]),
              lost=[x["unit"] for x in plan["lost"]], blocked=[x["unit"] for x in plan["blocked"]])
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    return 0


def owner_notice(snap):
    """The owner's interactive session hears it when nothing is collecting."""
    if foreman_alive(snap):
        return None
    return "foundry foreman is down — run `foundry up`"


# ---------- lead Monitor waits ----------

def cmd_wait_monitor(a):
    cap = _p()["monitor_max_wait_min"]
    if a.minutes > cap or a.minutes <= 0:
        print(f"refused: {a.minutes} min is over the Monitor cap of {cap} min: park and exit instead "
              f"(foundry wait add {a.id} ...)", file=sys.stderr)
        return 2
    snap = cl.load()
    if a.id not in snap["units"]:
        print(cl.unknown_unit(a.id, snap), file=sys.stderr)
        return 2
    until = cl.iso(cl.now() + dt.timedelta(minutes=a.minutes))
    cl.append("wait.monitor", unit=a.id, what=a.what, until=until)
    print(json.dumps({"wait_monitor": a.id, "what": a.what, "until": until}, ensure_ascii=False))
    return 0


# ---------- hooks and board ----------

def post_tool_use(inp, snap, ctx):
    """Journal each cron the foreman creates or deletes, so their age is known after a restart."""
    tool, ti = ctx.get("tool"), ctx.get("input") or {}
    if tool not in ("CronCreate", "CronDelete") or ctx.get("caller") == roles.OUTSIDE:
        return
    resp = inp.get("tool_response")
    resp = resp if isinstance(resp, dict) else {}
    if tool == "CronCreate":
        cid = resp.get("id") or resp.get("job_id") or resp.get("cron_id")
        cl.append("cron.created", key=cron_key(ti, cid), id=cid, cron=ti.get("cron"))
    else:
        cl.append("cron.deleted", id=ti.get("id") or ti.get("job_id"))


def pre_tool_use(inp, snap, ctx):
    if ctx.get("tool") != "Write":
        return None
    size = len(str((ctx.get("input") or {}).get("content") or "").encode())
    if size > LARGE_WRITE_BYTES:
        return ("warn", f"Large Write (~{size // 1024} KB): {WRITE_IN_PARTS}")
    return None


def _stall_text(st):
    base = STALL_TEXT.get(st.get("kind")) or i18n.t("cli.stall_other")
    n = st.get("count") or 0
    return i18n.t("cli.stall_minutes" if st.get("kind") == "no_progress" else "cli.stall_times", text=base, n=n)


def board(snap, m):
    now = cl.now()
    alive = foreman_alive(snap, now)
    f = snap.get("foreman") or {}
    m["foreman"] = {"alive": alive, "ts": f.get("ts")}
    lags = sync_lags(snap, now)
    for sec in m.get("sections") or []:
        for r in sec["rows"]:
            st = (snap.get("stalls") or {}).get(r["id"]) or {}
            last = ((st.get("actions") or [{}])[-1] or {}).get("action")
            r["stall"] = ({"kind": st["kind"], "text": _stall_text(st), "action": last,
                           "escalated": bool(st.get("escalated"))} if st.get("active") else None)
            mw = (snap["units"].get(r["id"]) or {}).get("monitor_wait") or {}
            until = cl.parse_ts(mw.get("until"))
            if until and until > now:
                r["monitor_wait"] = {"what": mw.get("what"), "until": mw.get("until"),
                                     "text": i18n.t("cli.ci_wait_monitor")}
                r["summary"] = i18n.t("cli.ci_wait_monitor_what", what=mw.get("what") or "")[:160]
            r.setdefault("sync_lag_min", max(lags[r["id"]].values()) if r["id"] in lags else None)
    m.setdefault("sync_lagging", sorted(u for u, t in lags.items() if max(t.values()) >= _p()["sync"]["lag_warn_min"]))
    m["monitor"] = monitor_model(snap, now, alive)


TILES = (("load", "load_per_cpu", "/cpu", "high"), ("memory", "mem_reclaimable_pct", "%", "low"),
         ("disk", "disk_free_gb", "GB", "low"), ("docker", "docker_cpu_pct", "%", "high"),
         ("cache", "docker_build_cache_gb", "GB", "high"))
# A tile's other values from the newest sample, for its value line.
TILE_EXTRA = {"docker_build_cache_gb": ("docker_cache_old_gb", "docker_images")}
# Tiles shown only once some sample has a value (no Docker, no tile).
OPTIONAL_TILES = {"docker_build_cache_gb"}


def tiles_for(rows):
    return [t for t in TILES if t[1] not in OPTIONAL_TILES or any(isinstance(r.get(t[1]), (int, float)) for r in rows)]
WORSE = {key: worse for _, key, _, worse in TILES}


def leads_model(snap):
    """Live sessions against max_concurrent and whether a new launch would pass (launcher.held_reason's rules)."""
    live = len(cl.slot_holders(snap))
    cap = cl.load_config().get("max_concurrent") or 8
    room = cl.pressure_room(snap)
    return {"live": live, "cap": cap, "launch": live < cap and room != 0}


def monitor_model(snap, now, alive):
    rows = read_metrics()
    last = rows[-1] if rows else {}
    ts = cl.parse_ts(last.get("ts"))
    level = (snap.get("pressure") or {}).get("level") or "ok"
    status = level if level in ("ok", "warn", "danger") else "danger"
    pp = _p()["pressure"]
    tiles = [{"name": n, "value": last.get(k), "unit": unit, "worse": worse,
              "status": level_of(k, last.get(k), pp) or "ok",
              "spark": [r.get(k) for r in rows[-30:]]} for n, k, unit, worse in tiles_for(rows)]
    # The heading is the worst of the foreman's sustained level and each tile's own, so it never reads ok above a 주의 tile.
    rank = ("ok", "warn", "danger")
    status = max([status] + [t["status"] for t in tiles], key=rank.index)
    import i18n
    footer = i18n.t("board.monitor_collecting" if alive else "board.monitor_stopped")
    return {"status": status, "last_sample_age_s": int((now - ts).total_seconds()) if ts else None,
            "collecting": alive, "tiles": tiles, "footer": footer, "last_ts": last.get("ts"),
            "leads": leads_model(snap),
            "timeline": [{"ts": r.get("ts"), "level": r.get("level")} for r in rows[-60:]]}


def add_cli(sp):
    w = sp.add_parser("watch", help="foreman Monitor streams").add_subparsers(dest="sub", required=True)
    s = w.add_parser("stream")
    s.add_argument("--kind", required=True, choices=KINDS)
    s.add_argument("--once", action="store_true")
    s.add_argument("--interval", type=float)
    s.add_argument("--max-minutes", type=float)
    s.set_defaults(func=cmd_watch)
    up = sp.add_parser("up", help="what the foreman arms after a start or reboot")
    up.add_argument("--check", action="store_true",
                    help="list the streams and events wait to re-arm (none running), a sleep gap, and with --crons "
                         "(CronList as JSON) the crons to recreate")
    up.add_argument("--crons", help="a JSON file (or - for stdin): [{id, cron, prompt}, ...] as CronList shows them")
    up.set_defaults(func=cmd_up)
    st = sp.add_parser("streams", help="running streams and events waits").add_subparsers(dest="sub", required=True)
    st.add_parser("status", help="each kind: owner pid, version, age; duplicates flagged").set_defaults(
        func=cmd_streams)


def cmd_streams(a):
    rows = cl.instances()
    out = {k: {"owner": (v["owner"] or {}).get("pid"), "version": (v["owner"] or {}).get("version"),
               "running": [{"pid": r["pid"], "version": r.get("version"), "age_min": r.get("age_min")}
                           for r in v["running"]], "duplicates": v["duplicates"]} for k, v in sorted(rows.items())}
    print(json.dumps({"streams": out, "duplicates": sorted(k for k, v in out.items() if v["duplicates"])},
                     ensure_ascii=False, indent=1))
    return 0


def add_unit_cli(u):
    wm = u.add_parser("wait-monitor", help="record a short Monitor wait (<= monitor_max_wait_min)")
    wm.add_argument("id")
    wm.add_argument("--what", required=True)
    wm.add_argument("--minutes", type=float, required=True)
    wm.set_defaults(func=cmd_wait_monitor)
