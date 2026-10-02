"""The facility dimension: one row per facility the feed has ever named.

The natural key is the feed's name, `facility_name` (ADR 004); facts use the surrogate
`facility_key`. The attributes are recomputed from silver on every run and overwrite the old ones
(type 1):

- `latitude`, `longitude`: from the facility's latest fetch that had them
- `register_id`, `capacity`, `capacity_changed_at`: the register area the mapping links the name to
  (ADR 005), from the latest register snapshot: its paid spaces, plus the spaces the mapping says
  are reserved for others (`reserved_spaces`, which the register leaves out and the feed counts),
  and when the register last changed the area. Unknown (null) when the facility is not in the
  mapping, the register has not been collected, the area is missing from the snapshot or has no
  paid count, or the provider has deactivated it
- `first_seen`, `last_seen`: the first and last fetch that included the facility
- `is_active`: the facility is in the latest fetched snapshot

The keys are not recomputed: a facility keeps its key for as long as the dimension exists, and a
new facility gets the next free key. That is why the dimension is maintained with MERGE rather than
rebuilt (`gold.build`). The unknown member, `facility_key = -1`, is always present, so a fact whose
facility cannot be resolved still has a row to point at.
"""

import polars as pl

from stavanger_parking.facilities import FacilityMapping
from stavanger_parking.silver.parse import COORDINATE

UNKNOWN_KEY = -1
UNKNOWN_NAME = "(unknown)"

FACILITY_SCHEMA = {
    "facility_key": pl.Int32,
    "facility_name": pl.String,
    "latitude": COORDINATE,
    "longitude": COORDINATE,
    "register_id": pl.Int64,
    "capacity": pl.Int32,
    "capacity_changed_at": pl.Datetime("us", "UTC"),
    "first_seen": pl.Datetime("us", "UTC"),
    "last_seen": pl.Datetime("us", "UTC"),
    "is_active": pl.Boolean,
}
ATTRIBUTES = [c for c in FACILITY_SCHEMA if c != "facility_key"]


def facility_attributes(
    fetches: pl.DataFrame, areas: pl.DataFrame, mapping: tuple[FacilityMapping, ...]
) -> pl.DataFrame:
    """The attributes of every facility in the fetches, without keys."""
    ordered = fetches.sort("ingested_at", "raw_file", "record_index")
    latest_snapshot = ordered["raw_file"][-1] if ordered.height else None
    seen = ordered.group_by("facility").agg(
        pl.col("latitude").drop_nulls().last(),
        pl.col("longitude").drop_nulls().last(),
        pl.col("ingested_at").min().alias("first_seen"),
        pl.col("ingested_at").max().alias("last_seen"),
        # Without fetches there are no facilities to mark, and no snapshot to compare with
        (pl.col("raw_file") == pl.lit(latest_snapshot or "", pl.String)).any().alias("is_active"),
    )

    links = pl.DataFrame(
        [
            {"facility": m.facility, "register_id": m.register_id, "reserved": m.reserved_spaces}
            for m in mapping
        ],
        schema={"facility": pl.String, "register_id": pl.Int64, "reserved": pl.Int64},
    )
    latest_areas = areas.filter(pl.col("ingested_at") == pl.col("ingested_at").max()).select(
        "register_id",
        pl.when(pl.col("deactivated_at").is_null()).then("paid_spaces").alias("paid"),
        pl.col("changed_at").alias("capacity_changed_at"),
    )
    return (
        seen.join(links, on="facility", how="left")
        .join(latest_areas, on="register_id", how="left")
        # Without the register's paid count the capacity stays unknown, reserved spaces or not
        .with_columns((pl.col("paid") + pl.col("reserved")).alias("capacity"))
        .rename({"facility": "facility_name"})
        .select(pl.col(c).cast(FACILITY_SCHEMA[c]) for c in ATTRIBUTES)
        .sort("first_seen", "facility_name")
    )


def assign_keys(attributes: pl.DataFrame, existing: pl.DataFrame) -> pl.DataFrame:
    """The attributes with keys: existing names keep theirs, new names get the next free keys.

    `existing` has `facility_name` and `facility_key` of the current dimension (empty on the first
    run). New facilities are numbered in the order they were first seen. The unknown member is
    always included.
    """
    known = existing.select("facility_name", "facility_key")
    new = attributes.join(known, on="facility_name", how="anti").sort("first_seen", "facility_name")
    next_key = max(int(known["facility_key"].max() or 0), 0) + 1
    new_keys = new.select("facility_name").with_columns(
        (pl.int_range(pl.len()) + next_key).alias("facility_key")
    )
    unknown = pl.DataFrame(
        {"facility_key": [UNKNOWN_KEY], "facility_name": [UNKNOWN_NAME], "is_active": [False]}
    )
    keyed = attributes.join(
        pl.concat([known, new_keys], how="vertical_relaxed"), on="facility_name"
    )
    return (
        pl.concat([unknown, keyed], how="diagonal_relaxed")
        .select(pl.col(c).cast(t) for c, t in FACILITY_SCHEMA.items())
        .sort("facility_key")
    )
