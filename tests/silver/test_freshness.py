from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from stavanger_parking.silver.freshness import PERIOD_SCHEMA, SNAPSHOT_SCHEMA, freshness
from stavanger_parking.silver.parse import READING_SCHEMA

STALE_AFTER = timedelta(minutes=15)
T0 = datetime(2026, 9, 23, 17, 16, tzinfo=UTC)


def fetches(*snapshots: tuple[timedelta, timedelta, int]) -> pl.DataFrame:
    """Fetch rows: (fetched after T0, source timestamp after T0, facilities) per snapshot."""
    rows = [
        {
            "source_id": "stavanger_parking",
            "raw_file": f"f{i}.json",
            "record_index": r,
            "ingested_at": T0 + fetched,
            "facility": f"facility {r}",
            "reading_at": T0 + reading,
        }
        for i, (fetched, reading, facilities) in enumerate(snapshots)
        for r in range(facilities)
    ]
    return pl.DataFrame(rows, schema_overrides={"record_index": pl.Int32}).with_columns(
        pl.lit(None, t).alias(c) for c, t in READING_SCHEMA.items() if c not in rows[0]
    )


def minutes(m: float) -> timedelta:
    return timedelta(minutes=m)


def test_a_fresh_snapshot_is_not_stale():
    snapshots, periods = freshness(fetches((minutes(3), minutes(0), 9)), STALE_AFTER)

    assert snapshots.select("source_age_minutes", "is_stale").rows() == [(3.0, False)]
    assert periods.is_empty()


def test_one_row_per_snapshot_whatever_the_number_of_facilities():
    snapshots, _ = freshness(fetches((minutes(3), minutes(0), 9)), STALE_AFTER)

    assert snapshots.height == 1


def test_exactly_the_threshold_is_not_stale():
    snapshots, _ = freshness(fetches((minutes(15), minutes(0), 1)), STALE_AFTER)

    assert snapshots["is_stale"].to_list() == [False]


def test_the_newest_timestamp_in_a_snapshot_counts():
    rows = pl.concat(
        [fetches((minutes(20), minutes(0), 1)), fetches((minutes(20), minutes(18), 1))]
    ).with_columns(pl.lit("f0.json").alias("raw_file"))

    snapshots, _ = freshness(rows, STALE_AFTER)

    assert snapshots.select("source_reading_at", "is_stale").rows() == [(T0 + minutes(18), False)]


def test_a_frozen_timestamp_forms_one_ongoing_period():
    snapshots, periods = freshness(
        fetches(
            (minutes(3), minutes(0), 9),
            (minutes(20), minutes(0), 9),
            (minutes(40), minutes(0), 9),
            (timedelta(days=6), minutes(0), 9),
        ),
        STALE_AFTER,
    )

    assert snapshots["is_stale"].to_list() == [False, True, True, True]
    assert periods.rows(named=True) == [
        {
            "source_id": "stavanger_parking",
            "source_reading_at": T0,
            "stale_from": T0 + STALE_AFTER,
            "first_stale_fetch_at": T0 + minutes(20),
            "last_stale_fetch_at": T0 + timedelta(days=6),
            "stale_fetches": 3,
            "ongoing": True,
        }
    ]


def test_a_period_ends_when_a_newer_timestamp_is_fetched():
    _, periods = freshness(
        fetches(
            (minutes(20), minutes(0), 1),
            (minutes(40), minutes(0), 1),
            (minutes(45), minutes(43), 1),
        ),
        STALE_AFTER,
    )

    assert periods.select("last_stale_fetch_at", "ongoing").rows() == [(T0 + minutes(40), False)]


def test_separate_outages_are_separate_periods():
    _, periods = freshness(
        fetches(
            (minutes(20), minutes(0), 1),
            (minutes(25), minutes(23), 1),
            (minutes(60), minutes(40), 1),
        ),
        STALE_AFTER,
    )

    assert periods.select("source_reading_at", "stale_fetches", "ongoing").rows() == [
        (T0, 1, False),
        (T0 + minutes(40), 1, True),
    ]


def test_an_old_timestamp_served_again_is_a_new_period():
    # Stale, recovered, then the old file is served again: the fresh time in between is not stale
    _, periods = freshness(
        fetches(
            (minutes(20), minutes(0), 1),
            (minutes(40), minutes(38), 1),
            (minutes(60), minutes(0), 1),
        ),
        STALE_AFTER,
    )

    assert periods.select("source_reading_at", "first_stale_fetch_at", "ongoing").rows() == [
        (T0, T0 + minutes(20), False),
        (T0, T0 + minutes(60), True),
    ]


def test_a_gap_in_collection_does_not_extend_a_period():
    # The source recovered at some point between the fetch at +40 and the one three hours later;
    # the period ends at the last fetch that saw it stale, not at the next fetch
    _, periods = freshness(
        fetches((minutes(40), minutes(0), 1), (timedelta(hours=3), minutes(178), 1)),
        STALE_AFTER,
    )

    assert periods["last_stale_fetch_at"].to_list() == [T0 + minutes(40)]


def test_a_source_timestamp_ahead_of_the_fetch_is_not_stale():
    snapshots, _ = freshness(fetches((minutes(0), minutes(2), 1)), STALE_AFTER)

    assert snapshots.select("source_age_minutes", "is_stale").rows() == [(-2.0, False)]


@pytest.mark.parametrize("stale_after", [minutes(5), minutes(60)])
def test_the_threshold_decides(stale_after):
    snapshots, _ = freshness(fetches((minutes(20), minutes(0), 1)), stale_after)

    assert snapshots["is_stale"].to_list() == [stale_after < minutes(20)]


def test_schemas_are_fixed():
    snapshots, periods = freshness(fetches((minutes(20), minutes(0), 1)), STALE_AFTER)

    assert snapshots.schema == pl.Schema(SNAPSHOT_SCHEMA)
    assert periods.schema == pl.Schema(PERIOD_SCHEMA)


def test_no_fetches_give_empty_tables():
    snapshots, periods = freshness(pl.DataFrame(schema=READING_SCHEMA), STALE_AFTER)

    assert snapshots.is_empty()
    assert periods.is_empty()
