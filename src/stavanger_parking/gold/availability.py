"""The availability fact: one row per facility per source reading.

`fact_parking_availability` is a periodic snapshot fact built from silver's deduplicated readings
(ADR 009). Its grain is one reading of one facility, whatever the number of times it was fetched.

- Keys: `facility_key` from `dim_parking_facility` by name, or the unknown member (-1) if the name
  is not there; `date_key` and `time_key` from the reading's local Oslo date and minute, so reports
  are about when the parking situation occurred, not when it was fetched.
- Validity (ADR 003): collection is adaptive, so readings are irregular. Each reading is valid from
  its source timestamp (`valid_from`) until the facility's next reading (`valid_to`, null for the
  current one), and `duration_minutes` is the time between. Averages over time must be weighted by
  it. `last_fetched_at` is the last fetch that still saw the reading: `valid_to` assumes the value
  held until the next reading, also across gaps in collection; `last_fetched_at` is where the
  evidence ends.
- `is_stale`: the source was seen stale while this reading was its newest (silver's stale periods).
- `occupied_spaces`: the facility's capacity minus its free spaces, when both are known. It is not
  hidden when negative: a negative value means the capacity is wrong, which the quality checks flag.
- `is_source_glitch`: a single reading that does not fit between its neighbours, a failure in the
  source rather than a real change (the source's `glitch` thresholds). The facility's previous and
  next readings agree, and this one is far from both: they are counts at most
  `max_neighbour_difference` apart and this one is a count at least `min_jump` away from both, or
  they are both `open` and this one is a count. The row stays, so nothing is dropped silently; the
  hourly fact leaves it out. A facility's first and current readings have no neighbour on one side
  and are never glitches; the current one is judged when the next reading arrives.

`available_spaces` and `occupied_spaces` are semi-additive: they can be summed across facilities
at one point in time, but not across time; across time, use time-weighted averages, minimum and
maximum.
"""

import polars as pl

from stavanger_parking.config import Glitch
from stavanger_parking.gold.facility import UNKNOWN_KEY
from stavanger_parking.silver.parse import NUMERIC, OPEN

AVAILABILITY_SCHEMA = {
    "facility_key": pl.Int32,
    "date_key": pl.Int32,
    "time_key": pl.Int16,
    "valid_from": pl.Datetime("us", "UTC"),
    "valid_to": pl.Datetime("us", "UTC"),
    "duration_minutes": pl.Float64,
    "available_spaces": pl.Int32,
    "status": pl.String,
    "occupied_spaces": pl.Int32,
    "is_stale": pl.Boolean,
    "is_source_glitch": pl.Boolean,
    "first_ingested_at": pl.Datetime("us", "UTC"),
    "last_fetched_at": pl.Datetime("us", "UTC"),
}


def availability(
    readings: pl.DataFrame,
    fetches: pl.DataFrame,
    facilities: pl.DataFrame,
    stale_periods: pl.DataFrame,
    glitch: Glitch | None,
) -> pl.DataFrame:
    """The fact rows for silver's readings, keyed to the dimensions.

    Without `glitch` thresholds, no reading is a glitch.
    """
    last_fetched = fetches.group_by("facility", "reading_at").agg(
        pl.col("ingested_at").max().alias("last_fetched_at")
    )
    stale = (
        # Staleness belongs to a source's timestamp, not to the timestamp alone
        stale_periods.select("source_id", pl.col("source_reading_at").alias("reading_at"))
        .unique()
        .with_columns(pl.lit(True).alias("is_stale"))
    )
    keys = facilities.select(pl.col("facility_name").alias("facility"), "facility_key", "capacity")
    date = pl.col("reading_date")
    minute = pl.col("reading_minute_of_day").cast(pl.Int16)
    return (
        readings.sort("facility", "reading_at")
        .with_columns(pl.col("reading_at").shift(-1).over("facility").alias("valid_to"))
        .join(last_fetched, on=["facility", "reading_at"], how="left")
        .join(stale, on=["source_id", "reading_at"], how="left")
        .join(keys, on="facility", how="left")
        .with_columns(
            pl.col("facility_key").fill_null(UNKNOWN_KEY),
            # Parts of a date are Int8 in Polars: widen before multiplying
            (
                date.dt.year().cast(pl.Int32) * 10000
                + date.dt.month().cast(pl.Int32) * 100
                + date.dt.day().cast(pl.Int32)
            ).alias("date_key"),
            (minute // 60 * 100 + minute % 60).alias("time_key"),
            pl.col("reading_at").alias("valid_from"),
            ((pl.col("valid_to") - pl.col("reading_at")).dt.total_seconds() / 60).alias(
                "duration_minutes"
            ),
            (pl.col("capacity") - pl.col("available_spaces")).alias("occupied_spaces"),
            pl.col("is_stale").fill_null(False),
            _is_glitch(glitch).alias("is_source_glitch"),
            pl.col("ingested_at").alias("first_ingested_at"),
        )
        .select(pl.col(c).cast(t) for c, t in AVAILABILITY_SCHEMA.items())
        .sort("facility_key", "valid_from")
    )


def _is_glitch(glitch: Glitch | None) -> pl.Expr:
    """Whether a reading is a glitch, judged against the facility's previous and next readings."""
    if glitch is None:
        return pl.lit(False)
    spaces, status = pl.col("available_spaces"), pl.col("status")

    def neighbour(column: pl.Expr, n: int) -> pl.Expr:
        return column.shift(n).over("facility", order_by="reading_at")

    # A missing neighbour or count compares as null: never a glitch on its own
    before, after = neighbour(spaces, 1), neighbour(spaces, -1)
    jump = (
        ((before - after).abs() <= glitch.max_neighbour_difference)
        & ((spaces - before).abs() >= glitch.min_jump)
        & ((spaces - after).abs() >= glitch.min_jump)
    )
    # 01.10.2026 22:40: Kyrre and Posten, always "Open", reported 0 in a single snapshot
    count_between_open = (
        (status == NUMERIC) & (neighbour(status, 1) == OPEN) & (neighbour(status, -1) == OPEN)
    )
    return (jump | count_between_open).fill_null(False)
