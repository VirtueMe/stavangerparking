from datetime import UTC, datetime, timedelta

import polars as pl

from stavanger_parking.gold.stale_period import STALE_PERIOD_SCHEMA, stale_periods
from stavanger_parking.silver.freshness import PERIOD_SCHEMA, freshness
from stavanger_parking.silver.parse import READING_SCHEMA

STALE_AFTER = timedelta(minutes=15)
# 17:16 UTC is 19:16 in Oslo (summer time)
T0 = datetime(2026, 9, 23, 17, 16, tzinfo=UTC)


def minutes(m: float) -> timedelta:
    return timedelta(minutes=m)


def periods(*snapshots: tuple[timedelta, timedelta]) -> pl.DataFrame:
    """Gold stale periods of fetched snapshots: (fetched after T0, source timestamp after T0)."""
    rows = [
        {
            "source_id": "stavanger_parking",
            "raw_file": f"f{i}.json",
            "record_index": 0,
            "ingested_at": T0 + fetched,
            "facility": "Forum",
            "reading_at": T0 + reading,
        }
        for i, (fetched, reading) in enumerate(snapshots)
    ]
    fetches = pl.DataFrame(rows, schema_overrides={"record_index": pl.Int32}).with_columns(
        pl.lit(None, t).alias(c) for c, t in READING_SCHEMA.items() if c not in rows[0]
    )
    _, silver = freshness(fetches, STALE_AFTER)
    return stale_periods(silver)


def test_a_closed_period_ends_at_its_last_stale_fetch():
    gold = periods((minutes(20), minutes(0)), (minutes(40), minutes(0)), (minutes(45), minutes(43)))

    assert gold.select("stale_from", "stale_until", "duration_minutes", "ongoing").rows() == [
        (T0 + STALE_AFTER, T0 + minutes(40), 25.0, False)
    ]


def test_an_ongoing_period_has_no_end_yet():
    gold = periods((minutes(20), minutes(0)), (minutes(40), minutes(0)))

    row = gold.row(0, named=True)
    assert row["ongoing"]
    assert row["stale_until"] is None
    assert row["last_stale_fetch_at"] == T0 + minutes(40)
    assert row["duration_minutes"] == 25.0


def test_a_recovery_and_a_new_freeze_are_two_periods():
    gold = periods(
        (minutes(20), minutes(0)),
        (minutes(30), minutes(28)),
        (minutes(60), minutes(40)),
        (minutes(80), minutes(40)),
    )

    assert gold.select("source_reading_at", "stale_until", "stale_fetches", "ongoing").rows() == [
        (T0, T0 + minutes(20), 1, False),
        (T0 + minutes(40), None, 2, True),
    ]


def test_the_date_key_is_the_local_date_the_period_started():
    # Stale from 22:31 UTC, which is 00:31 the next day in Oslo
    gold = periods((minutes(330), minutes(300)))

    assert gold["date_key"].to_list() == [20260924]


def test_schema_is_fixed_and_no_periods_give_an_empty_table():
    gold = stale_periods(pl.DataFrame(schema=PERIOD_SCHEMA))

    assert gold.schema == pl.Schema(STALE_PERIOD_SCHEMA)
    assert gold.height == 0
