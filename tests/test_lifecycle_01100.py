"""0.1.100: inventory stages. An open PR's stage says its CI first (ci-failing, ci-running: the github stream's
latest-run bucket kept on the PR in the snapshot, no new journal record), and a released PR's stage follows its tag
through deploy.done (deployed-dev; prod-waiting while the unit's prod card is open; deployed once prod has the tag
or a later one), staying in stock until prod."""

import json
import os
import subprocess
import sys

import test_foundry as tf

sys.path.insert(0, tf.SCRIPTS)
import factory  # noqa: E402


class StageOf(tf.Base):
    def test_open_pr_ci_first(self):
        self.assertEqual(factory.stage_of({"state": "OPEN", "ci": "failure", "review": "APPROVED"}), "ci-failing")
        self.assertEqual(factory.stage_of({"state": "OPEN", "ci": "pending"}), "ci-running")
        self.assertEqual(factory.stage_of({"state": "OPEN", "ci": "success", "review": "APPROVED"}), "approved")
        self.assertEqual(factory.stage_of({"state": "OPEN", "ci": "needs_approval"}), "review")
        self.assertEqual(factory.stage_of({"state": "OPEN", "isDraft": True, "ci": "failure"}), "draft")

    def snap_with(self, deploys, card=None):
        units = {"rel": {"id": "rel", "release": {"repo": "o/app", "prs": ["o/app#5"], "released": {}},
                         "deployed": deploys},
                 "maker": {"id": "maker"}}
        cards = {"d-1": {"key": "rel:prod-deploy", "answer": card}} if card is not False else {}
        return {"units": units, "cards": cards}

    def test_deploy_stages_follow_the_tag(self):
        pr = {"state": "MERGED", "released": "app-rel-3", "unit": "maker"}
        ref = "o/app#5"
        self.assertIsNone(factory.deploy_stage(self.snap_with({}), ref, pr))
        dev = {"dev": {"app": "app", "tag": "app-rel-3"}}
        self.assertEqual(factory.deploy_stage(self.snap_with(dev, card=False), ref, pr), "deployed-dev")
        self.assertEqual(factory.deploy_stage(self.snap_with(dev, card=None), ref, pr), "prod-waiting")
        self.assertEqual(factory.deploy_stage(self.snap_with(dev, card="go"), ref, pr), "deployed-dev")
        later = {"dev": {"tag": "app-rel-3"}, "prod": {"tag": "app-rel-4"}}
        self.assertEqual(factory.deploy_stage(self.snap_with(later), ref, pr), "deployed")


class Inventory(tf.Base):
    def inventory(self):
        r = subprocess.run([sys.executable, os.path.join(tf.SCRIPTS, "foundry.py"), "inventory", "--json"],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        rows = out.get("repos") or out
        return {x["ref"]: x for xs in (rows.values() if isinstance(rows, dict) else []) for x in xs
                if isinstance(x, dict) and x.get("kind") == "pr"}

    def test_failing_ci_and_a_dev_release_show_in_stock(self):
        self.foundry("unit", "new", "--id", "u1", "--title", "U1", "--repo", "o/app")
        self.record("pr.state", unit="u1", repo="o/app", number=7, state="OPEN", ci="failure", title="t7")
        self.record("pr.state", unit="u1", repo="o/app", number=5, state="MERGED", title="t5")
        self.record("pr.released", unit="u1", repo="o/app", number=5, tag="v1.2.0")
        self.record("deploy.done", unit="u1", app="app", env="development", tag="v1.2.0")
        inv = self.inventory()
        self.assertEqual(inv["o/app#7"]["stage"], "ci-failing")
        self.assertEqual(inv["o/app#5"]["stage"], "deployed-dev")
        self.record("deploy.done", unit="u1", app="app", env="production", tag="v1.2.0")
        self.assertNotIn("o/app#5", self.inventory())
        self.assertFalse([r for r in self.journal() if r["op"] in ("pr.ci", "ci.state")])
