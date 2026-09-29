import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from stavanger_parking.bronze.collector import render_raw_path, sidecar_path
from stavanger_parking.bronze.load import load_source
from stavanger_parking.config import load_sources
from stavanger_parking.silver import build
from stavanger_parking.silver.build import (
    QUARANTINE_TABLE,
    READING_TABLE,
    BuildError,
    table_path,
)

REPO_CONFIG = Path(__file__).parent.parent.parent / "config" / "sources.json"
T0 = datetime(2026, 9, 28, 12, 3, tzinfo=UTC)
RECORDS = [
    {
        "Dato": "28.09.2026",
        "Klokkeslett": "14:00",
        "Sted": "Jernbanen",
        "Latitude": "58.966341",
        "Longitude": "5.732047",
        "Antall_ledige_plasser": "285",
    },
    {
        "Dato": "28.09.2026",
        "Klokkeslett": "14:00",
        "Sted": "Posten",
        "Latitude": "58.969721",
        "Longitude": "5.729923",
        "Antall_ledige_plasser": "Open",
    },
    {
        "Dato": "28.09.2026",
        "Klokkeslett": "14:00",
        "Sted": "",
        "Latitude": "58.975601",
        "Longitude": "5.721537",
        "Antall_ledige_plasser": "144",
    },
]


@pytest.fixture
def source():
    return load_sources(REPO_CONFIG)[0]


@pytest.fixture
def tables(tmp_path, source):
    """A bronze table loaded from one raw snapshot, the way the collector stores it."""
    raw_root, tables_root = tmp_path / "raw", str(tmp_path / "tables")
    raw_file = render_raw_path(source.raw_path, T0)
    (raw_root / raw_file).parent.mkdir(parents=True)
    (raw_root / raw_file).write_text(json.dumps(RECORDS))
    (raw_root / sidecar_path(raw_file)).write_text(json.dumps({"ingested_at": T0.isoformat()}))
    load_source(source, raw_root, tables_root, T0)
    return tables_root


def test_build_writes_readings_and_quarantine(source, tables):
    result = build.build(source, tables)

    readings = pl.read_delta(table_path(tables, READING_TABLE))
    quarantine = pl.read_delta(table_path(tables, QUARANTINE_TABLE))
    assert readings["facility"].to_list() == ["Jernbanen", "Posten"]
    assert readings["status"].to_list() == ["numeric", "open"]
    assert quarantine.select("field", "reason", "reading_excluded").rows() == [
        ("Sted", "missing_value", True)
    ]
    assert result == build.BuildResult(bronze_rows=3, readings=2, quarantined=1, excluded=1)


def test_a_rebuild_gives_the_same_tables(source, tables):
    build.build(source, tables)
    first = pl.read_delta(table_path(tables, READING_TABLE))

    build.build(source, tables)

    assert pl.read_delta(table_path(tables, READING_TABLE)).equals(first)
    assert pl.read_delta(table_path(tables, QUARANTINE_TABLE)).height == 1


def test_an_empty_quarantine_is_still_a_table(source, tmp_path):
    raw_root, tables_root = tmp_path / "raw", str(tmp_path / "tables")
    raw_file = render_raw_path(source.raw_path, T0)
    (raw_root / raw_file).parent.mkdir(parents=True)
    (raw_root / raw_file).write_text(json.dumps(RECORDS[:1]))
    (raw_root / sidecar_path(raw_file)).write_text(json.dumps({"ingested_at": T0.isoformat()}))
    load_source(source, raw_root, tables_root, T0)

    build.build(source, tables_root)

    assert pl.read_delta(table_path(tables_root, QUARANTINE_TABLE)).is_empty()


def test_build_without_bronze_fails_clearly(source, tmp_path):
    with pytest.raises(BuildError, match="load bronze first"):
        build.build(source, str(tmp_path / "tables"))


def test_main_reports_counts(tables, capsys):
    assert build.main(["--config", str(REPO_CONFIG), "--tables-root", tables]) == 0

    out = capsys.readouterr().out
    assert "3 bronze row(s) → 2 reading(s); 1 value(s) quarantined, 1 reading(s) left out" in out


def test_main_without_bronze_exits_with_an_error(tmp_path, capsys):
    assert build.main(["--config", str(REPO_CONFIG), "--tables-root", str(tmp_path)]) == 1
    assert "load bronze first" in capsys.readouterr().err
