"""hooks.json matchers route every tool the hook code handles: a handler its matcher never reaches is dead."""

import ast
import glob
import json
import os
import re
import unittest

from test_foundry import ROOT, SCRIPTS

SLACK = "mcp__claude_ai_Slack__slack_send_message"
HANDLERS = {"pre_tool_use": "PreToolUse", "post_tool_use": "PostToolUse",
            "post_tool_use_failure": "PostToolUseFailure"}
TOOL_RE = re.compile(r"^[A-Z][a-z]+(?:[A-Z][a-z]+)*$")  # Bash, TaskCreate; not MERGED or OPEN

ROUTED = {
    "PreToolUse": ("Agent", "Task", "SendMessage", "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", SLACK, "mcp__x__slack_schedule_message"),
    "PostToolUse": ("Bash", "TaskCreate", "TaskUpdate", "TodoWrite", "CronCreate", "CronDelete", SLACK),
    "PostToolUseFailure": ("Bash", SLACK),
}
NOT_ROUTED = {
    "PreToolUse": ("Read", "TaskCreate", "WriteX", "BashOutput", "CronCreate"),
    "PostToolUse": ("Write", "Read", "Agent", "CronList", "TaskList"),
    "PostToolUseFailure": ("Write", "Agent"),
}


def matchers():
    with open(os.path.join(ROOT, "hooks", "hooks.json")) as f:
        hooks = json.load(f)["hooks"]
    return {ev: [h.get("matcher") for h in hooks.get(ev) or []] for ev in HANDLERS.values()}


def routed(event, tool):
    return any(m in (None, "", "*") or re.search(m, tool) for m in matchers()[event])


def handled_tools():
    """{(file, handler): {tool names}} compared against in each module's tool hook functions."""
    out = {}
    for path in sorted(glob.glob(os.path.join(SCRIPTS, "*.py"))):
        with open(path) as f:
            tree = ast.parse(f.read())
        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)):
                vals = [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        consts[t.id] = vals
        for fn in tree.body:
            if not (isinstance(fn, ast.FunctionDef) and fn.name in HANDLERS):
                continue
            names = set()
            for cmp in ast.walk(fn):
                if not isinstance(cmp, ast.Compare):
                    continue
                for side in [cmp.left, *cmp.comparators]:
                    elts = side.elts if isinstance(side, (ast.Tuple, ast.List, ast.Set)) else [side]
                    for e in elts:
                        if isinstance(e, ast.Constant) and isinstance(e.value, str):
                            names.add(e.value)
                        elif isinstance(e, ast.Name):
                            names.update(consts.get(e.id) or [])
            tools = {n for n in names if TOOL_RE.match(n)}
            if tools:
                out[(os.path.basename(path), fn.name)] = tools
    return out


class Routing(unittest.TestCase):
    def test_each_event_routes_exactly_the_expected_tools(self):
        for event, tools in ROUTED.items():
            for tool in tools:
                self.assertTrue(routed(event, tool), f"{event} does not route {tool}")
        for event, tools in NOT_ROUTED.items():
            for tool in tools:
                self.assertFalse(routed(event, tool), f"{event} routes {tool}")

    def test_every_tool_a_handler_names_reaches_it(self):
        found = handled_tools()
        self.assertIn("Write", found[("watchers.py", "pre_tool_use")])
        self.assertIn("CronCreate", found[("watchers.py", "post_tool_use")])
        self.assertIn("TodoWrite", found[("hook.py", "post_tool_use")])
        for (path, fn), tools in found.items():
            for tool in tools:
                self.assertTrue(routed(HANDLERS[fn], tool), f"{path}:{fn} handles {tool}, but the "
                                                            f"{HANDLERS[fn]} matcher never routes it")


if __name__ == "__main__":
    unittest.main()
