# Contributing

## Pull requests and commit messages

- All changes reach `main` through a pull request. `main` is protected by a ruleset.
- Pull requests are **squash merged**, and the PR title becomes the commit message on `main`.
- PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/), for example `feat(silver): typed parsing of snapshots`. The `PR title` check blocks the merge otherwise.

## Changelog

`CHANGELOG.md` is generated, never edited by hand. On every push to `main`, the `Changelog` workflow runs [git-cliff](https://git-cliff.org) with `cliff.toml` and commits the result back to `main` as `chore(changelog): update CHANGELOG.md [skip ci]`.

To let that single workflow push to the protected `main`, it authenticates with a **deploy key** (secret `CHANGELOG_DEPLOY_KEY`) that is on the ruleset's bypass list. A deploy key is scoped to this repository only and needs no personal token. `[skip ci]` stops the changelog commit from triggering the workflow again.

To preview the changelog locally: `git cliff -o CHANGELOG.md`.
