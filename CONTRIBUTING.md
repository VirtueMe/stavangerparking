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

## Architecture decisions and weaknesses

Significant decisions are recorded as ADRs in [`docs/adr/`](docs/adr/README.md). Trade-offs and known limitations are added to [`docs/weaknesses.md`](docs/weaknesses.md) in the same pull request that introduces them.

## Changelog

`CHANGELOG.md` is generated, never edited by hand. On every push to `main`, the `Changelog` workflow runs [git-cliff](https://git-cliff.org) with `cliff.toml` and commits the result back to `main` as `chore(changelog): update CHANGELOG.md`. The workflow ignores pushes that only change `CHANGELOG.md`, so its own commit does not trigger it again.

Only user-facing types reach the changelog: `feat`, `fix`, `perf`, `docs` and `revert`, plus any breaking change (`!` or a `BREAKING CHANGE:` footer) whatever its type. `chore`, `ci`, `build`, `test`, `style` and `refactor` are left out. Merging one of those produces no changelog change, so no changelog commit follows and other open pull requests do not fall behind `main`.

To let that single workflow push to the protected `main`, it authenticates with a **deploy key** that is on the ruleset's bypass list. The key is stored as `CHANGELOG_DEPLOY_KEY` in the `changelog` environment, which only the `main` branch may use, so workflows on other branches cannot read it. The workflow token itself stays read-only.

Do not write the literal skip-CI marker (`[skip ci]` and its variants) in a PR description: the description becomes the squash commit body, and GitHub then skips every workflow for that commit, including the changelog.

To preview the changelog locally: `git cliff -o CHANGELOG.md`.
