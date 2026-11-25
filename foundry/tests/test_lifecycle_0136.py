"""0.1.36: the owner's prod go is relayed by the foreman onto the release unit before its prod PR exists."""

import json
import os
import unittest

import test_gitpolicy as tg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ReleaseOrder(tg.GitPolicy):
    CMD = "gh pr merge 8 -R o/infra-ops --squash --match-head-commit abc"

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.dir, "repos.json")) as f:
            repos = json.load(f)
        repos["infra-ops"] = {"path": os.path.join(self.dir, "co"), "slug": "o/infra-ops"}
        with open(os.path.join(self.dir, "repos.json"), "w") as f:
            json.dump(repos, f)
        snap = self.snap()
        snap["units"]["alpha"]["release"] = {"prs": ["example-org/webapp#12"]}
        self.record("snapshot", state=snap)
        for n in (8, 9):
            self.gh_pr(f"o/infra-ops#{n}", files=[{"path": "apps/x/overlays/production/kustomization.yaml"}])

    def order(self, role, *args, ok=(0,)):
        self.env["FOUNDRY_ROLE"] = role
        try:
            return self.foundry("unit", "order", *args, ok=ok)
        finally:
            self.env.pop("FOUNDRY_ROLE")

    def test_unit_wide_order_covers_the_prod_pr_opened_later(self):
        self.assertIn("production paths", self.reason(self.pre(self.CMD)))
        self.order("foreman", "alpha", "--by", "foreman", "--quote", " ", ok=(2,))
        self.order("line-lead", "alpha", "--quote", "prod 배포해", ok=(2,))  # a lead never records it
        self.order("foreman", "alpha", "--by", "foreman", "--quote", "prod 배포해")
        # The order came first; the prod PR is opened afterwards by the unit and then merges through the gate.
        self.assertIn("production paths", self.reason(self.pre(self.CMD)))
        self.record("pr.opened", unit="alpha", repo="o/infra-ops", number=8,
                    url="https://github.com/o/infra-ops/pull/8")
        self.assertIsNone(self.pre(self.CMD))
        # A PR the unit did not open stays the owner's.
        self.assertIn("production paths",
                      self.reason(self.pre("gh pr merge 9 -R o/infra-ops --squash --match-head-commit abc")))

    def test_unit_wide_order_needs_a_release_unit(self):
        self.foundry("unit", "new", "--id", "beta", "--title", "Beta")
        self.order("foreman", "beta", "--by", "foreman", "--quote", "머지해", ok=(2,))

    def test_merge_refusal_names_the_foreman_relay(self):
        self.assertIn("--by foreman --quote", self.reason(self.pre(self.CMD)))


class Text(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(ROOT, rel)) as f:
            return f.read()

    def test_foreman_relays_and_never_hands_merge_lines(self):
        t = self.read("agents/foreman.md")
        for s in ("--by foreman --quote", "There is no `--text` flag", "never run it with `--by owner`",
                  "never hand the owner a `gh pr merge` line or a `/foundry unit order` command"):
            self.assertIn(s, t)

    def test_lead_merges_prod_itself_after_the_order(self):
        t = self.read("agents/line-lead.md")
        self.assertIn("merge the prod PR yourself through the same gate", t)
        self.assertIn("never hand the owner a merge line or a `/foundry` command", t)


class ProdCardGo(ReleaseOrder):
    """0.1.37: the owner's `go` on the release unit's prod card is the order; no PR-number re-recording."""

    def card(self, answer, quote="prod 배포하ㅏ"):
        self.foundry("decide", "open", "--id", "d-0901", "--unit", "alpha", "--key", "alpha:prod-deploy",
                     "--option", "go:prod 배포", "--option", "hold:보류", "--question", "prod?",
                     "--asked-by", "line-lead")
        self.env["FOUNDRY_ROLE"] = "foreman"
        try:
            self.foundry("decide", "answer", "d-0901", answer, "--by", "foreman", "--quote", quote)
        finally:
            self.env.pop("FOUNDRY_ROLE")
        self.record("pr.opened", unit="alpha", repo="o/infra-ops", number=8,
                    url="https://github.com/o/infra-ops/pull/8")

    def test_go_answer_orders_the_units_prod_pr(self):
        self.card("go")
        self.assertIsNone(self.pre(self.CMD))
        self.assertIn("production paths",
                      self.reason(self.pre("gh pr merge 9 -R o/infra-ops --squash --match-head-commit abc")))

    def test_hold_answer_is_no_order(self):
        self.card("hold", quote="보류")
        why = self.reason(self.pre(self.CMD))
        self.assertIn("prod-deploy", why)
        self.assertIn("never hand the owner a merge line", why)


class NoOwnerMergePath(unittest.TestCase):
    def test_no_owner_merge_hand_off_for_prod(self):
        for rel in ("agents/foreman.md", "agents/line-lead.md"):
            with open(os.path.join(ROOT, rel)) as f:
                t = f.read()
            for bad in ("the owner merges it", "relay the merge line", "merge 명령어를 드립니다",
                        "prod deploys and prod merges"):
                self.assertNotIn(bad, t, rel)
        with open(os.path.join(ROOT, "agents/foreman.md")) as f:
            stays = next(line for line in f.read().splitlines() if "These stay the owner's" in line)
        self.assertNotIn("prod merge", stays)
        self.assertIn("prod Argo Sync, prune and rollback", stays)


if __name__ == "__main__":
    unittest.main()
