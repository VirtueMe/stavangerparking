# Gold: the star schema

Gold holds the dimensions and facts of the star schema described in [`docs/architecture.md`](architecture.md#star-schema).

```sh
uv run python -m stavanger_parking.gold.build --tables-root <tables>
```

`--tables-root` is the same folder or URI as for bronze and silver; silver must be built first. The build writes the date, time and facility dimensions, the availability fact, the hourly fact, and the suggested prices ([`docs/pricing.md`](pricing.md)).

## Date and time dimensions

Both are **generated**, not derived from the data, so every run writes the same tables. Facts key on the source's local Oslo time, which silver keeps next to the UTC instant ([ADR 009](adr/009-silver-model.md)): `date_key` comes from `reading_date`, and `time_key` from `reading_minute_of_day`, which `dim_time` also carries as `minute_of_day`.

### `dim_date`

One row per day from **2020-01-01 to 2035-12-31**. The range is fixed rather than taken from the data, so a rebuild never depends on the data or on today's date; it covers the source's known history (the Wayback Machine has it from 2019) and the years ahead. A reading outside the range would have no date key, which the facts check (#13).

| Column | Meaning |
|---|---|
| `date_key` | `yyyymmdd`, e.g. `20260517` |
| `date` | The date |
| `year`, `quarter`, `month`, `month_name`, `day_of_month` | Calendar parts; names in English |
| `iso_year`, `iso_week` | ISO 8601 week numbering, as used in Norway: 1 January 2021 is in week 53 of 2020 |
| `weekday_number`, `weekday` | 1 (Monday) to 7 (Sunday), and the name |
| `is_weekend` | Saturday or Sunday |
| `is_public_holiday`, `holiday_name` | One of Norway's 13 public holidays, with its official Norwegian name |

The public holidays are the five on fixed dates (1 January, 1 May, 17 May, 25 and 26 December) and the eight that follow Easter: Palmesøndag, Skjærtorsdag, Langfredag, Første and Andre påskedag, Kristi himmelfartsdag, and Første and Andre pinsedag. Easter is computed with the anonymous Gregorian algorithm. When a moving holiday falls on a fixed one, both names are kept: 17 May 2027 and 2032 are `Grunnlovsdag, Andre pinsedag`.

### `dim_time`

One row per minute of the day.

| Column | Meaning |
|---|---|
| `time_key` | `hhmm`, e.g. `1415` |
| `minute_of_day` | 0 to 1439; matches silver's `reading_minute_of_day` |
| `hour`, `minute` | The parts |
| `time` | `HH:MM` |
| `quarter_hour` | The start of the minute's 15-minute bucket, `HH:MM`: 14:00 to 14:14 belong to `14:00` |
| `day_part` | `night` (00–06), `morning` (06–10), `midday` (10–14), `afternoon` (14–18), `evening` (18–24) |

The day parts are a convention for reporting, not a property of the data; change `DAY_PARTS` in `gold/calendar.py` if the analyses need other boundaries.

## `dim_parking_facility`

One row per facility the feed has ever named, plus the unknown member. Maintained with **MERGE**, not rebuilt ([`gold/facility.py`](../src/stavanger_parking/gold/facility.py)).

| Column | Meaning |
|---|---|
| `facility_key` | Surrogate key used by the facts; `-1` is the unknown member, so a fact whose facility cannot be resolved still has a row |
| `facility_name` | The feed's `Sted`, exactly as delivered: the natural key ([ADR 004](adr/004-facility-name-as-natural-key.md)) |
| `latitude`, `longitude` | From the facility's latest fetch that had them |
| `register_id` | The facility's area in the national parking register, from the [facility mapping](config.md#facility-mapping) |
| `capacity` | The area's paid spaces in the latest register snapshot ([ADR 005](adr/005-capacity-as-reference-data.md)); null (unknown) if the facility is not in the mapping, the register has not been collected, or the area is missing or deactivated |
| `capacity_changed_at` | When the register last changed the area |
| `first_seen`, `last_seen` | The first and last fetch that included the facility; fetch time, since the source's own timestamp can be frozen for days |
| `is_active` | The facility is in the latest fetched snapshot |

**How a run changes it:**

- The attributes are recomputed from silver (`silver_parking_fetch`, `silver_parking_area`) and the mapping, and **overwrite** the old ones (type 1).
- A facility **keeps its key** for as long as the dimension exists. A **new facility** needs no work: it is inserted on the next run with the next free key and unknown capacity until it is added to the mapping.
- A facility that **disappears** from the feed becomes inactive (`is_active = false`), and active again with the same key if it returns. A facility that is gone from silver altogether is kept and marked inactive: rows are **never deleted**.
- Facilities first seen in the same run are numbered in the order they were first seen, then by name. Because the keys are kept rather than recomputed, a dimension built from scratch can number facilities differently from one built up run by run; the facts are built from the same dimension, so the model stays consistent.

## `fact_parking_availability`

A periodic snapshot fact: **one row per facility per source reading**, from silver's deduplicated readings ([ADR 009](adr/009-silver-model.md)). A reading fetched many times, as during an outage, is one row; how often it was fetched stays in silver. The fact is rebuilt on every run.

| Column | Meaning |
|---|---|
| `facility_key` | From `dim_parking_facility` by name; `-1` (unknown member) if the name is not there, so no reading is lost |
| `date_key`, `time_key` | The reading's local Oslo date (`yyyymmdd`) and minute (`hhmm`): when the parking situation occurred, not when it was fetched. A reading outside `dim_date`'s range stops the build |
| `valid_from` | The source's timestamp (UTC) |
| `valid_to` | The facility's next reading (UTC); null for the current one |
| `duration_minutes` | `valid_to − valid_from`; null for the current reading |
| `available_spaces`, `status` | As in silver: a count, or null with `status` `open` or `unknown` |
| `occupied_spaces` | The facility's capacity minus its free spaces, when both are known |
| `is_stale` | The source was seen stale while this was its newest reading ([stale periods](silver.md#source-staleness)) |
| `first_ingested_at`, `last_fetched_at` | The first and last fetch that saw the reading |

### Time-weighting

Collection is adaptive, every 5 or 20 minutes ([ADR 003](adr/003-polling-interval.md)), so readings are irregular. Each reading holds **from `valid_from` until `valid_to`**, and any average over time must be **weighted by `duration_minutes`**: a plain average of rows over-weights short readings. Readings of 100 free spaces for 20 minutes and 50 for 5 minutes average 90, not 75.

`valid_to` assumes a value held until the next reading, also across a gap in collection. `last_fetched_at` is where the evidence ends, so coverage (`covered_minutes` in the hourly fact, #14) can be counted from evidence rather than assumption.

### Semi-additive measures

`available_spaces` and `occupied_spaces` are **semi-additive**:

- **Across facilities, at one point in time, they add up:** the free spaces in the city centre right now are the sum over the facilities' current readings.
- **Across time, they do not:** summing a facility's readings over a day counts the same cars many times. Use time-weighted averages, minimum and maximum instead, and only then sum the averages across facilities.

In Power BI, the measures should aggregate over time with a time-weighted average (or `LASTNONBLANK` for "now"), never with `SUM` over the date or time dimension.

### Occupancy

`occupied_spaces` uses the facility's current capacity from the dimension (type 1), for all of history. A **negative** value is shown, not hidden: it means more spaces are free than the register says exist, so the capacity is wrong. It already happens: on 23 September 2026 Forum reported 292 free spaces against the register's 289 (the operator's website says 325), noted in the [facility mapping](config.md#facility-mapping).

## `fact_parking_hourly`

Availability **per facility per hour**, aggregated from `fact_parking_availability` and silver's stale periods ([`gold/hourly.py`](../src/stavanger_parking/gold/hourly.py)). Rebuilt on every run.

| Column | Meaning |
|---|---|
| `facility_key` | As in the availability fact |
| `hour_start` | The start of the hour in UTC: the grain, with `facility_key` |
| `date_key`, `hour` | The hour's local Oslo date and hour, for the date dimension and reports |
| `avg_available_spaces` | Time-weighted average over the counted minutes; null if the hour only has `open` or `unknown` time |
| `min_available_spaces`, `max_available_spaces` | Over the readings with a count in the hour |
| `observation_count` | Readings overlapping the hour |
| `covered_minutes` | How much of the hour the readings cover (0 to 60) |
| `counted_minutes` | The covered minutes with a count, not `open` or `unknown`: the weight of `avg_available_spaces` when hours are combined |
| `stale_minutes` | How much of the covered time rests on stale data |

### Coverage

A reading **covers** the time from its source timestamp until the facility's next reading, but **never more than one slow polling interval (20 minutes, `polling.slow_interval_minutes`) past the last fetch that saw it**: the collector never waits longer than that between fetches, so beyond it, nobody looked. The current reading, which has no next one, covers until its last fetch.

- In normal operation, readings follow each other within a polling interval, and the whole hour is covered.
- A **gap in collection** shows as missing coverage, instead of the last value stretched across it. Low `covered_minutes` means "we don't know", not "nothing changed".
- A reading can cover time before collection began: a fetch that sees a timestamp as the source's newest proves that nothing newer was published in between. The outage of 23 September – 1 October 2026 is covered from 23 September, although collection started on 28 September; that time is all stale.

The average, minimum and maximum are taken over the covered time only.

### Stale minutes

The covered minutes inside a stale period of the source ([stale periods](silver.md#source-staleness)): from the time the reading became older than the threshold until the last fetch that saw it stale. `stale_minutes` close to `covered_minutes` means the hour's numbers describe an old state of the facility, not that hour; reports should exclude or flag such hours.

### Daylight saving time

The grain is the **UTC** hour. At the autumn change, the local hour 02 happens twice, so a day has 25 hourly rows per facility, two of them with the same `date_key` and `hour` but different `hour_start`; in spring, 02 does not exist and the day has 23. Grouping by `date_key` and `hour` in a report merges the two 02 hours of the autumn day, which is what a local-time report expects.
