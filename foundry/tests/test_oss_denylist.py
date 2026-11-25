"""OSS readiness: no organization, people or machine-specific data in the plugin tree. The owner's own values live in
their prefs (materialized in 0.1.65/0.1.66); the suite's fixtures use example-org.

The owner's public GitHub name (LICENSE, docs/publishing.md) is allowed; their machine paths are not.
No exceptions: agent tool lists name generic MCP servers, mapped per install (scripts/mcpmap.py).
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Built from parts so this file does not match itself.
_TERMS = ["fr" + "eee", "C-" + "FO", "AI" + "PE", "flu" + "gel", "cluster" + "ops", "ClusterO" + "ps", "sushi" + "-ci",
          "ai" + "-lab", "kib" + "an", "hyst" + "king", "okazaki" + "-kk", "HikaruE" + "gashira", "reiko" + "0000",
          "jaesoon" + "-jeong", "conductor/" + "repos", "bru" + "cke", "presi" + "dio"]
DENY = re.compile(r"(?i)" + "|".join(re.escape(t) for t in _TERMS))
REAL_IDS = re.compile("|".join(["U040NP" + "BGZ9S", "U039EU" + "SSVJ7", "U050J4" + "KSCA3", "U0B5NG" + "WGGG4",
                                "C08HUH" + "L0K1N"]))
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
TEXT = (".py", ".md", ".json", ".ts", ".tsx", ".sh", ".html", ".diff", ".patch", ".txt", ".yaml", ".yml", "")


def files():
    # The published root also carries the marketplace manifest and .gitignore beside the plugin.
    top = os.path.dirname(ROOT)
    for rel in (os.path.join(".claude-plugin", "marketplace.json"), ".gitignore"):
        if os.path.exists(os.path.join(top, rel)):
            yield os.path.join("..", rel), os.path.join(top, rel)
    for d, dirs, names in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for n in names:
            p = os.path.join(d, n)
            rel = os.path.relpath(p, ROOT)
            if rel == os.path.join("tests", "test_oss_denylist.py"):
                continue
            if os.path.splitext(n)[1] in TEXT:
                yield rel, p


class Denylist(unittest.TestCase):
    def test_no_org_people_or_machine_data(self):
        hits = []
        for rel, p in files():
            try:
                with open(p, encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except UnicodeDecodeError:
                continue
            for i, line in enumerate(lines, 1):
                m = DENY.search(line) or REAL_IDS.search(line)
                if m:
                    hits.append(f"{rel}:{i}: {m.group(0)}")
        self.assertEqual(hits, [], "\n".join(hits[:50]))

    def test_the_slides_are_scanned(self):
        scanned = {rel for rel, _ in files()}
        for lang in ("en", "ja", "ko"):
            self.assertIn(os.path.join("docs", "slides", f"foundry.{lang}.md"), scanned)

    def test_the_denylist_catches_what_it_should(self):
        self.assertTrue(DENY.search("see " + "C-" + "FO/x"))
        self.assertTrue(DENY.search("~/" + "conductor/" + "repos/app"))
        self.assertTrue(DENY.search("mcp__plugin_" + "bru" + "cke_" + "bru" + "cke-slack__x"))
        self.assertTrue(DENY.search("release-" + "presi" + "dio-ja-gateway"))
        self.assertFalse(DENY.search("U0000000001 example-org/webapp"))


if __name__ == "__main__":
    unittest.main()
