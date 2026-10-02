from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from stavanger_parking.gold.availability import AVAILABILITY_SCHEMA
from stavanger_parking.gold.hourly import HOURLY_SCHEMA, hourly
from stavanger_parking.silver.freshness import PERIOD_SCHEMA

# 12:00 UTC is 14:00 in Oslo (summer time)
T0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
MAX_GAP = timedelta(minutes=20)


def m(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def reading(start, end, fetched, spaces=100, status="numeric", key=2, glitch=False) -> dict:
    return {
        "facility_key": key,
        "valid_from": start,
        "valid_to": end,
        "last_fetched_at": fetched,
        "available_spaces": spaces,
        "status": status,
        "is_source_glitch": glitch,
    }


def fact(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(
        {c: [r.get(c) for r in rows] for c in AVAILABILITY_SCHEMA}, schema=AVAILABILITY_SCHEMA
    )


def stale(*periods: tuple[datetime, datetime, datetime]) -> pl.DataFrame:
    """Stale periods: (source timestamp, stale from, last stale fetch)."""
    return (
        pl.DataFrame(
            {
                "source_id": ["stavanger_parking"] * len(periods),
                "source_reading_at": [p[0] for p in periods],
                "stale_from": [p[1] for p in periods],
                "last_stale_fetch_at": [p[2] for p in periods],
            }
        ).select(
            (
                pl.col(c)
                if c in ("source_id", "source_reading_at", "stale_from", "last_stale_fetch_at")
                else pl.lit(None)
            )
            .cast(t)
            .alias(c)
            for c, t in PERIOD_SCHEMA.items()
        )
        if periods
        else pl.DataFrame(schema=PERIOD_SCHEMA)
    )


def run(*rows: dict, periods=None) -> pl.DataFrame:
    return hourly(fact(*rows), periods if periods is not None else stale(), MAX_GAP)


def only(rows: pl.DataFrame) -> dict:
    assert rows.height == 1
    return rows.row(0, named=True)


def test_the_average_is_weighted_by_time():
    # 100 free for 20 minutes, then 50 for the remaining 40: 66.7, not the plain average 75
    row = only(run(reading(m(0), m(20), m(18), 100), reading(m(20), m(60), m(58), 50)))

    assert row["avg_available_spaces"] == pytest.approx(200 / 3)
    assert (row["min_available_spaces"], row["max_available_spaces"]) == (50, 100)
    assert (row["observation_count"], row["covered_minutes"]) == (2, 60.0)
    assert (row["date_key"], row["hour"], row["hour_start"]) == (20260928, 14, T0)


def test_a_reading_is_split_across_the_hours_it_covers():
    rows = run(reading(m(30), m(150), m(148)))

    assert rows.select("hour", "covered_minutes").rows() == [(14, 30.0), (15, 60.0), (16, 30.0)]
    assert rows["observation_count"].to_list() == [1, 1, 1]


def test_coverage_stops_one_slow_interval_after_the_last_fetch():
    # Seen last at 14:10; the next reading came at 15:10: a gap in collection, not 70 minutes of 100
    rows = run(reading(m(0), m(70), m(10)), reading(m(70), m(120), m(118)))

    assert rows.select("hour", "covered_minutes").rows() == [(14, 30.0), (15, 50.0)]


def test_the_current_reading_covers_until_its_last_fetch():
    row = only(run(reading(m(0), None, m(25))))

    assert row["covered_minutes"] == 25.0


def test_open_time_is_covered_but_has_no_count():
    row = only(
        run(reading(m(0), m(30), m(28), 100), reading(m(30), m(60), m(58), None, status="open"))
    )

    assert (row["covered_minutes"], row["counted_minutes"]) == (60.0, 30.0)
    assert row["avg_available_spaces"] == 100.0
    assert (row["min_available_spaces"], row["max_available_spaces"]) == (100, 100)


def test_an_hour_with_only_open_time_has_no_average():
    row = only(run(reading(m(0), m(60), m(58), None, status="open")))

    assert row["avg_available_spaces"] is None
    assert (row["covered_minutes"], row["counted_minutes"]) == (60.0, 0.0)


def test_a_glitch_is_covered_but_not_counted():
    # 01.10.2026 22:40: "Fullt" (0) for 5 minutes between two readings of ~395
    row = only(
        run(
            reading(m(0), m(40), m(38), 395),
            reading(m(40), m(45), m(43), 0, glitch=True),
            reading(m(45), m(60), m(58), 396),
        )
    )

    assert row["avg_available_spaces"] == pytest.approx((395 * 40 + 396 * 15) / 55)
    assert (row["min_available_spaces"], row["max_available_spaces"]) == (395, 396)
    assert (row["covered_minutes"], row["counted_minutes"]) == (60.0, 55.0)
    assert row["observation_count"] == 3


def test_stale_minutes_are_the_covered_minutes_inside_a_stale_period():
    # A frozen reading from 14:00: stale from 14:15 until the last fetch that saw it, 15:40
    rows = run(reading(m(0), None, m(100)), periods=stale((m(0), m(15), m(100))))

    assert rows.select("hour", "covered_minutes", "stale_minutes").rows() == [
        (14, 60.0, 45.0),
        (15, 40.0, 40.0),
    ]


def test_another_readings_stale_period_does_not_count():
    rows = run(reading(m(0), m(60), m(58)), periods=stale((m(-60), m(-45), m(30))))

    assert rows["stale_minutes"].to_list() == [0.0]


def test_facilities_are_aggregated_separately():
    rows = run(reading(m(0), m(60), m(58), 10, key=1), reading(m(0), m(60), m(58), 20, key=2))

    assert rows.select("facility_key", "avg_available_spaces").rows() == [(1, 10.0), (2, 20.0)]


def test_the_repeated_hour_at_the_autumn_dst_change_stays_two_rows():
    # 25 October 2026: 00:00 and 01:00 UTC are both 02:00 in Oslo (summer, then winter time)
    start = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    rows = run(reading(start, start + timedelta(hours=2), start + timedelta(minutes=118)))

    assert rows.select("date_key", "hour", "hour_start").rows() == [
        (20261025, 2, start),
        (20261025, 2, start + timedelta(hours=1)),
    ]


def test_schema_is_fixed_and_no_readings_give_no_rows():
    assert run(reading(m(0), m(60), m(58))).schema == pl.Schema(HOURLY_SCHEMA)
    assert hourly(fact(), stale(), MAX_GAP).is_empty()
