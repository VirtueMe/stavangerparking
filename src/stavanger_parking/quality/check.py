"""Run the data quality checks, store their results, and fail on a critical one.

    python -m stavanger_parking.quality.check --tables-root DIR [--config FILE] [--mapping FILE]

Runs after silver and gold (`quality.checks`). Every run appends its results to
`quality_check_results` under `--tables-root`, one row per check (per facility for checks about
facilities), prints them, and adds them to the GitHub Actions run summary. The results are stored
first, so a failed run still leaves its evidence.

The exit code is 1 if a critical check failed: that is what stops the pipeline, locally now and
through orchestration later (#19). The checks never stop collection: the source only shows its
current state, so a stopped collector would lose history for good.
"""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from deltalake import DeltaTable

from stavanger_parking.bronze.load import ISSUE_SCHEMA, bronze_path, issues_path
from stavanger_parking.config import DEFAULT_CONFIG, load_sources
from stavanger_parking.facilities import DEFAULT_MAPPING, load_facility_mapping
from stavanger_parking.quality import checks
from stavanger_parking.quality.checks import CRITICAL, Result
from stavanger_parking.tables import (
    AREA_TABLE,
    FACILITY_TABLE,
    FETCH_TABLE,
    FRESHNESS_TABLE,
    HOURLY_TABLE,
    PARKING_SOURCE_ID,
    QUALITY_TABLE,
    QUARANTINE_TABLE,
    table_path,
)

RESULT_SCHEMA = {
    "checked_at": pl.Datetime("us", "UTC"),
    "snapshot": pl.String,
    "check": pl.String,
    "severity": pl.String,
    "passed": pl.Boolean,
    "subject": pl.String,
    "detail": pl.String,
}


class CheckError(RuntimeError):
    """The checks could not run."""


def run_checks(tables_root: str, mapping_path, config_path, storage_options=None):
    """The results of every check on the latest data, and the snapshot they are about."""

    def read(path: str, schema: dict | None = None) -> pl.DataFrame:
        if DeltaTable.is_deltatable(path, storage_options=storage_options):
            return pl.read_delta(path, storage_options=storage_options)
        if schema is None:
            raise CheckError(f"no table at {path}; build bronze, silver and gold first")
        return pl.DataFrame(schema=schema)

    parking = next(s for s in load_sources(config_path) if s.id == PARKING_SOURCE_ID)
    mapping = load_facility_mapping(mapping_path)
    bronze = read(bronze_path(tables_root, parking))
    load_issues = read(issues_path(tables_root, parking), ISSUE_SCHEMA)
    snapshot = checks.latest_snapshot(bronze, load_issues)
    if snapshot is None:
        raise CheckError("the parking source has no snapshots yet")

    fetches = read(table_path(tables_root, FETCH_TABLE))
    facilities = read(table_path(tables_root, FACILITY_TABLE))
    snapshot_fetches = fetches.filter(pl.col("raw_file") == snapshot)
    capacities = dict(facilities.select("facility_name", "capacity").rows())
    active_keys = facilities.filter("is_active")["facility_key"].to_list()
    latest_fetch = fetches["ingested_at"].max()

    results = [
        *checks.schema_drift(bronze, snapshot),
        checks.empty_snapshot(snapshot, load_issues, fetches),
        # An empty snapshot is reported once, by `empty_snapshot`, not again per facility
        *(
            [
                checks.facility_count(snapshot_fetches, mapping),
                *checks.facility_checks(snapshot_fetches, capacities, mapping),
            ]
            if snapshot_fetches.height
            else []
        ),
        *checks.quarantine_checks(read(table_path(tables_root, QUARANTINE_TABLE)), snapshot),
        checks.register_missing_areas(
            read(
                table_path(tables_root, AREA_TABLE),
                {"register_id": pl.Int64, "ingested_at": pl.Datetime("us", "UTC")},
            ),
            mapping,
        ),
        *checks.source_stale(read(table_path(tables_root, FRESHNESS_TABLE)), snapshot),
    ]
    if latest_fetch is not None:
        hourly = read(table_path(tables_root, HOURLY_TABLE))
        results.append(checks.low_coverage(hourly, active_keys, latest_fetch))
    return snapshot, results


def store(results: list[Result], snapshot: str, tables_root: str, now, storage_options=None):
    frame = pl.DataFrame(
        [
            {
                "checked_at": now,
                "snapshot": snapshot,
                "check": r.check,
                "severity": r.severity,
                "passed": r.passed,
                "subject": r.subject,
                "detail": r.detail,
            }
            for r in results
        ],
        schema=RESULT_SCHEMA,
    )
    frame.write_delta(
        table_path(tables_root, QUALITY_TABLE), mode="append", storage_options=storage_options
    )
    return frame


def report(results: list[Result], snapshot: str) -> str:
    failed = [r for r in results if not r.passed]
    lines = [f"quality: {len(results)} result(s) for {snapshot}, {len(failed)} failed"]
    for r in sorted(failed, key=lambda r: (r.severity != CRITICAL, r.check, r.subject or "")):
        subject = f" {r.subject}:" if r.subject else ""
        lines.append(f"  {r.severity.upper()} {r.check}{subject} {r.detail}")
    return "\n".join(lines)


@dataclass(frozen=True)
class Outcome:
    report: str
    critical: bool


def run(tables_root: str, mapping_path, config_path, now, storage_options=None) -> Outcome:
    """Run the checks and store their results, before saying whether a critical one failed."""
    snapshot, results = run_checks(tables_root, mapping_path, config_path, storage_options)
    store(results, snapshot, tables_root, now, storage_options)
    critical = any(r.severity == CRITICAL and not r.passed for r in results)
    return Outcome(report(results, snapshot), critical)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.quality.check")
    parser.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    args = parser.parse_args(argv)

    try:
        outcome = run(args.tables_root, args.mapping, args.config, datetime.now(UTC))
    except CheckError as e:
        print(e, file=sys.stderr)
        return 1
    print(outcome.report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("```\n" + outcome.report + "\n```\n")
    return 1 if outcome.critical else 0


if __name__ == "__main__":
    sys.exit(main())
