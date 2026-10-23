"""The collector's outage records: a source's server errors, loaded into bronze (#124).

    python -m stavanger_parking.bronze.outages --raw-root DIR --tables-root DIR

A source that answers with a server error is down, and nothing here can fix that, so the collector
does not fail the run: it leaves a record, `<source dir>/unavailable/<time>.json`, at most one an
hour (`bronze.collector`). The records sit outside the date folders, so `bronze.load` never reads
them as snapshots. This module loads them into `bronze_source_outages`, one row per record, for
every source, so an outage shows in the data and in the quality checks, not only on the branch.

Records never change, so the table is append-only and a record is loaded once. A file that is not
an outage record is reported and not loaded.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from deltalake import DeltaTable

from stavanger_parking.bronze.collector import unavailable_dir
from stavanger_parking.config import DEFAULT_CONFIG, load_sources
from stavanger_parking.tables import OUTAGES_TABLE, table_path

OUTAGES_SCHEMA = {
    "source_id": pl.String,
    "observed_at": pl.Datetime("us", "UTC"),
    "source_url": pl.String,
    "run_id": pl.String,
    "http_status": pl.Int32,
    "raw_file": pl.String,
    "ingested_at": pl.Datetime("us", "UTC"),
}


def record_files(raw_root: Path, config_path) -> list[str]:
    """Every source's outage records under the root, relative to it, oldest first."""
    return sorted(
        p.relative_to(raw_root).as_posix()
        for source in load_sources(config_path)
        for p in (raw_root / unavailable_dir(source.raw_path)).glob("*.json")
    )


def read_record(raw_root: Path, raw_file: str, now: datetime) -> dict | None:
    """The row for one record file, or None if it is not an outage record."""
    try:
        record = json.loads((raw_root / raw_file).read_text(encoding="utf-8"))
        return {
            "source_id": str(record["source_id"]),
            "observed_at": datetime.fromisoformat(record["observed_at"]),
            "source_url": str(record["source_url"]),
            "run_id": str(record["run_id"]),
            "http_status": int(record["http_status"]),
            "raw_file": raw_file,
            "ingested_at": now,
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run(
    config_path, raw_root: Path, tables_root: str, now: datetime, storage_options=None
) -> list[str]:
    """Load the outage records the table lacks; returns the report, a line per file not loaded."""
    path = table_path(tables_root, OUTAGES_TABLE)
    loaded: set[str] = set()
    if DeltaTable.is_deltatable(path, storage_options=storage_options):
        table = pl.read_delta(path, columns=["raw_file"], storage_options=storage_options)
        loaded = set(table["raw_file"].to_list())
    rows, rejected = [], []
    new = [f for f in record_files(raw_root, config_path) if f not in loaded]
    for raw_file in new:
        row = read_record(raw_root, raw_file, now)
        if row is None:
            rejected.append(raw_file)
        else:
            rows.append(row)
    if rows:
        pl.DataFrame(rows, schema=OUTAGES_SCHEMA).write_delta(
            path, mode="append", storage_options=storage_options
        )
    skipped = len(loaded)
    return [
        f"outages: loaded {len(rows)} record(s), skipped {skipped} already loaded",
        *[f"  not an outage record: {f}" for f in rejected],
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.bronze.outages")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--raw-root", type=Path, required=True, help="root of the raw files")
    parser.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    args = parser.parse_args(argv)
    for line in run(args.config, args.raw_root, args.tables_root, datetime.now(UTC)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
