"""The hourly fact: availability per facility per hour, from the availability fact.

`fact_parking_hourly` aggregates the readings of `fact_parking_availability` (ADR 003: readings are
irregular, so every measure is weighted by time):

- Each reading **covers** the time from its source timestamp until the facility's next reading, but
  never more than one slow polling interval past the last fetch that saw it: beyond that, nobody
  looked. The current reading, with no next one, covers until its last fetch. Normal operation
  therefore covers the whole hour, and a gap in collection shows as missing coverage instead of the
  last value stretched across it.
- A reading's covered time is split across the hours it overlaps. Per facility and hour:
  `covered_minutes`; `counted_minutes`, the covered minutes with a count (`open` and `unknown` time
  is covered but has none); the time-weighted `avg_available_spaces` over the counted minutes;
  `min_available_spaces` and `max_available_spaces` of those readings; `observation_count`, the
  readings overlapping the hour; and `stale_minutes`, the covered minutes inside a stale period.

The grain is one row per facility per **UTC hour** (`hour_start`), with the local Oslo `date_key`
and `hour` as attributes: at the autumn DST change the local hour 02 happens twice, and keying on
it would give two hours the same key. That day has 25 rows per facility, the spring one 23.
"""

from datetime import timedelta

import polars as pl

OSLO = "Europe/Oslo"
HOUR = timedelta(hours=1)

HOURLY_SCHEMA = {
    "facility_key": pl.Int32,
    "date_key": pl.Int32,
    "hour": pl.Int8,
    "hour_start": pl.Datetime("us", "UTC"),
    "avg_available_spaces": pl.Float64,
    "min_available_spaces": pl.Int32,
    "max_available_spaces": pl.Int32,
    "observation_count": pl.Int32,
    "covered_minutes": pl.Float64,
    "counted_minutes": pl.Float64,
    "stale_minutes": pl.Float64,
}


def _minutes(start: pl.Expr, end: pl.Expr) -> pl.Expr:
    """Minutes from start to end; 0 when the interval is empty."""
    return pl.max_horizontal((end - start).dt.total_microseconds() / 60e6, pl.lit(0.0))


def hourly(
    availability: pl.DataFrame, stale_periods: pl.DataFrame, max_gap: timedelta
) -> pl.DataFrame:
    """Hourly rows from the availability fact and the source's stale periods.

    `max_gap` is how long past its last fetch a reading may still count as covering: the slow
    polling interval, since that is the longest the collector waits between fetches.
    """
    stale = stale_periods.select(
        pl.col("source_reading_at").alias("valid_from"), "stale_from", "last_stale_fetch_at"
    )
    covered_to = (
        pl.when(pl.col("valid_to").is_null())
        .then(pl.col("last_fetched_at"))
        .otherwise(pl.min_horizontal("valid_to", pl.col("last_fetched_at") + max_gap))
    )
    intervals = (
        availability.with_columns(covered_to.alias("covered_to"))
        .filter(pl.col("covered_to") > pl.col("valid_from"))
        .join(stale, on="valid_from", how="left")
        .with_columns(
            pl.datetime_ranges(
                pl.col("valid_from").dt.truncate("1h"),
                pl.col("covered_to") - timedelta(microseconds=1),
                interval="1h",
            ).alias("hour_start")
        )
        # Every range has at least one hour: intervals that cover nothing were filtered out above
        .explode("hour_start", empty_as_null=False)
    )
    hour_end = pl.col("hour_start") + HOUR
    start = pl.max_horizontal("valid_from", "hour_start")
    end = pl.min_horizontal("covered_to", hour_end)
    segments = intervals.with_columns(
        _minutes(start, end).alias("minutes"),
        # min/max_horizontal skip nulls, so a reading without a stale period must be caught first
        pl.when(pl.col("stale_from").is_null())
        .then(0.0)
        .otherwise(
            _minutes(
                pl.max_horizontal(start, "stale_from"),
                pl.min_horizontal(end, "last_stale_fetch_at"),
            )
        )
        .alias("stale"),
    )
    counted = pl.col("available_spaces").is_not_null()
    local = pl.col("hour_start").dt.convert_time_zone(OSLO)
    return (
        segments.group_by("facility_key", "hour_start")
        .agg(
            # No counted minutes (only `open` or `unknown`): no average, rather than 0 / 0 = NaN
            pl.when(pl.col("minutes").filter(counted).sum() > 0)
            .then(
                (pl.col("available_spaces") * pl.col("minutes")).filter(counted).sum()
                / pl.col("minutes").filter(counted).sum()
            )
            .alias("avg_available_spaces"),
            pl.col("available_spaces").min().alias("min_available_spaces"),
            pl.col("available_spaces").max().alias("max_available_spaces"),
            pl.len().alias("observation_count"),
            pl.col("minutes").sum().alias("covered_minutes"),
            # The weight of the average: covered minutes with a count, so hours combine correctly
            pl.col("minutes").filter(counted).sum().alias("counted_minutes"),
            pl.col("stale").sum().alias("stale_minutes"),
        )
        .with_columns(
            # Parts of a date are Int8 in Polars: widen before multiplying
            (
                local.dt.year().cast(pl.Int32) * 10000
                + local.dt.month().cast(pl.Int32) * 100
                + local.dt.day().cast(pl.Int32)
            ).alias("date_key"),
            local.dt.hour().alias("hour"),
        )
        .select(pl.col(c).cast(t) for c, t in HOURLY_SCHEMA.items())
        .sort("facility_key", "hour_start")
    )
