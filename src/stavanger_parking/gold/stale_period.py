"""The stale period fact: the source's stale periods, as the report shows them.

`fact_source_stale_period` has one row per stale period of silver's `silver_stale_period` (the same
freshness logic behind the `source_stale` quality check), shaped for the report:

- `stale_until` is when the period ended, and **blank while it is ongoing**: an incident that has
  not ended has no end yet. The evidence stays in `last_stale_fetch_at`, the last fetch that saw the
  period, ongoing or not. A period is evidence-based (docs/silver.md#source-staleness), so a closed
  period ends at its last stale fetch, the same end `stale_minutes` in the hourly fact uses.
- `duration_minutes` runs from `stale_from` to the period's end, or, while it is ongoing, to the
  last stale fetch: how long the source is known to have been stale so far.
- `date_key` is the local Oslo date the period started, for `dim_date`.

The table describes the mechanism, not a state: it holds whatever periods the fetches show, and an
ongoing one is simply a period without an end.
"""

import polars as pl

OSLO = "Europe/Oslo"

STALE_PERIOD_SCHEMA = {
    "source_id": pl.String,
    "date_key": pl.Int32,
    "source_reading_at": pl.Datetime("us", "UTC"),
    "stale_from": pl.Datetime("us", "UTC"),
    "stale_until": pl.Datetime("us", "UTC"),
    "last_stale_fetch_at": pl.Datetime("us", "UTC"),
    "duration_minutes": pl.Float64,
    "stale_fetches": pl.Int32,
    "ongoing": pl.Boolean,
}


def stale_periods(periods: pl.DataFrame) -> pl.DataFrame:
    """The report's stale periods, from silver's."""
    date = pl.col("stale_from").dt.convert_time_zone(OSLO).dt.date()
    return (
        periods.with_columns(
            pl.when(pl.col("ongoing"))
            .then(None)
            .otherwise(pl.col("last_stale_fetch_at"))
            .alias("stale_until"),
            # Parts of a date are Int8 in Polars: widen before multiplying
            (
                date.dt.year().cast(pl.Int32) * 10000
                + date.dt.month().cast(pl.Int32) * 100
                + date.dt.day().cast(pl.Int32)
            ).alias("date_key"),
            (
                (pl.col("last_stale_fetch_at") - pl.col("stale_from")).dt.total_microseconds()
                / 60e6
            ).alias("duration_minutes"),
        )
        .select(pl.col(c).cast(t) for c, t in STALE_PERIOD_SCHEMA.items())
        .sort("source_id", "stale_from")
    )
