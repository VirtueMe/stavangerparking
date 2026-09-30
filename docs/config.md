# Source configuration

Every source is described in [`src/stavanger_parking/config/sources.json`](../src/stavanger_parking/config/sources.json). The collector and the ingestion code read it through `stavanger_parking.config.load_sources`; nothing source-specific is hardcoded.

## Fields

| Field | Meaning |
|---|---|
| `id` | Stable name of the source, used in code and logs. Lowercase letters, digits and underscores; unique |
| `ckan` or `http` | Where the data comes from: exactly one of the two |
| `ckan.base_url` | Base URL of the CKAN portal (https) |
| `ckan.package_id` | CKAN package (dataset) id or name |
| `ckan.format` | Resource format to download. The package must have exactly one resource in this format |
| `http.url` | A fixed download URL (https), for a source that is not published through CKAN, such as the national parking register |
| `raw_path` | Where raw snapshots are written, relative to the storage root. Must contain `{yyyy}`, `{mm}`, `{dd}` and `{HHmmss}` (UTC) once each and in that order, so that sorting paths sorts snapshots by time |
| `bronze_table` | Name of the bronze table, without catalog or schema |
| `licence.id`, `licence.url`, `licence.publisher` | Licence of the data and who publishes it, used for attribution |
| `polling.fast_interval_minutes` | Fetch interval while values change. The Collect run starts every 5 minutes ([ADR 008](adr/008-trigger-collection-externally.md)), so 5 means every run; a longer interval, such as the register's 60, means every run once it has passed ([ADR 003](adr/003-polling-interval.md), [ADR 010](adr/010-collect-the-register-hourly.md)) |
| `polling.slow_interval_minutes` | Fetch interval once values have stopped changing; not shorter than the fast interval |
| `polling.unchanged_snapshots_for_slow` | How many consecutive snapshots with identical values switch to the slow interval |
| `polling.change_ignores_fields` | Fields left out when comparing snapshots, such as the data timestamp that advances regardless of the values |
| `filter.field`, `filter.mapping_field` | Optional: store only the records whose `field` matches a `mapping_field` (such as `register_id`) in the [facility mapping](#facility-mapping); the stored file is then a filtered copy of the response ([ADR 010](adr/010-collect-the-register-hourly.md)) |
| `freshness.stale_after_minutes` | How old the source's newest timestamp may be when fetched before the snapshot counts as stale ([silver](silver.md#source-staleness)) |

`polling`, `freshness` and `filter` are optional; every other field is required. A source with `polling` is collected by the scheduled collector; a source without it is collected only when named (`collect run --source <id>`), such as reference data fetched once a month. `freshness` only applies to sources whose data carries its own timestamp. Unknown fields are rejected, so a misspelt field fails instead of being silently ignored. The config is validated on load, and every problem is reported at once, naming the source and field.

**Relative on purpose:** the storage root (a local folder, the Lakehouse `Files/` area or a Unity Catalog volume) and the catalog or schema of the tables belong to the runtime environment, not to the source. The same config works locally and on the platform.

## Adding a source

Add an entry to `sources` with a new `id`, its CKAN location or URL, its own `raw_path` and `bronze_table`, and its licence; add `polling` if it should be collected on the schedule. No code changes are needed. `uv run pytest` validates the repository config as part of the test suite, so an invalid entry fails in the pull request.

## Getting the config to the platform runtime

The configuration ships **inside the package**: `sources.json`, `facility_mapping.json`, `tariffs.json` and `pricing_rules.json` sit in `src/stavanger_parking/config/` and are part of the wheel (#70). Code and configuration are versioned and deployed as one unit, on both platforms ([ADR 011](adr/011-one-repository-two-platforms.md)). Every loader and command defaults to the packaged files, wherever it runs: from the repository, from the collector's checkout, or from a wheel installed on a platform. A path argument (`--config`, `--mapping`, `--tariffs`, `--rules`) still overrides them, for trying out a change without a release.

Changing the configuration therefore means a new package version: the change is merged like code, the release workflow builds the wheel and attaches it to the GitHub release, and the platform installs that version.

Other routes were considered: uploading the files as a Fabric environment resource (a manual step), copying them to the Lakehouse `Files/config/` in CI (a deploy credential, and config versioned apart from the code), and deploying them with a Databricks Asset Bundle (Databricks only). Each would give the two platforms different ways to find the same file.

## Facility mapping

[`src/stavanger_parking/config/facility_mapping.json`](../src/stavanger_parking/config/facility_mapping.json) links each facility in the parking feed to its parking area in the national parking register, where its capacity comes from ([ADR 005](adr/005-capacity-as-reference-data.md)). It is maintained by hand, because the names differ between the feed, the register and the operator's website ([ADR 004](adr/004-facility-name-as-natural-key.md)). It is read through `stavanger_parking.facilities.load_facility_mapping` and validated on load, like the source configuration.

| Field | Meaning |
|---|---|
| `facility` | The facility's name in the feed (`Sted`), exactly as delivered: the natural key. Unique |
| `register_id` | The parking area's id in Parkeringsregisteret. Unique |
| `register_name`, `operator_name` | The names the register and the operator's website use, for reference |
| `note` | Optional: anything a reader should know, such as sources that disagree about the capacity |

When a facility is added to the feed or renamed, add or change its entry; until then its capacity is unknown. The tests check that the mapping covers every facility seen in the feed and that every `register_id` is an active area with the recorded name.

## Tariffs and pricing rules

[`src/stavanger_parking/config/tariffs.json`](../src/stavanger_parking/config/tariffs.json) (the operator's prices per facility) and [`src/stavanger_parking/config/pricing_rules.json`](../src/stavanger_parking/config/pricing_rules.json) (occupancy bands, rush hours, limits) drive the suggested prices. Both are validated on load; see [`docs/pricing.md`](pricing.md).
