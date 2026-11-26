"""0.1.116 (owner-approved): inline review comments through `gh api`. A POST to pulls/<n>/comments, or to
pulls/<n>/reviews with event COMMENT or REQUEST_CHANGES (from -f/-F event= or the --input JSON), passes the Bash
guard. A review POST with APPROVE, or with no or an unreadable event, merges and ref writes stay refused."""

import json
import os

import test_gitpolicy as tg

REVIEWS = "gh api -X POST repos/example-org/webapp/pulls/12/reviews"


class ReviewPosts(tg.GitPolicy):
    def body(self, name, data):
        with open(os.path.join(self.wt, name), "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def allowed(self, cmd):
        out = self.pre(cmd)
        self.assertFalse(self.denied(out), out)

    def refused(self, cmd, words):
        self.assertIn(words, self.reason(self.pre(cmd)))

    def test_comment_and_request_changes_reviews_pass(self):
        self.allowed(f"{REVIEWS} -f event=COMMENT -f body=nits")
        self.allowed(f"{REVIEWS} -F event=REQUEST_CHANGES -f body=fix")
        self.allowed("gh api repos/example-org/webapp/pulls/12/reviews -f event=COMMENT -f body='APPROVE later'")
        self.body("review.json", {"event": "COMMENT", "body": "x", "comments": [{"path": "a.py", "line": 3, "body": "y"}]})
        self.allowed(f"{REVIEWS} --input review.json")
        self.body("rc.json", {"event": "REQUEST_CHANGES", "body": "x"})
        self.allowed(f"{REVIEWS} --input=rc.json")

    def test_reading_reviews_passes(self):
        self.allowed("gh api repos/example-org/webapp/pulls/12/reviews")
        self.allowed("gh api repos/example-org/webapp/pulls/12/reviews --paginate --jq '.[].state'")

    def test_inline_review_comments_pass(self):
        self.allowed("gh api -X POST repos/example-org/webapp/pulls/12/comments -f body=x -f path=a.py -F line=3 "
                     "-f commit_id=abc")
        self.body("c.json", {"body": "x", "path": "a.py", "line": 3})
        self.allowed("gh api repos/example-org/webapp/pulls/12/comments --input c.json")

    def test_approvals_and_unknown_events_stay_refused(self):
        gate = "gh pr review 12 -R example-org/webapp --approve"
        self.refused(f"{REVIEWS} -f event=APPROVE", gate)
        self.body("ap.json", {"event": "APPROVE", "body": "lgtm"})
        self.refused(f"{REVIEWS} --input ap.json", gate)
        self.refused(f"{REVIEWS} -f body=x", gate)
        self.body("none.json", {"body": "x"})
        self.refused(f"{REVIEWS} --input none.json", gate)
        self.body("bad.json", "{not json")
        self.refused(f"{REVIEWS} --input bad.json", gate)
        self.refused(f"{REVIEWS} --input missing.json", gate)
        self.refused(f"{REVIEWS} --input -", gate)
        self.refused(f"{REVIEWS} -f event=COMMENT --input ap.json", gate)

    def test_merges_and_ref_writes_stay_refused(self):
        self.refused("gh api -X PUT repos/example-org/webapp/pulls/12/merge", "gh pr merge 12 -R example-org/webapp")
        self.refused("gh api -X PATCH repos/example-org/webapp/git/refs/heads/main -f sha=abc", "no branch moves")
        self.refused("gh api -X POST repos/example-org/webapp/merges -f base=main -f head=x", "no branch moves")
        self.refused("gh api -X POST repos/example-org/webapp/pulls/12/comments --input c.json "
                     "repos/example-org/webapp/git/refs", "no branch moves")
