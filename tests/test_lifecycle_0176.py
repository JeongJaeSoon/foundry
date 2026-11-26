"""0.1.76: agent tool lists name generic MCP servers; each install maps them to its own servers (prefs `mcp_servers`,
else a unique `-<name>` suffix among the installed servers), the board-pane module re-registers the agents with the
mapped names, and the lead ask rules carry them too."""

import json
import os
import re
import subprocess
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import mcpmap  # noqa: E402

ROOT = os.path.dirname(tf.SCRIPTS)
SERVERS = ["plugin_acme_acme-slack", "plugin_acme_acme-atlassian", "acme-atlassian-dev", "plugin_acme_acme-datadog",
           "obsidian"]


class Mapping(tf.Base):
    CREW = False

    def test_discovery_takes_a_unique_suffix_and_skips_near_names(self):
        found, ambiguous = mcpmap.discover({"slack", "atlassian", "obsidian", "linear"}, set(SERVERS))
        self.assertEqual(found, {"slack": "plugin_acme_acme-slack", "atlassian": "plugin_acme_acme-atlassian"})
        self.assertEqual(ambiguous, {})
        _, ambiguous = mcpmap.discover({"slack"}, {"a-slack", "b_slack"})
        self.assertEqual(ambiguous, {"slack": ["a-slack", "b_slack"]})

    def test_prefs_win_over_discovery(self):
        m = mcpmap.mapping({"mcp_servers": {"slack": ["my_slack"]}}, set(SERVERS))
        self.assertEqual(m["slack"], ["my_slack"])
        self.assertEqual(m["atlassian"], ["plugin_acme_acme-atlassian"])

    def test_prefs_reject_server_names_that_are_not_plain(self):
        import prefs
        for bad in ({"slack": ["x__y"]}, {"slack": ["mcp_*"]}, {"slack": "plugin_x"}, {"slack": []}):
            self.assertTrue([e for e in prefs.validate({"mcp_servers": bad}) if e.startswith("mcp_servers")], bad)
        self.assertEqual([e for e in prefs.validate({"mcp_servers": {"slack": ["plugin_x_slack"]}})
                          if e.startswith("mcp_servers")], [])

    def test_specs_equal_the_files_with_only_server_names_added(self):
        m = mcpmap.mapping({}, set(SERVERS))
        specs = {s["name"]: s for s in mcpmap.specs(m)}
        self.assertEqual(sorted(specs), ["dispatcher", "foreman", "line-lead", "shop-boss"])
        for name, spec in specs.items():
            with open(os.path.join(ROOT, "agents", name + ".md"), encoding="utf-8") as f:
                text = f.read()
            head, body = text.split("\n---\n", 1)
            meta = dict(re.match(r"([A-Za-z]+):\s*(.*)", ln).groups() for ln in head.splitlines()[1:])
            self.assertEqual(spec["prompt"], body.lstrip("\n"))
            self.assertEqual(spec["description"], meta["description"])
            self.assertEqual((spec.get("model"), spec.get("effort"), spec.get("color")),
                             (meta.get("model"), meta.get("effort"), meta.get("color")))
            file_tools = [t.strip() for t in meta["tools"].split(",")]
            self.assertTrue(set(file_tools) <= set(spec["tools"]))
            for extra in set(spec["tools"]) - set(file_tools):
                srv, tool = re.match(r"mcp__(.+?)__(.+)", extra).groups()
                self.assertIn(srv, sum(m.values(), []))
                self.assertIn(f"__{tool}", " ".join(t for t in file_tools if t.startswith("mcp__")))
        disp = specs["dispatcher"]["tools"]
        self.assertIn("mcp__plugin_acme_acme-atlassian__addOrEditJiraIssueComment", disp)
        self.assertIn("mcp__plugin_acme_acme-slack__slack_send_message", disp)
        lead = specs["line-lead"]["tools"]
        self.assertNotIn("mcp__plugin_acme_acme-slack__slack_send_message", lead)  # leads still only read Slack

    def test_no_mapping_no_specs(self):
        self.assertEqual(mcpmap.specs({}), [])

    def test_specs_cli_and_the_preflight_check(self):
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "mcp", "specs"], env=self.env,
                           capture_output=True, text=True)
        names = [s["name"] for s in json.loads(r.stdout)["agents"]]
        self.assertIn("dispatcher", names)
        self.env["FOUNDRY_MCP_SERVERS"] = json.dumps(["a-slack", "b_slack"])
        rc, out = self.foundry("mcp", "map", ok=(1,))
        self.assertFalse(out["ok"])
        self.assertIn("mcp_servers.slack", out["detail"])

    def test_installed_servers_reads_user_config_and_enabled_plugins(self):
        cdir = os.path.join(self.dir, "cfg")
        plug = os.path.join(self.dir, "plug")
        os.makedirs(os.path.join(cdir, "plugins"))
        os.makedirs(plug)
        with open(os.path.join(plug, ".mcp.json"), "w") as f:
            json.dump({"mcpServers": {"gw-slack": {}}}, f)
        with open(os.path.join(cdir, "plugins", "installed_plugins.json"), "w") as f:
            json.dump({"plugins": {"gw@m": [{"installPath": plug}], "off@m": [{"installPath": plug}]}}, f)
        with open(os.path.join(cdir, "settings.json"), "w") as f:
            json.dump({"enabledPlugins": {"gw@m": True, "off@m": False}}, f)
        with open(os.path.join(cdir, ".claude.json"), "w") as f:
            json.dump({"mcpServers": {"obsidian": {}}, "projects": {"/x": {"mcpServers": {"my.jira": {}}}}}, f)
        old = {k: os.environ.pop(k, None) for k in ("FOUNDRY_MCP_SERVERS", "HOME")}
        os.environ.update({"CLAUDE_CONFIG_DIR": cdir, "HOME": self.dir})  # the real home config stays out
        try:
            self.assertEqual(mcpmap.installed_servers(), {"plugin_gw_gw-slack", "obsidian", "my_jira"})
        finally:
            os.environ.pop("CLAUDE_CONFIG_DIR")
            os.environ.update({k: v for k, v in old.items() if v is not None})


class AskRules(tf.Base):
    CREW = False

    def test_lead_ask_rules_carry_the_mapped_slack_sends(self):
        import foundrylib as cl
        rules = mcpmap.map_tools(cl.load_config()["lead_ask_rules"], mcpmap.mapping({}, set(SERVERS)))
        for r in ("mcp__slack__slack_send_message", "mcp__plugin_acme_acme-slack__slack_send_message",
                  "mcp__plugin_acme_acme-slack__slack_schedule_message"):
            self.assertIn(r, rules)
