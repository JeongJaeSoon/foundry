"""Batch releases and the dev deploy stage after them, owned by foundry rather than by a person.

A repo releases in batches when repos.json says `release: batch` or its name matches a prefs `release.batch_repos`
entry. The github stream (`release_due`) queues one release unit per such repo once merged, unreleased PRs have
waited long enough: `release.batch_min_prs` or more, and either the newest merge is `release.batch_quiet_min` old
or the oldest is `release.batch_max_wait_h` old, outside the night window. The unit is a plain queued project unit,
so auto_launch starts it in `foundry next` order with no owner start order; its handoff goal is the repo's release
procedure (the batch_repos entry's `procedure`) and, for a repo with a `dev_app`, the dev deploy and verify stage.

Hard rules, enforced here and in graph.set_status rather than in a prompt:
- a release unit is not `done` until every PR it covers is seen in a release tag (`pr.released`, the github
  stream's release check), and, with a dev_app, until its lead recorded `foundry unit deployed <unit> --app
  <dev_app> --env dev` (after the dev auto-sync check, the pod tag and D1-D3);
- prod is not part of it: the prod GitOps PR stays an owner merge (gitpolicy merge.owner_paths) until
  `foundry unit order` records the owner's words.
"""

import datetime as dt
import fnmatch
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import foundrylib as cl  # noqa: E402
import i18n  # noqa: E402

UNIT_FIELDS = {"release": None, "deployed": None}
DEV_ENVS = ("dev", "development")


def _op_release_unit(s, r, ts):
    u = (s.get("units") or {}).get(r.get("id"))
    if u is not None:
        u["release"] = {"repo": r.get("repo"), "prs": list(r.get("prs") or []), "procedure": r.get("procedure"),
                        "dev_app": r.get("dev_app"), "dev_deployed": None}


def _op_deploy_done(s, r, ts):
    """Observe graph's deploy.done: a release unit's dev stage is done once its own lead records it; every unit
    keeps its newest deploy per environment (dev / prod, normalized) for the 릴리스 진행 section."""
    u = (s.get("units") or {}).get(r.get("unit"))
    if u is not None and r.get("env"):
        u["deployed"] = {**(u.get("deployed") or {}),
                         cl.norm_env(r["env"]): {"app": r.get("app"), "tag": r.get("tag"), "ts": ts}}
    rel = (u or {}).get("release")
    if rel and rel.get("dev_app") and r.get("app") == rel["dev_app"] and str(r.get("env") or "").lower() in DEV_ENVS:
        rel["dev_deployed"] = r.get("tag") or ts


def _op_release_tags(s, r, ts):
    rel = ((s.get("units") or {}).get(r.get("id")) or {}).get("release")
    if rel is not None:
        rel["tags"] = sorted(set(rel.get("tags") or []) | set(r.get("tags") or []))


OPS = {"release.unit": _op_release_unit, "deploy.done": _op_deploy_done, "release.tags": _op_release_tags}


def _names(ref, rs):
    import gitpolicy
    return gitpolicy._names(gitpolicy.repo_by_ref(ref, rs) or {"name": ref}) | {str(ref).lower()}


def batch_entry(ref, p, rs=None):
    """The prefs release.batch_repos entry (or {} for repos.json `release: batch`) for a batch-release repo, else None."""
    import gitpolicy
    rs = gitpolicy.repos() if rs is None else rs
    names = _names(ref, rs)
    rp = p.get("release") or {}
    if any(fnmatch.fnmatch(n, g) for n in names for g in rp.get("none") or []):
        return None
    for e in rp.get("batch_repos") or []:
        if isinstance(e, dict) and any(fnmatch.fnmatch(n, str(e.get("repo") or "")) for n in names):
            return e
    return {} if (gitpolicy.repo_by_ref(ref, rs) or {}).get("release") == "batch" else None


def _covered(snap):
    """PR refs some release unit already covers (open or closed): never batched twice."""
    return {ref for u in snap["units"].values() for ref in ((u.get("release") or {}).get("prs") or [])}


def pending(snap, p, now=None, rs=None):
    """{repo: [{ref, unit, merged}]} merged PRs of batch-release repos that no release covers yet."""
    import gitpolicy
    rs = gitpolicy.repos() if rs is None else rs
    now = now or cl.now()
    cut = now - dt.timedelta(days=(p.get("release") or {}).get("watch_days", 14))
    covered, out = _covered(snap), {}
    for u in snap["units"].values():
        if u["kind"] == "foreman" or u.get("release"):
            continue
        for pr in u.get("prs") or []:
            ref = f"{pr.get('repo')}#{pr.get('number')}"
            t = cl.parse_ts(pr.get("merged"))
            if (str(pr.get("state") or "").upper() != "MERGED" or pr.get("released") or pr.get("deployed")
                    or not pr.get("repo") or ref in covered or not t or t < cut):
                continue
            if batch_entry(pr["repo"], p, rs) is not None:
                out.setdefault(pr["repo"], []).append({"ref": ref, "unit": u["id"], "merged": pr["merged"]})
    return out


def open_release(snap, repo):
    return next((u["id"] for u in snap["units"].values()
                 if u["status"] != "done" and (u.get("release") or {}).get("repo") == repo), None)


def _goal(repo, prs, entry, uid):
    proc = entry.get("procedure") or f"{repo}'s release procedure (its release workflow or tag convention)"
    steps = [f"Cut ONE batch release of {repo} covering {', '.join(prs)}, following {proc}. Release only what "
             "these PRs changed; if a PR turns out not to need a release, say so in a checkpoint instead of skipping it.",
             "A release bump PR (from the bump bot in prefs merge.bump_pr_authors, on a merge.bump_branch_glob branch "
             "like release/bump-v<version>, changing only version lines in merge.bump_files) is yours under the owner's "
             "standing rule, with no per-release order: no `foundry review assign`, no pr-review wait and no Slack "
             "review ask (all three are refused for it). Approve it with the owner account, `gh pr review <n> -R "
             f"{repo} --approve --body \"<version> release bump (owner standing rule)\"`, and once checks are green "
             "and it is CLEAN merge it with `--match-head-commit`. If the hook says it is not version-only, it takes "
             "the normal review path."]
    if entry.get("dev_app"):
        steps.append(
            f"Then the dev stage, part of this unit: the dev GitOps bump merges (dev needs no owner order) and "
            f"Argo auto-syncs; confirm LAST SYNC contains the merge sha, the pods run the new tag with 0 restarts, "
            f"and run the D1-D3 dev checks of {proc}; then `foundry unit deployed {uid} --app "
            f"{entry['dev_app']} --env dev --tag <tag> --evidence '<sync rev, pod tag, D1-D3 readings>'`.")
    steps.append(f"Prod is the owner's: open the prod card with the dev evidence, `foundry decide open --unit {uid} "
                 f"--key {uid}:prod-deploy --option 'go:{i18n.t('release.prod_go')}' --option 'hold:{i18n.t('release.prod_hold')}' --question '<...>' --asked-by line-lead`; "
                 "its `go` answer (the owner's, or the foreman's relay with the owner's quote) is the owner's order, "
                 "as is `foundry unit order <unit> --by foreman --quote ...`; both cover the prod PR even if it is "
                 "opened afterwards. Once the go exists the rest is yours, never the owner's: when the prod GitOps "
                 "PR comes from the deploy bot (prefs merge.deploy_pr_authors), check its diff only moves this "
                 "release's image tags, record the head you read it at (`foundry unit reviewed <unit> --pr <owner/repo#n> "
                 "--sha <headRefOid>`) and approve it pinned to that head, `gh pr review <n> -R <repo> --approve "
                 "--body \"<tag> prod deploy at <sha12> (owner order: <quote>)\"` (the approve hook re-checks bot "
                 "author, tag-only diff, the reviewed head and the recorded go; anything else is a card for the "
                 "owner, never an approval), merge it with "
                 "`--match-head-commit` (the merge takes the Argo app lock, one prod merge per app), verify the sync, "
                 "the PreSync hook and the release procedure's prod checks, record `foundry unit deployed <unit> --app "
                 "<app> --env prod --tag <tag> --evidence '...'`, then `foundry lock release argo:<app> --unit "
                 "<unit>`. Never hand the owner a merge line or a /foundry command, and never offer a \"Slack review "
                 "ask or auto-merge\" choice for this PR. Prod Argo Sync, prune and rollback stay the owner's: if the "
                 "app does not sync by itself, ask on a card. A human-authored prod PR, or one that changes more "
                 "than the tags, stays the owner's to review. `foundry unit status <unit> done` is refused until the PRs are seen in a release tag"
                 + (" and the dev deploy is recorded." if entry.get("dev_app") else "."))
    return " ".join(steps)


def release_id(snap, repo, now):
    base = f"release-{repo.rsplit('/', 1)[-1]}-{now.astimezone(cl.zone()).strftime('%Y%m%d-%H%M')}"
    n = 2
    uid = base
    while uid in snap["units"]:
        uid, n = f"{base}-{n}", n + 1
    return uid


def unit_records(uid, repo, prs, entry, note):
    short = repo.rsplit("/", 1)[-1]
    return [
        ("unit.new", {"id": uid, "kind": "project", "title": f"Release {short}: {len(prs)} merged PR(s)",
                      "repo": repo, "lead": "foundry:line-lead", "pr": [], "ticket": [], "topic": ["release"]}),
        ("release.unit", {"id": uid, "repo": repo, "prs": prs, "procedure": entry.get("procedure"),
                          "dev_app": entry.get("dev_app")}),
        ("handoff.write", {"id": uid, "goal": _goal(repo, prs, entry, uid),
                           "next": "Read the merged PRs, then cut the release."}),
        ("unit.status", {"id": uid, "status": "queue", "note": note}),
    ]


def new_release(snap, p, repo, prs, now, rs=None):
    """(records, None) for a release unit the owner ordered now, over `prs` (default: every pending merged PR of
    the repo); it may run beside an open batch release. (None, why) when the repo is no batch-release repo or another
    release unit already covers a PR."""
    if batch_entry(repo, p, rs) is None:
        return None, f"refused: {repo} is not a batch-release repo (prefs release.batch_repos / repos.json release: batch)"
    short = repo.rsplit("/", 1)[-1]
    mine = [i["ref"] for k, v in pending(snap, p, now, rs).items() if k.rsplit("/", 1)[-1] == short for i in v]
    prs = sorted(set(prs or mine))
    if not prs:
        return None, f"refused: no merged, unreleased PR of {repo} to release: name them with --pr owner/repo#n"
    import gitpolicy
    other = [r for r in prs if not gitpolicy.ORDER_RE.match(r) or r.split("#")[0].rsplit("/", 1)[-1] != short]
    held = sorted(set(prs) & _covered(snap))
    if other or held:
        return None, (f"refused: {', '.join(other)} not in {repo}" if other else
                      f"refused: {', '.join(held)} already in a release unit: route the order to it (foundry inbox route)")
    full = repo if "/" in repo else prs[0].split("#")[0]
    uid = release_id(snap, full, now)
    return unit_records(uid, full, prs, batch_entry(full, p, rs) or {}, f"release ordered now: {', '.join(prs)}"), None


def release_due(now, p, snap=None, rs=None):
    """(records, lines): one queued release unit per batch-release repo whose merged PRs waited long enough."""
    import gitpolicy
    import prefs
    rp = p.get("release") or {}
    if prefs.is_night(now, p):
        return [], []
    snap = snap or cl.load()
    rs = gitpolicy.repos() if rs is None else rs
    recs, lines = [], []
    for repo, items in sorted(pending(snap, p, now, rs).items()):
        if len(items) < int(rp.get("batch_min_prs", 1) or 1) or open_release(snap, repo):
            continue
        times = sorted(cl.parse_ts(i["merged"]) for i in items)
        quiet = now - times[-1] >= dt.timedelta(minutes=rp.get("batch_quiet_min", 60))
        overdue = now - times[0] >= dt.timedelta(hours=rp.get("batch_max_wait_h", 24))
        if not (quiet or overdue):
            continue
        uid = release_id(snap, repo, now)
        prs = sorted(i["ref"] for i in items)
        recs += unit_records(uid, repo, prs, batch_entry(repo, p, rs) or {}, f"batch release queued by foundry: {', '.join(prs)}")
        lines.append(f"release {uid}: {len(prs)} merged PR(s) of {repo} wait for a release; queued, "
                     "launch it in `foundry next` order (prefs release.batch_repos; no owner order needed)")
    return recs, lines


STAGE_KO = {"dev": "stage_dev", "prod": "stage_prod", "done": "stage_done"}  # cli catalog keys; tag: stage_tag_progress


def is_release_unit(u):
    return bool(u.get("release")) or str(u.get("id") or "").startswith("release-")


def progress(snap):
    """릴리스 진행: every open release unit with its stage (태그 → dev → prod), its PRs released / covered, and the
    newest dev / prod deploy. A unit opened by hand as `release-…` counts too (its own PRs are what it covers)."""
    released = _released_refs(snap)
    out = []
    for u in snap["units"].values():
        if not is_release_unit(u) or u.get("status") in ("done", "cancelled"):
            continue
        rel = u.get("release") or {}
        prs = list(rel.get("prs") or []) or [f"{p.get('repo')}#{p.get('number')}" for p in u.get("prs") or []
                                             if isinstance(p, dict)]
        dep = u.get("deployed") or {}
        n_rel = sum(1 for r in prs if r in released)
        stage = ("tag" if prs and n_rel < len(prs) and not dep else "dev" if "dev" not in dep
                 else "prod" if "prod" not in dep else "done")
        tag = (dep.get("prod") or dep.get("dev") or {}).get("tag")
        label = (i18n.t("cli.stage_tag_progress", released=n_rel, total=len(prs)) if stage == "tag"
                 else i18n.t("cli." + STAGE_KO[stage]))
        out.append({"unit": u["id"], "repo": rel.get("repo") or u.get("repo"), "prs": prs, "released": n_rel,
                    "stage": stage, "stage_ko": label,
                    "tag": tag, "dev": (dep.get("dev") or {}).get("tag"), "prod": (dep.get("prod") or {}).get("tag"),
                    "status": u.get("status")})
    return sorted(out, key=lambda x: x["unit"])


def board(snap, m):
    m["releases"] = progress(snap)


def _released_refs(snap):
    return {f"{pr.get('repo')}#{pr.get('number')}" for x in snap["units"].values() for pr in x.get("prs") or []
            if pr.get("released") or pr.get("deployed")}


def _pr_of(snap, ref):
    """(unit id, pr record) of the unit that holds this PR, release units last; (None, None) when none does."""
    repo, num = ref.rsplit("#", 1)
    for u in sorted(snap["units"].values(), key=lambda x: bool(x.get("release"))):
        for pr in u.get("prs") or []:
            if pr.get("repo") == repo and str(pr.get("number")) == num:
                return u["id"], pr
    return None, None


def tag_containing(repo, num, pattern, sha=None):
    """A release tag (matching `pattern`) that contains the PR's merge commit, or None. The local clone answers
    first (`git tag --contains`); else GitHub's compare API over the newest matching tags. Read-only."""
    import subprocess
    import factory
    import gitpolicy
    import watchers
    sha = sha or ((factory.gh("pr", "view", str(num), "-R", repo, "--json", "mergeCommit") or {}).get("mergeCommit")
                  or {}).get("oid")
    if not sha:
        return None
    path = (gitpolicy.repo_by_ref(repo) or {}).get("path")
    if path and os.path.isdir(path):
        try:
            r = subprocess.run(["git", "-C", path, "tag", "--contains", sha, "--list", pattern], capture_output=True,
                               text=True, timeout=30)
            names = sorted(r.stdout.split(), key=watchers._natural) if r.returncode == 0 else []
        except (OSError, subprocess.TimeoutExpired):
            names = []
        if names:
            return names[0]
    tags = factory.gh("api", f"repos/{repo}/tags?per_page=100")
    names = sorted((str(t.get("name") or "") for t in tags if fnmatch.fnmatch(str(t.get("name") or ""), pattern)),
                   key=watchers._natural, reverse=True) if isinstance(tags, list) else []
    for tag in names[:20]:
        if ((factory.gh("api", f"repos/{repo}/compare/{sha}...{tag}") or {}).get("status")) in ("ahead", "identical"):
            return tag
    return None


def backfill(snap, uid, p=None):
    """pr.released records for a release unit's PRs that a tag already holds but the journal never saw: a tag cut
    before the unit existed (or before the github stream's check reached the PR) left no record. Read-only."""
    import factory
    u = snap["units"].get(uid) or {}
    rel = u.get("release") or {}
    done = _released_refs(snap) | set(rel.get("released") or {})
    recs = []
    for ref in rel.get("prs") or []:
        if ref in done or "#" not in ref:
            continue
        repo, num = ref.rsplit("#", 1)
        pattern = factory.release_tag(repo, p)
        if pattern is None:
            continue
        owner, pr = _pr_of(snap, ref)
        tag = tag_containing(repo, num, pattern, (pr or {}).get("merge_commit"))
        if tag:
            recs.append(("pr.released", {"unit": owner or uid, "repo": repo, "number": int(num), "tag": tag,
                                         "note": "backfilled at the done gate: the tag predates the record"}))
    return recs


def done_refusal(snap, u):
    """Why a release unit may not be closed yet, or None."""
    rel = (u or {}).get("release")
    if not rel:
        return None
    released = _released_refs(snap) | set(rel.get("released") or {})
    missing = [r for r in rel.get("prs") or [] if r not in released]
    if missing:
        return (f"{u['id']} is not released yet: no release tag contains {', '.join(missing)} (the github stream "
                "records pr.released once a tag holds the merge commit)")
    if rel.get("dev_app") and not rel.get("dev_deployed"):
        return (f"record the dev stage first: foundry unit deployed {u['id']} --app {rel['dev_app']} --env dev "
                "--tag <tag> --evidence '<sync rev, pod tag, D1-D3 readings>'")
    return None


def gate_passed(snap):
    """Open release units whose done gate holds now (every PR in a tag, the dev stage recorded when it has one)."""
    return sorted(u["id"] for u in snap["units"].values()
                  if (u.get("release") or {}).get("prs") and u["status"] not in ("done", "cancelled")
                  and done_refusal(snap, u) is None)


def gate_records(snap):
    """(records, lines) once per release unit whose done gate just passed: closing it is its lead's act (an inbox
    line that resumes a parked or ended lead) or the foreman's (an event), never an owner chore."""
    seen = {e.get("eid") for e in snap.get("events") or []}
    recs, lines = [], []
    for uid in gate_passed(snap):
        eid = f"ev-{uid}-release-gate"
        if eid in seen:
            continue
        say = (f"{uid}: release done gate passed (PRs in a release tag"
               + (", dev deploy recorded" if (snap["units"][uid].get("release") or {}).get("dev_app") else "")
               + f"). Close it now: `foundry unit status {uid} done --note \"<tag, dev evidence>\"` "
               "(the gate is the evidence; no owner order needed).")
        recs += [("event", {"eid": eid, "unit": "_foreman", "wid": None, "kind": "release-gate",
                            "ref": f"unit:{uid}", "summary": say}),
                 ("inbox.add", {"id": uid, "iid": f"rel-{uid}", "from": "foundry", "text": say})]
        lines.append(f"event _foreman release-gate: {say}")
    return recs, lines


def _release_slug(rel, u):
    import gitpolicy
    r = gitpolicy.repo_by_ref(rel.get("repo") or u.get("repo")) or {}
    for x in (r.get("slug"), rel.get("repo"), u.get("repo")) + tuple(str(p).split("#")[0] for p in rel.get("prs") or []):
        if x and "/" in str(x):
            return str(x)
    return None


def _tag_sha(slug, tag):
    import gitpolicy
    import urllib.parse
    v = gitpolicy._gh_api(f"repos/{slug}/commits/{urllib.parse.quote(tag, safe='')}")
    return str(v.get("sha") or "") if isinstance(v, dict) else ""


def record_tags(snap, uid, tags):
    """(records, None) when every tag exists in the release's repo at the commit of a tag the unit already recorded
    (one batch release: one commit, one tag per app); else ([], why). Fails closed when GitHub cannot be read."""
    import gitpolicy
    u = (snap.get("units") or {}).get(uid)
    rel = (u or {}).get("release")
    if rel is None:
        return [], f"refused: {uid} is not a release unit"
    base = sorted(gitpolicy.release_tags(u) - set(tags))
    if not base:
        return [], (f"refused: {uid} has no release tag recorded yet: record the release and the dev deploy first "
                    "(foundry unit deployed --tag)")
    slug = _release_slug(rel, u)
    if not slug:
        return [], f"refused: no GitHub repo known for {uid}'s release to check the tags in"
    shas = {}
    for t in base:
        shas[t] = _tag_sha(slug, t)
        if not shas[t]:
            return [], f"refused: could not read tag {t} in {slug} from GitHub; retry"
    for t in tags:
        sha = _tag_sha(slug, t)
        if not sha:
            return [], f"refused: tag {t} not found in {slug} (or GitHub could not be read)"
        if sha not in shas.values():
            return [], (f"refused: tag {t} is at {sha[:12]}, not at the commit of {uid}'s release tag "
                        f"({', '.join(f'{k} {v[:12]}' for k, v in shas.items())})")
    return [("release.tags", {"id": uid, "tags": sorted(set(tags))})], None


def cmd_tag(a):
    import json
    import graph
    snap = cl.load()
    by, err = graph.check_by(a.by, "recording release tags")
    if err:
        print(err, file=sys.stderr)
        return 2
    if by not in ("owner", "lead") or (by == "lead" and not graph.own_unit(snap, by, a.unit)):
        print(f"refused: only the owner or {a.unit}'s own lead records its release tags", file=sys.stderr)
        return 2
    recs, why = record_tags(snap, a.unit, a.tag)
    if why:
        print(why, file=sys.stderr)
        return 2
    cl.append_many(recs)
    print(json.dumps({"unit": a.unit, "tags": recs[0][1]["tags"]}, ensure_ascii=False))
    return 0


def cmd_release(a):
    import json
    import prefs
    p, snap = prefs.load(), cl.load()
    if a.sub == "pending":
        print(json.dumps(pending(snap, p), ensure_ascii=False, indent=1))
        return 0
    if a.sub == "new":
        recs, why = new_release(snap, p, a.repo, a.pr, cl.now())
        if why:
            print(why, file=sys.stderr)
            return 2
        cl.append_many(recs)
        uid = recs[0][1]["id"]
        print(json.dumps({"unit": uid, "prs": recs[1][1]["prs"], "next": f"foundry unit launch {uid}"},
                         ensure_ascii=False))
        return 0
    recs, lines = release_due(cl.now(), p, snap)
    if recs and not a.dry_run:
        cl.append_many(recs)
    print(json.dumps({"queued": [r[1]["id"] for r in recs if r[0] == "release.unit"], "lines": lines,
                      "dry_run": bool(a.dry_run)}, ensure_ascii=False, indent=1))
    return 0


def add_cli(sp):
    r = sp.add_parser("release", help="batch releases foundry queues as units").add_subparsers(dest="sub", required=True)
    r.add_parser("pending", help="merged PRs of batch-release repos no release covers yet").set_defaults(func=cmd_release)
    n = r.add_parser("new", help="a release unit the owner ordered now (beside an open batch release, too)")
    n.add_argument("repo")
    n.add_argument("--pr", action="append", help="owner/repo#n; default every merged PR no release covers yet")
    n.set_defaults(func=cmd_release)
    t = r.add_parser("tag", help="record the other app tags of one batch release (same commit as its release tag)")
    t.add_argument("unit")
    t.add_argument("--tag", action="append", required=True)
    t.add_argument("--by", choices=["owner", "lead"])
    t.set_defaults(func=cmd_tag)
    c = r.add_parser("check", help="queue the release units that are due now (the github stream does this)")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_release)
