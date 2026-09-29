"""Deduplication of fetched readings into one row per facility per source reading.

The source is re-published every 2 minutes and fetched every 5 or 20 (ADR 003), and a frozen feed
repeats the same reading for days, so one source reading is usually fetched many times. The key of
a reading is (`facility`, `reading_at`). The first fetch wins: the earliest `ingested_at`, then
`raw_file` and `record_index`, so the winner does not depend on the order files were processed in.

Normally the repeats are identical. A fetch whose values differ from the winner's, because the
source changed a value without advancing its timestamp or listed a facility twice, is a conflict:
each differing value is quarantined as `conflicting_duplicate`, so no value disappears silently.

Both results are derived from all fetches at once, which is what makes incremental runs and a full
rebuild give the same tables.
"""

import polars as pl

from stavanger_parking.silver.parse import (
    LINEAGE_SCHEMA,
    NUMERIC,
    OPEN,
    OPEN_VALUE,
    QUARANTINE_SCHEMA,
)

CONFLICTING_DUPLICATE = "conflicting_duplicate"

KEY = ("facility", "reading_at")
FIRST_FETCH = ("ingested_at", "raw_file", "record_index")

# Values compared between fetches of the same reading, with the source field each comes from
COMPARED = {
    "Latitude": pl.col("latitude").cast(pl.String),
    "Longitude": pl.col("longitude").cast(pl.String),
    "Antall_ledige_plasser": pl.when(pl.col("status") == NUMERIC)
    .then(pl.col("available_spaces").cast(pl.String))
    .when(pl.col("status") == OPEN)
    .then(pl.lit(OPEN_VALUE)),
}


def deduplicate(fetches: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """One reading per key (the first fetch) and the conflicting values of the other fetches."""
    ranked = fetches.sort(*KEY, *FIRST_FETCH).with_columns(
        (pl.int_range(pl.len()).over(KEY) == 0).alias("_first"),
        *(value.alias(f"_{field}") for field, value in COMPARED.items()),
    )
    readings = ranked.filter("_first").select(fetches.columns).sort("raw_file", "record_index")

    winners = ranked.filter("_first").select(
        *KEY, *(pl.col(f"_{field}").alias(f"_winner_{field}") for field in COMPARED)
    )
    others = ranked.filter(~pl.col("_first")).join(winners, on=list(KEY))
    conflicts = [
        others.filter(pl.col(f"_{field}").ne_missing(pl.col(f"_winner_{field}"))).select(
            *LINEAGE_SCHEMA,
            pl.lit(field).alias("field"),
            pl.col(f"_{field}").alias("raw_value"),
            pl.lit(CONFLICTING_DUPLICATE).alias("reason"),
            # The fetch itself is in silver (`silver_parking_fetch`); only its reading lost
            pl.lit(False).alias("reading_excluded"),
        )
        for field in COMPARED
    ]
    quarantine = (
        pl.concat(conflicts)
        .select(pl.col(c).cast(t) for c, t in QUARANTINE_SCHEMA.items())
        .sort("raw_file", "record_index", "field")
    )
    return readings, quarantine
