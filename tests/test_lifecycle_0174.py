"""0.1.74: MIT license, the publishing procedure, design notes out of the tree, autoMode rules in docs."""

import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class OssPassOne(unittest.TestCase):
    def test_license_file_and_manifest_agree(self):
        with open(os.path.join(ROOT, "LICENSE")) as f:
            self.assertTrue(f.readline().startswith("MIT License"))
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as f:
            self.assertEqual(json.load(f)["license"], "MIT")

    def test_publishing_commits_the_tree_alone_with_the_noreply_author(self):
        with open(os.path.join(ROOT, "docs", "publishing.md")) as f:
            doc = f.read()
        self.assertIn("git commit-tree -S <tree> -p publish", doc)  # 0.1.110: signed, on the last public commit
        self.assertEqual(doc.count("53788601+JeongJaeSoon@users.noreply.github.com"), 3)  # author, committer, prose

    def test_internal_docs_and_drafts_are_gone(self):
        for rel in ("docs/design.ko.md", "docs/foundry-overview.html", "settings-draft"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_automode_rules_are_valid_json_strings(self):
        with open(os.path.join(ROOT, "docs", "recommended-automode.md")) as f:
            block = f.read().split("```json\n", 1)[1].split("```", 1)[0]
        rules = json.loads("{" + block.strip() + "}")["autoMode"]["allow"]
        self.assertEqual(rules[0], "$defaults")
        self.assertEqual(len(rules), 10)
        self.assertFalse([r for r in rules if "Orchestration" in r])


if __name__ == "__main__":
    unittest.main()
