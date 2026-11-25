# Publishing

foundry is developed in a private repository whose history carries organization data and work email addresses
(`docs/oss-readiness.md`, Scans). The public repository therefore never receives that history: each publication is a
single parentless commit of the current tree.

- Public repository: `JeongJaeSoon/foundry` (the marketplace root: `.claude-plugin/marketplace.json` and `foundry/`).
- License: MIT (`foundry/LICENSE`, `license` in `plugin.json`).
- Commit author for public commits: `JeongJaeSoon <53788601+JeongJaeSoon@users.noreply.github.com>`.

## Procedure

Run from the private repository's root once `master` has the release to publish.

1. Check the tree: the full suite passes (`python3 -m unittest discover -s tests` and `claude plugin test .` in
   `foundry/`), including `tests/test_oss_denylist.py`, and gitleaks finds nothing in the tree:
   `gitleaks dir . --no-banner`.
2. Build the single commit from `master`'s tree, with no parent and the public author as both author and
   committer (`git switch --orphan` would start from an empty tree, and `git add -A` would pick up untracked files):

   ```sh
   git rev-parse 'master^{tree}'                     # prints <tree>
   GIT_AUTHOR_NAME=JeongJaeSoon GIT_AUTHOR_EMAIL=53788601+JeongJaeSoon@users.noreply.github.com \
   GIT_COMMITTER_NAME=JeongJaeSoon GIT_COMMITTER_EMAIL=53788601+JeongJaeSoon@users.noreply.github.com \
     git commit-tree <tree> -m "foundry <version>"   # prints <commit>
   git branch publish <commit>
   git log --format='%an <%ae> / %cn <%ce> parents=[%p]' -1 publish   # the noreply address twice, no parent
   gitleaks git . --no-banner --log-opts=publish    # only this commit
   ```

3. Push that branch alone as the public `main` (the owner's step; nothing in foundry pushes it):
   `git push <public remote> publish:main`. A later publication repeats step 2 (a new parentless commit) and pushes it
   the same way (a force push of `main`), or, to keep a public history, commits the new tree on top of the public
   `main` with the same author settings.
4. Delete the local `publish` branch.

The design notes (Korean design record and the overview page) are kept outside the repository and are not
published.
