"""Build the silver tables of the parking source from bronze, incrementally or from scratch.

    python -m stavanger_parking.silver.build --tables-root DIR [--rebuild]

Next to the source's bronze table under `--tables-root`:

- `silver_parking_fetch`: every parsed bronze row, one per facility per fetch; append-only
- `silver_parking_reading`: one row per facility per source reading, the first fetch of each
- `silver_quarantine`: values that could not be parsed, and values of conflicting duplicates

By default a run parses only the bronze rows not yet handled: rows already in the fetch table, or
left out and recorded in quarantine. Parse quarantine rows are inserted before the fetches are
appended, and inserted only if not already there, so a run that stops halfway is completed by the
next one without losing or repeating anything. Readings and conflicts are then derived from all
fetches, which makes the result of any sequence of incremental runs the same as a rebuild.

`--rebuild` replaces all three tables from all of bronze, for example after a change to the parsing.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from deltalake import DeltaTable

from stavanger_parking.bronze.load import DEFAULT_CONFIG, bronze_path
from stavanger_parking.config import Source, load_sources
from stavanger_parking.silver.dedup import CONFLICTING_DUPLICATE, deduplicate
from stavanger_parking.silver.parse import parse_readings

SOURCE_ID = "stavanger_parking"
FETCH_TABLE = "silver_parking_fetch"
READING_TABLE = "silver_parking_reading"
QUARANTINE_TABLE = "silver_quarantine"

ROW = ["raw_file", "record_index"]
QUARANTINE_KEY = [*ROW, "field", "reason"]


class BuildError(RuntimeError):
    """The silver tables could not be built."""


@dataclass(frozen=True)
class BuildResult:
    new_rows: int
    fetches: int
    quarantined: int
    excluded: int
    readings: int
    conflicts: int


def table_path(tables_root: str, name: str) -> str:
    return f"{str(tables_root).rstrip('/')}/{name}"


def handled_rows(fetch: str, quarantine: str, storage_options=None) -> pl.DataFrame:
    """Bronze rows already handled: in the fetch table, or left out and recorded in quarantine."""
    handled = [pl.DataFrame(schema={"raw_file": pl.String, "record_index": pl.Int32})]
    if DeltaTable.is_deltatable(fetch, storage_options=storage_options):
        handled.append(pl.read_delta(fetch, columns=ROW, storage_options=storage_options))
    if DeltaTable.is_deltatable(quarantine, storage_options=storage_options):
        excluded = pl.read_delta(
            quarantine, columns=[*ROW, "reading_excluded"], storage_options=storage_options
        )
        handled.append(excluded.filter("reading_excluded").select(ROW))
    return pl.concat(handled).unique()


def build(
    source: Source, tables_root: str, rebuild: bool = False, storage_options=None
) -> BuildResult:
    """Bring the silver tables up to date with the source's bronze table."""
    bronze = bronze_path(tables_root, source)
    if not DeltaTable.is_deltatable(bronze, storage_options=storage_options):
        raise BuildError(f"no bronze table at {bronze}; load bronze first")
    fetch, reading, quarantine = (
        table_path(tables_root, name) for name in (FETCH_TABLE, READING_TABLE, QUARANTINE_TABLE)
    )

    rows = pl.read_delta(bronze, storage_options=storage_options)
    if not rebuild:
        rows = rows.join(handled_rows(fetch, quarantine, storage_options), on=ROW, how="anti")
    fetches, parse_quarantine = parse_readings(rows)

    # Quarantine before fetches: a row counts as handled once it is in the fetch table
    if rebuild:
        _overwrite(parse_quarantine, quarantine, storage_options)
        _overwrite(fetches, fetch, storage_options)
    else:
        _insert_new(parse_quarantine, quarantine, QUARANTINE_KEY, storage_options)
        _append(fetches, fetch, storage_options)

    readings, conflicts = deduplicate(pl.read_delta(fetch, storage_options=storage_options))
    _overwrite(readings, reading, storage_options)
    conflicts.write_delta(
        quarantine,
        mode="overwrite",
        storage_options=storage_options,
        delta_write_options={"predicate": f"reason = '{CONFLICTING_DUPLICATE}'"},
    )

    excluded = parse_quarantine.filter("reading_excluded").select(ROW).n_unique()
    return BuildResult(
        new_rows=rows.height,
        fetches=fetches.height,
        quarantined=parse_quarantine.height,
        excluded=excluded,
        readings=readings.height,
        conflicts=conflicts.height,
    )


def _overwrite(frame: pl.DataFrame, path: str, storage_options) -> None:
    frame.write_delta(
        path,
        mode="overwrite",
        storage_options=storage_options,
        delta_write_options={"schema_mode": "overwrite"},
    )


def _append(frame: pl.DataFrame, path: str, storage_options) -> None:
    # An empty append would only add a commit, except that it creates the table on a first run
    if frame.is_empty() and DeltaTable.is_deltatable(path, storage_options=storage_options):
        return
    frame.write_delta(path, mode="append", storage_options=storage_options)


def _insert_new(frame: pl.DataFrame, path: str, key: list[str], storage_options) -> None:
    """Insert the rows whose key is not in the table yet; running it twice changes nothing."""
    if not DeltaTable.is_deltatable(path, storage_options=storage_options):
        frame.write_delta(path, storage_options=storage_options)
        return
    if frame.is_empty():
        return
    predicate = " AND ".join(f"s.{k} = t.{k}" for k in key)
    frame.write_delta(
        path,
        mode="merge",
        storage_options=storage_options,
        delta_merge_options={"predicate": predicate, "source_alias": "s", "target_alias": "t"},
    ).when_not_matched_insert_all().execute()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.silver.build")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    parser.add_argument(
        "--rebuild", action="store_true", help="replace the silver tables from all of bronze"
    )
    args = parser.parse_args(argv)

    source = next((s for s in load_sources(args.config) if s.id == SOURCE_ID), None)
    if source is None:
        print(f"no source {SOURCE_ID!r} in {args.config}", file=sys.stderr)
        return 1
    try:
        r = build(source, args.tables_root, rebuild=args.rebuild)
    except BuildError as e:
        print(e, file=sys.stderr)
        return 1
    print(
        f"{source.id}: {'rebuilt from' if args.rebuild else 'parsed'} {r.new_rows} bronze row(s) "
        f"→ {r.fetches} fetch(es), {r.quarantined} value(s) quarantined, "
        f"{r.excluded} reading(s) left out; {r.readings} reading(s) after deduplication, "
        f"{r.conflicts} conflicting value(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
