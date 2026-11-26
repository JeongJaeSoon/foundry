"""0.1.129: fixups. api_review_post reads an empty --method= / -X= as before 0.1.126 (not a POST), and a repo whose
tags gh could not read is kept as unknown for TAGS_UNKNOWN_TTL (tests/test_lifecycle_01125.py)."""

import sys
import unittest

from test_foundry import SCRIPTS

sys.path.insert(0, SCRIPTS)
import gitpolicy  # noqa: E402


class EmptyMethod(unittest.TestCase):
    def test_an_empty_method_is_not_the_allowed_post(self):
        path = "repos/example-org/app/pulls/1/comments"
        self.assertTrue(gitpolicy.api_review_post(["gh", "api", path, "-f", "body=x"]))
        self.assertTrue(gitpolicy.api_review_post(["gh", "api", "--method=POST", path, "-f", "body=x"]))
        for flag in ("--method=", "-X="):
            self.assertFalse(gitpolicy.api_review_post(["gh", "api", flag, path, "-f", "body=x"]), flag)


if __name__ == "__main__":
    unittest.main()
