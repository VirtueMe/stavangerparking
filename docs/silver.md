# Silver: typed readings

Bronze keeps every source field as the string it was delivered as ([`docs/bronze.md`](bronze.md)). Silver gives the readings types and quarantines every value that cannot be parsed.

```sh
uv run python -m stavanger_parking.silver.build --tables-root <tables>
```

`--tables-root` is the same folder or URI as for bronze loading. Each run rebuilds `silver_parking_reading` and `silver_quarantine` from all of bronze, so the result depends on bronze alone. Incremental runs and deduplication of repeated readings come in #9.

## `silver_parking_reading`

One row per bronze row, that is, per facility per fetched snapshot, unless the reading was left out (below).

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
| `reading_excluded` | Whether the reading was left out of `silver_parking_reading` |

| `reason` | Meaning |
|---|---|
| `missing_value` | The field is null, empty or not delivered at all |
| `invalid_date_time` | `Dato` and `Klokkeslett` are not `DD.MM.YYYY` and `HH:MM` |
| `nonexistent_local_time` | The local time was skipped by the spring DST change |
| `not_a_decimal` | A coordinate is not a decimal number |
| `not_a_count` | `Antall_ledige_plasser` is neither a whole number nor `"Open"` |

A reading with a bad value stays in silver with that value null. Only a reading that has lost its identity, with no valid time or no facility, is left out; all of its bad values are still quarantined.
