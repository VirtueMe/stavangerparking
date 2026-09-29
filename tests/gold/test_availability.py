from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from stavanger_parking.gold.availability import AVAILABILITY_SCHEMA, availability
from stavanger_parking.gold.facility import FACILITY_SCHEMA, UNKNOWN_KEY
from stavanger_parking.silver.freshness import PERIOD_SCHEMA
from stavanger_parking.silver.parse import READING_SCHEMA

# 28.09.2026 14:00 in Oslo (summer time) is 12:00 UTC
T0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def minutes(m: float) -> timedelta:
    return timedelta(minutes=m)


def frame(rows: list[dict], schema: dict) -> pl.DataFrame:
    return pl.DataFrame({c: [r.get(c) for r in rows] for c in schema}, schema=schema)


def reading(facility: str, at: timedelta, spaces: int | None = 100, status="numeric", **extra):
    reading_at = T0 + at
    local = reading_at + timedelta(hours=2)
    return {
        "facility": facility,
        "reading_at": reading_at,
        "reading_date": local.date(),
        "reading_minute_of_day": local.hour * 60 + local.minute,
        "available_spaces": spaces,
        "status": status,
        "ingested_at": reading_at + minutes(3),
        "source_id": "stavanger_parking",
        **extra,
    }


def readings(*rows: dict) -> pl.DataFrame:
    return frame(list(rows), READING_SCHEMA)


def fetches(*rows: tuple[str, timedelta, timedelta]) -> pl.DataFrame:
    """Fetch rows: (facility, reading at, fetched at), both after T0."""
    return frame(
        [{"facility": f, "reading_at": T0 + r, "ingested_at": T0 + i} for f, r, i in rows],
        READING_SCHEMA,
    )


FACILITIES = frame(
    [
        {"facility_key": UNKNOWN_KEY, "facility_name": "(unknown)"},
        {"facility_key": 1, "facility_name": "Forum", "capacity": 289},
        {"facility_key": 2, "facility_name": "Jernbanen", "capacity": 390},
        {"facility_key": 3, "facility_name": "Lervig"},
    ],
    FACILITY_SCHEMA,
)
NO_STALE = pl.DataFrame(schema=PERIOD_SCHEMA)


def build(rows: pl.DataFrame, fetch_rows=None, stale=NO_STALE) -> pl.DataFrame:
    return availability(rows, fetch_rows if fetch_rows is not None else rows, FACILITIES, stale)


def test_keys_come_from_the_dimensions_and_local_time():
    fact = build(readings(reading("Jernbanen", minutes(16))))

    row = fact.row(0, named=True)
    assert (row["facility_key"], row["date_key"], row["time_key"]) == (2, 20260928, 1416)
    assert fact.schema == pl.Schema(AVAILABILITY_SCHEMA)


def test_an_unknown_facility_maps_to_the_unknown_member():
    fact = build(readings(reading("Hundvåg", minutes(0))))

    assert fact["facility_key"].to_list() == [UNKNOWN_KEY]


def test_each_reading_is_valid_until_the_facilitys_next_one():
    fact = build(
        readings(
            reading("Jernbanen", minutes(0)),
            reading("Jernbanen", minutes(20)),
            reading("Jernbanen", minutes(25)),
            reading("Forum", minutes(4)),
        )
    )

    jernbanen = fact.filter(pl.col("facility_key") == 2)
    assert jernbanen["valid_to"].to_list() == [T0 + minutes(20), T0 + minutes(25), None]
    assert jernbanen["duration_minutes"].to_list() == [20.0, 5.0, None]
    assert fact.filter(pl.col("facility_key") == 1)["valid_to"].to_list() == [None]


def test_time_weighted_average_differs_from_a_plain_average():
    # 100 free for 20 minutes, then 50 free for 5: the plain average over-weights the short reading
    fact = build(
        readings(
            reading("Jernbanen", minutes(0), 100),
            reading("Jernbanen", minutes(20), 50),
            reading("Jernbanen", minutes(25), 50),
        )
    ).drop_nulls("duration_minutes")

    weighted = (fact["available_spaces"] * fact["duration_minutes"]).sum() / fact[
        "duration_minutes"
    ].sum()
    assert weighted == 90.0
    assert fact["available_spaces"].mean() == 75.0


def test_last_fetched_at_is_the_last_fetch_that_saw_the_reading():
    fact = build(
        readings(reading("Jernbanen", minutes(0))),
        fetches(
            ("Jernbanen", minutes(0), minutes(3)),
            ("Jernbanen", minutes(0), minutes(23)),
            ("Jernbanen", minutes(40), minutes(43)),
        ),
    )

    row = fact.row(0, named=True)
    assert (row["first_ingested_at"], row["last_fetched_at"]) == (T0 + minutes(3), T0 + minutes(23))


def test_a_reading_the_source_was_seen_stale_on_is_stale():
    stale = frame([{"source_id": "stavanger_parking", "source_reading_at": T0}], PERIOD_SCHEMA)

    fact = build(
        readings(reading("Jernbanen", minutes(0)), reading("Forum", minutes(5))), stale=stale
    )

    assert dict(fact.select("facility_key", "is_stale").rows()) == {2: True, 1: False}


def test_another_sources_stale_period_does_not_make_a_reading_stale():
    stale = frame([{"source_id": "sandnes_parking", "source_reading_at": T0}], PERIOD_SCHEMA)

    fact = build(readings(reading("Jernbanen", minutes(0))), stale=stale)

    assert fact["is_stale"].to_list() == [False]


@pytest.mark.parametrize(
    ("facility", "spaces", "status", "occupied"),
    [
        ("Jernbanen", 285, "numeric", 105),
        ("Jernbanen", None, "open", None),  # no count: occupancy unknown
        ("Lervig", 10, "numeric", None),  # no capacity: occupancy unknown
        ("Forum", 300, "numeric", -11),  # more free than capacity: shown, not hidden
    ],
)
def test_occupied_spaces(facility, spaces, status, occupied):
    fact = build(readings(reading(facility, minutes(0), spaces, status)))

    assert fact["occupied_spaces"].to_list() == [occupied]


def test_available_spaces_sum_across_facilities_at_one_point_in_time():
    fact = build(readings(reading("Jernbanen", minutes(0), 285), reading("Forum", minutes(0), 292)))

    assert fact.filter(pl.col("valid_from") == T0)["available_spaces"].sum() == 577


def test_the_local_date_follows_oslo_time_not_utc():
    # 23:30 UTC on 28 September is 01:30 on 29 September in Oslo
    fact = build(readings(reading("Jernbanen", timedelta(hours=11, minutes=30))))

    assert (fact["date_key"][0], fact["time_key"][0]) == (20260929, 130)
