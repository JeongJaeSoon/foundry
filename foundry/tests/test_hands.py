"""Tests for `foundry spawn-check` (the mod's agent.spawn check) and the hands the board lists."""

import json
import os
import sys
import unittest

from test_foundry import LEAD, SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import hands  # noqa: E402


class SpawnCheck(Base):
    def check(self, **req):
        _, out = self.foundry("spawn-check", "--json", json.dumps(req))
        return out

    def test_foreman_main_thread_cannot_spawn_a_hand(self):
        out = self.check(subagentType="foundry:hand", tool_use_id="t9", session_id="S1")
        self.assertIn("never spawns", out["deny"])
        self.assertEqual(self.snap()["hands"], {})

    def test_foreman_spawns_staff(self):
        out = self.check(subagentType="foundry:dispatcher", tool_use_id="t2", session_id="S1")
        self.assertNotIn("deny", out)
        self.assertEqual(out["caller"], "foreman")

    def test_lead_spawn_pins_model_and_journals_once(self):
        self.start_lead("alpha")
        req = dict(subagentType="foundry:hand-light", model="opus", tool_use_id="t1", session_id="SL", prompt="go")
        out = self.check(**req)
        self.assertEqual(out["model"], "haiku")
        self.check(**req)
        hs = list(self.snap()["hands"].values())
        self.assertEqual(len(hs), 1)
        self.assertEqual((hs[0]["unit"], hs[0]["role"], hs[0]["parent"], hs[0]["name"]),
                         ("alpha", "hand", "main", "foundry:hand-light"))

    def test_same_model_or_none_is_not_rewritten(self):
        self.start_lead("alpha")
        self.assertNotIn("model", self.check(subagentType="foundry:hand", model="sonnet", tool_use_id="a", session_id="SL"))
        self.assertNotIn("model", self.check(subagentType="foundry:hand", tool_use_id="b", session_id="SL"))
        self.assertNotIn("model", self.check(subagentType="Explore", model="opus", tool_use_id="c", session_id="SL"))

    def test_unit_comes_from_the_pretooluse_record(self):
        self.start_lead("alpha")
        self.spawn("foundry:hand", prompt="UNIT: beta\nwork", **LEAD)
        self.check(subagentType="foundry:hand", tool_use_id="t1", session_id="SL")
        self.assertEqual(self.snap()["hands"]["t1"]["unit"], "beta")

    def test_a_hand_cannot_spawn(self):
        self.start_lead("alpha")
        self.spawn("foundry:hand", **LEAD)
        self.hook("SubagentStart", session_id="SL", agent_id="P1", agent_type="foundry:hand")
        out = self.check(subagentType="Explore", parentAgentId="P1", tool_use_id="t3", session_id="SL")
        self.assertIn("leaf", out["deny"])

    def test_outside_session_is_left_alone(self):
        out = self.check(subagentType="foundry:hand", model="opus", tool_use_id="t4", session_id="OTHER")
        self.assertEqual(out, {"caller": "outside"})
        self.assertEqual(self.snap()["hands"], {})

    def test_bad_json_fails(self):
        rc, out = self.foundry("spawn-check", "--json", "{", ok=(2,))
        self.assertIn("bad --json", out["error"])

    def test_board_lists_hands_with_agent_state(self):
        self.foundry("unit", "new", "--id", "alpha", "--title", "Alpha")
        self.start_lead("alpha")
        self.spawn("foundry:hand", **LEAD)
        self.check(subagentType="foundry:hand", tool_use_id="t1", session_id="SL", name="fixer")
        self.hook("SubagentStart", session_id="SL", agent_id="P1", agent_type="foundry:hand")
        _, m = self.foundry("board", "--format", "json")
        row = next(r for s in m["sections"] for r in s["rows"] if r["id"] == "alpha")
        self.assertEqual([(h["name"], h["role"], h["state"]) for h in row["hands"]], [("fixer", "hand", "running")])


class AgentModel(unittest.TestCase):
    def test_frontmatter_models(self):
        self.assertEqual(hands.agent_model("foundry:hand"), "sonnet")
        self.assertEqual(hands.agent_model("foundry:hand-heavy"), "opus")
        self.assertIsNone(hands.agent_model("Explore"))
        self.assertIsNone(hands.agent_model("foundry:../x"))
        self.assertIsNone(hands.agent_model("foundry:nope"))


if __name__ == "__main__":
    unittest.main()
