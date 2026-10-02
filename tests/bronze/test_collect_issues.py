import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from deltalake import DeltaTable

from stavanger_parking.bronze import collect as collect_cli
from stavanger_parking.bronze.collect_issues import (
    FETCH_FAILED,
    RUN_FAILED,
    CollectIssue,
    issue_path,
    read_issues,
    record_failed_runs,
    unrecorded_runs,
    write_issue,
)
from stavanger_parking.bronze.collector import CollectError
from stavanger_parking.bronze.load import COLLECT_ISSUE_SCHEMA, load_collect_issues
from stavanger_parking.tables import COLLECT_ISSUES_TABLE, table_path

T0 = datetime(2026, 10, 1, 16, 17, 14, tzinfo=UTC)
NOW = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


def fetch_failed(run_id="36890842735-1", source="stavanger_parking") -> CollectIssue:
    return CollectIssue(FETCH_FAILED, run_id, T0, "Download failed: 502", source)


def failed_run(run_id: str, step: str | None = "Commit and push snapshots") -> dict:
    return {
        "run_id": run_id,
        "started_at": T0.isoformat(),
        "failed_step": step,
        "url": f"https://github.com/o/r/actions/runs/{run_id.split('-')[0]}",
    }


# --- records ------------------------------------------------------------------------------------


def test_a_record_is_stored_by_time_run_kind_and_source(tmp_path):
    path = write_issue(tmp_path, fetch_failed(), NOW)

    assert (
        path == "bronze/collect_issues/2026/10/01/"
        "161714-36890842735-1-fetch_failed-stavanger_parking.json"
    )
    record = json.loads((tmp_path / path).read_text())
    assert record == {
        "issue": FETCH_FAILED,
        "run_id": "36890842735-1",
        "occurred_at": T0.isoformat(),
        "detail": "Download failed: 502",
        "source_id": "stavanger_parking",
        "url": None,
        "recorded_at": NOW.isoformat(),
    }


def test_a_run_record_has_no_source_in_its_path():
    issue = CollectIssue(RUN_FAILED, "36890842735-1", T0, "failed step: Commit and push snapshots")

    assert issue_path(issue).endswith("/161714-36890842735-1-run_failed.json")


def test_a_record_is_never_overwritten(tmp_path):
    write_issue(tmp_path, fetch_failed(), NOW)

    with pytest.raises(FileExistsError):
        write_issue(tmp_path, fetch_failed(), NOW + timedelta(minutes=5))


def test_records_are_outside_every_sources_raw_files(tmp_path):
    """Bronze finds a source's files by its raw_path glob; a record must never match one."""
    from stavanger_parking.bronze.load import raw_files
    from stavanger_parking.config import DEFAULT_CONFIG, load_sources

    write_issue(tmp_path, fetch_failed(), NOW)

    assert all(raw_files(tmp_path, s) == [] for s in load_sources(DEFAULT_CONFIG))


# --- failed runs -------------------------------------------------------------------------------


def test_failed_runs_are_recorded_once(tmp_path):
    runs = [
        failed_run("36890842735-1"),
        failed_run("36642529839-1", "Fail the run if a source failed"),
    ]

    first = record_failed_runs(tmp_path, runs, NOW)
    again = record_failed_runs(tmp_path, runs, NOW)

    assert len(first) == 2 and again == []
    details = sorted(r["detail"] for _, r in read_issues(tmp_path))
    assert details == [
        "failed step: Commit and push snapshots",
        "failed step: Fail the run if a source failed",
    ]


def test_a_run_that_recorded_its_own_failure_is_not_recorded_again(tmp_path):
    write_issue(tmp_path, fetch_failed("36642529839-1"), NOW)

    assert record_failed_runs(tmp_path, [failed_run("36642529839-1")], NOW) == []


def test_a_run_without_a_reported_step_is_still_recorded(tmp_path):
    (path,) = record_failed_runs(tmp_path, [failed_run("1-1", step=None)], NOW)

    assert json.loads((tmp_path / path).read_text())["detail"] == "failed; no step reported"


def test_unrecorded_runs_are_listed_once_in_order(tmp_path):
    write_issue(tmp_path, fetch_failed("2-1"), NOW)

    assert unrecorded_runs(tmp_path, ["3-1", "2-1", "1-1", "3-1"]) == ["3-1", "1-1"]


# --- the collector's command line ----------------------------------------------------------


def test_a_source_that_fails_is_recorded_and_fails_the_run(tmp_path, monkeypatch, capsys):
    def fail(source, storage, client, now, run_id, keep):
        raise CollectError(f"Download failed for {source.id}: 502")

    monkeypatch.setattr(collect_cli, "make_client", nullcontext)
    monkeypatch.setattr(collect_cli, "collect", fail)

    code = collect_cli.main(["run", "--storage", str(tmp_path), "--run-id", "7-1"])

    assert code == 1
    records = [r for _, r in read_issues(tmp_path)]
    # Every polled source failed, and each has its own record
    assert {(r["issue"], r["source_id"], r["run_id"]) for r in records} == {
        (FETCH_FAILED, "stavanger_parking", "7-1"),
        (FETCH_FAILED, "parkeringsregisteret", "7-1"),
    }
    assert "recorded in bronze/collect_issues/" in capsys.readouterr().out


def test_a_record_that_cannot_be_written_does_not_stop_the_other_sources(
    tmp_path, monkeypatch, capsys
):
    calls = []

    def fail(source, storage, client, now, run_id, keep):
        calls.append(source.id)
        raise CollectError(f"Download failed for {source.id}: 502")

    def unwritable(storage, issue, recorded_at):
        raise OSError("No space left on device")

    monkeypatch.setattr(collect_cli, "make_client", nullcontext)
    monkeypatch.setattr(collect_cli, "collect", fail)
    monkeypatch.setattr(collect_cli, "write_issue", unwritable)

    code = collect_cli.main(["run", "--storage", str(tmp_path), "--run-id", "7-1"])

    assert code == 1
    assert calls == ["stavanger_parking", "parkeringsregisteret"]
    assert capsys.readouterr().out.count("not recorded: No space left on device") == 2


def test_cli_records_failed_runs_from_a_file(tmp_path, capsys):
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps([failed_run("36890842735-1")]))
    storage = tmp_path / "data"

    assert (
        collect_cli.main(
            ["unrecorded-runs", "--storage", str(storage), "--run-id", "36890842735-1"]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == "36890842735-1"
    assert (
        collect_cli.main(["record-failed-runs", "--storage", str(storage), "--runs", str(runs)])
        == 0
    )
    assert "recorded 1 failed run(s)" in capsys.readouterr().out
    assert (
        collect_cli.main(
            ["unrecorded-runs", "--storage", str(storage), "--run-id", "36890842735-1"]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == ""


# --- bronze ------------------------------------------------------------------------------------


def test_records_load_into_bronze_once(tmp_path):
    raw, tables = tmp_path / "raw", str(tmp_path / "tables")
    write_issue(raw, fetch_failed(), NOW)
    record_failed_runs(raw, [failed_run("36642529839-1")], NOW)

    assert load_collect_issues(raw, tables, NOW) == 2
    assert load_collect_issues(raw, tables, NOW) == 0

    frame = pl.read_delta(table_path(tables, COLLECT_ISSUES_TABLE))
    assert frame.schema == pl.Schema(COLLECT_ISSUE_SCHEMA)
    assert sorted(frame["issue"].to_list()) == [FETCH_FAILED, RUN_FAILED]


def test_the_table_exists_before_any_failure(tmp_path):
    tables = str(tmp_path / "tables")

    assert load_collect_issues(tmp_path / "raw", tables, NOW) == 0
    assert DeltaTable.is_deltatable(table_path(tables, COLLECT_ISSUES_TABLE))
