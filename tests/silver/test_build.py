import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from stavanger_parking.bronze.collector import render_raw_path, sidecar_path
from stavanger_parking.bronze.load import load_source
from stavanger_parking.config import load_sources
from stavanger_parking.silver import build
from stavanger_parking.silver.build import (
    FETCH_TABLE,
    FRESHNESS_TABLE,
    QUARANTINE_TABLE,
    READING_TABLE,
    STALE_PERIOD_TABLE,
    BuildError,
)
from stavanger_parking.tables import table_path

REPO_CONFIG = Path(__file__).parent.parent.parent / "config" / "sources.json"
T0 = datetime(2026, 9, 28, 12, 3, tzinfo=UTC)
TABLES = (FETCH_TABLE, READING_TABLE, QUARANTINE_TABLE, FRESHNESS_TABLE, STALE_PERIOD_TABLE)


def record(sted="Jernbanen", klokkeslett="14:00", spaces="285", latitude="58.966341") -> dict:
    return {
        "Dato": "28.09.2026",
        "Klokkeslett": klokkeslett,
        "Sted": sted,
        "Latitude": latitude,
        "Longitude": "5.732047",
        "Antall_ledige_plasser": spaces,
    }


SNAPSHOT = [record(), record(sted="Posten", spaces="Open"), record(sted="", spaces="144")]


@pytest.fixture
def source():
    return load_sources(REPO_CONFIG)[0]


@pytest.fixture
def roots(tmp_path):
    return tmp_path / "raw", str(tmp_path / "tables")


def collect(source, raw_root, at: datetime, records: list[dict]) -> None:
    """Store a raw snapshot and its sidecar the way the collector does."""
    raw_file = render_raw_path(source.raw_path, at)
    (raw_root / raw_file).parent.mkdir(parents=True, exist_ok=True)
    (raw_root / raw_file).write_text(json.dumps(records))
    (raw_root / sidecar_path(raw_file)).write_text(json.dumps({"ingested_at": at.isoformat()}))


def load_and_build(source, roots, **kwargs) -> build.BuildResult:
    raw_root, tables_root = roots
    load_source(source, raw_root, tables_root, datetime.now(UTC))
    return build.build(source, tables_root, **kwargs)


def read(tables_root: str, name: str) -> pl.DataFrame:
    """A table in a fixed order, so tables built in different ways can be compared."""
    frame = pl.read_delta(table_path(tables_root, name))
    order = [
        c for c in ("raw_file", "record_index", "field", "source_reading_at") if c in frame.columns
    ]
    return frame.sort(order)


def snapshot_of(tables_root: str) -> dict[str, pl.DataFrame]:
    return {name: read(tables_root, name) for name in TABLES}


def assert_same_tables(left: dict[str, pl.DataFrame], right: dict[str, pl.DataFrame]) -> None:
    for name in TABLES:
        assert left[name].equals(right[name]), name


def test_a_first_build_writes_all_tables(source, roots):
    collect(source, roots[0], T0, SNAPSHOT)

    result = load_and_build(source, roots)

    tables = snapshot_of(roots[1])
    assert tables[FETCH_TABLE]["facility"].to_list() == ["Jernbanen", "Posten"]
    assert tables[READING_TABLE]["status"].to_list() == ["numeric", "open"]
    assert tables[QUARANTINE_TABLE].select("field", "reason", "reading_excluded").rows() == [
        ("Sted", "missing_value", True)
    ]
    assert result == build.BuildResult(
        new_rows=3,
        fetches=2,
        quarantined=1,
        excluded=1,
        readings=2,
        conflicts=0,
        stale_snapshots=0,
        stale_periods=0,
    )


def test_repeated_fetches_become_one_reading(source, roots):
    for minutes in (0, 5, 10):
        collect(source, roots[0], T0 + timedelta(minutes=minutes), SNAPSHOT)

    result = load_and_build(source, roots)

    assert read(roots[1], FETCH_TABLE).height == 6
    assert read(roots[1], READING_TABLE)["ingested_at"].unique().to_list() == [T0]
    assert result.readings == 2


def test_rerunning_the_same_batch_changes_nothing(source, roots):
    collect(source, roots[0], T0, SNAPSHOT)
    load_and_build(source, roots)
    before = snapshot_of(roots[1])

    result = build.build(source, roots[1])

    assert_same_tables(snapshot_of(roots[1]), before)
    assert (result.new_rows, result.fetches, result.quarantined) == (0, 0, 0)


def test_a_run_only_parses_new_bronze_rows(source, roots):
    collect(source, roots[0], T0, SNAPSHOT)
    load_and_build(source, roots)
    collect(source, roots[0], T0 + timedelta(minutes=5), [record(klokkeslett="14:04")])

    result = load_and_build(source, roots)

    assert (result.new_rows, result.fetches, result.readings) == (1, 1, 3)


def test_a_late_earlier_fetch_takes_over_the_reading(source, roots):
    collect(source, roots[0], T0 + timedelta(minutes=5), [record(spaces="280")])
    load_and_build(source, roots)
    collect(source, roots[0], T0, [record()])

    result = load_and_build(source, roots)

    readings = read(roots[1], READING_TABLE)
    assert readings.select("ingested_at", "available_spaces").rows() == [(T0, 285)]
    conflicts = read(roots[1], QUARANTINE_TABLE)
    assert conflicts.select("ingested_at", "raw_value", "reason").rows() == [
        (T0 + timedelta(minutes=5), "280", "conflicting_duplicate")
    ]
    assert result.conflicts == 1


def test_incremental_runs_give_the_same_tables_as_a_rebuild(source, roots, tmp_path):
    batches = [
        (T0 + timedelta(minutes=10), [record(klokkeslett="14:08"), record(sted="Posten")]),
        (T0, SNAPSHOT),
        (T0 + timedelta(minutes=20), [record(klokkeslett="14:08", spaces="270")]),
        (T0 + timedelta(minutes=5), [record(spaces="999"), record(sted="Posten", spaces="x")]),
        (T0 + timedelta(minutes=30), [record(klokkeslett="31:00")]),
    ]
    for at, records in batches:
        collect(source, roots[0], at, records)
        load_and_build(source, roots)
    incremental = snapshot_of(roots[1])

    rebuilt_root = str(tmp_path / "rebuilt")
    shutil.copytree(roots[1], rebuilt_root)
    build.build(source, rebuilt_root, rebuild=True)

    assert_same_tables(snapshot_of(rebuilt_root), incremental)
    # Conflicts: Jernbanen 14:00 (999 vs 285) and 14:08 (270 vs 285); Posten 14:00 (285, and "x",
    # vs the winning "Open"). "x" is also quarantined as not a count
    assert read(roots[1], QUARANTINE_TABLE)["reason"].value_counts().sort("reason").rows() == [
        ("conflicting_duplicate", 4),
        ("invalid_date_time", 1),
        ("missing_value", 1),
        ("not_a_count", 1),
    ]


def test_a_run_that_stops_after_the_quarantine_is_completed_by_the_next(source, roots, monkeypatch):
    collect(source, roots[0], T0, [record(), record(sted="Posten", latitude="x")])
    load_source(source, roots[0], roots[1], T0)

    def stop(*args):
        raise RuntimeError("stopped")

    with monkeypatch.context() as m:
        m.setattr(build, "_append", stop)
        with pytest.raises(RuntimeError):
            build.build(source, roots[1])
    result = build.build(source, roots[1])

    assert read(roots[1], FETCH_TABLE).height == 2
    assert read(roots[1], QUARANTINE_TABLE).select("field", "reason").rows() == [
        ("Latitude", "not_a_decimal")
    ]
    assert result.new_rows == 2


def test_rebuild_replaces_the_tables(source, roots):
    collect(source, roots[0], T0, SNAPSHOT)
    load_and_build(source, roots)
    before = snapshot_of(roots[1])

    result = build.build(source, roots[1], rebuild=True)

    assert_same_tables(snapshot_of(roots[1]), before)
    assert result.new_rows == 3


def test_a_first_run_with_only_left_out_readings_creates_the_tables(source, roots):
    collect(source, roots[0], T0, [record(sted="")])

    result = load_and_build(source, roots)

    assert read(roots[1], FETCH_TABLE).is_empty()
    assert read(roots[1], READING_TABLE).is_empty()
    assert result.excluded == 1


def test_a_frozen_source_shows_as_a_stale_period(source, roots):
    # 14:00 Oslo is 12:00 UTC; the threshold in the repository config is 15 minutes
    for minutes in (0, 10, 20, 40):
        collect(source, roots[0], T0 + timedelta(minutes=minutes), SNAPSHOT)

    result = load_and_build(source, roots)

    snapshots = read(roots[1], FRESHNESS_TABLE).sort("ingested_at")
    assert snapshots["source_age_minutes"].to_list() == [3.0, 13.0, 23.0, 43.0]
    assert snapshots["is_stale"].to_list() == [False, False, True, True]
    period = read(roots[1], STALE_PERIOD_TABLE).row(0, named=True)
    assert period["stale_from"] == datetime(2026, 9, 28, 12, 15, tzinfo=UTC)
    assert period["last_stale_fetch_at"] == T0 + timedelta(minutes=40)
    assert period["ongoing"] is True
    assert (result.stale_snapshots, result.stale_periods) == (2, 1)


def test_build_without_bronze_fails_clearly(source, tmp_path):
    with pytest.raises(BuildError, match="load bronze first"):
        build.build(source, str(tmp_path / "tables"))


def test_main_reports_counts(source, roots, capsys):
    collect(source, roots[0], T0, SNAPSHOT)
    load_source(source, roots[0], roots[1], T0)

    assert build.main(["--config", str(REPO_CONFIG), "--tables-root", roots[1]]) == 0
    assert build.main(["--config", str(REPO_CONFIG), "--tables-root", roots[1], "--rebuild"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("stavanger_parking: parsed 3 bronze row(s) → 2 fetch(es)")
    assert (
        lines[1] == "parkeringsregisteret: no bronze table yet; collect and load the register first"
    )
    assert lines[2].startswith("stavanger_parking: rebuilt from 3 bronze row(s)")


def test_main_without_bronze_exits_with_an_error(tmp_path, capsys):
    assert build.main(["--config", str(REPO_CONFIG), "--tables-root", str(tmp_path)]) == 1
    assert "load bronze first" in capsys.readouterr().err
