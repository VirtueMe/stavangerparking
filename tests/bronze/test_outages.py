import json
from datetime import UTC, datetime, timedelta

import polars as pl

from stavanger_parking.bronze import outages
from stavanger_parking.bronze.outages import OUTAGES_SCHEMA
from stavanger_parking.config import DEFAULT_CONFIG
from stavanger_parking.tables import OUTAGES_TABLE, table_path

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
PARKING = "bronze/parking/unavailable"
REGISTER = "bronze/parkeringsregisteret/unavailable"


def write_record(raw, folder: str, at: datetime, source_id: str, status: int = 500) -> str:
    path = f"{folder}/{at:%Y%m%dT%H%M%S}.json"
    record = {
        "source_id": source_id,
        "observed_at": at.isoformat(),
        "source_url": f"https://example.com/{source_id}",
        "run_id": "37614755050",
        "http_status": status,
    }
    (raw / path).parent.mkdir(parents=True, exist_ok=True)
    (raw / path).write_text(json.dumps(record))
    return path


def table(tables_root) -> pl.DataFrame:
    return pl.read_delta(table_path(str(tables_root), OUTAGES_TABLE))


def run(raw, tables_root, now=NOW) -> list[str]:
    return outages.run(DEFAULT_CONFIG, raw, str(tables_root), now)


def test_outage_records_are_loaded_one_row_each(tmp_path):
    raw, tables = tmp_path / "raw", tmp_path / "tables"
    first = datetime(2026, 10, 7, 6, 42, 9, tzinfo=UTC)
    paths = [
        write_record(raw, REGISTER, first, "parkeringsregisteret"),
        write_record(raw, REGISTER, first + timedelta(hours=1), "parkeringsregisteret", 503),
        write_record(raw, PARKING, first, "stavanger_parking"),
    ]

    report = run(raw, tables)

    rows = table(tables)
    assert rows.schema == pl.Schema(OUTAGES_SCHEMA)
    assert sorted(rows["raw_file"].to_list()) == sorted(paths)
    register = rows.filter(pl.col("source_id") == "parkeringsregisteret").sort("observed_at")
    assert register["http_status"].to_list() == [500, 503]
    assert register["observed_at"][0] == first
    assert register["ingested_at"].unique().to_list() == [NOW]
    assert report == ["outages: loaded 3 record(s), skipped 0 already loaded"]


def test_a_record_is_loaded_once(tmp_path):
    raw, tables = tmp_path / "raw", tmp_path / "tables"
    at = datetime(2026, 10, 7, 6, 42, tzinfo=UTC)
    write_record(raw, REGISTER, at, "parkeringsregisteret")
    run(raw, tables)
    write_record(raw, REGISTER, at + timedelta(hours=1), "parkeringsregisteret")

    report = run(raw, tables, NOW + timedelta(hours=1))

    assert table(tables).height == 2
    assert report == ["outages: loaded 1 record(s), skipped 1 already loaded"]


def test_no_records_leave_no_table(tmp_path):
    report = run(tmp_path / "raw", tmp_path / "tables")

    assert report == ["outages: loaded 0 record(s), skipped 0 already loaded"]
    assert not (tmp_path / "tables" / OUTAGES_TABLE).exists()


def test_a_snapshot_is_not_an_outage_record(tmp_path):
    raw, tables = tmp_path / "raw", tmp_path / "tables"
    snapshot = raw / "bronze/parking/2026/10/07/064200.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("[]")

    run(raw, tables)

    assert not (tables / OUTAGES_TABLE).exists()


def test_a_file_that_is_not_an_outage_record_is_reported_not_loaded(tmp_path):
    raw, tables = tmp_path / "raw", tmp_path / "tables"
    good = write_record(raw, REGISTER, datetime(2026, 10, 7, 6, 42, tzinfo=UTC), "x")
    (raw / REGISTER / "20261007T074200.json").write_text("not json")
    (raw / REGISTER / "20261007T084200.json").write_text(json.dumps({"source_id": "x"}))

    report = run(raw, tables)

    assert table(tables)["raw_file"].to_list() == [good]
    assert report[0] == "outages: loaded 1 record(s), skipped 0 already loaded"
    assert sorted(report[1:]) == [
        f"  not an outage record: {REGISTER}/20261007T074200.json",
        f"  not an outage record: {REGISTER}/20261007T084200.json",
    ]
