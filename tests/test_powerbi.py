"""The Power BI project stays in step with the code.

Power BI Desktop cannot run here, so these tests check what can be checked without it: the model's
tables and columns are the ones the gold build writes, with matching types; relationships join
existing columns; TMDL uses tab indentation; and every project JSON file is valid against the
published schema of its format (kept in tests/fixtures/powerbi_schemas).
"""

import json
import re
from pathlib import Path

import polars as pl
import pytest
from jsonschema import Draft7Validator

from stavanger_parking.gold.availability import AVAILABILITY_SCHEMA
from stavanger_parking.gold.calendar import DATE_SCHEMA, TIME_SCHEMA
from stavanger_parking.gold.facility import FACILITY_SCHEMA
from stavanger_parking.gold.hourly import HOURLY_SCHEMA
from stavanger_parking.tables import (
    AVAILABILITY_TABLE,
    DATE_TABLE,
    FACILITY_TABLE,
    HOURLY_TABLE,
    TIME_TABLE,
)

ROOT = Path(__file__).parent.parent / "powerbi"
MODEL = ROOT / "StavangerParking.SemanticModel" / "definition"
REPORT = ROOT / "StavangerParking.Report"
SCHEMAS = Path(__file__).parent / "fixtures" / "powerbi_schemas"

GOLD = {
    DATE_TABLE: DATE_SCHEMA,
    TIME_TABLE: TIME_SCHEMA,
    FACILITY_TABLE: FACILITY_SCHEMA,
    AVAILABILITY_TABLE: AVAILABILITY_SCHEMA,
    HOURLY_TABLE: HOURLY_SCHEMA,
}


def tmdl_type(dtype: pl.DataType) -> str:
    if dtype.is_integer():
        return "int64"
    if dtype == pl.Float64:
        return "double"
    if isinstance(dtype, pl.Decimal):
        return "decimal"
    if dtype in (pl.String, pl.Boolean):
        return "string" if dtype == pl.String else "boolean"
    if dtype == pl.Date or isinstance(dtype, pl.Datetime):
        return "dateTime"
    raise ValueError(dtype)


def model_columns(table: str) -> dict[str, str]:
    """Column name to dataType, as declared in the table's TMDL file."""
    text = (MODEL / "tables" / f"{table}.tmdl").read_text(encoding="utf-8")
    return dict(re.findall(r"^\tcolumn (\S+)\n(?:\t\t.*\n)*?\t\tdataType: (\w+)$", text, re.M))


def test_the_model_has_exactly_the_gold_tables():
    files = {p.stem for p in (MODEL / "tables").glob("*.tmdl")}
    refs = re.findall(r"^ref table (\S+)$", (MODEL / "model.tmdl").read_text(), re.M)

    assert files == set(GOLD)
    assert sorted(refs) == sorted(GOLD)


@pytest.mark.parametrize("table", sorted(GOLD))
def test_model_columns_match_the_tables_the_code_writes(table):
    expected = {c: tmdl_type(t) for c, t in GOLD[table].items()}

    assert model_columns(table) == expected


@pytest.mark.parametrize("table", sorted(GOLD))
def test_every_partition_reads_its_own_delta_table(table):
    text = (MODEL / "tables" / f"{table}.tmdl").read_text()

    assert f'source = DeltaTable("{table}")' in text


def test_relationships_join_existing_columns_from_fact_to_dimension():
    text = (MODEL / "relationships.tmdl").read_text()
    pairs = re.findall(r"fromColumn: (\w+)\.(\w+)\n\ttoColumn: (\w+)\.(\w+)", text)

    assert len(pairs) == 5
    for from_table, from_column, to_table, to_column in pairs:
        assert from_table.startswith("fact_") and to_table.startswith("dim_")
        assert from_column in GOLD[from_table] and to_column in GOLD[to_table]


def test_measures_are_declared_on_the_facts():
    names = {
        table: re.findall(
            r"^\tmeasure '?([^'=]+?)'? =", (MODEL / "tables" / f"{table}.tmdl").read_text(), re.M
        )
        for table in GOLD
    }

    assert set(names[HOURLY_TABLE]) >= {"Free spaces (avg)", "Occupancy", "Stale share"}
    assert set(names[AVAILABILITY_TABLE]) >= {
        "Free spaces (latest)",
        "Source status",
        "Attribution",
    }
    assert not any(names[t] for t in (DATE_TABLE, TIME_TABLE, FACILITY_TABLE))


def test_tmdl_is_indented_with_tabs_only():
    for path in MODEL.rglob("*.tmdl"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not line.startswith(" "), f"{path.name}:{number} is indented with spaces"


SM, RP = "StavangerParking.SemanticModel", "StavangerParking.Report"
PROJECT_FILES = {
    "StavangerParking.pbip": "pbip.json",
    f"{SM}/definition.pbism": "semanticModel_definitionProperties_1.0.0.json",
    f"{RP}/definition.pbir": "report_definitionProperties_2.0.0.json",
    f"{RP}/definition/version.json": "report_definition_versionMetadata_1.0.0.json",
    f"{RP}/definition/report.json": "report_definition_report_3.3.0.json",
    f"{RP}/definition/pages/pages.json": "report_definition_pagesMetadata_1.1.0.json",
}


@pytest.mark.parametrize(
    ("path", "schema"),
    [
        *PROJECT_FILES.items(),
        *(
            (str(p.relative_to(ROOT)), "report_definition_page_2.1.0.json")
            for p in sorted(REPORT.glob("definition/pages/*/page.json"))
        ),
    ],
)
def test_project_files_are_valid_against_their_published_schema(path, schema):
    document = json.loads((ROOT / path).read_text(encoding="utf-8"))
    validator = Draft7Validator(json.loads((SCHEMAS / schema).read_text(encoding="utf-8")))

    assert [e.message for e in validator.iter_errors(document)] == []


def test_every_page_in_the_page_order_exists():
    order = json.loads((REPORT / "definition" / "pages" / "pages.json").read_text())["pageOrder"]
    pages = {p.parent.name for p in REPORT.glob("definition/pages/*/page.json")}

    assert set(order) == pages
    assert "about" in pages
