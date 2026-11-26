"""0.1.124: the release watcher's candidates, the release batcher's pending PRs and the daily report's release gaps
all ask factory.pr_release_state, so none of them can call a PR 릴리스 대기 that the board does not."""

import datetime as dt
import sys

from test_foundry import SCRIPTS, Base

sys.path.insert(0, SCRIPTS)
import release  # noqa: E402
import report  # noqa: E402
import watchers  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 27, 15, 0, tzinfo=KST)
P = {"release": {"none": ["*ops"], "tag": "v*", "watch_days": 14,
                 "batch_repos": [{"repo": "*app", "procedure": "x", "dev_app": "app"}]},
     "timezone": "Asia/Seoul", "report": {"exclude_repos": []}}


def pr(n, repo="example-org/app", days_ago=1, **kw):
    return {"repo": repo, "number": n, "state": "MERGED", "merged": (NOW - dt.timedelta(days=days_ago)).isoformat(), **kw}


class OneReleaseState(Base):
    CREW = False

    def test_watcher_batcher_and_report_agree(self):
        snap = {"units": {
            "u1": {"id": "u1", "kind": "project", "status": "done",
                   "prs": [pr(1), pr(2, repo="example-org/cluster-ops"), pr(3, days_ago=20), pr(4, released="v1.0.0"),
                           pr(5)]},
            "rel": {"id": "rel", "kind": "project", "status": "done",
                    "release": {"repo": "example-org/app", "released": {"example-org/app#5": "v1.0.1"}}, "prs": []}}}
        self.assertEqual(sorted(watchers._merged_refs(snap, NOW, P)), ["example-org/app#1"])
        self.assertEqual([x["ref"] for x in release.pending(snap, P, NOW, rs={}).get("example-org/app", [])],
                         ["example-org/app#1"])
        gaps = report.release_gaps(snap, [], P, NOW, untracked=False)
        self.assertEqual([g["ref"] for g in gaps if g["gap"] == "unreleased"], ["example-org/app#1"])
