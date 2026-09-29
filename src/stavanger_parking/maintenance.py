"""Delta table maintenance: compact small files, then remove files no longer referenced.

    python -m stavanger_parking.maintenance --tables-root DIR [--retention-days N] [--dry-run]

Every run of the pipeline adds files. Append-only tables (bronze, the silver fetch table, the
quality results) get a new small file per run, and tables that are rewritten on every run (the
derived silver tables, the dimensions and facts) leave their previous files behind. For every
table of the pipeline under `--tables-root`:

- `OPTIMIZE` compacts the table's small files into fewer, larger ones (a new table version; readers
  of older versions are unaffected)
- `VACUUM` deletes the files that no version newer than the retention period refers to

The retention defaults to 7 days, Delta's default: time travel and readers that started before a
rewrite keep working for that long. A shorter retention is refused unless `--force-short-retention`
is given, since it can break a reader that is still running.

The tables root and storage options are the same as for the builds, so the maintenance runs locally
and on either platform; scheduling it is the job of the pipeline entry point and the platform (#69).
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from deltalake import DeltaTable

from stavanger_parking.bronze.load import bronze_path, issues_path
from stavanger_parking.config import DEFAULT_CONFIG, load_sources
from stavanger_parking.tables import PIPELINE_TABLES, table_path

DEFAULT_RETENTION = timedelta(days=7)


class MaintenanceError(ValueError):
    """The maintenance was asked to do something unsafe."""


@dataclass(frozen=True)
class TableResult:
    table: str
    files_before: int
    files_after: int
    files_vacuumed: int


def pipeline_tables(tables_root: str, config_path=DEFAULT_CONFIG) -> list[str]:
    """Paths of every table the pipeline writes: each source's bronze tables, then the rest."""
    paths = []
    for source in load_sources(config_path):
        paths += [bronze_path(tables_root, source), issues_path(tables_root, source)]
    return paths + [table_path(tables_root, name) for name in PIPELINE_TABLES]


def maintain(
    paths: list[str],
    retention: timedelta = DEFAULT_RETENTION,
    dry_run: bool = False,
    force_short_retention: bool = False,
    storage_options=None,
) -> list[TableResult]:
    """Compact and vacuum each existing table; tables that do not exist yet are skipped."""
    if retention < DEFAULT_RETENTION and not force_short_retention:
        raise MaintenanceError(
            f"a retention of {retention} is shorter than {DEFAULT_RETENTION}; a reader that is "
            "still running could lose its files (force it with force_short_retention)"
        )
    results = []
    for path in paths:
        if not DeltaTable.is_deltatable(path, storage_options=storage_options):
            continue
        table = DeltaTable(path, storage_options=storage_options)
        before = len(table.file_uris())
        if not dry_run:
            table.optimize.compact()
            table = DeltaTable(path, storage_options=storage_options)
        vacuumed = table.vacuum(
            retention_hours=int(retention.total_seconds() // 3600),
            dry_run=dry_run,
            enforce_retention_duration=not force_short_retention,
        )
        results.append(
            TableResult(
                path.rstrip("/").rsplit("/", 1)[-1], before, len(table.file_uris()), len(vacuumed)
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.maintenance")
    parser.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--retention-days", type=float, default=DEFAULT_RETENTION.days)
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    parser.add_argument(
        "--force-short-retention",
        action="store_true",
        help="allow a retention under 7 days, even though a running reader could lose files",
    )
    args = parser.parse_args(argv)

    try:
        results = maintain(
            pipeline_tables(args.tables_root, args.config),
            timedelta(days=args.retention_days),
            dry_run=args.dry_run,
            force_short_retention=args.force_short_retention,
        )
    except MaintenanceError as e:
        print(e, file=sys.stderr)
        return 1
    verb = "would vacuum" if args.dry_run else "vacuumed"
    for r in results:
        print(
            f"{r.table}: {r.files_before} → {r.files_after} active file(s); "
            f"{verb} {r.files_vacuumed} unreferenced file(s)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
