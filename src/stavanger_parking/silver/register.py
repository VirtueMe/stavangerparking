"""Typed parking areas from the national parking register (ADR 005).

Bronze keeps each register area as a row of text: `id` as a string, and the nested `aktivVersjon`
(the current version of the area) and `deaktivert` as JSON text. Parsing gives one typed row per
area per register snapshot, in `silver_parking_area`:

- `register_id`, and from the current version: `register_name`, the number of paid, free,
  charging and accessible spaces, and `changed_at`, when the register last changed the area
- `deactivated_at`, if the provider has deactivated the area

The register is small (Stavanger Parkering has fewer than 200 areas) and fetched about once a
month, so rows are parsed one at a time, which lets every bad value be quarantined on its own with
the same rules as the parking feed (`silver.parse`): the row stays with the value null, and is left
out only when its identity, `register_id`, is lost.
"""

import json
from datetime import datetime

import polars as pl

from stavanger_parking.silver.parse import (
    LINEAGE_SCHEMA,
    MISSING_VALUE,
    NOT_A_COUNT,
    QUARANTINE_SCHEMA,
)

INVALID_JSON = "invalid_json"
INVALID_VALUE = "invalid_value"

AREA_SCHEMA = {
    **LINEAGE_SCHEMA,
    "register_id": pl.Int64,
    "register_name": pl.String,
    "paid_spaces": pl.Int32,
    "free_spaces": pl.Int32,
    "charging_spaces": pl.Int32,
    "accessible_spaces": pl.Int32,
    "changed_at": pl.Datetime("us", "UTC"),
    "deactivated_at": pl.Datetime("us", "UTC"),
}

# Columns of the area, and where they are in the register's current version
SPACES = {
    "paid_spaces": "antallAvgiftsbelagtePlasser",
    "free_spaces": "antallAvgiftsfriePlasser",
    "charging_spaces": "antallLadeplasser",
    "accessible_spaces": "antallForflytningshemmede",
}
VERSION = "aktivVersjon"
DEACTIVATED = "deaktivert"


def parse_areas(bronze: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Typed register areas and quarantined values from bronze rows of the register."""
    areas, quarantine = [], []
    for row in bronze.iter_rows(named=True):
        lineage = {c: row[c] for c in LINEAGE_SCHEMA}
        problems: list[tuple[str, str | None, str]] = []
        area = {**lineage, **_area(row, problems)}
        excluded = area["register_id"] is None
        if not excluded:
            areas.append(area)
        quarantine.extend(
            {
                **lineage,
                "field": field,
                "raw_value": raw,
                "reason": reason,
                "reading_excluded": excluded,
            }
            for field, raw, reason in problems
        )
    return (
        pl.DataFrame(areas, schema=AREA_SCHEMA, orient="row").sort("raw_file", "register_id"),
        pl.DataFrame(quarantine, schema=QUARANTINE_SCHEMA).sort(
            "raw_file", "record_index", "field"
        ),
    )


def _area(row: dict, problems: list) -> dict:
    register_id = _integer(row.get("id"), "id", problems)
    version = _json_object(row.get(VERSION), VERSION, problems) or {}
    # No `deaktivert` means the area is active
    deactivated = _json_object(row.get(DEACTIVATED), DEACTIVATED, problems, required=False) or {}
    name = version.get("navn")
    return {
        "register_id": register_id,
        "register_name": name if isinstance(name, str) else None,
        **{
            column: _count(version.get(key), f"{VERSION}.{key}", problems)
            for column, key in SPACES.items()
        },
        "changed_at": _timestamp(version.get("sistEndret"), f"{VERSION}.sistEndret", problems),
        "deactivated_at": _timestamp(
            deactivated.get("deaktivertTidspunkt"),
            f"{DEACTIVATED}.deaktivertTidspunkt",
            problems,
            required=False,
        ),
    }


def _integer(text: str | None, field: str, problems: list) -> int | None:
    if text is None or text == "":
        problems.append((field, text, MISSING_VALUE))
        return None
    if not text.isdigit():
        problems.append((field, text, INVALID_VALUE))
        return None
    return int(text)


def _json_object(
    text: str | None, field: str, problems: list, required: bool = True
) -> dict | None:
    if text is None:
        if required:
            problems.append((field, None, MISSING_VALUE))
        return None
    try:
        value = json.loads(text)
    except ValueError:
        problems.append((field, text, INVALID_JSON))
        return None
    if value is None:
        return None
    if not isinstance(value, dict):
        problems.append((field, text, INVALID_JSON))
        return None
    return value


def _count(value, field: str, problems: list) -> int | None:
    # The register leaves a count out when the provider has not given it: unknown, not an error
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        problems.append((field, json.dumps(value, ensure_ascii=False), NOT_A_COUNT))
        return None
    return value


def _timestamp(value, field: str, problems: list, required: bool = True) -> datetime | None:
    if value is None:
        if required:
            problems.append((field, None, MISSING_VALUE))
        return None
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        problems.append((field, json.dumps(value, ensure_ascii=False), INVALID_VALUE))
        return None
    return parsed
