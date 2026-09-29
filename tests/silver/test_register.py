import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from stavanger_parking.silver.parse import MISSING_VALUE, NOT_A_COUNT, QUARANTINE_SCHEMA
from stavanger_parking.silver.register import (
    AREA_SCHEMA,
    INVALID_JSON,
    INVALID_VALUE,
    parse_areas,
)

REGISTER = (
    Path(__file__).parent.parent / "fixtures" / "parkeringsregisteret_stavanger_parkering.json"
)
T0 = datetime(2026, 9, 29, 10, 58, tzinfo=UTC)


def area(**overrides) -> dict:
    """A bronze row of the register, the way the loader stores it: nested values as JSON text."""
    version = {
        "navn": "P- Jernbanen",
        "antallAvgiftsbelagtePlasser": 390,
        "antallAvgiftsfriePlasser": 0,
        "antallLadeplasser": 30,
        "antallForflytningshemmede": 5,
        "sistEndret": "2024-02-16T10:53:13Z",
    }
    return {"id": "3650", "aktivVersjon": json.dumps(version), "deaktivert": None} | overrides


def bronze(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                **r,
                "source_id": "parkeringsregisteret",
                "raw_file": "r.json",
                "record_index": i,
                "ingested_at": T0,
            }
            for i, r in enumerate(rows)
        ],
        schema_overrides={
            "record_index": pl.Int32,
            "ingested_at": pl.Datetime("us", "UTC"),
            "deaktivert": pl.String,
        },
    )


def problems(quarantine: pl.DataFrame) -> list[tuple]:
    return quarantine.select("field", "raw_value", "reason", "reading_excluded").rows()


def test_the_real_register_response_parses_without_problems():
    records = json.loads(REGISTER.read_text(encoding="utf-8"))
    rows = [
        {k: v if isinstance(v, str) or v is None else json.dumps(v) for k, v in r.items()}
        for r in records
    ]

    areas, quarantine = parse_areas(bronze(*rows))

    assert areas.height == 10
    assert quarantine.is_empty()
    assert areas.schema == pl.Schema(AREA_SCHEMA)
    assert quarantine.schema == pl.Schema(QUARANTINE_SCHEMA)


def test_an_area_is_typed():
    areas, _ = parse_areas(bronze(area()))

    row = areas.row(0, named=True)
    assert (row["register_id"], row["register_name"], row["paid_spaces"]) == (
        3650,
        "P- Jernbanen",
        390,
    )
    assert (row["free_spaces"], row["charging_spaces"], row["accessible_spaces"]) == (0, 30, 5)
    assert row["changed_at"] == datetime(2024, 2, 16, 10, 53, 13, tzinfo=UTC)
    assert row["deactivated_at"] is None


def test_a_deactivated_area_has_its_time():
    deactivated = json.dumps({"deaktivertTidspunkt": "2026-01-31T23:00:00Z"})

    areas, _ = parse_areas(bronze(area(deaktivert=deactivated)))

    assert areas["deactivated_at"][0] == datetime(2026, 1, 31, 23, tzinfo=UTC)


@pytest.mark.parametrize(("value", "reason"), [(None, MISSING_VALUE), ("abc", INVALID_VALUE)])
def test_an_area_without_a_valid_id_is_left_out(value, reason):
    areas, quarantine = parse_areas(bronze(area(id=value)))

    assert areas.is_empty()
    assert problems(quarantine) == [("id", value, reason, True)]


def test_a_version_that_is_not_json_is_quarantined_and_the_area_kept():
    areas, quarantine = parse_areas(bronze(area(aktivVersjon="{broken")))

    assert areas["paid_spaces"].to_list() == [None]
    assert ("aktivVersjon", "{broken", INVALID_JSON, False) in problems(quarantine)


@pytest.mark.parametrize("value", [-1, "390", 3.5, True])
def test_a_bad_count_is_quarantined(value):
    version = json.loads(area()["aktivVersjon"]) | {"antallAvgiftsbelagtePlasser": value}

    areas, quarantine = parse_areas(bronze(area(aktivVersjon=json.dumps(version))))

    assert areas["paid_spaces"].to_list() == [None]
    assert problems(quarantine) == [
        ("aktivVersjon.antallAvgiftsbelagtePlasser", json.dumps(value), NOT_A_COUNT, False)
    ]


def test_a_count_the_register_leaves_out_is_unknown_not_an_error():
    version = json.loads(area()["aktivVersjon"])
    del version["antallLadeplasser"]

    areas, quarantine = parse_areas(bronze(area(aktivVersjon=json.dumps(version))))

    assert areas["charging_spaces"].to_list() == [None]
    assert quarantine.is_empty()


def test_a_timestamp_without_a_zone_is_quarantined():
    version = json.loads(area()["aktivVersjon"]) | {"sistEndret": "2024-02-16T10:53:13"}

    _, quarantine = parse_areas(bronze(area(aktivVersjon=json.dumps(version))))

    assert problems(quarantine)[0][2] == INVALID_VALUE
