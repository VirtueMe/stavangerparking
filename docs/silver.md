# Silver: typed readings

Bronze keeps every source field as the string it was delivered as ([`docs/bronze.md`](bronze.md)). Silver gives the readings types, collapses repeated fetches of the same reading, and quarantines every value that cannot be parsed.

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

## Runs and rebuilds

**Incremental (the default).** A run parses only the bronze rows not yet handled, that is, rows that are neither in `silver_parking_fetch` nor left out and recorded in `silver_quarantine`. Their quarantine rows are inserted first, and only if not already there; then their fetches are appended. A run that stops halfway is therefore completed by the next one, without losing or repeating anything, and re-running the same batch changes nothing.

`silver_parking_reading` and the conflicts are then derived from all fetches, so the tables after any sequence of incremental runs, in any order and with files arriving late, are the same as after a rebuild. The tests check exactly that.

**Rebuild (`--rebuild`).** Replaces all three tables from all of bronze. Use it after a change to the parsing or deduplication, or if a silver table is lost; bronze, and the raw files behind it, are the source of truth.

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
