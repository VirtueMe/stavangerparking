# Source configuration

Every source is described in [`config/sources.json`](../config/sources.json). The collector and the ingestion code read it through `stavanger_parking.config.load_sources`; nothing source-specific is hardcoded.

## Fields

| Field | Meaning |
|---|---|
| `id` | Stable name of the source, used in code and logs. Lowercase letters, digits and underscores; unique |
| `ckan.base_url` | Base URL of the CKAN portal (https) |
| `ckan.package_id` | CKAN package (dataset) id or name |
| `ckan.format` | Resource format to download. The package must have exactly one resource in this format |
| `raw_path` | Where raw snapshots are written, relative to the storage root. Must contain `{yyyy}`, `{mm}`, `{dd}` and `{HHmmss}` (UTC) once each and in that order, so that sorting paths sorts snapshots by time |
| `bronze_table` | Name of the bronze table, without catalog or schema |
| `licence.id`, `licence.url`, `licence.publisher` | Licence of the data and who publishes it, used for attribution |
| `polling.fast_interval_minutes` | Fetch interval while values change; must match the collector's trigger in cron-job.org ([ADR 003](adr/003-polling-interval.md), [ADR 008](adr/008-trigger-collection-externally.md)) |
| `polling.slow_interval_minutes` | Fetch interval once values have stopped changing; not shorter than the fast interval |
| `polling.unchanged_snapshots_for_slow` | How many consecutive snapshots with identical values switch to the slow interval |
| `polling.change_ignores_fields` | Fields left out when comparing snapshots, such as the data timestamp that advances regardless of the values |

All fields are required. Unknown fields are rejected, so a misspelt field fails instead of being silently ignored. The config is validated on load, and every problem is reported at once, naming the source and field.

**Relative on purpose:** the storage root (a local folder, the Lakehouse `Files/` area or a Unity Catalog volume) and the catalog or schema of the tables belong to the runtime environment, not to the source. The same config works locally and on the platform.

## Adding a source

Add an entry to `sources` with a new `id`, its CKAN location, its own `raw_path` and `bronze_table`, and its licence. No code changes are needed. `uv run pytest` validates the repository config as part of the test suite, so an invalid entry fails in the pull request.

## Getting the config to the platform runtime

Files in this repository are not automatically available to notebooks on the data platform. The loader takes a file path, so any of these routes works. The choice is made together with the platform (see #17):

| Route | How | Trade-off |
|---|---|---|
| Ship it inside the package | Move the file into `src/stavanger_parking/` as package data and install the package (wheel) on the platform | Code and config are versioned and deployed as one unit; changing the config means a new package version |
| Fabric environment resource | Upload `sources.json` to the environment's resources; notebooks read it from the resources folder | Simple, but a manual upload unless automated |
| Deploy step to `Files/config/` | A CI job copies the file to the Lakehouse `Files/config/` after merge | Config can change without a new package version; needs a deploy credential |
| Databricks Asset Bundle | The bundle deploys the repository files, including `config/`, to the workspace | Code and config deploy together from `main` |

Until the platform is chosen, the collector running outside the platform reads the file directly from the repository checkout.
