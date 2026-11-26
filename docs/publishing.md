# Publishing

foundry is developed in a private repository whose history carries organization data and work email addresses
(`docs/oss-readiness.md`, Scans). The public repository therefore never receives that history: each publication is
one signed commit of the plugin directory's tree, on top of the previous public commit.

- Public repository: `JeongJaeSoon/foundry`. Its root is this plugin directory (`master:foundry` in the private
  repository), so `README.md`, `LICENSE` and `docs/` sit at the top. `.claude-plugin/marketplace.json` beside
  `plugin.json` makes it a single-plugin marketplace named `foundry` (`"source": "./"`):
  `claude plugin marketplace add JeongJaeSoon/foundry`, then `claude plugin install foundry@foundry`.
- License: MIT (`LICENSE`, `license` in `plugin.json`).
- Author and committer of public commits: `JeongJaeSoon <53788601+JeongJaeSoon@users.noreply.github.com>`.
- The private repository's root marketplace (`.claude-plugin/marketplace.json`, named `local-foundry`,
  `"source": "./foundry"`) stays for local installs and is not published. `install.sh` and `uninstall.sh` read the
  name from whichever of the two marketplaces they use, so the private install stays `foundry@local-foundry`.

## Procedure

Run from the private repository's root once `master` has the release to publish. The local branch `publish` holds the
last public commit (fetch it from the public `main` if it is missing).

1. Check the tree: the full suite passes (`python3 -m unittest discover -s tests` and `claude plugin test .` in
   `foundry/`), including `tests/test_oss_denylist.py`, and gitleaks finds nothing in it: `gitleaks dir foundry --no-banner`.
2. Build one signed commit of `master:foundry` whose parent is the previous public commit, so the public history
   continues (`git add` is never involved: untracked files cannot slip in):

   ```sh
   git rev-parse 'master:foundry'                    # prints <tree>
   GIT_AUTHOR_NAME=JeongJaeSoon GIT_AUTHOR_EMAIL=53788601+JeongJaeSoon@users.noreply.github.com \
   GIT_COMMITTER_NAME=JeongJaeSoon GIT_COMMITTER_EMAIL=53788601+JeongJaeSoon@users.noreply.github.com \
     git commit-tree -S <tree> -p publish -m "foundry <version>"   # prints <commit>
   git branch -f publish <commit>
   git log --format='%an <%ae> / %cn <%ce> parents=[%p] %G?' -1 publish   # noreply twice, one parent, G
   gitleaks git . --no-banner --log-opts=publish^..publish                 # only this commit
   ```

3. Push it as the public `main` (the owner's step; nothing in foundry pushes): `git push <public remote> publish:main`.
   It is a fast-forward of the previous public commit.

The design notes (the Korean design record and the overview page) are kept outside the repository and are not
published.
