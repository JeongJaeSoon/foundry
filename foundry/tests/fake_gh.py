"""Stand-in for `gh pr view`, `gh api <path>` (key "api <path>"), `gh search prs` (key: the args before --json) and
`gh api graphql` (key "api graphql owner/name#n"): answers from the JSON file named by FAKE_GH_DATA;
a key holding `*` answers every target it matches."""

import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_GH_DATA"]) as f:
    data = json.load(f)
target = f"api {args[1]}" if args[0] == "api" else args[2] if len(args) > 2 else ""
if "-R" in args:
    target = f"{args[args.index('-R') + 1]}#{target}"
if args[0] == "search":  # "search prs --author @me --updated >=2026-11-18": the flags before --json
    target = " ".join(args[:args.index("--json")] if "--json" in args else args)
if args[:2] == ["api", "graphql"]:  # one PR per query: "api graphql owner/name#n"
    f = dict(a.split("=", 1) for a in args[2:] if "=" in a and not a.startswith("query="))
    target = f"api graphql {f.get('owner')}/{f.get('name')}#{f.get('number')}"
if target not in data:  # a key with `*` stands for a family of queries (fnmatch), exact keys first
    import fnmatch
    target = next((k for k in data if "*" in k and fnmatch.fnmatchcase(target, k)), None)
if target is None or target not in data:
    print("no pull requests found", file=sys.stderr)
    sys.exit(1)
v = data[target]
print(v["_raw"] if isinstance(v, dict) and "_raw" in v else json.dumps(v))  # _raw: text output (gh pr diff)
