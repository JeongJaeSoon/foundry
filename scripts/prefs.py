"""Per-person preferences: plugin defaults (defaults/prefs.json) deep-merged with the user's own
<state>/prefs.json. Secrets (owner ids, DM channels) stay in config.json, never here.

An invalid user file is refused with a clear error and the last valid merge (prefs.valid.json) is used;
nothing silently falls back to defaults. `foundry prefs show|validate|set` writes atomically and journals
`prefs.changed`.
"""

import contextlib
import copy
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402

DEFAULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "defaults", "prefs.json")
WINDOW_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
LAST_ERROR = {"error": None}


def defaults():
    """The shipped defaults, plus FOUNDRY_PREFS_OVERLAY when set (the test suite's org fixture)."""
    with open(DEFAULTS) as f:
        base = json.load(f)
    over = os.environ.get("FOUNDRY_PREFS_OVERLAY")
    if over:
        with open(over) as f:
            base = merge(base, json.load(f))
    return base


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def _num(v, lo=None):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and (lo is None or v >= lo)


def validate(p):
    """A list of problems; empty means valid. The shape follows defaults/prefs.json."""
    errs = []
    base = defaults()

    def walk(d, b, where):
        for k, v in d.items():
            key = f"{where}{k}"
            if k not in b:
                errs.append(f"{key}: unknown key")
                continue
            dv = b[k]
            if isinstance(dv, dict) and not isinstance(v, dict) and dv:
                errs.append(f"{key}: must be an object")
            elif isinstance(dv, dict) and dv and isinstance(v, dict):
                walk(v, dv, key + ".")
            elif isinstance(dv, bool) and not isinstance(v, bool):
                errs.append(f"{key}: must be true or false")
            elif isinstance(dv, list) and not isinstance(v, list):
                errs.append(f"{key}: must be a list")
            elif isinstance(dv, str) and not isinstance(v, str):
                errs.append(f"{key}: must be a string")
            elif _num(dv) and not _num(v, 0):
                errs.append(f"{key}: must be a number >= 0")

    walk(p, base, "")
    for k in ("night_window", "slack_quiet"):
        if not WINDOW_RE.match(str(p.get(k, base[k]))):
            errs.append(f"{k}: must look like 22:00-08:00")
    if not HHMM_RE.match(str(p.get("handover_time", base["handover_time"]))):
        errs.append("handover_time: must look like 08:00")
    try:
        import zoneinfo
        zoneinfo.ZoneInfo(p.get("timezone", base["timezone"]))
    except Exception:
        if p.get("timezone", base["timezone"]) != "Asia/Tokyo":
            errs.append("timezone: unknown zone")
    import i18n
    ui = p.get("ui") if isinstance(p.get("ui"), dict) else {}
    if "language" in ui and ui["language"] not in i18n.LANGS:
        errs.append(f"ui.language: one of {', '.join(i18n.LANGS)} (the board, pane and CLI; separate from sync.languages)")
    if p.get("glyphs", base["glyphs"]) not in ("nerd", "emoji", "auto"):
        errs.append("glyphs: one of nerd, emoji, auto")
    mg = p.get("merge") if isinstance(p.get("merge"), dict) else {}
    if mg.get("default", "self") not in ("self", "auto", "owner"):
        errs.append("merge.default: one of self, auto, owner")
    for e in mg.get("owner_paths") or [] if isinstance(mg.get("owner_paths"), list) else []:
        if not (isinstance(e, dict) and e.get("repo") and isinstance(e.get("paths"), list)):
            errs.append(f"merge.owner_paths: each entry needs repo and a paths list: {e!r}")
    for e in mg.get("argo_apps") or [] if isinstance(mg.get("argo_apps"), list) else []:
        if not (isinstance(e, dict) and e.get("repo") and e.get("app")
                and isinstance(e.get("paths", []), list)):
            errs.append(f"merge.argo_apps: each entry needs repo, app and an optional paths list: {e!r}")
    for k in ("deploy_pr_authors", "deploy_tag_files", "bump_pr_authors", "bump_files"):
        if k in mg and not (isinstance(mg[k], list) and all(isinstance(x, str) and x for x in mg[k])):
            errs.append(f"merge.{k}: a list of non-empty strings")
    if "bump_branch_glob" in mg and not (isinstance(mg["bump_branch_glob"], str) and "*" in mg["bump_branch_glob"]):
        errs.append("merge.bump_branch_glob: a branch glob whose * stands for the version, like release/bump-v*")
    sy = p.get("sync") if isinstance(p.get("sync"), dict) else {}
    if "languages" in sy and not (isinstance(sy["languages"], dict)
                                  and all(isinstance(x, str) and x.strip() for x in sy["languages"].values())):
        errs.append("sync.languages: an object of non-empty language codes per target (jira, linear, github, obs)")
    gh = sy.get("github") if isinstance(sy.get("github"), dict) else {}
    if "labels" in gh and not (isinstance(gh["labels"], list) and all(isinstance(x, str) and x.strip() for x in gh["labels"])):
        errs.append("sync.github.labels: a list of label names foundry adds to the issues it comments on")
    rl = p.get("release") if isinstance(p.get("release"), dict) else {}
    for e in rl.get("batch_repos") or [] if isinstance(rl.get("batch_repos"), list) else []:
        if not (isinstance(e, dict) and e.get("repo")):
            errs.append(f"release.batch_repos: each entry needs repo (procedure, dev_app optional): {e!r}")
    for e in p.get("required_mcp") or [] if isinstance(p.get("required_mcp"), list) else []:
        if not (isinstance(e, dict) and e.get("stage") and isinstance(e.get("servers"), list) and e["servers"]
                and (e.get("tracker") or e.get("repos"))):
            errs.append(f"required_mcp: each entry needs stage, servers and a tracker or repos match: {e!r}")
    ig = (p.get("review") or {}).get("ignore", []) if isinstance(p.get("review"), dict) else []
    if not (isinstance(ig, list) and all(isinstance(r, dict) and r and set(r) <= {"author", "title", "repo", "label"}
                                         and all(isinstance(v, str) and v.strip() for v in r.values()) for r in ig)):
        errs.append("review.ignore: a list of rules, each naming any of author, title, repo, label (globs), e.g. "
                    "{\"author\": \"app/*\"} or {\"title\": \"[Auto] *\"}")
    ms = p.get("mcp_servers", {})
    if not (isinstance(ms, dict) and all(isinstance(v, list) and v and all(isinstance(x, str) and re.fullmatch(
            r"[A-Za-z0-9_-]+", x) and "__" not in x for x in v) for v in ms.values())):
        errs.append("mcp_servers: {generic: [server, ...]}, plain server names as their tools are prefixed "
                    "(no `__`, no `*`), e.g. {\"slack\": [\"plugin_x_slack\"]}")
    tr = p.get("trackers", base["trackers"])
    if not isinstance(tr, list) or any(t not in TRACKERS for t in tr):
        errs.append(f"trackers: a list of {', '.join(TRACKERS)} (empty: no tracker)")
    sk = p.get("slack") if isinstance(p.get("slack"), dict) else {}
    if "footer" in sk and not (isinstance(sk["footer"], str) and sk["footer"].strip()):
        errs.append("slack.footer: a non-empty string (the signature every foundry Slack message ends with)")
    rp = p.get("report") if isinstance(p.get("report"), dict) else {}
    if "remind_time" in rp and not HHMM_RE.match(str(rp["remind_time"])):
        errs.append("report.remind_time: must look like 09:00")
    known = ("lines", "done", "ops", "release_gaps", "review_gaps", "tonight", "tomorrow", "owner")
    if isinstance(rp.get("sections"), list) and any(x not in known for x in rp["sections"]):
        errs.append(f"report.sections: a list of {', '.join(known)}")
    for s in p.get("schedules", []) if isinstance(p.get("schedules"), list) else []:
        if not (isinstance(s, dict) and s.get("name") and s.get("cron") and s.get("run")):
            errs.append(f"schedules: each entry needs name, cron and run: {s!r}")
    return errs


TRACKERS = ("jira", "linear", "github")


def trackers(p=None):
    """The ticket trackers this install uses (prefs `trackers`); Linear is off unless listed."""
    return list((p or load()).get("trackers") or [])


def tracker_refusal(tracker, p=None):
    """Why `tracker` may not be set on a repo or unit, or None. `none` is always allowed."""
    if tracker in (None, "none") or tracker in trackers(p):
        return None
    return (f"refused: tracker {tracker} is not enabled (prefs trackers = {trackers(p)}). "
            f"Enable it first: foundry prefs set trackers '[\"jira\", \"{tracker}\"]'")


def user_path():
    return cl.path("prefs.json")


def _last_valid():
    """prefs.valid.json, or None when it is missing or unreadable."""
    try:
        with open(cl.path("prefs.valid.json")) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def load():
    """The effective prefs. A bad user file: the last valid merge, and LAST_ERROR names the problem."""
    if not os.path.exists(cl.path(MARKER)):
        try:
            materialize(lock=False)  # before this load rewrites prefs.valid.json from the neutral defaults
        except (OSError, ValueError) as e:
            last = _last_valid()
            if last is not None:
                LAST_ERROR["error"] = f"prefs materialize refused: {e}"
                return last
            # nothing readable to keep: carry on with the defaults and the user's file
    base = defaults()
    p = user_path()
    LAST_ERROR["error"] = None
    if not os.path.exists(p):
        return base
    try:
        with open(p) as f:
            user = json.load(f)
        if not isinstance(user, dict):
            raise ValueError("top level must be an object")
        errs = validate(user)
        if errs:
            raise ValueError("; ".join(errs))
    except (OSError, ValueError) as e:
        LAST_ERROR["error"] = f"{p} refused: {e}"
        return _last_valid() or base
    eff = merge(base, user)
    try:
        cl.write_atomic(cl.path("prefs.valid.json"), json.dumps(eff, ensure_ascii=False, indent=1))
    except OSError:
        pass
    return eff


# Values the shipped defaults no longer carry (people, org repos and services, one owner's rules and words; 0.1.66).
# `materialize` copies each one the user's prefs.json does not set yet from the last effective prefs
# (prefs.valid.json, written by every load of an earlier version), once, so an existing install keeps its values
# even when it skips the version that first shipped materialize.
ORG_KEYS = ("timezone", "language", "ui.language", "review.people", "review.channel", "review.repos",
            "review.human_approval_checks", "release.none", "release.batch_repos", "merge.owner_repos",
            "merge.owner_paths", "merge.argo_apps", "merge.deploy_pr_authors", "merge.deploy_tag_files",
            "merge.bump_pr_authors", "required_mcp", "trackers", "sync.comment_footer", "sync.languages", "sync.jira",
            "slack.footer", "report.title", "report.kind_emoji", "schedules")
MARKER = "prefs.materialized"


def _get(d, dotted):
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return None, False
        d = d[part]
    return d, True


def materialize(lock=True):
    """Copy every ORG_KEYS value the user's prefs.json lacks from prefs.valid.json, once (marker file). A fresh
    install (no valid.json) has nothing to keep and only gets the marker. Returns the keys copied, or None when
    already done. Refuses (raises ValueError) rather than write an invalid file. lock=False is load()'s path, which
    may run under the caller's lock; two racing copies write the same thing."""
    if os.path.exists(cl.path(MARKER)):
        return None
    good = cl.path("prefs.valid.json")
    base = {}
    if os.path.exists(good):
        with open(good) as f:
            base = json.load(f)
    with (cl.locked() if lock else contextlib.nullcontext()):
        p = user_path()
        user = {}
        if os.path.exists(p):
            with open(p) as f:
                user = json.load(f)
        copied = []
        for k in ORG_KEYS:
            v, ok = _get(base, k)
            if not ok or _get(user, k)[1]:
                continue
            cur = user
            parts = k.split(".")
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = copy.deepcopy(v)
            copied.append(k)
        errs = validate(user)
        if errs:
            raise ValueError("; ".join(errs))
        if copied:
            cl.write_atomic(p, json.dumps(user, ensure_ascii=False, indent=1))
        # UTC, not cl.iso(): the clock reads prefs, and prefs are what is being materialized
        ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        cl.write_atomic(cl.path(MARKER), json.dumps({"ts": ts, "keys": copied}) + "\n")
    if lock:
        cl.append("prefs.materialized", keys=copied)
    return copied


def tz(p=None):
    p = p or load()
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(p["timezone"])
    except Exception:
        return cl.JST


def in_window(t, window, zone):
    """True when t falls in 'HH:MM-HH:MM' (wrapping past midnight) in the given zone."""
    a, b = window.split("-")
    m = t.astimezone(zone)
    cur = m.hour * 60 + m.minute
    s = int(a[:2]) * 60 + int(a[3:])
    e = int(b[:2]) * 60 + int(b[3:])
    return (s <= cur < e) if s < e else (cur >= s or cur < e)


def is_night(t=None, p=None):
    p = p or load()
    return in_window(t or cl.now(), p["night_window"], tz(p))


def window_end(t, window, zone):
    """The next time the window ends after t (for 'schedule it for 08:00')."""
    e = window.split("-")[1]
    m = t.astimezone(zone)
    end = m.replace(hour=int(e[:2]), minute=int(e[3:]), second=0, microsecond=0)
    return end if end > m else end + dt.timedelta(days=1)


def get(key, p=None):
    cur = p or load()
    for part in key.split("."):
        cur = cur[part]
    return cur


# ---------- CLI ----------

# Keys the owner sets alone even when the foreman has their words: merge policy, release targets, worktree
# trust.
OWNER_ONLY_KEYS = ("merge", "release", "auto_trust_worktrees")

# Values foundry can discover for itself (and so never asks the owner for): the foreman sets them, naming where the
# value came from. Only link bases: nothing here widens what merges or runs unattended.
DISCOVERABLE_KEYS = {"jira_base": lambda v: isinstance(v, str) and re.fullmatch(r"https://[\w.-]+(:\d+)?/?", v)}


def set_discovered(key, value, evidence, by="foundry"):
    """Write a discoverable key into the user prefs and journal where the value came from. Returns errors."""
    ok = DISCOVERABLE_KEYS.get(key)
    if not ok or not ok(value):
        return [f"{key}: not a discoverable key or not a valid value"]
    p = user_path()
    user = {}
    if os.path.exists(p):
        with open(p) as f:
            user = json.load(f)
    old = user.get(key)
    user[key] = str(value).rstrip("/")
    errs = validate(user)
    if errs:
        return errs
    cl.write_atomic(p, json.dumps(user, ensure_ascii=False, indent=1))
    cl.append("prefs.changed", key=key, old=old, new=user[key], by=by, evidence=evidence)
    return []


def cmd_unset(a):
    """Remove a key from the user's prefs.json (the default applies again): the owner's own command. Works on a file
    that no longer validates, so a key a newer version dropped can be cleared; the result must validate."""
    err = cl.require_owner(f"changing prefs ({a.key})")
    if err:
        print(err, file=sys.stderr)
        return 2
    p = user_path()
    user = {}
    if os.path.exists(p):
        with open(p) as f:
            user = json.load(f)
    parts = a.key.split(".")
    cur = user
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
    if not isinstance(cur, dict) or parts[-1] not in cur:
        print(json.dumps({"unset": a.key, "changed": False}, ensure_ascii=False))
        return 0
    old = cur.pop(parts[-1])
    errs = validate(user)
    if errs:
        print("refused: " + "; ".join(errs), file=sys.stderr)
        return 2
    cl.write_atomic(p, json.dumps(user, ensure_ascii=False, indent=1))
    cl.append("prefs.changed", key=a.key, old=old, new=None)
    print(json.dumps({"unset": a.key, "changed": True}, ensure_ascii=False))
    return 0


def cmd_prefs(a):
    if a.sub == "show":
        eff = load()
        print(json.dumps({"prefs": eff, "file": user_path(), "error": LAST_ERROR["error"]}, ensure_ascii=False, indent=1))
        return 0
    if a.sub == "validate":
        p = user_path()
        if not os.path.exists(p):
            print(json.dumps({"valid": True, "file": None}))
            return 0
        try:
            with open(p) as f:
                errs = validate(json.load(f))
        except (OSError, ValueError) as e:
            errs = [str(e)]
        print(json.dumps({"valid": not errs, "errors": errs, "file": p}, ensure_ascii=False, indent=1))
        return 0 if not errs else 2
    if a.sub == "unset":
        return cmd_unset(a)
    # set
    if a.key in DISCOVERABLE_KEYS and a.by == "foreman" and not a.quote:
        import graph
        _, err = graph.check_by("foreman", f"setting discovered prefs ({a.key})")
        if err:
            print(err, file=sys.stderr)
            return 2
        if not (a.evidence or "").strip():
            print("refused: --evidence names where the value came from (e.g. getAccessibleAtlassianResources)",
                  file=sys.stderr)
            return 2
        errs = set_discovered(a.key, a.value, a.evidence.strip(), by="foreman")
        if errs:
            print("refused: " + "; ".join(errs), file=sys.stderr)
            return 2
        print(json.dumps({"set": a.key, "value": a.value.rstrip("/"), "by": "foreman"}, ensure_ascii=False))
        return 0
    relay = cl.relay_quote(a.by, a.quote)
    top = a.key.split(".")[0]
    if relay and top in OWNER_ONLY_KEYS:
        print(f"refused: prefs {top} stays the owner's even on a quote (it widens what merges or runs "
              f"unattended): relay it to the owner, who sets it from {cl.owner_how(f'prefs set {a.key} <value>')}",
              file=sys.stderr)
        return 2
    if relay:
        import graph
        _, err = graph.check_by("foreman", f"changing prefs ({a.key})")
    else:
        err = cl.require_owner(f"changing prefs ({a.key})")
    if err:
        print(err, file=sys.stderr)
        return 2
    try:
        value = json.loads(a.value)
    except ValueError:
        value = a.value
    p = user_path()
    user = {}
    if os.path.exists(p):
        with open(p) as f:
            user = json.load(f)
    cur = user
    parts = a.key.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    old = cur.get(parts[-1])
    cur[parts[-1]] = value
    errs = validate(user)
    if errs:
        print("refused: " + "; ".join(errs), file=sys.stderr)
        return 2
    cl.write_atomic(p, json.dumps(user, ensure_ascii=False, indent=1))
    cl.append("prefs.changed", key=a.key, old=old, new=value,
              **({"by": "foreman", "quote": relay, "note": cl.RELAY_NOTE} if relay else {}))
    print(json.dumps({"set": a.key, "value": value}, ensure_ascii=False))
    return 0


def cmd_materialize(a):
    err = cl.require_owner("materializing prefs")
    if err:
        print(err, file=sys.stderr)
        return 2
    try:
        keys = materialize()
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"materialized": keys is not None, "keys": keys or []}, ensure_ascii=False))
    return 0


def ensure_materialized():
    """For `foundry up` and preflight: materialize once; a refusal is reported, never fatal."""
    try:
        return materialize()
    except (OSError, ValueError) as e:
        return f"prefs materialize refused: {e}"


def add_cli(sp):
    s = sp.add_parser("prefs").add_subparsers(dest="sub", required=True)
    s.add_parser("show").set_defaults(func=cmd_prefs)
    s.add_parser("validate").set_defaults(func=cmd_prefs)
    s.add_parser("materialize", help="copy this install's org values from the defaults into prefs.json, once").set_defaults(
        func=cmd_materialize)
    un = s.add_parser("unset", help="remove a key from your prefs.json (its default applies again)")
    un.add_argument("key")
    un.set_defaults(func=cmd_prefs)
    st = s.add_parser("set")
    st.add_argument("key")
    st.add_argument("value")
    st.add_argument("--by", choices=("owner", "foreman"))
    st.add_argument("--quote", help="the foreman on the owner's word: the owner's exact chat words")
    st.add_argument("--evidence", help="a discoverable key (jira_base) set by the foreman: where the value came from")
    st.set_defaults(func=cmd_prefs)
