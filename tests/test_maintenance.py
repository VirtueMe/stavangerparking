import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable

from stavanger_parking import maintenance
from stavanger_parking.bronze.collector import render_raw_path, sidecar_path
from stavanger_parking.bronze.load import load_source
from stavanger_parking.config import DEFAULT_CONFIG, load_sources
from stavanger_parking.gold import build as gold
from stavanger_parking.maintenance import (
    DEFAULT_RETENTION,
    MaintenanceError,
    maintain,
    pipeline_tables,
)
from stavanger_parking.quality import check
from stavanger_parking.silver import build as silver
from stavanger_parking.tables import FETCH_TABLE, QUALITY_TABLE, table_path

REPO_CONFIG = DEFAULT_CONFIG
NO_RETENTION = timedelta(0)


def parquet_files(path: str) -> int:
    return sum(1 for f in os.listdir(path) if f.endswith(".parquet"))


@pytest.fixture
def appended(tmp_path) -> str:
    """An append-only table written in five runs: five small files."""
    path = str(tmp_path / "bronze_parking")
    for run in range(5):
        pl.DataFrame({"run": [run] * 3, "value": list(range(3))}).write_delta(path, mode="append")
    return path


@pytest.fixture
def rewritten(tmp_path) -> str:
    """A table replaced on every run: one active file, the old ones left on disk."""
    path = str(tmp_path / "fact_parking_hourly")
    for run in range(4):
        pl.DataFrame({"run": [run]}).write_delta(path, mode="overwrite")
    return path


def test_compaction_leaves_one_file_and_the_same_rows(appended):
    before = pl.read_delta(appended).sort("run", "value")

    (result,) = maintain([appended])

    assert (result.table, result.files_before, result.files_after) == ("bronze_parking", 5, 1)
    assert pl.read_delta(appended).sort("run", "value").equals(before)


def test_the_default_retention_keeps_recent_files(rewritten):
    (result,) = maintain([rewritten])

    assert result.files_vacuumed == 0
    assert parquet_files(rewritten) == 4


def test_vacuum_removes_files_no_version_refers_to(rewritten):
    (result,) = maintain([rewritten], NO_RETENTION, force_short_retention=True)

    assert (result.files_after, result.files_vacuumed) == (1, 3)
    assert parquet_files(rewritten) == 1
    assert pl.read_delta(rewritten)["run"].to_list() == [3]


def test_a_short_retention_is_refused_unless_forced(rewritten):
    with pytest.raises(MaintenanceError, match="shorter than 7 days"):
        maintain([rewritten], timedelta(days=1))

    assert parquet_files(rewritten) == 4


def test_a_dry_run_changes_nothing(appended, rewritten):
    results = maintain(
        [appended, rewritten], NO_RETENTION, dry_run=True, force_short_retention=True
    )

    assert [(r.files_before, r.files_after, r.files_vacuumed) for r in results] == [
        (5, 5, 0),
        (1, 1, 3),
    ]
    assert (parquet_files(appended), parquet_files(rewritten)) == (5, 4)
    assert DeltaTable(appended).version() == 4


def test_tables_that_do_not_exist_yet_are_skipped(tmp_path, appended):
    results = maintain([str(tmp_path / "missing"), appended])

    assert [r.table for r in results] == ["bronze_parking"]


def test_every_table_of_the_pipeline_is_maintained(tmp_path):
    paths = pipeline_tables(str(tmp_path), REPO_CONFIG)
    names = [p.rsplit("/", 1)[-1] for p in paths]

    assert names[:4] == [
        "bronze_parking",
        "bronze_parking_load_issues",
        "bronze_parkeringsregisteret",
        "bronze_parkeringsregisteret_load_issues",
    ]
    assert FETCH_TABLE in names and QUALITY_TABLE in names
    assert table_path(str(tmp_path), "dim_date") in paths
    assert DEFAULT_RETENTION == timedelta(days=7)


def test_cli_reports_each_table(tmp_path, capsys):
    root = str(tmp_path)
    for run in range(3):
        pl.DataFrame({"run": [run]}).write_delta(table_path(root, FETCH_TABLE), mode="append")

    code = maintenance.main(["--tables-root", root, "--config", str(REPO_CONFIG)])

    assert code == 0
    assert "silver_parking_fetch: 3 → 1 active file(s); vacuumed 0" in capsys.readouterr().out


def test_cli_refuses_a_short_retention(tmp_path, capsys):
    code = maintenance.main(
        ["--tables-root", str(tmp_path), "--config", str(REPO_CONFIG), "--retention-days", "1"]
    )

    assert code == 1
    assert "shorter than 7 days" in capsys.readouterr().err


def test_the_list_covers_every_table_a_full_run_writes(tmp_path):
    """A table the builds write but maintenance does not know would grow unnoticed."""
    mapping = REPO_CONFIG.parent / "facility_mapping.json"
    register = Path(__file__).parent / "fixtures" / "parkeringsregisteret_stavanger_parkering.json"
    raw, tables = tmp_path / "raw", str(tmp_path / "tables")
    at = datetime(2026, 9, 29, 12, 3, tzinfo=UTC)
    sources = {s.id: s for s in load_sources(REPO_CONFIG)}
    record = {
        "Dato": "29.09.2026",
        "Klokkeslett": "14:00",
        "Sted": "Jernbanen",
        "Latitude": "58.97",
        "Longitude": "5.73",
        "Antall_ledige_plasser": "1",
    }
    for source_id, payload in (
        ("stavanger_parking", json.dumps([record]).encode()),
        ("parkeringsregisteret", register.read_bytes()),
    ):
        raw_file = render_raw_path(sources[source_id].raw_path, at)
        (raw / raw_file).parent.mkdir(parents=True, exist_ok=True)
        (raw / raw_file).write_bytes(payload)
        (raw / sidecar_path(raw_file)).write_text(json.dumps({"ingested_at": at.isoformat()}))
        load_source(sources[source_id], raw, tables, at)
    silver.build(sources["stavanger_parking"], tables)
    silver.build_register(sources["parkeringsregisteret"], tables)
    gold.build(tables, mapping, REPO_CONFIG)
    check.main(["--tables-root", tables, "--config", str(REPO_CONFIG), "--mapping", str(mapping)])

    written = {p.name for p in Path(tables).iterdir() if DeltaTable.is_deltatable(str(p))}
    known = {p.rsplit("/", 1)[-1] for p in pipeline_tables(tables, REPO_CONFIG)}
    assert written <= known, f"not maintained: {sorted(written - known)}"
