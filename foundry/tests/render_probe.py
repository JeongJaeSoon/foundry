"""The owner-facing renders of one example state, for the language tests (test_lifecycle_0194): the text board, the
board model (without its catalog), the daily report in md and Slack form, release and review gaps, the handover,
kaizen and a decision card. Built in a throwaway state dir from example-org data; nothing real is read.

    python3 tests/render_probe.py ko|ja|en      (from foundry/; prints the renders as JSON)
"""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, os.path.join(ROOT, "tests"))
import test_report_0150 as r50  # noqa: E402

DAY = r50.DAY
NOW = "2026-11-18T18:00:00+09:00"


def run(env, *args, script="foundry.py", ok=(0, 1)):
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args], env=env, capture_output=True,
                       text=True)
    if r.returncode not in ok:
        raise SystemExit(f"{script} {' '.join(args)}: {r.returncode}\n{r.stderr}{r.stdout}")
    return r.stdout


def record(env, op, **kw):
    code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import foundrylib as cl; "
            "cl.append(sys.argv[2], **json.loads(sys.argv[3]))")
    subprocess.run([sys.executable, "-c", code, SCRIPTS, op, json.dumps(kw)], env=env, check=True)


def build(d, lang):
    env = {k: v for k, v in os.environ.items()
           if k not in ("FOUNDRY_PREFS_OVERLAY", "FOUNDRY_SESSION", "FOUNDRY_ROLE", "FOUNDRY_UNIT", "FOUNDRY_MODEL",
                        "ANTHROPIC_MODEL")}
    env.update({"FOUNDRY_STATE_DIR": d, "FOUNDRY_NOW": NOW, "FOUNDRY_CLAUDE": "false", "FOUNDRY_DOCKER": "false",
                "FOUNDRY_WORKTREE_DIRS": "", "FOUNDRY_MCP_SERVERS": "[]", "FOUNDRY_TEST": "1",
                "FOUNDRY_GH": f"{sys.executable} {os.path.join(ROOT, 'tests', 'fake_gh.py')}",
                "FAKE_GH_DATA": os.path.join(d, "gh.json"), "FOUNDRY_PRESSURE_FAKE": json.dumps(
                    {"disk_free_gb": {"/": 300}, "mem_free_pct": 55, "load_per_cpu": 0.3})})
    with open(env["FAKE_GH_DATA"], "w") as f:
        json.dump(r50.GH, f)
    with open(os.path.join(d, "prefs.json"), "w") as f:
        json.dump(r50.PREFS | {"ui": {"language": lang}, "timezone": "Asia/Tokyo"}, f)
    with open(os.path.join(d, "config.json"), "w") as f:
        json.dump({"owner_slack_user_id": "U0OWNER0001"}, f)
    for args in (["--id", "login-fix", "--title", "Fix the login redirect loop", "--repo", "example-org/app",
                  "--ticket", "PROJ-12", "--pr", "example-org/app#7", "--status", "in_progress"],
                 ["--id", "export-limit", "--title", "Rate-limit the export API", "--repo", "example-org/api",
                  "--status", "in_progress"],
                 ["--id", "faq", "--title", "Update the FAQ page", "--repo", "example-org/app", "--pr",
                  "example-org/app#5", "--status", "done"],
                 ["--id", "cache", "--title", "Warm the cache", "--repo", "example-org/api", "--status", "queue"],
                 ["--id", "keys", "--title", "Rotate the signing keys", "--repo", "example-org/api",
                  "--status", "waiting_owner"],
                 ["--id", "vendor", "--title", "Vendor sign-off", "--repo", "example-org/api",
                  "--status", "waiting_others"]):
        run(env, "unit", "new", *args, ok=(0,))
    record(env, "unit.worktree", id="login-fix", repo="example-org/app", path="/work/wt/app/login-fix",
           branch="foundry/login-fix")
    record(env, "pr.state", unit="login-fix", repo="example-org/app", number=7, state="OPEN",
           title="Fix the login redirect loop", review="REVIEW_REQUIRED")
    record(env, "wait.add", id="login-fix", wid="w1", kind="pr-review", ref="example-org/app#7", until=None,
           note="reviewer: alex")
    record(env, "pr.merged", unit="faq", repo="example-org/app", number=5)
    record(env, "approval.request", rid="ap-1", key="k1", session="S-login", unit="login-fix", role="line-lead",
           tool="Bash", summary="gh pr merge 7 -R example-org/app --squash", tier=3)
    run(env, "decide", "open", "--unit", "export-limit", "--key", "export-limit:limit", "--asked-by", "line-lead",
        "--question", "Which default limit should the export API ship with?",
        "--option", "A:60 requests a minute per key", "--option", "B:600 requests an hour per key",
        "--recommend", "A", "--reason", "Matches the current peak", ok=(0,))
    run(env, "owner", "todo", "add", "--unit", "keys", "--by", "foreman", "--kind", "go", "--text",
        "Confirm the key rotation window", "--why-owner", "a real choice between options", ok=(0,))
    run(env, "report", "slack-done", "--date", DAY, "--count", "0")
    return env


def renders(lang):
    with tempfile.TemporaryDirectory(prefix="foundry-probe-") as d:
        env = build(d, lang)
        model = json.loads(run(env, "json", script="board.py", ok=(0,)))
        model.pop("i18n", None)
        out = {"board": run(env, "board"), "model": json.dumps(model, ensure_ascii=False, sort_keys=True),
               "daily_md": run(env, "report", "daily", "--date", DAY),
               "daily_slack": run(env, "report", "daily", "--date", DAY, "--format", "slack"),
               "release_gaps": run(env, "report", "release-gaps"),
               "review_gaps": run(env, "report", "review-gaps"),
               "handover": run(env, "report", "handover"), "kaizen": run(env, "report", "kaizen"),
               "card": run(env, "decide", "show", "d-0001")}
        return {k: v.replace(d, "<state>") for k, v in out.items()}


if __name__ == "__main__":
    print(json.dumps(renders(sys.argv[1] if len(sys.argv) > 1 else "en"), ensure_ascii=False, indent=1))
