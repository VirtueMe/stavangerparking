from datetime import UTC, datetime, timedelta

import polars as pl

from stavanger_parking.silver.dedup import CONFLICTING_DUPLICATE, deduplicate
from stavanger_parking.silver.parse import QUARANTINE_SCHEMA, READING_SCHEMA, parse_readings

T0 = datetime(2026, 9, 28, 12, 3, tzinfo=UTC)


def record(**fields) -> dict:
    return {
        "Dato": "28.09.2026",
        "Klokkeslett": "14:00",
        "Sted": "Jernbanen",
        "Latitude": "58.966341",
        "Longitude": "5.732047",
        "Antall_ledige_plasser": "285",
    } | fields


def fetches(*snapshots: tuple[datetime, list[dict]]) -> pl.DataFrame:
    """Parsed fetches of snapshots, each fetched at its own time into its own raw file."""
    rows = [
        {
            **r,
            "source_id": "stavanger_parking",
            "raw_file": f"bronze/parking/{at:%Y/%m/%d/%H%M%S}.json",
            "record_index": i,
            "ingested_at": at,
        }
        for at, records in snapshots
        for i, r in enumerate(records)
    ]
    bronze = pl.DataFrame(
        rows,
        schema_overrides={"ingested_at": pl.Datetime("us", "UTC"), "record_index": pl.Int32},
    )
    return parse_readings(bronze)[0]


def test_repeated_fetches_of_a_reading_collapse_to_the_first():
    frame = fetches(
        (T0, [record()]),
        (T0 + timedelta(minutes=5), [record()]),
        (T0 + timedelta(minutes=10), [record()]),
    )

    readings, conflicts = deduplicate(frame)

    assert readings.height == 1
    assert readings["ingested_at"][0] == T0
    assert conflicts.is_empty()


def test_the_winner_does_not_depend_on_row_order():
    frame = fetches((T0, [record()]), (T0 + timedelta(minutes=5), [record()]))

    readings, _ = deduplicate(frame.reverse())

    assert readings["ingested_at"].to_list() == [T0]


def test_different_facilities_and_times_are_different_readings():
    frame = fetches(
        (T0, [record(), record(Sted="Posten")]),
        (T0 + timedelta(minutes=5), [record(Klokkeslett="14:04"), record(Sted="Posten")]),
    )

    readings, _ = deduplicate(frame)

    assert readings.select("facility", "reading_minute_of_day").sort("facility").rows() == [
        ("Jernbanen", 14 * 60),
        ("Jernbanen", 14 * 60 + 4),
        ("Posten", 14 * 60),
    ]


def test_a_changed_count_under_the_same_timestamp_is_a_conflict():
    later = T0 + timedelta(minutes=5)
    frame = fetches((T0, [record()]), (later, [record(Antall_ledige_plasser="280")]))

    readings, conflicts = deduplicate(frame)

    assert readings["available_spaces"].to_list() == [285]
    assert conflicts.select("ingested_at", "field", "raw_value", "reason").rows() == [
        (later, "Antall_ledige_plasser", "280", CONFLICTING_DUPLICATE)
    ]
    assert conflicts["reading_excluded"].to_list() == [False]


def test_open_versus_a_count_is_a_conflict():
    frame = fetches(
        (T0, [record()]), (T0 + timedelta(minutes=5), [record(Antall_ledige_plasser="Open")])
    )

    _, conflicts = deduplicate(frame)

    assert conflicts.select("field", "raw_value").rows() == [("Antall_ledige_plasser", "Open")]


def test_every_differing_value_is_its_own_conflict():
    frame = fetches(
        (T0, [record()]),
        (T0 + timedelta(minutes=5), [record(Latitude="58.9", Longitude="5.7")]),
    )

    _, conflicts = deduplicate(frame)

    assert conflicts.select("field", "raw_value").rows() == [
        ("Latitude", "58.9000000"),
        ("Longitude", "5.7000000"),
    ]


def test_a_facility_listed_twice_in_one_snapshot_is_a_conflict():
    frame = fetches((T0, [record(), record(Antall_ledige_plasser="12")]))

    readings, conflicts = deduplicate(frame)

    assert readings["record_index"].to_list() == [0]
    assert conflicts.select("record_index", "raw_value").rows() == [(1, "12")]


def test_a_value_missing_in_one_fetch_only_is_a_conflict():
    frame = fetches((T0, [record()]), (T0 + timedelta(minutes=5), [record(Latitude="x")]))

    _, conflicts = deduplicate(frame)

    assert conflicts.select("field", "raw_value").rows() == [("Latitude", None)]


def test_schemas_match_the_parsed_tables():
    readings, conflicts = deduplicate(fetches((T0, [record()])))

    assert readings.schema == pl.Schema(READING_SCHEMA)
    assert conflicts.schema == pl.Schema(QUARANTINE_SCHEMA)


def test_no_fetches_give_empty_results():
    readings, conflicts = deduplicate(pl.DataFrame(schema=READING_SCHEMA))

    assert readings.is_empty()
    assert conflicts.is_empty()
