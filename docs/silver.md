# Silver: typed readings

Bronze keeps every source field as the string it was delivered as ([`docs/bronze.md`](bronze.md)). Silver gives the readings types, collapses repeated fetches of the same reading, quarantines every value that cannot be parsed, and records when the source was stale.

```sh
uv run python -m stavanger_parking.silver.build --tables-root <tables>             # incremental
uv run python -m stavanger_parking.silver.build --tables-root <tables> --rebuild   # from scratch
```

`--tables-root` is the same folder or URI as for bronze loading.

| Table | Grain | Written |
|---|---|---|
| `silver_parking_fetch` | One row per facility per fetch: every parsed bronze row, unless it was left out | Appended |
| `silver_parking_reading` | One row per facility per source reading: the first fetch of each | Derived from the fetches on every run |
| `silver_quarantine` | One row per rejected value | Parse problems inserted; conflicts derived on every run |
| `silver_snapshot_freshness` | One row per fetched snapshot: how old the source's data was | Derived from the fetches on every run |
| `silver_stale_period` | One row per period the source was stale | Derived from the fetches on every run |
| `silver_parking_area` | One row per area in the national parking register per register snapshot | Rebuilt from the register's bronze table on every run |

## Runs and rebuilds

**Incremental (the default).** A run parses only the bronze rows not yet handled, that is, rows that are neither in `silver_parking_fetch` nor left out and recorded in `silver_quarantine`. Their quarantine rows are inserted first, and only if not already there; then their fetches are appended. A run that stops halfway is therefore completed by the next one, without losing or repeating anything, and re-running the same batch changes nothing.

`silver_parking_reading`, the conflicts and the freshness tables are then derived from all fetches, so the tables after any sequence of incremental runs, in any order and with files arriving late, are the same as after a rebuild. The tests check exactly that.

**Rebuild (`--rebuild`).** Replaces all silver tables from all of bronze. Use it after a change to the parsing or deduplication, or if a silver table is lost; bronze, and the raw files behind it, are the source of truth.

## Deduplication

The source is re-published every 2 minutes but fetched every 5 or 20 ([ADR 003](adr/003-polling-interval.md)), and a frozen feed repeats the same reading for days, so one source reading is usually fetched many times. Readings are keyed on **(`facility`, `reading_at`)**, and the **first fetch wins**: the earliest `ingested_at`, then `raw_file` and `record_index`. The winner therefore does not depend on the order the files were processed in, and an earlier fetch that arrives late (for example in a backfill) takes over. How often a reading was fetched stays visible in `silver_parking_fetch`.

Normally the repeats are identical. A fetch whose coordinates or count differ from the winner's, because the source changed a value without advancing its timestamp or listed a facility twice, is a **conflict**: every differing value is quarantined as `conflicting_duplicate`, with the value as parsed.

## `silver_parking_fetch` and `silver_parking_reading`

Both tables have the same columns. A fetch row is one facility in one fetched snapshot; a reading row is the first fetch of a source reading.

| Column | Type | From | Meaning |
|---|---|---|---|
| `source_id`, `raw_file`, `record_index`, `ingested_at` | | bronze | Lineage: the raw file and position the reading came from, and when it was fetched (UTC) |
| `facility` | string | `Sted` | The facility name, unchanged |
| `reading_at` | timestamp (UTC) | `Dato`, `Klokkeslett` | When the source says the reading was taken |
| `reading_date` | date | `Dato` | The reading's date in Oslo time |
| `reading_minute_of_day` | int16 | `Klokkeslett` | Minutes since local midnight (0–1439) |
| `utc_offset_minutes` | int16 | | 60 in winter, 120 in summer |
| `dst_resolved` | boolean | | The local time occurred twice (autumn DST change) and was resolved from `ingested_at` |
| `latitude`, `longitude` | decimal(10, 7) | `Latitude`, `Longitude` | Degrees; more than 7 decimal places (about 1 cm) are rounded |
| `available_spaces` | int32, nullable | `Antall_ledige_plasser` | Free spaces when the source gives a count |
| `status` | string | `Antall_ledige_plasser` | `numeric` for a count, `open` for the source's `"Open"`, `unknown` for anything else |

### Oslo time

The source gives the local time in Oslo to the minute, without a UTC offset. Silver stores the instant in UTC (`reading_at`) and keeps the local date, minute and offset next to it, so gold can build its date and time keys from local time (#11) without a timezone-less timestamp column. Such a column would need the Delta `timestampNtz` feature (reader version 3, writer version 7), which not every reader of the tables supports.

Daylight saving time makes two hours special:

- **Spring (last Sunday of March):** 02:00–02:59 does not exist. A reading with such a time is an error in the data. It is quarantined as `nonexistent_local_time` and left out.
- **Autumn (last Sunday of October):** 02:00–02:59 happens twice, first in summer time, then in winter time. A reading cannot come from the future, so the later candidate is used if it is not after `ingested_at`, otherwise the earlier one. The source's data is normally 2–4 minutes old, so this picks the right one. `dst_resolved` marks these readings.

### `status` and `available_spaces`

`"Open"` is a known value of the source: the facility reports that it is open instead of a count. It gives `status = open` and a null count, and is not quarantined. Any other value that is not a whole number of zero or more, such as `"open"`, `"-3"` or `"12.5"`, gives `status = unknown` and a null count, and is quarantined.

## `silver_quarantine`

One row per value that could not be parsed. Nothing is dropped silently.

| Column | Meaning |
|---|---|
| `source_id`, `raw_file`, `record_index`, `ingested_at` | Lineage, as above |
| `field` | The source field, or `Dato, Klokkeslett` for the timestamp |
| `raw_value` | The value as delivered (null if the field was missing) |
| `reason` | See below |
| `reading_excluded` | Whether the fetch was left out of silver because its identity was lost (no valid time or no facility) |

| `reason` | Meaning |
|---|---|
| `missing_value` | The field is null, empty or not delivered at all |
| `invalid_date_time` | `Dato` and `Klokkeslett` are not `DD.MM.YYYY` and `HH:MM` |
| `nonexistent_local_time` | The local time was skipped by the spring DST change |
| `not_a_decimal` | A coordinate is not a decimal number |
| `not_a_count` | `Antall_ledige_plasser` is neither a whole number nor `"Open"` |
| `conflicting_duplicate` | A later fetch of the same reading has a different value; `raw_value` is the value as parsed (null if it could not be parsed) |

A fetch with a bad value stays in silver with that value null. Only a reading that has lost its identity, with no valid time or no facility, is left out; all of its bad values are still quarantined.

## Source staleness

The source re-publishes its file every 2 minutes whether or not its data changed, and every metadata date moves with the re-upload ([weaknesses](weaknesses.md)). Only the data's own timestamp shows how old the data is, so staleness is judged per fetched snapshot, against `freshness.stale_after_minutes` in the [source configuration](config.md) (15 minutes: the data used to be 2–4 minutes old).

### `silver_snapshot_freshness`

| Column | Meaning |
|---|---|
| `source_id`, `raw_file`, `ingested_at` | The fetched snapshot and when it was fetched |
| `source_reading_at` | The newest source timestamp in the snapshot (UTC) |
| `source_age_minutes` | `ingested_at` minus `source_reading_at`; negative if the source's clock is ahead |
| `is_stale` | The age exceeds `stale_after_minutes` |

A snapshot whose readings were all left out (no valid time or facility) has no fetches, and therefore no row.

### `silver_stale_period`

A period is a run of consecutive stale snapshots, in fetch order, that repeat one source timestamp. It is a run in time: if the source serves an old file again after recovering, that is a new period, and the fresh time in between is not counted as stale.

| Column | Meaning |
|---|---|
| `source_id`, `source_reading_at` | The source and its frozen timestamp |
| `stale_from` | `source_reading_at` plus `stale_after_minutes`: when the data became too old |
| `first_stale_fetch_at`, `last_stale_fetch_at` | The first and last fetch that saw it stale |
| `stale_fetches` | How many fetches saw it stale |
| `ongoing` | The latest fetch still sees the period |

A period is **evidence-based**. It runs from `stale_from` to `last_stale_fetch_at`: a fetch that sees a timestamp as the source's newest proves that nothing newer was published before it, so `stale_from` may lie before our first fetch (the current outage started on 23 September; collection began on 28 September). After the last stale fetch there is no evidence either way, so a gap in collection does not extend a period.

Gold uses these to mark readings as stale and to count `stale_minutes` per hour (#13, #14): the minutes of the hour inside [`stale_from`, `last_stale_fetch_at`].

## `silver_parking_area`

The parking areas of the national parking register ([ADR 005](adr/005-capacity-as-reference-data.md)), typed from bronze, where the register's nested values are JSON text. One row per area per register snapshot; the facility dimension takes capacities from the latest snapshot. The register is small and fetched about once a month, so the table is rebuilt on every run, and rows are parsed one at a time, so that every bad value is quarantined on its own.

| Column | From | Meaning |
|---|---|---|
| `source_id`, `raw_file`, `record_index`, `ingested_at` | bronze | Lineage |
| `register_id` | `id` | The area's id in the register |
| `register_name` | `aktivVersjon.navn` | The register's name for the area |
| `paid_spaces`, `free_spaces`, `charging_spaces`, `accessible_spaces` | `aktivVersjon.antall…` | Spaces; null if the register leaves the count out |
| `changed_at` | `aktivVersjon.sistEndret` | When the register last changed the area |
| `deactivated_at` | `deaktivert.deaktivertTidspunkt` | When the provider deactivated the area; null if active |

Its quarantine rows are in `silver_quarantine` with `source_id = parkeringsregisteret` and are replaced on every run: `missing_value` and `invalid_value` for the id or a timestamp, `invalid_json` for a nested value that is not a JSON object, and `not_a_count` for a count that is not a whole number of zero or more. An area without a valid id is left out.
