import json
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable

from stavanger_parking import pipeline
from stavanger_parking.bronze.collector import render_raw_path, sidecar_path
from stavanger_parking.config import DEFAULT_CONFIG, load_sources
from stavanger_parking.facilities import DEFAULT_MAPPING
from stavanger_parking.gold.pricing import DEFAULT_RULES, DEFAULT_TARIFFS
from stavanger_parking.pipeline import run_pipeline
from stavanger_parking.tables import (
    FACILITY_TABLE,
    FETCH_TABLE,
    HOURLY_TABLE,
    QUALITY_TABLE,
    table_path,
)

ROOT = Path(__file__).parent.parent
REGISTER = ROOT / "tests" / "fixtures" / "parkeringsregisteret_stavanger_parkering.json"
T0 = datetime(2026, 9, 29, 12, 3, tzinfo=UTC)
NINE = ["Jernbanen", "Valberget", "Posten", "Jorenholmen", "St Olav", "Siddis", "Forum"]
NINE += ["Kyrre", "Parketten"]
FILES = {
    "config_path": DEFAULT_CONFIG,
    "mapping_path": DEFAULT_MAPPING,
    "tariffs_path": DEFAULT_TARIFFS,
    "rules_path": DEFAULT_RULES,
}


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
def roots(tmp_path):
    """A raw root with the register and one parking snapshot, and an empty tables root."""
    raw, tables = tmp_path / "raw", str(tmp_path / "tables")
    sources = {s.id: s for s in load_sources(FILES["config_path"])}

    def collect(source_id: str, at: datetime, payload: bytes) -> None:
        raw_file = render_raw_path(sources[source_id].raw_path, at)
        (raw / raw_file).parent.mkdir(parents=True, exist_ok=True)
        (raw / raw_file).write_bytes(payload)
        (raw / sidecar_path(raw_file)).write_text(json.dumps({"ingested_at": at.isoformat()}))

    def snapshot(records: list[dict]):
        collect("parkeringsregisteret", T0 - timedelta(hours=1), REGISTER.read_bytes())
        collect("stavanger_parking", T0, json.dumps(records).encode())
        return raw, tables

    return snapshot


def run(raw, tables) -> pipeline.PipelineResult:
    return run_pipeline(raw, tables, now=T0, **FILES)


def cli(raw, tables, *extra: str) -> int:
    files = [f"--{k.removesuffix('_path')}={v}" for k, v in FILES.items()]
    return pipeline.main(["run", "--raw-root", str(raw), "--tables-root", tables, *files, *extra])


def test_a_full_run_builds_every_layer_in_order(roots):
    raw, tables = roots([record(f) for f in NINE])

    result = run(raw, tables)

    assert [s.name for s in result.steps] == ["bronze", "silver", "gold", "quality"]
    assert (result.failed_step, result.critical, result.exit_code) == (None, False, 0)
    for name in (FETCH_TABLE, FACILITY_TABLE, HOURLY_TABLE, QUALITY_TABLE):
        assert DeltaTable.is_deltatable(table_path(tables, name)), name
    assert (
        "stavanger_parking: loaded 1 file(s), 9 row(s); skipped 0 already handled; 0 issue(s)"
        in result.steps[0].report
    )
    assert "fact_parking_hourly" in "\n".join(result.steps[2].report)


def test_a_second_run_adds_nothing_but_new_quality_results(roots):
    """So an orchestrator can retry a run safely."""
    raw, tables = roots([record(f) for f in NINE])
    run(raw, tables)
    results = pl.read_delta(table_path(tables, QUALITY_TABLE)).height

    again = run(raw, tables)

    assert again.exit_code == 0
    assert all("loaded 0 file(s)" in line for line in again.steps[0].report)
    assert pl.read_delta(table_path(tables, FETCH_TABLE)).height == 9
    assert pl.read_delta(table_path(tables, QUALITY_TABLE)).height == 2 * results


def test_a_step_that_cannot_run_stops_the_pipeline(tmp_path):
    raw, tables = tmp_path / "raw", str(tmp_path / "tables")

    result = run(raw, tables)

    # Nothing to load is not a failure, but silver has no bronze table to build from
    assert [(s.name, s.error is not None) for s in result.steps] == [
        ("bronze", False),
        ("silver", True),
    ]
    assert result.failed_step.error.startswith("no bronze table at")
    assert result.exit_code == 1
    assert not Path(table_path(tables, FACILITY_TABLE)).exists()
    assert "== silver FAILED" in result.report()


def test_a_critical_check_fails_the_run_after_its_results_are_stored(roots):
    # Forum reports more free spaces than the register's 289
    raw, tables = roots([record(f, "292" if f == "Forum" else "100") for f in NINE])

    result = run(raw, tables)

    assert (result.failed_step, result.critical, result.exit_code) == (None, True, 3)
    stored = pl.read_delta(table_path(tables, QUALITY_TABLE))
    assert stored.filter(~pl.col("passed") & (pl.col("severity") == "critical"))[
        "subject"
    ].to_list() == ["Forum"]


def test_cli_exit_codes(roots, tmp_path, capsys):
    raw, tables = roots([record(f, "292" if f == "Forum" else "100") for f in NINE])

    assert cli(raw, tables) == 3
    assert cli(tmp_path / "none", str(tmp_path / "empty")) == 1
    out, err = capsys.readouterr()
    assert "== quality" in out and "CRITICAL free_exceeds_capacity Forum" in out
    assert "a critical quality check failed" in err
    assert "silver could not run" in err


def test_cli_passes_storage_options(monkeypatch, tmp_path):
    seen = {}

    def fake(raw_root, tables_root, storage_options, *files):
        seen.update(tables_root=tables_root, storage_options=storage_options)
        return pipeline.PipelineResult([])

    monkeypatch.setattr(pipeline, "run_pipeline", fake)

    code = pipeline.main(
        [
            "run",
            "--raw-root=/Volumes/main/parking/raw",
            "--tables-root=abfss://x@y.dfs.core.windows.net/Tables",
            "--storage-option=bearer_token=abc=",
            "--storage-option=use_fabric_endpoint=true",
        ]
    )

    assert code == 0
    assert seen["storage_options"] == {"bearer_token": "abc=", "use_fabric_endpoint": "true"}


def test_the_entry_point_exits_with_the_code_of_the_run(monkeypatch, tmp_path):
    """A wheel task may call the function rather than run the script: the code must not be lost."""
    argv = ["run", "--raw-root", str(tmp_path / "raw"), "--tables-root", str(tmp_path / "t")]
    monkeypatch.setattr("sys.argv", ["stavanger-parking-pipeline", *argv])

    with pytest.raises(SystemExit) as exit:
        pipeline.entry()

    assert exit.value.code == 1


@pytest.mark.parametrize("code", [1, 3])
def test_the_entry_point_exits_on_a_failure(monkeypatch, code):
    monkeypatch.setattr(pipeline, "main", lambda: code)

    with pytest.raises(SystemExit) as exit:
        pipeline.entry()

    assert exit.value.code == code


def test_the_entry_point_returns_on_success(monkeypatch):
    """No SystemExit at all: a Databricks wheel task reports even `exit(0)` as a failure (#107)."""
    monkeypatch.setattr(pipeline, "main", lambda: 0)

    assert pipeline.entry() is None


def test_cli_rejects_a_malformed_storage_option(tmp_path):
    with pytest.raises(SystemExit) as exit:
        cli(tmp_path, str(tmp_path), "--storage-option=no-equals-sign")
    assert exit.value.code == 2


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to build and install the wheel")
def test_the_installed_wheel_runs_the_pipeline_with_its_own_configuration(roots, tmp_path):
    """What a platform does: install the wheel, and run the pipeline outside the repository."""
    raw, tables = roots([record(f) for f in NINE])
    dist, venv = tmp_path / "dist", tmp_path / "venv"
    subprocess.run(["uv", "build", "--wheel", "-o", dist], cwd=ROOT, check=True)
    (wheel,) = dist.glob("*.whl")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert wheel.name.startswith(f"stavanger_parking-{version}-")
    subprocess.run(
        ["uv", "venv", "-q", "--python", "{}.{}".format(*sys.version_info[:2]), venv], check=True
    )
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    subprocess.run(["uv", "pip", "install", "-q", "--python", python, wheel], check=True)

    # The console script the platforms' jobs call, with no configuration arguments, from a
    # working directory without a config folder
    script = python.parent / ("stavanger-parking-pipeline" + (".exe" if os.name == "nt" else ""))
    run = subprocess.run(
        [
            script,
            "run",
            "--raw-root",
            raw,
            "--tables-root",
            tables,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert "== quality" in run.stdout
    assert DeltaTable.is_deltatable(table_path(tables, HOURLY_TABLE))
    installed = subprocess.run(
        [python, "-c", "import stavanger_parking; print(stavanger_parking.__file__)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(installed.stdout.strip()).is_relative_to(venv)
