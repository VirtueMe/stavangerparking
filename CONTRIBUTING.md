# Contributing

## Pull requests and commit messages

- All changes reach `main` through a pull request. `main` is protected by a ruleset.
- Pull requests are **squash merged**, and the PR title becomes the commit message on `main`.
- PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/), for example `feat(silver): typed parsing of snapshots`. The `PR title` check blocks the merge otherwise.

## Changelog

`CHANGELOG.md` is generated, never edited by hand. On every push to `main`, the `Changelog` workflow runs [git-cliff](https://git-cliff.org) with `cliff.toml` and commits the result back to `main` as `chore(changelog): update CHANGELOG.md`. The workflow ignores pushes that only change `CHANGELOG.md`, so its own commit does not trigger it again.

To let that single workflow push to the protected `main`, it authenticates with a **deploy key** that is on the ruleset's bypass list. The key is stored as `CHANGELOG_DEPLOY_KEY` in the `changelog` environment, which only the `main` branch may use, so workflows on other branches cannot read it. The workflow token itself stays read-only.

Do not write the literal skip-CI marker (`[skip ci]` and its variants) in a PR description: the description becomes the squash commit body, and GitHub then skips every workflow for that commit, including the changelog.

To preview the changelog locally: `git cliff -o CHANGELOG.md`.
