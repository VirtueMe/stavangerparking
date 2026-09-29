# ADR 007: Collect raw snapshots outside the data platform while access is pending

- **Status:** Accepted
- **Date:** 2026-09-28
- **Issue:** #5

## Context

The case is to be built on Microsoft Fabric, but a Fabric trial cannot currently be activated, and the platform choice (Fabric or Databricks) is pending an answer about Fabric access. The source only exposes its current state (ADR 003), so every day without collection is history that cannot be recovered.

## Decision

Start collecting now, independently of the platform:

- A scheduled **GitHub Actions workflow** (#6) resolves the resource through CKAN (#4), downloads it and stores the response **unchanged** on a dedicated `data` branch, using the same relative layout the platform will use (`raw_path` in `config/sources.json`), with a metadata sidecar per file.
- The **raw files are the source of truth.** Bronze, silver and gold are derived from them and can be rebuilt at any time.
- When platform access is available, the raw files are copied to the platform's storage and bronze is rebuilt from them (#17). Platform ingestion (#18) then takes over, and both run in parallel until they produce matching snapshots.
- The transformations are built and tested locally on the same stack the platform will run (ADR 001), so they move to the platform without rewriting.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Wait for platform access | Every day of waiting is history lost for good |
| A cron job on a personal machine | No 5-minute guarantee when the machine sleeps, no visible run history, and not reproducible by others |
| A cloud function (Azure Functions, AWS Lambda) writing to object storage | More reliable scheduling, but new infrastructure, credentials and cost for a temporary measure |

## Consequences

- Because bronze is append-only and replayable, a later backfill gives the same bronze, silver and gold as if the platform had collected from the start. The one difference is `ingested_at`, which records when the GitHub runner fetched the file, and that is exactly what it should record.
- GitHub Actions scheduling is best effort: runs can be delayed or dropped under load, the minimum interval is 5 minutes, and scheduled workflows are disabled after 60 days without repository activity. Gaps are visible from the sidecars and are recorded in [`docs/weaknesses.md`](../weaknesses.md).
- The raw data becomes public on the `data` branch, which NLOD 2.0 allows with attribution (#32).
- Revisit when platform access arrives: the collector is retired once platform ingestion is verified (#18).

## Addendum 2026-09-28: scheduling in practice

This does not change the decision; it records evidence that sharpens its main consequence.

- After the collector was merged (#6, 2026-09-28 16:32 UTC), a manual run created the `data` branch and stored the first snapshot, but **no scheduled run had started within the first hour** (checked 17:33 UTC), although every documented requirement was met: the workflow is on the default branch and active, the cron is valid, Actions is enabled, and the repository is not a fork.
- GitHub describes scheduled workflows as best effort. Runs *"may be delayed or potentially dropped"* under load, new or inactive repositories may see delayed scheduling, and GitHub staff note that *"any commit pushed to the default branch will resync the impacted scheduled workflows"* ([community discussion #185355](https://github.com/orgs/community/discussions/185355)). Typical delays are 3–10 minutes, but can exceed an hour, and a run may be skipped for a whole day.
- If scheduling stays unreliable, the options are staggered minutes (`2-59/5`) with a commit to `main`, or an external scheduler (for example cron-job.org or a Cloudflare Workers cron trigger) calling the workflow's `workflow_dispatch` with a token limited to this repository. The latter would be a new, small dependency and would be recorded here or in a new ADR.

## Addendum 2026-09-29: external trigger

GitHub's schedule started 2 of about 145 expected runs in the first 12 hours. [ADR 008](008-trigger-collection-externally.md) replaces the workflow's `schedule` trigger with cron-job.org calling `workflow_dispatch`. The rest of this decision is unchanged.
