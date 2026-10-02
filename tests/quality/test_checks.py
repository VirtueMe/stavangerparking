from datetime import UTC, datetime, timedelta
from decimal import Decimal

import polars as pl
import pytest

from stavanger_parking.bronze.load import ISSUE_SCHEMA
from stavanger_parking.facilities import FacilityMapping
from stavanger_parking.quality import checks
from stavanger_parking.quality.checks import CRITICAL, WARNING
from stavanger_parking.silver.parse import QUARANTINE_SCHEMA

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
MAPPING = (
    FacilityMapping("Jernbanen", 3650, "P- Jernbanen", "P-Jernbanen"),
    FacilityMapping("Forum", 46816, "P - Forum", "P-Forum"),
)
SNAPSHOT = "bronze/parking/2026/09/29/120000.json"


def bronze(**fields) -> pl.DataFrame:
    record = {
        "Dato": "29.09.2026",
        "Klokkeslett": "14:00",
        "Sted": "Jernbanen",
        "Latitude": "58.966341",
        "Longitude": "5.732047",
        "Antall_ledige_plasser": "285",
    } | fields
    return pl.DataFrame([{**record, "raw_file": SNAPSHOT, "record_index": 0, "source_id": "p"}])


def fetches(*rows: tuple[str, int | None, str | None, str | None]) -> pl.DataFrame:
    """Fetch rows of the snapshot: (facility, free spaces, latitude, longitude)."""
    return pl.DataFrame(
        {
            "raw_file": [SNAPSHOT] * len(rows),
            "facility": [r[0] for r in rows],
            "available_spaces": [r[1] for r in rows],
            "latitude": [None if r[2] is None else Decimal(r[2]) for r in rows],
            "longitude": [None if r[3] is None else Decimal(r[3]) for r in rows],
        },
        schema_overrides={"available_spaces": pl.Int32},
    )


def by(results, check: str) -> list:
    return [(r.subject, r.passed, r.detail) for r in results if r.check == check]


def test_the_latest_snapshot_includes_files_that_could_not_be_loaded():
    loaded = pl.DataFrame({"raw_file": ["bronze/parking/2026/09/29/115500.json"]})
    issues = pl.DataFrame({"raw_file": [SNAPSHOT]})

    assert checks.latest_snapshot(loaded, issues) == SNAPSHOT
    assert checks.latest_snapshot(loaded.clear(), issues.clear()) is None


def test_expected_fields_are_not_schema_drift():
    (result,) = checks.schema_drift(bronze(), SNAPSHOT)

    assert (result.severity, result.passed) == (CRITICAL, True)


def test_a_missing_or_new_field_is_schema_drift():
    rows = bronze(Latitude=None, Kapasitet="500")

    (result,) = checks.schema_drift(rows, SNAPSHOT)

    assert not result.passed
    assert result.detail == "missing: Latitude; new: Kapasitet"


def test_a_field_only_in_older_snapshots_is_not_drift_now():
    older = bronze(Kapasitet="500").with_columns(pl.lit("old.json").alias("raw_file"))
    rows = pl.concat([older, bronze()], how="diagonal")

    (result,) = checks.schema_drift(rows, SNAPSHOT)

    assert result.passed


def test_a_snapshot_that_could_not_be_loaded_is_empty():
    issues = pl.DataFrame(
        [{"raw_file": SNAPSHOT, "issue": "unreadable_payload"}], schema_overrides={}
    ).select(
        pl.col(c).cast(t) if c in ("raw_file", "issue") else pl.lit(None).cast(t).alias(c)
        for c, t in ISSUE_SCHEMA.items()
    )

    result = checks.empty_snapshot(SNAPSHOT, issues, fetches())

    assert (result.severity, result.passed, result.detail) == (
        CRITICAL,
        False,
        "not loaded: unreadable_payload",
    )


def test_a_snapshot_without_surviving_readings_is_empty():
    result = checks.empty_snapshot(SNAPSHOT, pl.DataFrame(schema=ISSUE_SCHEMA), fetches())

    assert (result.passed, result.detail) == (False, "no reading survived parsing")


def test_the_facility_count_must_match_the_mapping():
    ok = checks.facility_count(
        fetches(("Jernbanen", 1, None, None), ("Forum", 1, None, None)), MAPPING
    )
    renamed = checks.facility_count(
        fetches(("Jernbanen", 1, None, None), ("Forum Stavanger", 1, None, None)), MAPPING
    )
    added = checks.facility_count(
        fetches(("Jernbanen", 1, None, None), ("Forum", 1, None, None), ("Lervig", 1, None, None)),
        MAPPING,
    )

    assert ok.passed and ok.severity == CRITICAL
    assert renamed.passed  # same count: the rename shows as an unmapped facility instead
    assert not added.passed
    assert added.detail == "3 facilities, 2 mapped; not mapped: Lervig"


def test_free_spaces_above_capacity_is_critical():
    results = checks.facility_checks(
        fetches(("Forum", 292, "58.95", "5.70"), ("Jernbanen", 285, "58.97", "5.73")),
        {"Forum": 289, "Jernbanen": 390},
        MAPPING,
    )

    assert by(results, "free_exceeds_capacity") == [
        ("Forum", False, "292 free of 289"),
        ("Jernbanen", True, "285 free of 390"),
    ]


def test_capacity_is_not_checked_without_a_count_or_a_capacity():
    results = checks.facility_checks(
        fetches(("Forum", None, "58.95", "5.70"), ("Lervig", 10, "58.97", "5.73")),
        {"Forum": 289},
        MAPPING,
    )

    assert by(results, "free_exceeds_capacity") == []


def test_an_unmapped_facility_is_a_warning():
    results = checks.facility_checks(fetches(("Lervig", 10, "58.97", "5.73")), {}, MAPPING)

    (result,) = [r for r in results if r.check == "unmapped_facility"]
    assert (result.severity, result.passed, result.subject) == (WARNING, False, "Lervig")


@pytest.mark.parametrize(
    ("lat", "lon", "inside"),
    [("58.966341", "5.732047", True), ("59.9139", "10.7522", False), (None, "5.73", False)],
)
def test_coordinates_must_be_in_the_stavanger_area(lat, lon, inside):
    results = checks.facility_checks(fetches(("Jernbanen", 1, lat, lon)), {}, MAPPING)

    assert by(results, "coordinates_outside_area")[0][1] is inside


def test_negative_spaces_and_other_quarantined_values_are_warnings():
    quarantine = pl.DataFrame(
        {
            "raw_file": [SNAPSHOT, SNAPSHOT, "other.json"],
            "field": ["Antall_ledige_plasser", "Latitude", "Antall_ledige_plasser"],
            "raw_value": ["-3", "x", "-1"],
        }
    ).select(
        pl.col(c).cast(t)
        if c in ("raw_file", "field", "raw_value")
        else pl.lit(None).cast(t).alias(c)
        for c, t in QUARANTINE_SCHEMA.items()
    )

    results = checks.quarantine_checks(quarantine, SNAPSHOT)

    assert [(r.check, r.passed, r.detail) for r in results] == [
        ("negative_available_spaces", False, "1 negative value(s)"),
        ("quarantined_values", False, "1 other value(s)"),
    ]


def test_mapped_areas_missing_from_the_latest_register_snapshot():
    areas = pl.DataFrame(
        {"register_id": [3650, 46816, 3650], "ingested_at": [T0 - timedelta(days=30)] * 2 + [T0]}
    )

    result = checks.register_missing_areas(areas, MAPPING)

    assert (result.severity, result.passed, result.detail) == (WARNING, False, "missing: 46816")


def test_no_register_snapshot_is_a_warning():
    result = checks.register_missing_areas(
        pl.DataFrame(schema={"register_id": pl.Int64, "ingested_at": pl.Datetime("us", "UTC")}),
        MAPPING,
    )

    assert (result.passed, result.detail) == (False, "no register snapshot")


def test_a_stale_source_is_a_warning():
    freshness = pl.DataFrame(
        {"raw_file": [SNAPSHOT], "source_age_minutes": [8357.2], "is_stale": [True]}
    )

    (result,) = checks.source_stale(freshness, SNAPSHOT)

    assert (result.severity, result.passed, result.detail) == (
        WARNING,
        False,
        "data 8357 minutes old",
    )


def test_hours_without_any_reading_count_as_low_coverage():
    # Facility 1 fully covered for the last day; facility 2 only for 20 of its 24 hours
    hours = [T0 - timedelta(hours=h) for h in range(1, 25)]
    hourly = pl.DataFrame(
        {
            "facility_key": [1] * 24 + [2] * 20,
            "hour_start": hours + hours[:20],
            "covered_minutes": [60.0] * 44,
        },
        schema_overrides={"facility_key": pl.Int32, "hour_start": pl.Datetime("us", "UTC")},
    )

    result = checks.low_coverage(hourly, [1, 2], T0 + timedelta(minutes=10))

    assert (result.severity, result.passed) == (WARNING, False)
    assert result.detail == "4 of 48 facility-hour(s) under 30 minutes"


def glitch_rows(*rows: tuple[int, datetime, bool]) -> pl.DataFrame:
    """Availability rows: (facility key, valid from, is a glitch)."""
    return pl.DataFrame(
        rows,
        schema={
            "facility_key": pl.Int32,
            "valid_from": pl.Datetime("us", "UTC"),
            "is_source_glitch": pl.Boolean,
        },
        orient="row",
    )


FACILITY_NAMES = pl.DataFrame(
    {"facility_key": [1, 2], "facility_name": ["Forum", "Jernbanen"]},
    schema_overrides={"facility_key": pl.Int32},
)


def test_glitches_in_the_last_day_are_a_warning_per_facility():
    availability = glitch_rows(
        (2, T0 - timedelta(hours=6), True),
        (2, T0 - timedelta(hours=1), True),
        (1, T0 - timedelta(hours=6), True),
        (1, T0 - timedelta(hours=25), True),  # older than a day: not counted
        (1, T0 - timedelta(hours=2), False),
    )

    result = checks.source_glitches(availability, FACILITY_NAMES, T0)

    assert (result.severity, result.passed) == (WARNING, False)
    assert result.detail == "3 glitch(es) in the last 24 hours: Forum 1, Jernbanen 2"


def test_no_glitches_pass():
    availability = glitch_rows((1, T0 - timedelta(hours=1), False))

    result = checks.source_glitches(availability, FACILITY_NAMES, T0)

    assert (result.passed, result.detail) == (True, "0 glitch(es) in the last 24 hours")


def failed_run_rows(*rows: tuple[str | None, datetime]) -> pl.DataFrame:
    """bronze_collect_runs rows: (failed step, started at)."""
    return pl.DataFrame(
        rows,
        schema={"failed_step": pl.String, "started_at": pl.Datetime("us", "UTC")},
        orient="row",
    )


def test_failed_collector_runs_in_the_last_day_are_a_warning():
    runs = failed_run_rows(
        ("Commit and push snapshots", T0 - timedelta(hours=20)),
        ("Fail the run if a source failed", T0 - timedelta(hours=2)),
        ("Fail the run if a source failed", T0 + timedelta(minutes=30)),  # after the latest fetch
        (None, T0 - timedelta(hours=1)),
        ("Commit and push snapshots", T0 - timedelta(hours=30)),  # older than a day
    )

    result = checks.collect_failures(runs, T0)

    assert (result.severity, result.passed) == (WARNING, False)
    assert result.detail == (
        "4 failed collector run(s) in the last 24 hours: Commit and push snapshots 1, "
        "Fail the run if a source failed 2, no step reported 1"
    )


def test_no_failed_collector_runs_pass():
    result = checks.collect_failures(failed_run_rows(), T0)

    assert (result.passed, result.detail) == (
        True,
        "0 failed collector run(s) in the last 24 hours",
    )
