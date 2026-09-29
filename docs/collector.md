# Collector

The collector fetches raw snapshots from every configured source and stores them unchanged. Until the data platform is available, it runs on GitHub Actions and stores onto the [`data` branch](https://github.com/VirtueMe/stavangerparking/tree/data) ([ADR 007](adr/007-collect-outside-the-platform.md)).

## What a run does

The `Collect` workflow ([`.github/workflows/collect.yml`](../.github/workflows/collect.yml)) is started every 5 minutes by cron-job.org ([below](#trigger)) and can also be started by hand (*Run workflow*). Each run:

1. Checks out the `data` branch. On the very first run, it creates the branch with its own history, containing only [`DATA-LICENCE.md`](../templates/DATA-LICENCE.md), before any snapshot is published.
2. Runs `python -m stavanger_parking.bronze.collect run`, which for every source with `polling` in [`config/sources.json`](../config/sources.json) (the register is collected separately, [below](#parkeringsregisteret)):
   - decides whether to fetch, from the latest sidecars (adaptive polling, below);
   - resolves the download URL through CKAN and downloads the snapshot;
   - stores the response unchanged at the source's `raw_path` and writes a sidecar next to it.
3. Commits and pushes whatever was stored to `data`.

The decision and its reason are printed for every source, including skips, and appear in the run's summary. A source that fails makes the run fail, which GitHub notifies about; snapshots from the other sources are still kept.

## Trigger

GitHub's own schedule barely ran the workflow, so [cron-job.org](https://cron-job.org) starts it instead ([ADR 008](adr/008-trigger-collection-externally.md)). The workflow has no `schedule` trigger; cron-job.org is the only automatic trigger.

The cron-job.org job, owned by the repository owner:

| Setting | Value |
|---|---|
| Schedule | Every hour, every day, at minutes 2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52 and 57 |
| URL | `https://api.github.com/repos/VirtueMe/stavangerparking/actions/workflows/collect.yml/dispatches` |
| Request method | `POST` |
| Headers | `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28` |
| Request body | `{"ref":"main"}` |
| HTTP authentication | None; the token goes in the `Authorization` header |
| Notifications | E-mail when a call fails |

A successful call returns **HTTP 204**, and a run with the event `workflow_dispatch` appears under *Actions*.

The token is a fine-grained personal access token with access to this repository only and the single permission *Actions: read and write*. It is stored only in cron-job.org, never in the repository.

### Pausing collection

Turn the job off in cron-job.org, and back on to resume. Runs can still be started by hand in the meantime. The gap shows up in `collect gaps`.

### Rotating the token

The token has an expiry date; the current one expires on **2026-12-28**. GitHub e-mails before it expires. To rotate it:

1. On GitHub, *Settings → Developer settings → Fine-grained tokens*, regenerate the token (this keeps its settings) or create a new one with the same scope.
2. In cron-job.org, replace the token in the `Authorization` header and use *Test run*: it should return 204.
3. If a new token was created, delete the old one.

### Troubleshooting

| Response | Cause |
|---|---|
| 401 | The token is not accepted: expired, revoked, or the header is malformed. The value must be exactly `Bearer <token>`, with one space and no colon after `Bearer`, and nothing set under HTTP authentication |
| 403 or 404 | The token is valid but lacks *Actions: read and write* on this repository |
| 422 | The workflow is disabled, has no `workflow_dispatch` trigger, or the `ref` does not exist |

cron-job.org may disable a job after repeated failures. After fixing the cause, check that the job is enabled again.

## Adaptive polling

As decided in [ADR 003](adr/003-polling-interval.md):

- **Fast mode:** fetch on every run (every 5 minutes) while values change.
- **Slow mode:** after 5 consecutive snapshots with identical values, fetch every 20 minutes. A run in slow mode fetches once 17.5 minutes have passed since the last snapshot (20 minus half a fast step), because runs can start late.
- **Back to fast** as soon as a fetched snapshot's values differ.

Values are compared through a fingerprint of every record with the fields in `polling.change_ignores_fields` left out (`Dato` and `Klokkeslett`, which advance even when nothing else changes). Both a quiet night and a frozen feed therefore slow down. All parameters are in the [source configuration](config.md).

## Files

```
data branch
├── DATA-LICENCE.md
└── bronze/parking/2026/09/28/
    ├── 142500.json        # the response, byte for byte
    └── 142500.meta.json   # the sidecar
```

The sidecar records:

| Field | Meaning |
|---|---|
| `source_id` | The source from the config |
| `ingested_at` | When the snapshot was fetched (UTC) |
| `source_url`, `resource_id` | The CKAN resource the snapshot came from |
| `run_id` | GitHub run id and attempt |
| `content_hash` | SHA-256 of the raw file |
| `values_fingerprint` | SHA-256 of the values without the ignored fields; `null` if the payload is not a JSON list of objects |
| `polling_mode`, `next_due` | The mode after this snapshot, and when the next fetch is due |

Files are never modified or deleted: the collector refuses to overwrite an existing file, and a ruleset on the `data` branch blocks force pushes and deletion of the branch.

## Gaps

Because every sidecar records when the next fetch was due, gaps can be listed from the data alone, long after the Actions logs have expired. An intended 20-minute interval in slow mode is not a gap; a missing run is.

```sh
git worktree add ../stavangerparking-data data
uv run python -m stavanger_parking.bronze.collect gaps --storage ../stavangerparking-data
```

`--tolerance-minutes` (default 10) sets how late a snapshot may be before it counts as a gap.

## Parkeringsregisteret

Facility capacities come from the national parking register ([ADR 005](adr/005-capacity-as-reference-data.md)), which is collected as a second source (`parkeringsregisteret` in the [source configuration](config.md)). It has a fixed URL, `http.url`: Stavanger Parkering's areas, by organisation number, with all fields. It has no `polling`, so the 5-minute Collect run skips it; it is fetched whenever it is collected by name, and its sidecars have no fingerprint or next due time.

The `Collect register` workflow ([`.github/workflows/collect-register.yml`](../.github/workflows/collect-register.yml)) collects it onto the `data` branch under `bronze/parkeringsregisteret/`. It shares Collect's concurrency group, so the two never push at the same time, and it keeps [`DATA-LICENCE.md`](../templates/DATA-LICENCE.md) on the branch in step with the template, which credits both publishers.

Capacities change rarely, so the intended trigger is a **monthly** cron-job.org job, set up like the [Collect job](#trigger) with its own schedule (for example the 1st of every month at 03:02) and the URL `…/actions/workflows/collect-register.yml/dispatches`; the same token works. During the case the job is not set up, since capacities are not expected to change before the meeting, and the workflow is started by hand (*Run workflow*).

Locally:

```sh
uv run python -m stavanger_parking.bronze.collect run --storage <data> --source parkeringsregisteret --run-id local
```
