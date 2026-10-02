# Contributing

## Local development

The project uses [uv](https://docs.astral.sh/uv/). The Python version is pinned in `.python-version` to match the notebook runtime on the data platform, and dependencies are locked in `uv.lock`.

```sh
uv run pytest               # install (first run) and run the tests
uv run ruff check           # lint
uv run ruff format          # format
uv add <package>            # add a dependency (updates pyproject.toml and uv.lock)
```

Transformation logic lives in `src/stavanger_parking/` as plain Python modules with tests in `tests/`. Notebooks stay thin and import from the package.

## Branches and worktrees

Every issue is worked on in its own branch, checked out as a git worktree under `.worktrees/` (ignored by git). The main checkout stays on `main`.

```sh
git worktree add .worktrees/{type}-{issue}-{slug} -b {type}/{issue}_{slug} main
```

## Pull requests and commit messages

- All changes reach `main` through a pull request. `main` is protected by a ruleset and must be valid at all times.
- Pull requests are **squash merged**, and the PR title becomes the commit message on `main`.
- PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/), for example `feat(silver): typed parsing of snapshots`. The `PR title` check blocks the merge otherwise.
- The `Lint and test` check (ruff and pytest) must pass, and the branch must be up to date with `main` before merging, so that what was tested is what lands on `main`.
- Link the issue with a closing keyword in the PR description (`Closes #N`), so the issue moves on the project board and closes on merge.
- A change to silver's parsing or deduplication applies only to bronze rows parsed after it. Its PR ends the test plan with the step that brings the platforms' history along: after the merge, `tools/deploy --prod`, then `tools/rebuild --prod` ([`docs/databricks.md`](docs/databricks.md#rebuilding-silver)).

## Architecture decisions and weaknesses

Significant decisions are recorded as ADRs in [`docs/adr/`](docs/adr/README.md). Trade-offs and known limitations are added to [`docs/weaknesses.md`](docs/weaknesses.md) in the same pull request that introduces them.

## Releases and changelog

Versions are created automatically as work is merged, independent of the milestones. On every push to `main`, the `Release` workflow asks [git-cliff](https://git-cliff.org) (configured in `cliff.toml`) for the next version based on the changelog-relevant commits since the last tag:

| Merged since the last tag | Next version |
|---|---|
| `feat` | minor (0.1.0 → 0.2.0) |
| `fix`, `perf`, `docs`, `revert` | patch (0.1.0 → 0.1.1) |
| Breaking change (`!` or a `BREAKING CHANGE:` footer), any type | minor below 1.0.0, major from 1.0.0 |
| Only `chore`, `ci`, `build`, `test`, `style`, `refactor` | no release |

For a release, the workflow writes `CHANGELOG.md` (one dated section per version, newest first), sets the package version in `pyproject.toml` and `uv.lock`, commits all three as `chore(release): vX.Y.Z`, tags that commit `vX.Y.Z`, pushes commit and tag atomically, and creates a GitHub Release with that version's section as notes. The workflow skips its own `chore(release):` commits. Merges that do not produce a release leave `main` untouched, so other open pull requests do not fall behind.

`CHANGELOG.md`, the tags and the package version are generated; never edit or create them by hand.

To let that single workflow push to the protected `main`, it authenticates with a **deploy key** that is on the ruleset's bypass list. The key is stored as `CHANGELOG_DEPLOY_KEY` in the `changelog` environment, which only the `main` branch may use, so workflows on other branches cannot read it. The job's token has `contents: write` only to create the GitHub Release; it is not on the bypass list and cannot push to `main`.

Do not write the literal skip-CI marker (`[skip ci]` and its variants) in a PR description: the description becomes the squash commit body, and GitHub then skips every workflow for that commit, including the release.

git-cliff is pinned in `uv.lock` (dependency group `release`), so local previews match the workflow: `uv run --only-group release git-cliff --bumped-version` for the next version, `uv run --only-group release git-cliff --bump --unreleased` for its notes.
