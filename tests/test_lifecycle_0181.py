"""0.1.81: a --bg lead takes its main agent from the file before the board-pane module re-registers it, so the
launch passes the mapped definition with `--agents`: the lead sees this install's MCP servers, not the generic names."""

import json
import os
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import launcher  # noqa: E402

SERVERS = ["plugin_acme_acme-slack", "plugin_acme_acme-atlassian", "plugin_acme_acme-datadog"]


class LaunchAgents(tf.Base):
    CREW = False

    def setUp(self):
        super().setUp()
        self.old = os.environ.get("FOUNDRY_MCP_SERVERS")
        os.environ["FOUNDRY_MCP_SERVERS"] = json.dumps(SERVERS)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("FOUNDRY_MCP_SERVERS", None)
        else:
            os.environ["FOUNDRY_MCP_SERVERS"] = self.old
        super().tearDown()

    def test_lead_launch_carries_the_mapped_definition(self):
        for lead in ("foundry:line-lead", "foundry:shop-boss"):
            flag = launcher.mapped_agent(lead)
            self.assertEqual(flag[0], "--agents")
            spec = json.loads(flag[1])[lead]
            self.assertIn("mcp__plugin_acme_acme-datadog__search_datadog_logs", spec["tools"])
            self.assertIn("mcp__plugin_acme_acme-slack__slack_read_thread", spec["tools"])
            self.assertTrue(spec["prompt"].strip())
            self.assertFalse(any("send_message" in t for t in spec["tools"]))  # no wider than the file

    def test_no_mapping_no_flag(self):
        os.environ["FOUNDRY_MCP_SERVERS"] = json.dumps([])
        self.assertEqual(launcher.mapped_agent("foundry:line-lead"), [])

    def test_argv_names_the_agent_after_its_definition(self):
        import foundrylib as cl
        argv = launcher.launch_argv(cl.load_config(), "lead-x", "foundry:line-lead", "opus", "high", "x", 10)
        self.assertLess(argv.index("--agents"), argv.index("--agent"))
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
