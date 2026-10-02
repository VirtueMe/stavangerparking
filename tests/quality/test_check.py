import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from stavanger_parking.bronze.collector import render_raw_path, sidecar_path
from stavanger_parking.bronze.load import load_source
from stavanger_parking.config import DEFAULT_CONFIG, load_sources
from stavanger_parking.facilities import DEFAULT_MAPPING
from stavanger_parking.gold import build as gold
from stavanger_parking.quality import check
from stavanger_parking.silver import build as silver
from stavanger_parking.tables import QUALITY_TABLE, table_path

ROOT = Path(__file__).parent.parent.parent
REPO_CONFIG = DEFAULT_CONFIG
MAPPING = DEFAULT_MAPPING
REGISTER = ROOT / "tests" / "fixtures" / "parkeringsregisteret_stavanger_parkering.json"
T0 = datetime(2026, 9, 29, 12, 3, tzinfo=UTC)
NINE = ["Jernbanen", "Valberget", "Posten", "Jorenholmen", "St Olav", "Siddis", "Forum"]
NINE += ["Kyrre", "Parketten"]


def record(sted: str, free: str = "100") -> dict:
    return {
        "Dato": "29.09.2026",
        "Klokkeslett": "14:00",
        "Sted": sted,
        "Latitude": "58.966341",
        "Longitude": "5.732047",
        "Antall_ledige_plasser": free,
    }


@pytest.fixture
def tables(tmp_path):
    """Raw snapshots through bronze, silver and gold; returns a function that adds a snapshot."""
    raw_root, tables_root = tmp_path / "raw", str(tmp_path / "tables")
    sources = {s.id: s for s in load_sources(REPO_CONFIG)}

    def collect(source_id: str, at: datetime, payload: bytes) -> None:
        raw_file = render_raw_path(sources[source_id].raw_path, at)
        (raw_root / raw_file).parent.mkdir(parents=True, exist_ok=True)
        (raw_root / raw_file).write_bytes(payload)
        (raw_root / sidecar_path(raw_file)).write_text(json.dumps({"ingested_at": at.isoformat()}))

    def snapshot(at: datetime, records: list[dict]) -> str:
        collect("stavanger_parking", at, json.dumps(records).encode())
        for source in sources.values():
            load_source(source, raw_root, tables_root, at)
        silver.build(sources["stavanger_parking"], tables_root)
        silver.build_register(sources["parkeringsregisteret"], tables_root)
        gold.build(tables_root, MAPPING, REPO_CONFIG)
        return tables_root

    collect("parkeringsregisteret", T0 - timedelta(hours=1), REGISTER.read_bytes())
    return snapshot


def run(tables_root: str) -> int:
    return check.main(
        ["--tables-root", tables_root, "--config", str(REPO_CONFIG), "--mapping", str(MAPPING)]
    )


def results(tables_root: str) -> pl.DataFrame:
    return pl.read_delta(table_path(tables_root, QUALITY_TABLE))


def failed(tables_root: str) -> pl.DataFrame:
    """Failed results, without `low_coverage`: a test pipeline has no history for the last day."""
    return results(tables_root).filter(~pl.col("passed") & (pl.col("check") != "low_coverage"))


def test_healthy_data_passes_and_the_results_are_stored(tables, capsys):
    root = tables(T0, [record(f) for f in NINE])

    assert run(root) == 0

    stored = results(root)
    assert failed(root).is_empty()
    assert stored["snapshot"].unique().to_list() == ["bronze/parking/2026/09/29/120300.json"]
    assert {
        "schema_drift",
        "facility_count",
        "free_exceeds_capacity",
        "low_coverage",
        "source_glitches",
        "collect_failures",
    } <= set(stored["check"])
    # The first snapshot has no history before it: the last day is uncovered, a warning only
    coverage = stored.filter(pl.col("check") == "low_coverage").row(0, named=True)
    assert (coverage["passed"], coverage["detail"]) == (
        False,
        "216 of 216 facility-hour(s) under 30 minutes",
    )
    assert "1 failed" in capsys.readouterr().out


def test_a_critical_failure_stops_the_run_but_its_results_are_kept(tables, capsys):
    # Forum reports more free spaces than the register's 289
    root = tables(T0, [record(f, "292" if f == "Forum" else "100") for f in NINE])

    assert run(root) == 1

    assert failed(root).select("check", "severity", "subject", "detail").rows() == [
        ("free_exceeds_capacity", "critical", "Forum", "292 free of 289")
    ]
    assert "CRITICAL free_exceeds_capacity Forum: 292 free of 289" in capsys.readouterr().out


def test_a_warning_alone_does_not_stop_the_run(tables):
    root = tables(T0, [record(f, "-3" if f == "Kyrre" else "100") for f in NINE])

    assert run(root) == 0

    assert failed(root)["check"].to_list() == ["negative_available_spaces"]


def test_a_new_facility_is_critical_until_it_is_mapped(tables):
    root = tables(T0, [record(f) for f in [*NINE, "Lervig"]])

    assert run(root) == 1

    assert set(failed(root)["check"]) == {"facility_count", "unmapped_facility"}


def test_results_accumulate_across_runs(tables):
    root = tables(T0, [record(f) for f in NINE])
    run(root)
    first = results(root).height

    run(tables(T0 + timedelta(minutes=5), [record(f) for f in NINE]))

    stored = results(root)
    assert stored.height == 2 * first
    assert stored["snapshot"].n_unique() == 2


def test_the_run_summary_gets_the_report(tables, monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    run(tables(T0, [record(f) for f in NINE]))

    assert "quality:" in summary.read_text()


def test_without_tables_the_check_fails_clearly(tmp_path, capsys):
    assert run(str(tmp_path)) == 1
    assert "build bronze, silver and gold first" in capsys.readouterr().err


def test_an_empty_snapshot_is_reported_once(tables):
    # Every reading lost its facility: nothing survives parsing
    root = tables(T0, [record("") for _ in NINE])

    assert run(root) == 1

    # The critical failure once; the quarantined values explain it; no failure per facility
    assert failed(root).select("check", "detail").rows() == [
        ("empty_snapshot", "no reading survived parsing"),
        ("quarantined_values", "9 other value(s)"),
    ]
