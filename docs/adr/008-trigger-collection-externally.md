# ADR 008: Trigger collection externally from cron-job.org, not from GitHub's schedule

- **Status:** Accepted
- **Date:** 2026-09-29
- **Issue:** #49

## Context

ADR 007 runs the collector on GitHub Actions and starts it from the workflow's own `schedule` trigger, every 5 minutes. In practice, GitHub's scheduler barely ran it. In the 12 hours after #6 was merged (2026-09-28 16:32 → 2026-09-29 04:48 UTC), the `*/5` schedule should have started about 145 runs; it started **2** (22:15 and 02:03 UTC), although every documented requirement was met. GitHub describes scheduled workflows as best effort: runs *"may be delayed or potentially dropped"* under load, and new or inactive repositories may see delayed scheduling ([community discussion #185355](https://github.com/orgs/community/discussions/185355)).

The source only exposes its current state (ADR 003), so every missed run is history lost for good. A run started through the `workflow_dispatch` API, by contrast, starts within seconds.

The collector decides per run whether to fetch (ADR 003), but in fast mode it fetches on every run. Two triggers a few minutes apart therefore store two snapshots a few minutes apart.

## Decision

- **[cron-job.org](https://cron-job.org) starts the `Collect` workflow every 5 minutes** (minutes 2, 7, 12, …) with `POST /repos/VirtueMe/stavangerparking/actions/workflows/collect.yml/dispatches` and the body `{"ref":"main"}`. GitHub answers HTTP 204. cron-job.org e-mails on failure.
- **The workflow's `schedule` trigger is removed.** cron-job.org is the only trigger, so no two triggers compete: a late GitHub run can no longer land minutes after an external one and store an extra fast-mode snapshot.
- **Authentication uses a fine-grained personal access token** limited to this repository with only *Actions: read and write* (GitHub adds *Metadata: read-only* to every token). It has an expiry date (the first expires 2026-12-28), is stored only in cron-job.org, and is never committed.
- Runs can still be started by hand (*Run workflow*), and adaptive polling is unchanged: each run still decides for itself whether to fetch.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Keep GitHub's schedule only, perhaps on staggered minutes (`2-59/5`) | 2 of about 145 runs in 12 hours; staggering avoids the busy top of the hour but gives no guarantee |
| cron-job.org plus GitHub's schedule as a fallback | The fallback almost never fires, and when it does, it stores an extra snapshot minutes after an external run (fast mode fetches on every run). Avoiding that would need a minimum spacing in the polling decision for a trigger that barely works |
| Cloudflare Workers cron trigger | Just as reliable, but code and a Cloudflare account to maintain, for a temporary measure |
| A cron job on a machine of our own | No guarantee while the machine sleeps or is offline, and not visible to others (ADR 007) |
| A cloud function writing to object storage | New infrastructure, credentials and cost for a temporary measure (ADR 007) |

## Consequences

- Runs start on time, so the run history on the `data` branch follows the intended 5- and 20-minute intervals.
- **cron-job.org becomes a dependency, with no fallback.** If it stops, or the token expires or is revoked, no runs start. cron-job.org e-mails when a call fails, and gaps can be listed from the sidecars (`collect gaps`). Runs can be started by hand in the meantime.
- **A token is stored at a third party.** If it leaks, it can only act on GitHub Actions in this repository: start, re-run, cancel and delete workflow runs and their logs, and disable or enable workflows. It could therefore stop collection or trigger extra runs, but it cannot change code, push to `data` or `main`, or read secrets. The snapshots themselves are on the `data` branch and do not depend on run logs. It is revoked on GitHub, and a new one is added to cron-job.org ([rotation](../collector.md#rotating-the-token)).
- The token expires. Before then, it must be rotated, or collection stops.
- Without a `schedule` trigger, the rule that GitHub disables scheduled workflows after 60 days without repository activity no longer applies.
- Revisit when collection moves to the platform (#18): cron-job.org and the token are retired together with the workflow.
