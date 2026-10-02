"""Typed parsing of parking readings: bronze rows (every field a string) into silver.

One bronze row is one facility's reading in one fetched snapshot. Parsing gives it types:

- `Dato` + `Klokkeslett` are Oslo local time to the minute. They become `reading_at` (UTC), with
  the local `reading_date`, `reading_minute_of_day` and `utc_offset_minutes` stored alongside, so
  local time needs no timezone-less timestamp column.
- At the autumn DST change, 02:00–02:59 happens twice. A reading cannot come from the future, so
  the later candidate is used if it is not after `ingested_at`, otherwise the earlier one;
  `dst_resolved` marks these rows. At the spring change, 02:00–02:59 does not exist, and such a
  reading is quarantined.
- `Latitude` and `Longitude` become decimals with 7 places (about 1 cm; more places are rounded).
- `Antall_ledige_plasser` becomes `available_spaces` (a nullable integer) and `status`: `numeric`
  for a count, `open` for the source's `"Open"`, `unknown` for anything else. The source's
  `"Fullt"` (full) is a count: 0 free spaces, `numeric`.

Every value that cannot be parsed becomes a quarantine row with the field, the raw value and the
reason; nothing is dropped silently. A reading stays in silver with the bad value as null, unless
its identity is lost (no valid time or no facility): then it is left out, and its quarantine rows
say so (`reading_excluded`).
"""

import polars as pl

OSLO = "Europe/Oslo"
COORDINATE = pl.Decimal(10, 7)
OPEN_VALUE = "Open"
FULL_VALUE = "Fullt"

NUMERIC = "numeric"
OPEN = "open"
UNKNOWN = "unknown"

MISSING_VALUE = "missing_value"
INVALID_DATE_TIME = "invalid_date_time"
NONEXISTENT_LOCAL_TIME = "nonexistent_local_time"
NOT_A_DECIMAL = "not_a_decimal"
NOT_A_COUNT = "not_a_count"

SOURCE_FIELDS = ("Dato", "Klokkeslett", "Sted", "Latitude", "Longitude", "Antall_ledige_plasser")
DATE_TIME_FIELD = "Dato, Klokkeslett"

LINEAGE_SCHEMA = {
    "source_id": pl.String,
    "raw_file": pl.String,
    "record_index": pl.Int32,
    "ingested_at": pl.Datetime("us", "UTC"),
}

READING_SCHEMA = {
    **LINEAGE_SCHEMA,
    "facility": pl.String,
    "reading_at": pl.Datetime("us", "UTC"),
    "reading_date": pl.Date,
    "reading_minute_of_day": pl.Int16,
    "utc_offset_minutes": pl.Int16,
    "dst_resolved": pl.Boolean,
    "latitude": COORDINATE,
    "longitude": COORDINATE,
    "available_spaces": pl.Int32,
    "status": pl.String,
}

QUARANTINE_SCHEMA = {
    **LINEAGE_SCHEMA,
    "field": pl.String,
    "raw_value": pl.String,
    "reason": pl.String,
    "reading_excluded": pl.Boolean,
}


def _missing(field: str) -> pl.Expr:
    return pl.col(field).is_null() | (pl.col(field) == "")


def parse_readings(bronze: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Typed readings and quarantined values from bronze rows of the parking source."""
    # A field the source never delivered is missing in every row, not an error in the code
    frame = bronze.with_columns(
        (pl.col(f) if f in bronze.columns else pl.lit(None)).cast(pl.String).alias(f)
        for f in SOURCE_FIELDS
    )

    local = pl.concat_str("Dato", pl.lit(" "), "Klokkeslett").str.strptime(
        pl.Datetime("us"), "%d.%m.%Y %H:%M", strict=False
    )

    def utc(ambiguous: str) -> pl.Expr:
        oslo = local.dt.replace_time_zone(OSLO, ambiguous=ambiguous, non_existent="null")
        return oslo.dt.convert_time_zone("UTC")

    frame = frame.with_columns(
        local.alias("_local"), utc("earliest").alias("_earliest"), utc("latest").alias("_latest")
    )
    reading_at = (
        pl.when(pl.col("_latest") <= pl.col("ingested_at"))
        .then(pl.col("_latest"))
        .otherwise(pl.col("_earliest"))
    )
    spaces = (
        pl.when(pl.col("Antall_ledige_plasser").str.contains(r"^[0-9]+$"))
        .then(pl.col("Antall_ledige_plasser").cast(pl.Int32, strict=False))
        .when(pl.col("Antall_ledige_plasser") == FULL_VALUE)
        .then(pl.lit(0, pl.Int32))
        .otherwise(None)
    )
    frame = frame.with_columns(
        reading_at.alias("reading_at"),
        spaces.alias("available_spaces"),
        pl.col("Latitude").cast(COORDINATE, strict=False).alias("latitude"),
        pl.col("Longitude").cast(COORDINATE, strict=False).alias("longitude"),
    ).with_columns(
        (pl.col("reading_at").is_null() | _missing("Sted")).alias("_excluded"),
        # The one place that decides what a count value means; the quarantine reads it
        pl.when(pl.col("available_spaces").is_not_null())
        .then(pl.lit(NUMERIC))
        .when(pl.col("Antall_ledige_plasser") == OPEN_VALUE)
        .then(pl.lit(OPEN))
        .otherwise(pl.lit(UNKNOWN))
        .alias("status"),
    )

    readings = (
        frame.filter(~pl.col("_excluded"))
        .with_columns(
            pl.col("Sted").alias("facility"),
            pl.col("_local").dt.date().alias("reading_date"),
            (pl.col("_local").dt.hour().cast(pl.Int16) * 60 + pl.col("_local").dt.minute())
            .cast(pl.Int16)
            .alias("reading_minute_of_day"),
            (pl.col("_local") - pl.col("reading_at").dt.replace_time_zone(None))
            .dt.total_minutes()
            .cast(pl.Int16)
            .alias("utc_offset_minutes"),
            (pl.col("_earliest") != pl.col("_latest")).alias("dst_resolved"),
        )
        .select(pl.col(c).cast(t) for c, t in READING_SCHEMA.items())
        .sort("raw_file", "record_index")
    )
    return readings, _quarantine(frame)


def _quarantine(frame: pl.DataFrame) -> pl.DataFrame:
    """One row per value that could not be parsed."""
    date_time = pl.concat_str("Dato", "Klokkeslett", separator=" ", ignore_nulls=True)
    checks = [
        (
            DATE_TIME_FIELD,
            date_time,
            pl.when(_missing("Dato") | _missing("Klokkeslett"))
            .then(pl.lit(MISSING_VALUE))
            .when(pl.col("_local").is_null())
            .then(pl.lit(INVALID_DATE_TIME))
            .when(pl.col("_earliest").is_null())
            .then(pl.lit(NONEXISTENT_LOCAL_TIME)),
        ),
        ("Sted", pl.col("Sted"), pl.when(_missing("Sted")).then(pl.lit(MISSING_VALUE))),
        *(
            (
                field,
                pl.col(field),
                pl.when(_missing(field))
                .then(pl.lit(MISSING_VALUE))
                .when(pl.col(column).is_null())
                .then(pl.lit(NOT_A_DECIMAL)),
            )
            for field, column in (("Latitude", "latitude"), ("Longitude", "longitude"))
        ),
        (
            "Antall_ledige_plasser",
            pl.col("Antall_ledige_plasser"),
            pl.when(_missing("Antall_ledige_plasser"))
            .then(pl.lit(MISSING_VALUE))
            .when(pl.col("status") == UNKNOWN)
            .then(pl.lit(NOT_A_COUNT)),
        ),
    ]
    rows = [
        frame.select(
            *LINEAGE_SCHEMA,
            pl.lit(field).alias("field"),
            raw.alias("raw_value"),
            reason.alias("reason"),
            pl.col("_excluded").alias("reading_excluded"),
        ).filter(pl.col("reason").is_not_null())
        for field, raw, reason in checks
    ]
    return (
        pl.concat(rows)
        .select(pl.col(c).cast(t) for c, t in QUARANTINE_SCHEMA.items())
        .sort("raw_file", "record_index", "field")
    )
