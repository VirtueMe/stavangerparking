import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable

from stavanger_parking.bronze import load
from stavanger_parking.bronze.collector import render_raw_path, sidecar_path
from stavanger_parking.bronze.load import (
    MISSING_SIDECAR,
    NO_RECORDS,
    UNREADABLE_PAYLOAD,
    UNREADABLE_SIDECAR,
    LoadError,
    bronze_path,
    issues_path,
    load_source,
)
from stavanger_parking.config import load_sources

REPO_CONFIG = Path(__file__).parent.parent.parent / "config" / "sources.json"
REGISTER = (
    Path(__file__).parent.parent / "fixtures" / "parkeringsregisteret_stavanger_parkering.json"
)
T0 = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
RECORDS = [
    {
        "Dato": "28.09.2026",
        "Klokkeslett": "14:00",
        "Sted": "Jernbanen",
        "Antall_ledige_plasser": "285",
    },
    {
        "Dato": "28.09.2026",
        "Klokkeslett": "14:00",
        "Sted": "Posten",
        "Antall_ledige_plasser": "Open",
    },
]


@pytest.fixture
def source():
    return load_sources(REPO_CONFIG)[0]


@pytest.fixture
def roots(tmp_path):
    return tmp_path / "raw", str(tmp_path / "tables")


def write_snapshot(source, raw_root, at, payload=None, sidecar=True, run_id="run-1") -> str:
    """Write a raw file (and its sidecar) the way the collector does."""
    raw_file = render_raw_path(source.raw_path, at)
    path = raw_root / raw_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(RECORDS).encode() if payload is None else payload)
    if sidecar:
        meta = {
            "source_id": source.id,
            "ingested_at": at.isoformat(),
            "source_url": "https://opencom.no/…/parking.json",
            "resource_id": "d1bdc6eb",
            "run_id": run_id,
            "content_hash": "sha256:abc",
            "values_fingerprint": "sha256:def",
            "polling_mode": "fast",
            "next_due": (at + timedelta(minutes=5)).isoformat(),
        }
        (raw_root / sidecar_path(raw_file)).write_text(json.dumps(meta))
    return raw_file


def bronze(source, tables_root) -> pl.DataFrame:
    return pl.read_delta(bronze_path(tables_root, source))


def test_loads_one_row_per_record_with_metadata(source, roots):
    raw_root, tables_root = roots
    raw_file = write_snapshot(source, raw_root, T0, run_id="36451890048-1")

    result = load_source(source, raw_root, tables_root, T0 + timedelta(minutes=1))

    assert (result.loaded_files, result.rows, result.issues) == ([raw_file], 2, [])
    rows = bronze(source, tables_root).sort("record_index")
    assert rows["Sted"].to_list() == ["Jernbanen", "Posten"]
    assert rows["Antall_ledige_plasser"].to_list() == ["285", "Open"]
    assert rows["raw_file"].unique().to_list() == [raw_file]
    assert rows["run_id"].unique().to_list() == ["36451890048-1"]
    assert rows["record_index"].to_list() == [0, 1]
    assert rows["ingested_at"][0] == T0
    assert rows.schema["ingested_at"] == pl.Datetime("us", "UTC")


def test_source_fields_are_stored_as_strings(source, roots):
    raw_root, tables_root = roots
    write_snapshot(source, raw_root, T0, payload=b'[{"Sted": "Forum", "Antall": 12, "X": null}]')

    load_source(source, raw_root, tables_root, T0)

    rows = bronze(source, tables_root)
    assert rows.schema["Antall"] == pl.String
    assert rows["Antall"].to_list() == ["12"]
    assert rows["X"].to_list() == [None]


def test_nested_values_are_stored_as_json_text(source, roots):
    raw_root, tables_root = roots
    payload = b'[{"id": 3650, "aktiv": true, "versjon": {"navn": "P- Jernbanen", "plasser": 390}}]'
    write_snapshot(source, raw_root, T0, payload=payload)

    load_source(source, raw_root, tables_root, T0)

    row = bronze(source, tables_root).row(0, named=True)
    assert (row["id"], row["aktiv"]) == ("3650", "true")
    assert json.loads(row["versjon"]) == {"navn": "P- Jernbanen", "plasser": 390}


def test_register_snapshots_load_into_their_own_table(roots):
    raw_root, tables_root = roots
    register = next(s for s in load_sources(REPO_CONFIG) if s.id == "parkeringsregisteret")
    write_snapshot(register, raw_root, T0, payload=REGISTER.read_bytes())

    result = load_source(register, raw_root, tables_root, T0)

    rows = pl.read_delta(bronze_path(tables_root, register))
    assert bronze_path(tables_root, register).endswith("/bronze_parkeringsregisteret")
    assert result.rows == rows.height == 10
    jernbanen = rows.filter(pl.col("id") == "3650").row(0, named=True)
    assert json.loads(jernbanen["aktivVersjon"])["antallAvgiftsbelagtePlasser"] == 390


def test_loading_is_idempotent(source, roots):
    raw_root, tables_root = roots
    write_snapshot(source, raw_root, T0)
    load_source(source, raw_root, tables_root, T0)

    again = load_source(source, raw_root, tables_root, T0 + timedelta(minutes=1))

    assert (again.loaded_files, again.rows, again.skipped) == ([], 0, 1)
    assert bronze(source, tables_root).height == 2


def test_only_new_files_are_appended_in_one_commit_per_run(source, roots):
    raw_root, tables_root = roots
    write_snapshot(source, raw_root, T0)
    load_source(source, raw_root, tables_root, T0)
    write_snapshot(source, raw_root, T0 + timedelta(minutes=5))
    write_snapshot(source, raw_root, T0 + timedelta(minutes=10))

    result = load_source(source, raw_root, tables_root, T0 + timedelta(minutes=11))

    assert len(result.loaded_files) == 2
    assert bronze(source, tables_root).height == 6
    assert DeltaTable(bronze_path(tables_root, source)).version() == 1  # two runs, two commits


def test_bronze_is_append_only(source, roots):
    raw_root, tables_root = roots
    for minutes in (0, 5, 10):
        write_snapshot(source, raw_root, T0 + timedelta(minutes=minutes))
        load_source(source, raw_root, tables_root, T0 + timedelta(minutes=minutes))

    history = DeltaTable(bronze_path(tables_root, source)).history()

    assert {h["operation"] for h in history} <= {"CREATE TABLE", "WRITE"}
    assert {h["operationParameters"]["mode"] for h in history} == {"Append"}


def test_a_new_source_field_becomes_a_new_column(source, roots):
    raw_root, tables_root = roots
    write_snapshot(source, raw_root, T0)
    load_source(source, raw_root, tables_root, T0)
    write_snapshot(
        source,
        raw_root,
        T0 + timedelta(minutes=5),
        payload=b'[{"Sted": "Forum", "Kapasitet": "500"}]',
    )

    load_source(source, raw_root, tables_root, T0 + timedelta(minutes=5))

    rows = bronze(source, tables_root)
    assert rows.height == 3
    assert rows.filter(pl.col("Sted") == "Forum")["Kapasitet"].to_list() == ["500"]
    assert rows.filter(pl.col("Sted") != "Forum")["Kapasitet"].null_count() == 2


@pytest.mark.parametrize(
    "payload, sidecar_content, issue",
    [
        (None, None, MISSING_SIDECAR),
        (None, "not json", UNREADABLE_SIDECAR),
        (None, "{}", UNREADABLE_SIDECAR),
        (b"<html>maintenance</html>", ..., UNREADABLE_PAYLOAD),
        (b'{"not": "a list"}', ..., UNREADABLE_PAYLOAD),
        (b"[]", ..., NO_RECORDS),
    ],
)
def test_problem_files_are_recorded_and_not_retried(source, roots, payload, sidecar_content, issue):
    raw_root, tables_root = roots
    raw_file = write_snapshot(source, raw_root, T0, payload=payload, sidecar=sidecar_content is ...)
    if sidecar_content not in (None, ...):
        (raw_root / sidecar_path(raw_file)).write_text(sidecar_content)

    first = load_source(source, raw_root, tables_root, T0)
    second = load_source(source, raw_root, tables_root, T0 + timedelta(minutes=1))

    assert [(i.raw_file, i.issue) for i in first.issues] == [(raw_file, issue)]
    assert first.rows == 0
    assert (second.issues, second.skipped) == ([], 1)
    issues = pl.read_delta(issues_path(tables_root, source))
    assert issues.select("raw_file", "issue").rows() == [(raw_file, issue)]


def test_good_files_load_even_when_others_have_issues(source, roots):
    raw_root, tables_root = roots
    write_snapshot(source, raw_root, T0, sidecar=False)
    write_snapshot(source, raw_root, T0 + timedelta(minutes=5))

    result = load_source(source, raw_root, tables_root, T0 + timedelta(minutes=6))

    assert (len(result.loaded_files), len(result.issues)) == (1, 1)
    assert bronze(source, tables_root).height == 2


def test_source_fields_clashing_with_metadata_stop_the_load(source, roots):
    raw_root, tables_root = roots
    write_snapshot(source, raw_root, T0, payload=b'[{"Sted": "Forum", "run_id": "x"}]')

    with pytest.raises(LoadError, match="clash with metadata columns"):
        load_source(source, raw_root, tables_root, T0)


def test_tables_root_may_end_with_a_slash(source, roots):
    raw_root, tables_root = roots
    write_snapshot(source, raw_root, T0)

    load_source(source, raw_root, tables_root + "/", T0)

    assert bronze(source, tables_root).height == 2


def test_cli_loads_and_reports(source, roots, capsys):
    raw_root, tables_root = roots
    write_snapshot(source, raw_root, T0)
    write_snapshot(source, raw_root, T0 + timedelta(minutes=5), sidecar=False)
    args = ["--config", str(REPO_CONFIG), "--raw-root", str(raw_root), "--tables-root", tables_root]

    assert load.main(args) == 0

    out = capsys.readouterr().out
    assert (
        "stavanger_parking: loaded 1 file(s), 2 row(s); skipped 0 already handled; 1 issue(s)"
        in out
    )
    assert "missing_sidecar" in out
