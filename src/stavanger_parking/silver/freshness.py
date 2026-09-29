"""Source staleness: whether the data we fetched was current when we fetched it.

The source re-publishes its file every 2 minutes whether or not its data changed, and every
metadata date moves with the re-upload (docs/weaknesses.md). Only the data's own timestamp tells
how old the data is, so staleness is judged per fetched snapshot:

- `source_reading_at`: the newest source timestamp in the snapshot
- `source_age_minutes`: how old that timestamp was when the snapshot was fetched (`ingested_at`)
- `is_stale`: the age exceeds the source's `freshness.stale_after_minutes`

A stale period is a run of consecutive stale snapshots, in fetch order, that repeat one source
timestamp. It is a run in time, not a timestamp value: if the source ever serves an old file again,
that is a new period, and the fresh time in between is not counted as stale. A period is
evidence-based: it starts when the timestamp became too old (`stale_from`) and ends at the last
fetch that saw it stale (`last_stale_fetch_at`). Time without fetches is a gap in collection, not
proof that the source was stale, so a period does not extend into it. `ongoing` means the latest
fetch still sees the period.

Both tables are derived from all fetches at once, like the readings, so they are replayable.
"""

from datetime import timedelta

import polars as pl

SNAPSHOT_SCHEMA = {
    "source_id": pl.String,
    "raw_file": pl.String,
    "ingested_at": pl.Datetime("us", "UTC"),
    "source_reading_at": pl.Datetime("us", "UTC"),
    "source_age_minutes": pl.Float64,
    "is_stale": pl.Boolean,
}

PERIOD_SCHEMA = {
    "source_id": pl.String,
    "source_reading_at": pl.Datetime("us", "UTC"),
    "stale_from": pl.Datetime("us", "UTC"),
    "first_stale_fetch_at": pl.Datetime("us", "UTC"),
    "last_stale_fetch_at": pl.Datetime("us", "UTC"),
    "stale_fetches": pl.Int32,
    "ongoing": pl.Boolean,
}


def freshness(fetches: pl.DataFrame, stale_after: timedelta) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Staleness of every fetched snapshot, and the stale periods they form."""
    snapshots = (
        fetches.group_by("source_id", "raw_file", "ingested_at")
        .agg(pl.col("reading_at").max().alias("source_reading_at"))
        .with_columns(
            ((pl.col("ingested_at") - pl.col("source_reading_at")).dt.total_seconds() / 60)
            .cast(pl.Float64)
            .alias("source_age_minutes")
        )
        .with_columns(
            (pl.col("source_age_minutes") > stale_after.total_seconds() / 60).alias("is_stale")
        )
        .select(pl.col(c).cast(t) for c, t in SNAPSHOT_SCHEMA.items())
        .sort("ingested_at", "raw_file")
    )

    previous = pl.col("source_reading_at").shift(1).over("source_id")
    starts_period = pl.col("is_stale") & (
        ~pl.col("is_stale").shift(1, fill_value=False).over("source_id")
        | pl.col("source_reading_at").ne_missing(previous)
    )
    runs = snapshots.sort("source_id", "ingested_at", "raw_file").with_columns(
        starts_period.cast(pl.Int32).cum_sum().over("source_id").alias("_period"),
        (pl.col("ingested_at") == pl.col("ingested_at").max().over("source_id")).alias("_latest"),
    )
    periods = (
        runs.filter("is_stale")
        .group_by("source_id", "_period")
        .agg(
            pl.col("source_reading_at").first(),
            pl.col("ingested_at").min().alias("first_stale_fetch_at"),
            pl.col("ingested_at").max().alias("last_stale_fetch_at"),
            pl.len().alias("stale_fetches"),
            pl.col("_latest").any().alias("ongoing"),
        )
        .with_columns((pl.col("source_reading_at") + stale_after).alias("stale_from"))
        .select(pl.col(c).cast(t) for c, t in PERIOD_SCHEMA.items())
        .sort("source_id", "first_stale_fetch_at")
    )
    return snapshots, periods
