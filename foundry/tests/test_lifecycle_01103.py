"""0.1.103: `foundry adopt <session>` (and `/foundry adopt`) makes an existing non-foundry `claude agents` session an
ad-hoc unit: unit.new + unit.adopted with its session id. The list is read live from `claude agents`; with no
session named it shows the adoptable ones and writes nothing. Foundry's own sessions and one already adopted are
refused."""

import json
import os
import sys

import test_foundry as tf

AGENTS = [{"name": "notes-tidy", "sessionId": "s-111", "id": "bg-1", "cwd": "/w/notes", "status": "idle"},
          {"name": "lead-u1", "sessionId": "s-222", "id": "bg-2"},
          {"name": "spike", "sessionId": "s-333", "id": "bg-3"}]


class Adopt(tf.Base):
    def setUp(self):
        super().setUp()
        p = os.path.join(self.dir, "claude.py")
        with open(p, "w") as f:
            f.write("import json, sys\n"
                    f"print(json.dumps({AGENTS!r}) if sys.argv[1:2] == ['agents'] else '')\n")
        self.env["FOUNDRY_CLAUDE"] = f"{sys.executable} {p}"

    def test_list_then_adopt(self):
        _, out = self.foundry("adopt")
        self.assertEqual([x["name"] for x in out["adoptable"]], ["notes-tidy", "spike"])  # lead-* is foundry's
        self.assertFalse([r for r in self.journal() if r["op"] in ("unit.new", "unit.adopted")])
        _, out = self.foundry("adopt", "s-111", "--title", "Tidy the notes")
        self.assertEqual(out, {"unit": "adopt-notes-tidy", "adopted": "s-111", "name": "notes-tidy"})
        u = self.snap()["units"]["adopt-notes-tidy"]
        self.assertEqual((u["title"], u["status"], u["adopted"]["session_id"], u["adopted"]["cwd"]),
                         ("Tidy the notes", "in_progress", "s-111", "/w/notes"))
        self.assertNotIn("notes-tidy", [x["name"] for x in self.foundry("adopt")[1]["adoptable"]])
        self.assertFalse([r for r in self.journal() if "agents" in r and isinstance(r["agents"], list)
                          and any("sessionId" in str(x) for x in r["agents"])])  # the list itself is not stored

    def test_refusals(self):
        self.assertEqual(self.foundry("adopt", "nope", ok=(2,))[0], 2)
        self.record("session.launch", name="lead-u9", unit="u9", role="line-lead", bg_id="bg-3", cwd="/w")
        self.assertEqual(self.foundry("adopt", "spike", ok=(2,))[0], 2)  # bg-3 is a foundry session
        self.foundry("adopt", "bg-1", "--id", "first")
        self.assertEqual(self.foundry("adopt", "notes-tidy", ok=(2,))[0], 2)  # adopted already

    def test_the_slash_verb(self):
        with open(os.path.join(os.path.dirname(tf.SCRIPTS), "hooks", "slash.ts")) as f:
            self.assertIn("'adopt'", f.read().split("SLASH_VERBS", 1)[1].split("\n", 1)[0])
