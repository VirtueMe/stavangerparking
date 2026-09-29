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

`available_spaces` and `occupied_spaces` are semi-additive: they can be summed across facilities
at one point in time, but not across time; across time, use time-weighted averages, minimum and
maximum.
"""

import polars as pl

from stavanger_parking.gold.facility import UNKNOWN_KEY

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
    "first_ingested_at": pl.Datetime("us", "UTC"),
    "last_fetched_at": pl.Datetime("us", "UTC"),
}


def availability(
    readings: pl.DataFrame,
    fetches: pl.DataFrame,
    facilities: pl.DataFrame,
    stale_periods: pl.DataFrame,
) -> pl.DataFrame:
    """The fact rows for silver's readings, keyed to the dimensions."""
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
            pl.col("ingested_at").alias("first_ingested_at"),
        )
        .select(pl.col(c).cast(t) for c, t in AVAILABILITY_SCHEMA.items())
        .sort("facility_key", "valid_from")
    )
