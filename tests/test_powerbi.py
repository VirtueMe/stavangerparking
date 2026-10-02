"""The Power BI project stays in step with the code.

Power BI Desktop cannot run here, so these tests check what can be checked without it: the model's
tables and columns are the ones the gold build and the quality checks write, with matching types;
relationships join existing columns; TMDL uses tab indentation; and every project JSON file is
valid against the published schema of its format (kept in tests/fixtures/powerbi_schemas).
"""

import json
import re
from pathlib import Path

import polars as pl
import pytest
from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT7

from stavanger_parking.gold.availability import AVAILABILITY_SCHEMA
from stavanger_parking.gold.calendar import DATE_SCHEMA, TIME_SCHEMA
from stavanger_parking.gold.facility import FACILITY_SCHEMA
from stavanger_parking.gold.hourly import HOURLY_SCHEMA
from stavanger_parking.gold.pricing import load_rules
from stavanger_parking.gold.stale_period import STALE_PERIOD_SCHEMA
from stavanger_parking.quality.check import RESULT_SCHEMA
from stavanger_parking.tables import (
    AVAILABILITY_TABLE,
    DATE_TABLE,
    FACILITY_TABLE,
    HOURLY_TABLE,
    QUALITY_TABLE,
    SOURCE_STALE_PERIOD_TABLE,
    TIME_TABLE,
)

ROOT = Path(__file__).parent.parent / "powerbi"
MODEL = ROOT / "StavangerParking.SemanticModel" / "definition"
REPORT = ROOT / "StavangerParking.Report"
SCHEMAS = Path(__file__).parent / "fixtures" / "powerbi_schemas"

TABLES = {
    DATE_TABLE: DATE_SCHEMA,
    TIME_TABLE: TIME_SCHEMA,
    FACILITY_TABLE: FACILITY_SCHEMA,
    AVAILABILITY_TABLE: AVAILABILITY_SCHEMA,
    HOURLY_TABLE: HOURLY_SCHEMA,
    SOURCE_STALE_PERIOD_TABLE: STALE_PERIOD_SCHEMA,
    QUALITY_TABLE: RESULT_SCHEMA,
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


def test_the_model_has_exactly_the_tables_the_code_writes():
    files = {p.stem for p in (MODEL / "tables").glob("*.tmdl")}
    refs = re.findall(r"^ref table (\S+)$", (MODEL / "model.tmdl").read_text(), re.M)

    assert files == set(TABLES)
    assert sorted(refs) == sorted(TABLES)


@pytest.mark.parametrize("table", sorted(TABLES))
def test_model_columns_match_the_tables_the_code_writes(table):
    expected = {c: tmdl_type(t) for c, t in TABLES[table].items()}

    assert model_columns(table) == expected


@pytest.mark.parametrize("table", sorted(TABLES))
def test_every_partition_reads_its_own_delta_table(table):
    text = (MODEL / "tables" / f"{table}.tmdl").read_text()

    assert f'source = DeltaTable("{table}")' in text


def test_relationships_join_existing_columns_from_fact_to_dimension():
    text = (MODEL / "relationships.tmdl").read_text()
    pairs = re.findall(r"fromColumn: (\w+)\.(\w+)\n\ttoColumn: (\w+)\.(\w+)", text)

    assert len(pairs) == 6
    for from_table, from_column, to_table, to_column in pairs:
        assert from_table.startswith("fact_") and to_table.startswith("dim_")
        assert from_column in TABLES[from_table] and to_column in TABLES[to_table]


def test_measures_are_declared_on_the_facts():
    names = {
        table: re.findall(
            r"^\tmeasure '?([^'=]+?)'? =", (MODEL / "tables" / f"{table}.tmdl").read_text(), re.M
        )
        for table in TABLES
    }

    assert set(names[HOURLY_TABLE]) >= {"Free spaces (avg)", "Occupancy", "Stale share"}
    assert set(names[SOURCE_STALE_PERIOD_TABLE]) >= {"Incidents", "Stale time (days)", "Ended"}
    assert set(names[AVAILABILITY_TABLE]) >= {
        "Free spaces (latest)",
        "Occupancy (latest)",
        "Source status",
        "Attribution",
    }
    assert not any(names[t] for t in (DATE_TABLE, TIME_TABLE, FACILITY_TABLE))


def test_fresh_occupancy_uses_the_stale_share_of_the_suggested_prices():
    text = (MODEL / "tables" / f"{HOURLY_TABLE}.tmdl").read_text()
    share = re.search(
        r"\[stale_minutes\] <= ([\d.]+) \* fact_parking_hourly\[covered_minutes\]", text
    )

    assert share and float(share.group(1)) == load_rules().max_stale_share


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


# --- visuals (#87) ---

VISUALS = sorted(REPORT.glob("definition/pages/*/visuals/*/visual.json"))
DEFINITIONS = SCHEMAS / "report_definition"
DEFINITION_URL = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/"


def retrieve(uri: str) -> Resource:
    """The published schemas a visual's schema refers to, from tests/fixtures, never the network."""
    if not uri.startswith(DEFINITION_URL):
        raise NoSuchResource(ref=uri)
    contents = json.loads((DEFINITIONS / uri.removeprefix(DEFINITION_URL)).read_text("utf-8"))
    return Resource.from_contents(contents, default_specification=DRAFT7)


def model_fields() -> dict[tuple[str, str], str]:
    """(table, name) to "Column" or "Measure", for every column and measure in the model."""
    fields = {}
    for path in (MODEL / "tables").glob("*.tmdl"):
        text = path.read_text(encoding="utf-8")
        fields |= {(path.stem, c): "Column" for c in re.findall(r"^\tcolumn (\S+)$", text, re.M)}
        fields |= {
            (path.stem, m): "Measure" for m in re.findall(r"^\tmeasure '?([^'=]+?)'? =", text, re.M)
        }
    return fields


def fields_used(node) -> list[tuple[str, str, str]]:
    """Every (kind, table, name) a visual refers to, anywhere in its query or formatting."""
    found = []
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            ref = node.get(kind)
            if isinstance(ref, dict) and "Property" in ref:
                found.append((kind, ref["Expression"]["SourceRef"]["Entity"], ref["Property"]))
        for value in node.values():
            found += fields_used(value)
    elif isinstance(node, list):
        for value in node:
            found += fields_used(value)
    return found


def test_every_page_has_visuals():
    pages = {p.parent.name for p in REPORT.glob("definition/pages/*/page.json")}

    assert {v.parents[2].name for v in VISUALS} == pages


@pytest.mark.parametrize("path", VISUALS, ids=lambda p: f"{p.parents[2].name}/{p.parent.name}")
def test_visuals_are_valid_against_their_published_schema(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads((DEFINITIONS / "visualContainer/2.12.0/schema.json").read_text("utf-8"))

    assert document["$schema"] == DEFINITION_URL + "visualContainer/2.12.0/schema.json"
    validator = Draft7Validator(schema, registry=Registry(retrieve=retrieve))
    assert [e.message for e in validator.iter_errors(document)] == []
    assert document["name"] == path.parent.name


@pytest.mark.parametrize("path", VISUALS, ids=lambda p: f"{p.parents[2].name}/{p.parent.name}")
def test_visuals_use_only_fields_in_the_model(path):
    fields = model_fields()
    used = fields_used(json.loads(path.read_text(encoding="utf-8")))

    assert [(kind, t, n) for kind, t, n in used if fields.get((t, n)) != kind] == []


@pytest.mark.parametrize("path", VISUALS, ids=lambda p: f"{p.parents[2].name}/{p.parent.name}")
def test_no_visual_shows_occupancy_that_includes_stale_hours(path):
    """The plain [Occupancy] averages in the hours of a frozen source, as a flat repeated value
    (#111): a visual shows [Occupancy (fresh data)] over time, or [Occupancy (latest)] for now."""
    used = fields_used(json.loads(path.read_text(encoding="utf-8")))

    assert ("Measure", HOURLY_TABLE, "Occupancy") not in used


@pytest.mark.parametrize("path", VISUALS, ids=lambda p: f"{p.parents[2].name}/{p.parent.name}")
def test_visuals_fit_on_their_page(path):
    page = json.loads((path.parents[2] / "page.json").read_text(encoding="utf-8"))
    box = json.loads(path.read_text(encoding="utf-8"))["position"]

    assert 0 <= box["x"] and box["x"] + box["width"] <= page["width"]
    assert 0 <= box["y"] and box["y"] + box["height"] <= page["height"]


def test_every_page_carries_the_attribution():
    """Both sources' NLOD attribution, in a footer on every page (#32, #54)."""
    for page in sorted(REPORT.glob("definition/pages/*/page.json")):
        footer = page.parent / "visuals" / "footer" / "visual.json"
        text = footer.read_text(encoding="utf-8")

        assert "distributed by Stavanger kommune and by Statens vegvesen" in text, page.parent.name
        assert "https://data.norge.no/nlod/en/2.0" in text, page.parent.name
