from datetime import UTC, date, datetime
from decimal import Decimal

import polars as pl
import pytest

from stavanger_parking.silver.parse import (
    DATE_TIME_FIELD,
    INVALID_DATE_TIME,
    MISSING_VALUE,
    NONEXISTENT_LOCAL_TIME,
    NOT_A_COUNT,
    NOT_A_DECIMAL,
    NUMERIC,
    OPEN,
    QUARANTINE_SCHEMA,
    READING_SCHEMA,
    UNKNOWN,
    parse_readings,
)

INGESTED = datetime(2026, 9, 28, 12, 3, tzinfo=UTC)


def record(**fields) -> dict:
    """A bronze row: a valid reading (14:00 Oslo summer time), overridden by `fields`."""
    return {
        "Dato": "28.09.2026",
        "Klokkeslett": "14:00",
        "Sted": "Jernbanen",
        "Latitude": "58.966341",
        "Longitude": "5.732047",
        "Antall_ledige_plasser": "285",
    } | fields


def bronze(*records: dict, ingested_at: datetime = INGESTED) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                **r,
                "source_id": "stavanger_parking",
                "raw_file": "bronze/parking/2026/09/28/120300.json",
                "record_index": i,
                "ingested_at": ingested_at,
            }
            for i, r in enumerate(records)
        ],
        schema_overrides={"ingested_at": pl.Datetime("us", "UTC"), "record_index": pl.Int32},
    )


def parse(*records: dict, ingested_at: datetime = INGESTED):
    return parse_readings(bronze(*records, ingested_at=ingested_at))


def reasons(quarantine: pl.DataFrame) -> list[tuple[str, str | None, str]]:
    return list(quarantine.select("field", "raw_value", "reason").iter_rows())


def test_a_valid_reading_is_typed():
    readings, quarantine = parse(record())

    row = readings.row(0, named=True)
    assert row["facility"] == "Jernbanen"
    assert row["reading_at"] == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    assert row["reading_date"] == date(2026, 9, 28)
    assert row["reading_minute_of_day"] == 14 * 60
    assert row["utc_offset_minutes"] == 120
    assert row["dst_resolved"] is False
    assert row["latitude"] == Decimal("58.9663410")
    assert row["longitude"] == Decimal("5.7320470")
    assert row["available_spaces"] == 285
    assert row["status"] == NUMERIC
    assert quarantine.is_empty()


def test_output_schemas_are_fixed():
    readings, quarantine = parse(record())

    assert readings.schema == pl.Schema(READING_SCHEMA)
    assert quarantine.schema == pl.Schema(QUARANTINE_SCHEMA)


def test_winter_time_has_a_one_hour_offset():
    readings, _ = parse(
        record(Dato="15.01.2026", Klokkeslett="08:30"),
        ingested_at=datetime(2026, 1, 15, 7, 33, tzinfo=UTC),
    )

    assert readings["reading_at"][0] == datetime(2026, 1, 15, 7, 30, tzinfo=UTC)
    assert readings["utc_offset_minutes"][0] == 60


def test_open_is_a_status_not_a_quarantined_value():
    readings, quarantine = parse(record(Antall_ledige_plasser="Open"))

    assert readings["status"][0] == OPEN
    assert readings["available_spaces"][0] is None
    assert quarantine.is_empty()


def test_zero_free_spaces_is_a_count():
    readings, _ = parse(record(Antall_ledige_plasser="0"))

    assert readings["available_spaces"][0] == 0
    assert readings["status"][0] == NUMERIC


@pytest.mark.parametrize("value", ["open", "-3", "12.5", " 12", "Full", "99999999999"])
def test_other_values_are_unknown_and_quarantined(value):
    readings, quarantine = parse(record(Antall_ledige_plasser=value))

    assert readings["status"][0] == UNKNOWN
    assert readings["available_spaces"][0] is None
    assert reasons(quarantine) == [("Antall_ledige_plasser", value, NOT_A_COUNT)]
    assert quarantine["reading_excluded"][0] is False


@pytest.mark.parametrize("value", [None, ""])
def test_a_missing_count_is_unknown_and_quarantined(value):
    readings, quarantine = parse(record(Antall_ledige_plasser=value))

    assert readings["status"][0] == UNKNOWN
    assert reasons(quarantine) == [("Antall_ledige_plasser", value, MISSING_VALUE)]


@pytest.mark.parametrize("field", ["Latitude", "Longitude"])
def test_an_invalid_coordinate_is_null_and_quarantined(field):
    readings, quarantine = parse(record(**{field: "58,97"}))

    assert readings[field.lower()][0] is None
    assert reasons(quarantine) == [(field, "58,97", NOT_A_DECIMAL)]
    assert quarantine["reading_excluded"][0] is False


def test_coordinates_are_rounded_to_seven_places():
    readings, quarantine = parse(record(Latitude="58.97218849"))

    assert readings["latitude"][0] == Decimal("58.9721885")
    assert quarantine.is_empty()


@pytest.mark.parametrize(
    ("dato", "klokkeslett", "raw_value", "reason"),
    [
        ("28.09.2026", "25:00", "28.09.2026 25:00", INVALID_DATE_TIME),
        ("2026-09-28", "14:00", "2026-09-28 14:00", INVALID_DATE_TIME),
        (None, "14:00", "14:00", MISSING_VALUE),
        ("28.09.2026", "", "28.09.2026 ", MISSING_VALUE),
    ],
)
def test_a_reading_without_a_valid_time_is_left_out(dato, klokkeslett, raw_value, reason):
    readings, quarantine = parse(record(Dato=dato, Klokkeslett=klokkeslett), record())

    assert readings["record_index"].to_list() == [1]
    assert reasons(quarantine) == [(DATE_TIME_FIELD, raw_value, reason)]
    assert quarantine["reading_excluded"][0] is True


@pytest.mark.parametrize("sted", [None, ""])
def test_a_reading_without_a_facility_is_left_out(sted):
    readings, quarantine = parse(record(Sted=sted))

    assert readings.is_empty()
    assert reasons(quarantine) == [("Sted", sted, MISSING_VALUE)]
    assert quarantine["reading_excluded"][0] is True


def test_every_bad_value_of_a_left_out_reading_is_quarantined():
    _, quarantine = parse(record(Sted="", Latitude="x", Antall_ledige_plasser="Full"))

    assert sorted(quarantine["field"].to_list()) == ["Antall_ledige_plasser", "Latitude", "Sted"]
    assert quarantine["reading_excluded"].all()


def test_a_field_the_source_never_delivered_is_quarantined_as_missing():
    rows = bronze(record()).drop("Longitude")

    readings, quarantine = parse_readings(rows)

    assert readings["longitude"][0] is None
    assert reasons(quarantine) == [("Longitude", None, MISSING_VALUE)]


# DST in Norway, 2026: summer time starts 29 March (02:00 → 03:00) and ends 25 October
# (03:00 → 02:00), so 02:00–02:59 does not exist in March and happens twice in October


def test_a_time_skipped_by_the_spring_dst_change_is_left_out():
    readings, quarantine = parse(
        record(Dato="29.03.2026", Klokkeslett="02:30"),
        ingested_at=datetime(2026, 3, 29, 1, 33, tzinfo=UTC),
    )

    assert readings.is_empty()
    assert reasons(quarantine) == [(DATE_TIME_FIELD, "29.03.2026 02:30", NONEXISTENT_LOCAL_TIME)]
    assert quarantine["reading_excluded"][0] is True


def test_the_first_pass_of_an_ambiguous_hour_is_summer_time():
    # 02:30 CEST is 00:30 UTC; fetched at 00:33 UTC, the 01:30 UTC candidate lies in the future
    readings, quarantine = parse(
        record(Dato="25.10.2026", Klokkeslett="02:30"),
        ingested_at=datetime(2026, 10, 25, 0, 33, tzinfo=UTC),
    )

    row = readings.row(0, named=True)
    assert row["reading_at"] == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert row["utc_offset_minutes"] == 120
    assert row["dst_resolved"] is True
    assert quarantine.is_empty()


def test_the_second_pass_of_an_ambiguous_hour_is_winter_time():
    readings, _ = parse(
        record(Dato="25.10.2026", Klokkeslett="02:30"),
        ingested_at=datetime(2026, 10, 25, 1, 33, tzinfo=UTC),
    )

    row = readings.row(0, named=True)
    assert row["reading_at"] == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    assert row["utc_offset_minutes"] == 60
    assert row["reading_date"] == date(2026, 10, 25)
    assert row["reading_minute_of_day"] == 2 * 60 + 30
    assert row["dst_resolved"] is True


def test_readings_around_the_autumn_dst_change_stay_in_order():
    times = ["01:55", "02:00", "02:55", "02:00", "02:55", "03:00"]
    fetched = [
        datetime(2026, 10, 24, 23, 57, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 2, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 57, tzinfo=UTC),
        datetime(2026, 10, 25, 1, 2, tzinfo=UTC),
        datetime(2026, 10, 25, 1, 57, tzinfo=UTC),
        datetime(2026, 10, 25, 2, 2, tzinfo=UTC),
    ]
    frames = [
        parse(record(Dato="25.10.2026", Klokkeslett=t), ingested_at=f)[0]
        for t, f in zip(times, fetched, strict=True)
    ]

    reading_at = pl.concat(frames)["reading_at"].to_list()
    assert reading_at == sorted(reading_at)
    assert len(set(reading_at)) == len(reading_at)
