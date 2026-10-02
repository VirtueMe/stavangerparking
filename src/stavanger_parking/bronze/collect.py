"""Command line for the collector.

    python -m stavanger_parking.bronze.collect run --storage DIR --run-id ID [--source ID]
        [--mapping FILE]
    python -m stavanger_parking.bronze.collect gaps --storage DIR
    python -m stavanger_parking.bronze.collect unrecorded-runs --storage DIR --run-id ID [...]
    python -m stavanger_parking.bronze.collect record-failed-runs --storage DIR --runs FILE

`run` performs one scheduled collection for every polled source and prints what it did, including
skips and their reason. With `--source`, it collects only that source, which is how sources without
polling are collected. A source with a `filter` keeps only the records of the facility mapping
(`--mapping`, ADR 010). `gaps` lists periods where no snapshot of a polled
source arrived by the time the previous one said the next was due. `run` exits non-zero on failure,
after recording each failed source in storage (`bronze.collect_issues`).

`unrecorded-runs` prints which of the given run ids have no failure record yet, and
`record-failed-runs` records the failed runs in a JSON file that have none: how the orchestrator
records a run that failed without leaving a record, such as a failed push (#119).
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stavanger_parking.bronze.ckan import CkanError, make_client
from stavanger_parking.bronze.collect_issues import (
    FETCH_FAILED,
    CollectIssue,
    record_failed_runs,
    unrecorded_runs,
    write_issue,
)
from stavanger_parking.bronze.collector import CollectError, collect, find_gaps, read_sidecars
from stavanger_parking.config import DEFAULT_CONFIG, load_sources
from stavanger_parking.facilities import DEFAULT_MAPPING, MappingError, load_facility_mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.bronze.collect")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="collect a snapshot for every source that is due")
    run.add_argument(
        "--storage", type=Path, required=True, help="storage root, e.g. the data branch"
    )
    run.add_argument("--run-id", required=True)
    run.add_argument("--source", help="collect only this source (default: every polled source)")
    run.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)

    gaps = commands.add_parser("gaps", help="list gaps in collection")
    gaps.add_argument("--storage", type=Path, required=True)
    gaps.add_argument("--tolerance-minutes", type=float, default=10)

    unrecorded = commands.add_parser(
        "unrecorded-runs", help="print the run ids that have no failure record yet"
    )
    unrecorded.add_argument("--storage", type=Path, required=True)
    unrecorded.add_argument("--run-id", action="append", default=[], dest="run_ids")

    failed = commands.add_parser(
        "record-failed-runs", help="record failed runs that left no record of their own"
    )
    failed.add_argument("--storage", type=Path, required=True)
    failed.add_argument(
        "--runs",
        type=Path,
        required=True,
        help='JSON list of {"run_id", "started_at", "failed_step", "url"}',
    )

    args = parser.parse_args(argv)
    if args.command == "unrecorded-runs":
        print("\n".join(unrecorded_runs(args.storage, args.run_ids)))
        return 0
    if args.command == "record-failed-runs":
        runs = json.loads(args.runs.read_text(encoding="utf-8"))
        written = record_failed_runs(args.storage, runs, datetime.now(UTC))
        _report([f"recorded {len(written)} failed run(s)", *(f"  {p}" for p in written)])
        return 0
    sources = load_sources(args.config)
    polled = [s for s in sources if s.polling is not None]
    if args.command == "gaps":
        return _gaps(polled, args.storage, timedelta(minutes=args.tolerance_minutes))
    if args.source is None:
        return _run(polled, args.storage, args.run_id, args.mapping)
    named = [s for s in sources if s.id == args.source]
    if not named:
        print(f"no source {args.source!r} in {args.config}", file=sys.stderr)
        return 1
    return _run(named, args.storage, args.run_id, args.mapping)


def _run(sources, storage: Path, run_id: str, mapping_path: Path) -> int:
    lines, failed = [], False
    with make_client() as client:
        for source in sources:
            now = datetime.now(UTC)
            try:
                keep = _keep(source, mapping_path)
                outcome = collect(source, storage, client, now, run_id, keep)
            except (CkanError, CollectError, MappingError) as e:
                failed = True
                lines.append(f"{source.id}: FAILED: {e}")
                # The failure is data too: stored with this run's files, so bronze has it (#119)
                issue = CollectIssue(FETCH_FAILED, run_id, now, str(e), source.id)
                try:
                    lines.append(f"  recorded in {write_issue(storage, issue, now)}")
                except OSError as record_error:
                    # Recording must never stop the other sources; the log still has the failure
                    lines.append(f"  not recorded: {record_error}")
                continue
            d = outcome.decision
            action = f"fetched -> {outcome.raw_file}" if d.fetch else "skipped"
            lines.append(f"{source.id}: {action} ({d.mode} mode: {d.reason})")
    _report(lines)
    return 1 if failed else 0


def _keep(source, mapping_path: Path) -> frozenset[str] | None:
    """The values a source's filter keeps, from the facility mapping; None without a filter."""
    if source.filter is None:
        return None
    mapping = load_facility_mapping(mapping_path)
    return frozenset(str(getattr(m, source.filter.mapping_field)) for m in mapping)


def _gaps(sources, storage: Path, tolerance: timedelta) -> int:
    lines = []
    for source in sources:
        found = list(find_gaps(read_sidecars(storage, source), tolerance, datetime.now(UTC)))
        lines.append(f"{source.id}: {len(found)} gap(s)")
        for gap in found:
            until = gap.next_snapshot.isoformat() if gap.next_snapshot else "now (still open)"
            lines.append(
                f"  after {gap.after.isoformat()}, due {gap.expected_by.isoformat()}, until {until}"
            )
    _report(lines)
    return 0


def _report(lines: list[str]) -> None:
    text = "\n".join(lines)
    print(text)
    # On GitHub Actions, the same text goes into the run summary
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("```\n" + text + "\n```\n")


def entry() -> None:
    """The console script: exits with `main`'s code, however the caller starts it.

    Success returns instead of exiting: a Databricks wheel task runs the function in IPython,
    which reports any `SystemExit` as a failed task, even with code 0 (#107).
    """
    if code := main():
        sys.exit(code)


if __name__ == "__main__":
    entry()
