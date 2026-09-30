from datetime import UTC, datetime, timedelta
from decimal import Decimal

import polars as pl

from stavanger_parking.facilities import FacilityMapping
from stavanger_parking.gold.facility import (
    FACILITY_SCHEMA,
    UNKNOWN_KEY,
    assign_keys,
    facility_attributes,
)
from stavanger_parking.silver.parse import READING_SCHEMA
from stavanger_parking.silver.register import AREA_SCHEMA

T0 = datetime(2026, 9, 28, 12, 3, tzinfo=UTC)
MAPPING = (
    FacilityMapping("Jernbanen", 3650, "P- Jernbanen", "P-Jernbanen"),
    FacilityMapping("Forum", 46816, "P - Forum", "P-Forum"),
)


def fetches(*rows: tuple[str, str, timedelta, str | None]) -> pl.DataFrame:
    """Fetch rows: (raw file, facility, fetched after T0, latitude)."""
    return pl.DataFrame(
        [
            {
                "raw_file": raw_file,
                "record_index": i,
                "facility": facility,
                "ingested_at": T0 + after,
                "latitude": None if lat is None else Decimal(lat),
                "longitude": Decimal("5.7"),
            }
            for i, (raw_file, facility, after, lat) in enumerate(rows)
        ]
    ).select(
        (
            pl.col(c)
            if c in ("raw_file", "record_index", "facility", "ingested_at", "latitude", "longitude")
            else pl.lit(None)
        )
        .cast(t)
        .alias(c)
        for c, t in READING_SCHEMA.items()
    )


def areas(
    *rows: tuple[int, int | None, datetime | None], at: datetime = T0, **spaces: list[int | None]
) -> pl.DataFrame:
    """Register areas of one snapshot: (register id, paid spaces, deactivated at).

    Other kinds of space, such as `charging_spaces=[30]`, are given per row; left out, they are 0.
    """
    given = {
        "register_id": [rid for rid, _, _ in rows],
        "paid_spaces": [paid for _, paid, _ in rows],
        "deactivated_at": [gone for _, _, gone in rows],
        "ingested_at": [at] * len(rows),
        "changed_at": [datetime(2024, 2, 16, tzinfo=UTC)] * len(rows),
    }
    for kind in ("free_spaces", "charging_spaces", "accessible_spaces"):
        given[kind] = spaces.get(kind, [0] * len(rows))
    return pl.DataFrame(
        {c: given.get(c, [None] * len(rows)) for c in AREA_SCHEMA}, schema=AREA_SCHEMA
    )


def by_name(frame: pl.DataFrame) -> dict[str, dict]:
    return {r["facility_name"]: r for r in frame.iter_rows(named=True)}


def test_attributes_come_from_the_fetches():
    rows = fetches(
        ("a.json", "Jernbanen", timedelta(0), "58.1"),
        ("b.json", "Jernbanen", timedelta(minutes=5), "58.2"),
        ("c.json", "Jernbanen", timedelta(minutes=10), None),
        ("c.json", "Forum", timedelta(minutes=10), "58.3"),
    )

    attrs = by_name(facility_attributes(rows, areas(), MAPPING))

    jernbanen = attrs["Jernbanen"]
    assert jernbanen["latitude"] == Decimal("58.2")  # the latest fetch that had one
    assert (jernbanen["first_seen"], jernbanen["last_seen"]) == (T0, T0 + timedelta(minutes=10))
    assert jernbanen["is_active"] and attrs["Forum"]["is_active"]


def test_only_facilities_in_the_latest_snapshot_are_active():
    rows = fetches(
        ("a.json", "Jernbanen", timedelta(0), "58.1"),
        ("a.json", "Forum", timedelta(0), "58.3"),
        ("b.json", "Jernbanen", timedelta(minutes=5), "58.1"),
    )

    attrs = by_name(facility_attributes(rows, areas(), MAPPING))

    assert (attrs["Jernbanen"]["is_active"], attrs["Forum"]["is_active"]) == (True, False)


def test_capacity_comes_from_the_latest_register_snapshot():
    rows = fetches(("a.json", "Jernbanen", timedelta(0), "58.1"))
    register = pl.concat(
        [areas((3650, 500, None), at=T0 - timedelta(days=60)), areas((3650, 390, None))]
    )

    attrs = by_name(facility_attributes(rows, register, MAPPING))

    assert (attrs["Jernbanen"]["register_id"], attrs["Jernbanen"]["capacity"]) == (3650, 390)


def test_a_deactivated_or_missing_area_has_unknown_capacity():
    rows = fetches(
        ("a.json", "Jernbanen", timedelta(0), "58.1"),
        ("a.json", "Forum", timedelta(0), "58.3"),
        ("a.json", "Lervig", timedelta(0), "58.4"),
    )
    register = areas((3650, 390, T0 - timedelta(days=1)))

    attrs = by_name(facility_attributes(rows, register, MAPPING))

    assert attrs["Jernbanen"]["capacity"] is None  # deactivated
    assert (attrs["Forum"]["register_id"], attrs["Forum"]["capacity"]) == (46816, None)  # missing
    assert (attrs["Lervig"]["register_id"], attrs["Lervig"]["capacity"]) == (None, None)  # unmapped


def test_first_keys_follow_first_seen_then_name():
    rows = fetches(
        ("a.json", "Jernbanen", timedelta(0), "58.1"),
        ("a.json", "Forum", timedelta(0), "58.3"),
        ("b.json", "Aarstad", timedelta(minutes=5), "58.4"),
    )

    keyed = assign_keys(
        facility_attributes(rows, areas(), MAPPING), pl.DataFrame(schema=FACILITY_SCHEMA)
    )

    assert keyed.select("facility_key", "facility_name").rows() == [
        (UNKNOWN_KEY, "(unknown)"),
        (1, "Forum"),
        (2, "Jernbanen"),
        (3, "Aarstad"),
    ]
    assert keyed.schema == pl.Schema(FACILITY_SCHEMA)


def test_existing_keys_are_kept_and_new_ones_continue_after_the_highest():
    rows = fetches(
        ("a.json", "Jernbanen", timedelta(0), "58.1"),
        ("a.json", "Forum", timedelta(0), "58.3"),
    )
    existing = pl.DataFrame(
        {"facility_key": [UNKNOWN_KEY, 7, 3], "facility_name": ["(unknown)", "Forum", "Gone"]},
        schema_overrides={"facility_key": pl.Int32},
    )

    keyed = assign_keys(facility_attributes(rows, areas(), MAPPING), existing)

    assert dict(keyed.select("facility_name", "facility_key").rows()) == {
        "(unknown)": UNKNOWN_KEY,
        "Forum": 7,
        "Jernbanen": 8,
    }


def test_capacity_counts_every_kind_of_space():
    """Forum on 2026-09-30: 289 paid, 19 charging and 2 accessible spaces; the feed saw 292 free."""
    rows = fetches(("a.json", "Forum", timedelta(0), "58.3"))
    register = areas(
        (46816, 289, None), free_spaces=[0], charging_spaces=[19], accessible_spaces=[2]
    )

    attrs = by_name(facility_attributes(rows, register, MAPPING))

    assert attrs["Forum"]["capacity"] == 310


def test_a_kind_the_register_leaves_out_adds_nothing_but_paid_spaces_are_needed():
    rows = fetches(
        ("a.json", "Jernbanen", timedelta(0), "58.1"),
        ("a.json", "Forum", timedelta(0), "58.3"),
    )
    register = areas(
        (3650, 390, None),
        (46816, None, None),
        free_spaces=[None, 0],
        charging_spaces=[30, 19],
        accessible_spaces=[None, 2],
    )

    attrs = by_name(facility_attributes(rows, register, MAPPING))

    assert attrs["Jernbanen"]["capacity"] == 420
    assert attrs["Forum"]["capacity"] is None
