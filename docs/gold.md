# Gold: the star schema

Gold holds the dimensions and facts of the star schema described in [`docs/architecture.md`](architecture.md#star-schema-draft).

```sh
uv run python -m stavanger_parking.gold.build --tables-root <tables>
```

`--tables-root` is the same folder or URI as for bronze and silver; silver must be built first. So far the build writes the date, time and facility dimensions; the facts follow (#13, #14).

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
