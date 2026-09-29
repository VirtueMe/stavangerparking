"""Rebuild the silver tables of the parking source from bronze.

    python -m stavanger_parking.silver.build --tables-root DIR

Reads the source's bronze table under `--tables-root`, parses it (`silver.parse`) and replaces
`silver_parking_reading` and `silver_quarantine` next to it. Every run rebuilds both tables from
all of bronze, so the result depends on bronze alone; incremental runs and deduplication of
repeated readings come later (#9).
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from deltalake import DeltaTable

from stavanger_parking.bronze.load import DEFAULT_CONFIG, bronze_path
from stavanger_parking.config import Source, load_sources
from stavanger_parking.silver.parse import parse_readings

SOURCE_ID = "stavanger_parking"
READING_TABLE = "silver_parking_reading"
QUARANTINE_TABLE = "silver_quarantine"


class BuildError(RuntimeError):
    """The silver tables could not be built."""


@dataclass(frozen=True)
class BuildResult:
    bronze_rows: int
    readings: int
    quarantined: int
    excluded: int


def table_path(tables_root: str, name: str) -> str:
    return f"{str(tables_root).rstrip('/')}/{name}"


def build(source: Source, tables_root: str, storage_options=None) -> BuildResult:
    """Replace the silver tables with the parsed contents of the source's bronze table."""
    bronze = bronze_path(tables_root, source)
    if not DeltaTable.is_deltatable(bronze, storage_options=storage_options):
        raise BuildError(f"no bronze table at {bronze}; load bronze first")
    rows = pl.read_delta(bronze, storage_options=storage_options)
    readings, quarantine = parse_readings(rows)

    for frame, name in ((readings, READING_TABLE), (quarantine, QUARANTINE_TABLE)):
        frame.write_delta(
            table_path(tables_root, name),
            mode="overwrite",
            storage_options=storage_options,
            delta_write_options={"schema_mode": "overwrite"},
        )
    excluded = quarantine.filter("reading_excluded").select("raw_file", "record_index").n_unique()
    return BuildResult(rows.height, readings.height, quarantine.height, excluded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.silver.build")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    args = parser.parse_args(argv)

    source = next((s for s in load_sources(args.config) if s.id == SOURCE_ID), None)
    if source is None:
        print(f"no source {SOURCE_ID!r} in {args.config}", file=sys.stderr)
        return 1
    try:
        r = build(source, args.tables_root)
    except BuildError as e:
        print(e, file=sys.stderr)
        return 1
    print(
        f"{source.id}: {r.bronze_rows} bronze row(s) → {r.readings} reading(s); "
        f"{r.quarantined} value(s) quarantined, {r.excluded} reading(s) left out"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
