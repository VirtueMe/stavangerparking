"""Build the silver tables of the parking source from bronze, incrementally or from scratch.

    python -m stavanger_parking.silver.build --tables-root DIR [--rebuild]

Next to the source's bronze table under `--tables-root`:

- `silver_parking_fetch`: every parsed bronze row, one per facility per fetch; append-only
- `silver_parking_reading`: one row per facility per source reading, the first fetch of each
- `silver_quarantine`: values that could not be parsed, and values of conflicting duplicates
- `silver_snapshot_freshness` and `silver_stale_period`: how old the source's data was at each
  fetch, and the periods it was stale (`silver.freshness`)
- `silver_parking_area`: the parking areas in the national parking register, one typed row per area
  per register snapshot (`silver.register`), rebuilt from the register's bronze table on every run;
  skipped until the register has been collected

By default a run parses only the bronze rows not yet handled: rows already in the fetch table, or
left out and recorded in quarantine. Parse quarantine rows are inserted before the fetches are
appended, and inserted only if not already there, so a run that stops halfway is completed by the
next one without losing or repeating anything. Readings and conflicts are then derived from all
fetches, and so are the freshness tables, which makes the result of any sequence of incremental
runs the same as a rebuild.

`--rebuild` replaces all the parking tables from all of bronze, for example after a change to the
parsing.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import polars as pl
from deltalake import DeltaTable

from stavanger_parking.bronze.load import bronze_path
from stavanger_parking.config import DEFAULT_CONFIG, Source, load_sources
from stavanger_parking.silver.dedup import CONFLICTING_DUPLICATE, deduplicate
from stavanger_parking.silver.freshness import freshness
from stavanger_parking.silver.parse import parse_readings
from stavanger_parking.silver.register import parse_areas
from stavanger_parking.tables import (
    AREA_TABLE,
    FETCH_TABLE,
    FRESHNESS_TABLE,
    PARKING_SOURCE_ID,
    QUARANTINE_TABLE,
    READING_TABLE,
    STALE_PERIOD_TABLE,
    table_path,
)

REGISTER_SOURCE_ID = "parkeringsregisteret"

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
    stale_snapshots: int
    stale_periods: int


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

    all_fetches = pl.read_delta(fetch, storage_options=storage_options)
    readings, conflicts = deduplicate(all_fetches)
    _overwrite(readings, reading, storage_options)
    stale_after = timedelta(minutes=source.freshness.stale_after_minutes)
    snapshots, periods = freshness(all_fetches, stale_after)
    _overwrite(snapshots, table_path(tables_root, FRESHNESS_TABLE), storage_options)
    _overwrite(periods, table_path(tables_root, STALE_PERIOD_TABLE), storage_options)
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
        stale_snapshots=snapshots.filter("is_stale").height,
        stale_periods=periods.height,
    )


@dataclass(frozen=True)
class RegisterResult:
    bronze_rows: int
    areas: int
    quarantined: int


def build_register(source: Source, tables_root: str, storage_options=None) -> RegisterResult | None:
    """Replace `silver_parking_area` from the register's bronze table; None if it has none yet."""
    bronze = bronze_path(tables_root, source)
    if not DeltaTable.is_deltatable(bronze, storage_options=storage_options):
        return None
    rows = pl.read_delta(bronze, storage_options=storage_options)
    areas, quarantine = parse_areas(rows)
    _overwrite(areas, table_path(tables_root, AREA_TABLE), storage_options)

    # The register's quarantine rows are derived like its areas: replace them, and only them
    path = table_path(tables_root, QUARANTINE_TABLE)
    if DeltaTable.is_deltatable(path, storage_options=storage_options):
        quarantine.write_delta(
            path,
            mode="overwrite",
            storage_options=storage_options,
            delta_write_options={"predicate": f"source_id = '{source.id}'"},
        )
    else:
        quarantine.write_delta(path, storage_options=storage_options)
    return RegisterResult(rows.height, areas.height, quarantine.height)


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


def run(config_path, tables_root: str, rebuild: bool = False, storage_options=None) -> list[str]:
    """Build the parking source's silver tables, and the register's once it is collected."""
    sources = {s.id: s for s in load_sources(config_path)}
    source = sources.get(PARKING_SOURCE_ID)
    if source is None:
        raise BuildError(f"no source {PARKING_SOURCE_ID!r} in {config_path}")
    r = build(source, tables_root, rebuild, storage_options)
    lines = [
        f"{source.id}: {'rebuilt from' if rebuild else 'parsed'} {r.new_rows} bronze row(s) "
        f"→ {r.fetches} fetch(es), {r.quarantined} value(s) quarantined, "
        f"{r.excluded} reading(s) left out; {r.readings} reading(s) after deduplication, "
        f"{r.conflicts} conflicting value(s); {r.stale_snapshots} stale snapshot(s) "
        f"in {r.stale_periods} stale period(s)"
    ]
    if REGISTER_SOURCE_ID in sources:
        register = build_register(sources[REGISTER_SOURCE_ID], tables_root, storage_options)
        if register is None:
            lines.append(
                f"{REGISTER_SOURCE_ID}: no bronze table yet; collect and load the register first"
            )
        else:
            lines.append(
                f"{REGISTER_SOURCE_ID}: {register.bronze_rows} bronze row(s) → "
                f"{register.areas} area(s), {register.quarantined} value(s) quarantined"
            )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.silver.build")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    parser.add_argument(
        "--rebuild", action="store_true", help="replace the silver tables from all of bronze"
    )
    args = parser.parse_args(argv)

    try:
        lines = run(args.config, args.tables_root, rebuild=args.rebuild)
    except BuildError as e:
        print(e, file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
