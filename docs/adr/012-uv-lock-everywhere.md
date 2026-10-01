# ADR 012: uv.lock is the one source of dependency versions on every platform

- **Status:** Accepted
- **Date:** 2026-10-01
- **Issue:** #101

## Context

The package's dependencies are declared with minimum versions only (`polars>=1.44`, `deltalake>=1.6`, `httpx>=0.28.1`), as a library's should be, and `uv.lock` pins the exact versions of everything they pull in. CI tests with `uv.lock`: every pull request runs against those versions and no others.

The platforms did not. Databricks' job environment installed the wheel, and Fabric's notebook ran `%pip install` on it, so pip resolved the dependencies again on every run and took the newest versions the minimums allowed. A run could get a Polars or deltalake that no test had seen, on a day nobody changed anything, and two platforms could run the same release with different versions. Microsoft's Fabric guidance warns against exactly this: unpinned `%pip install` "can produce inconsistent results from run to run". Rolling back to an older release made it worse: its wheel came back, but with today's dependencies.

## Decision

**`uv.lock` decides which versions run, everywhere.** At deploy time, [`tools/requirements.sh`](../../tools/requirements.sh) exports it with `uv export --frozen --no-dev --no-emit-project`: every runtime dependency at its locked version, without the dev, release and Power BI groups, and without the package itself.

- **The lock of the version deployed.** For dev it is the checkout's `uv.lock`. For prod it is **the release tag's own**, taken from the tag (`git archive <tag> pyproject.toml uv.lock`), so a prod deploy of v0.20.0 gets v0.20.0's versions, and a revert gets the old versions with the old wheel.
- **Then the wheel, adding nothing.** The requirements are installed first and the wheel after them, so the wheel's own minimums are already met and nothing is resolved anew.
- **Per platform:**
  - **Fabric** installs in two steps, `%pip install -r requirements.txt` and then `%pip install --no-deps <wheel>`, so the requirements keep **every file's hash** and pip checks them.
  - **Databricks** lists both in the job environment (`-r ${workspace.file_path}/dist/requirements.txt`, then the wheel), which pip installs in one run. A hashed requirements file would make pip demand a hash for the wheel too, so Databricks gets the same pinned versions **without hashes**.
- **An upgrade is a pull request:** `uv lock --upgrade` (or `--upgrade-package polars`), CI tests the new versions, and a release takes them to the platforms. Nothing changes on a platform that has not been deployed.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Exact pins in `pyproject.toml` (`polars==1.44.2`) | The wheel would carry them, but only for direct dependencies; their dependencies would still float. And `pyproject.toml` and `uv.lock` would be two lists of versions to keep in step |
| A Fabric Environment item with the wheel as a custom library | Microsoft's recommendation for pipelines, but Fabric-only, published inside Fabric (3–6 minutes) and resolved there; it would not pin Databricks, and it cannot be tried without a capacity |
| Attach `requirements.txt` to each GitHub Release in the release workflow | Pins prod from the release itself, but only for releases made after the change: reverting to an older release would find no file. Exporting from the tag's `uv.lock` works for every release |
| Leave it to pip | The situation this decision ends |

## Consequences

- What CI tests is what runs, on both platforms and in every release, including a revert.
- Deploying needs `uv` and a git checkout that can fetch the release tag, which every deploy already needed.
- Databricks' requirements are not hash-checked, because of how its environment installs; the versions are still pinned. If Databricks gains a two-step install, it can take the hashes too.
- A security fix in a dependency reaches the platforms only through `uv lock --upgrade`, a pull request and a release, not by itself on the next run. That is deliberate, and the reason to keep releases small and frequent.
- Revisit if the platforms install from a lock file natively (for example `uv` in the job environment).
