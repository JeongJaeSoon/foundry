"""The mod's owner nonce: a board button or `/foundry` inside the foreman acts as the owner, once; tools cannot."""

import os
import sys
import time
import uuid

from test_foundry import SCRIPTS
from test_review_fable_b import Review

sys.path.insert(0, SCRIPTS)

import foundry  # noqa: E402
import foundrylib as cl  # noqa: E402

FOREMAN = {"FOUNDRY_ROLE": "foreman"}


class OwnerNonce(Review):
    def setUp(self):
        super().setUp()
        self.foundry("unit", "new", "--id", "a", "--title", "A")
        self.foundry("halt", "--reason", "x")

    def mint(self, age=0):
        n = uuid.uuid4().hex
        d = os.path.join(self.dir, cl.OWNER_NONCE_DIR)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, n)
        with open(p, "w") as f:
            f.write(n)
        if age:
            t = time.time() - age
            os.utime(p, (t, t))
        return n, p

    def as_foreman(self, *args, nonce=None):
        env = dict(FOREMAN, **({cl.OWNER_NONCE_ENV: nonce} if nonce else {}))
        return self.run_f(*args, env=env)

    def test_nonce_lets_the_foreman_resume_for_the_owner_once(self):
        n, p = self.mint()
        rc, out, err = self.as_foreman("resume-line", nonce=n)
        self.assertEqual(rc, 0, err + out)
        self.assertFalse(os.path.exists(p))
        self.foundry("halt", "--reason", "y")
        rc, out, err = self.as_foreman("resume-line", nonce=n)  # reused
        self.assertNotEqual(rc, 0, out)
        self.assertIn("refused", err + out)

    def test_old_missing_or_malformed_nonce_is_refused(self):
        old, p = self.mint(age=cl.OWNER_NONCE_TTL + 5)
        for n in (old, uuid.uuid4().hex, "../../x", "ABC"):
            rc, out, err = self.as_foreman("resume-line", nonce=n)
            self.assertNotEqual(rc, 0, n)
            self.assertIn("refused", err + out)
        self.assertFalse(os.path.exists(p))  # the stale file is swept

    def test_nonce_passes_by_owner_check_on_owner_verbs(self):
        n, _ = self.mint()
        rc, out, err = self.as_foreman("unit", "set", "a", "--status", "parked", nonce=n)
        self.assertEqual(rc, 0, err + out)
        self.assertEqual(self.snap()["units"]["a"]["status"], "parked")

    def test_with_owner_by_adds_by_only_where_the_verb_takes_it(self):
        old = cl._OWNER
        try:
            cl._OWNER = True
            self.assertEqual(foundry.with_owner_by(["resume-line"]), ["resume-line", "--by", "owner"])
            self.assertEqual(foundry.with_owner_by(["up"]), ["up"])
            self.assertEqual(foundry.with_owner_by(["board"]), ["board"])
            self.assertEqual(foundry.with_owner_by(["resume-line", "--by", "foreman"]), ["resume-line", "--by", "foreman"])
            cl._OWNER = False
            self.assertEqual(foundry.with_owner_by(["resume-line"]), ["resume-line"])
        finally:
            cl._OWNER = old

    def test_hook_refuses_tool_calls_that_name_the_nonce(self):
        cmds = [
            ("Bash", {"command": f"{cl.OWNER_NONCE_ENV}=abc foundry resume-line"}),
            ("Bash", {"command": f"touch ~/.local/state/foundry/{cl.OWNER_NONCE_DIR}/abc"}),
            ("Write", {"file_path": f"/x/{cl.OWNER_NONCE_DIR}/abc", "content": "abc"}),
            ("Edit", {"file_path": "/x/y.sh", "old_string": "a", "new_string": cl.OWNER_NONCE_ENV}),
        ]
        for tool, ti in cmds:
            out = self.hook("PreToolUse", tool_name=tool, tool_use_id="t1", tool_input=ti, agent_type="foundry:foreman")
            self.assertTrue(self.denied(out), (tool, ti))
            self.assertIn("cannot act as the owner", out["hookSpecificOutput"]["permissionDecisionReason"])
        out = self.hook("PreToolUse", tool_name="Bash", tool_use_id="t1", tool_input={"command": "foundry board"},
                        agent_type="foundry:foreman")
        self.assertFalse(self.denied(out))
