"""0.1.83: the foreman's start line hands it the MCP-mapped definition with `--agents` (from `foundry mcp agent`),
like a lead launch: a --bg `--agent` session would keep the file's generic server names."""

import json
import os
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import mcpmap  # noqa: E402

ROOT = os.path.dirname(tf.SCRIPTS)
SERVERS = ["plugin_acme_acme-atlassian", "plugin_acme_acme-slack"]
SUB = '--agents "$(foundry mcp agent foundry:foreman 2>/dev/null || echo {})" --agent foundry:foreman'


class ForemanAgents(tf.Base):
    CREW = False

    def test_agent_cli_prints_the_mapped_foreman(self):
        self.env["FOUNDRY_MCP_SERVERS"] = json.dumps(SERVERS)
        out = self.foundry("mcp", "agent", "foundry:foreman")[1]
        tools = out["foundry:foreman"]["tools"]
        self.assertIn("mcp__plugin_acme_acme-atlassian__getJiraIssue", tools)
        self.assertFalse(any("slack" in t for t in tools))  # no wider than the file
        self.env["FOUNDRY_MCP_SERVERS"] = json.dumps([])
        self.assertEqual(self.foundry("mcp", "agent", "foundry:foreman")[1], {})

    def test_lead_launch_and_foreman_share_one_definition(self):
        m = mcpmap.mapping({}, set(SERVERS))
        self.assertEqual(sorted(mcpmap.agent_flag("foundry:foreman", m)), ["foundry:foreman"])
        self.assertEqual(mcpmap.agent_flag("foundry:hand", m), {})

    def test_every_start_line_carries_it(self):
        import firstrun
        import foundrylib as cl
        self.assertIn(SUB, firstrun.foreman_command(cl.load_config()))
        with open(os.path.join(ROOT, "README.md")) as f:
            self.assertIn(SUB, f.read())
        with open(os.path.join(ROOT, "install.sh")) as f:  # it prints foreman_command (`foundry foreman line`)
            self.assertIn('foundry.py" foreman line', f.read())
        with open(os.path.join(ROOT, "skills", "foundry-handoff", "SKILL.md")) as f:
            self.assertIn("claude stop <id>", f.read())
