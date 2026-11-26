"""0.1.110: the plugin directory is the public repository's root. Its own .claude-plugin/marketplace.json is a
single-plugin marketplace (`"source": "./"`); install.sh adds the parent marketplace when the local layout has one,
else the plugin's own. 0.1.114: the public marketplace is named foundry (`foundry@foundry`); install.sh and
uninstall.sh read the name from the marketplace they use."""

import json
import os
import shutil

import test_install

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP = os.path.dirname(ROOT)


def load(path):
    with open(path) as f:
        return json.load(f)


class Manifest(test_install.unittest.TestCase):
    def test_single_plugin_marketplace_at_the_plugin_root(self):
        own = load(os.path.join(ROOT, ".claude-plugin", "marketplace.json"))
        plugin = load(os.path.join(ROOT, ".claude-plugin", "plugin.json"))
        (entry,) = own["plugins"]
        self.assertEqual((entry["name"], entry["source"], entry["version"]), (plugin["name"], "./", plugin["version"]))
        local = os.path.join(TOP, ".claude-plugin", "marketplace.json")
        self.assertEqual(own["name"], "foundry")
        if os.path.exists(local):  # the private layout: its own marketplace name, the same version
            parent = load(local)
            self.assertEqual(parent["plugins"][0]["source"], "./foundry")
            self.assertEqual(parent["plugins"][0]["version"], plugin["version"])

    def test_publishing_builds_from_the_plugin_tree_on_top_of_the_last_public_commit(self):
        with open(os.path.join(ROOT, "docs", "publishing.md")) as f:
            doc = f.read()
        for words in ("git rev-parse 'master:foundry'", "commit-tree -S <tree> -p publish", "publish:main",
                      "53788601+JeongJaeSoon@users.noreply.github.com", "gitleaks git"):
            self.assertIn(words, doc)


class InstallLayouts(test_install.Install):
    def marketplace_adds(self):
        with open(os.path.join(self.home, "claude-calls.txt")) as f:
            return [x.split("plugin marketplace add ", 1)[1].strip() for x in f if "plugin marketplace add" in x]

    def calls(self):
        with open(os.path.join(self.home, "claude-calls.txt")) as f:
            return f.read().splitlines()

    def test_a_clone_adds_the_plugin_itself(self):
        self.run_sh("install.sh")
        self.assertEqual(self.marketplace_adds(), [self.p])
        self.assertIn("plugin install foundry@foundry", self.calls())

    def test_the_local_layout_adds_its_parent_by_its_own_name(self):
        parent = os.path.dirname(self.p)
        os.makedirs(os.path.join(parent, ".claude-plugin"))
        with open(os.path.join(parent, ".claude-plugin", "marketplace.json"), "w") as f:
            json.dump({"name": "local-foundry", "owner": {"name": "local"},
                       "plugins": [{"name": "foundry", "source": "./foundry"}]}, f)
        self.run_sh("install.sh")
        self.assertEqual(self.marketplace_adds(), [parent])
        self.run_sh("uninstall.sh")
        calls = self.calls()
        for line in ("plugin install foundry@local-foundry", "plugin uninstall foundry@local-foundry",
                     "plugin marketplace remove local-foundry"):
            self.assertIn(line, calls)
        self.assertFalse([c for c in calls if c.endswith("@foundry")])


def load_tests(loader, tests, pattern):
    suite = test_install.unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(Manifest))
    suite.addTests(InstallLayouts(n) for n in ("test_a_clone_adds_the_plugin_itself",
                                                             "test_the_local_layout_adds_its_parent_by_its_own_name"))
    return suite
