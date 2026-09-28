# Collector

The collector fetches raw snapshots from every configured source and stores them unchanged. Until the data platform is available, it runs on GitHub Actions and stores onto the [`data` branch](https://github.com/VirtueMe/stavangerparking/tree/data) ([ADR 007](adr/007-collect-outside-the-platform.md)).

## What a run does

The `Collect` workflow ([`.github/workflows/collect.yml`](../.github/workflows/collect.yml)) is scheduled every 5 minutes and can also be started by hand (*Run workflow*). Each run:

1. Checks out the `data` branch. On the very first run, it creates the branch with its own history, containing only [`DATA-LICENCE.md`](../templates/DATA-LICENCE.md), before any snapshot is published.
2. Runs `python -m stavanger_parking.bronze.collect run`, which for every source in [`config/sources.json`](../config/sources.json):
   - decides whether to fetch, from the latest sidecars (adaptive polling, below);
   - resolves the download URL through CKAN and downloads the snapshot;
   - stores the response unchanged at the source's `raw_path` and writes a sidecar next to it.
3. Commits and pushes whatever was stored to `data`.

The decision and its reason are printed for every source, including skips, and appear in the run's summary. A source that fails makes the run fail, which GitHub notifies about; snapshots from the other sources are still kept.

## Adaptive polling

As decided in [ADR 003](adr/003-polling-interval.md):

- **Fast mode:** fetch on every run (every 5 minutes) while values change.
- **Slow mode:** after 5 consecutive snapshots with identical values, fetch every 20 minutes. A run in slow mode fetches once 17.5 minutes have passed since the last snapshot (20 minus half a fast step), because scheduled runs start late.
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
