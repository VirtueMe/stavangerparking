"""Build the gold tables.

    python -m stavanger_parking.gold.build --tables-root DIR [--mapping FILE]

Under `--tables-root`:

- `dim_date` and `dim_time` are generated (`gold.calendar`) and replaced on every run.
- `dim_parking_facility` is maintained with MERGE (`gold.facility`): facilities keep their
  surrogate keys, new facilities are inserted with the next free key, attributes are overwritten,
  and a facility no longer in silver is marked inactive, never deleted. It needs silver's fetch
  table; capacities come from `silver_parking_area` through the facility mapping, and are unknown
  until the register has been collected.

The facts come later (#13, #14).
"""

import argparse
import sys
from pathlib import Path

import polars as pl
from deltalake import DeltaTable

from stavanger_parking.facilities import load_facility_mapping
from stavanger_parking.gold.calendar import dim_date, dim_time
from stavanger_parking.gold.facility import (
    ATTRIBUTES,
    FACILITY_SCHEMA,
    UNKNOWN_KEY,
    assign_keys,
    facility_attributes,
)
from stavanger_parking.silver.register import AREA_SCHEMA
from stavanger_parking.tables import (
    AREA_TABLE,
    DATE_TABLE,
    FACILITY_TABLE,
    FETCH_TABLE,
    TIME_TABLE,
    table_path,
)

DEFAULT_MAPPING = Path("config/facility_mapping.json")


class BuildError(RuntimeError):
    """The gold tables could not be built."""


def build(tables_root: str, mapping_path=DEFAULT_MAPPING, storage_options=None) -> dict[str, int]:
    """Write the gold tables; returns the number of rows per table."""
    written = {}
    for name, frame in ((DATE_TABLE, dim_date()), (TIME_TABLE, dim_time())):
        _overwrite(frame, table_path(tables_root, name), storage_options)
        written[name] = frame.height
    written[FACILITY_TABLE] = build_facilities(tables_root, mapping_path, storage_options)
    return written


def build_facilities(tables_root: str, mapping_path, storage_options=None) -> int:
    """Merge the current facilities into `dim_parking_facility`; returns its number of rows."""
    fetch = table_path(tables_root, FETCH_TABLE)
    if not DeltaTable.is_deltatable(fetch, storage_options=storage_options):
        raise BuildError(f"no silver fetch table at {fetch}; build silver first")
    fetches = pl.read_delta(fetch, storage_options=storage_options)
    areas = _read_or_empty(table_path(tables_root, AREA_TABLE), AREA_SCHEMA, storage_options)
    attributes = facility_attributes(fetches, areas, load_facility_mapping(mapping_path))

    path = table_path(tables_root, FACILITY_TABLE)
    existing = _read_or_empty(path, FACILITY_SCHEMA, storage_options)
    rows = assign_keys(attributes, existing)
    if not DeltaTable.is_deltatable(path, storage_options=storage_options):
        rows.write_delta(path, storage_options=storage_options)
    else:
        (
            rows.write_delta(
                path,
                mode="merge",
                storage_options=storage_options,
                delta_merge_options={
                    "predicate": "t.facility_name = s.facility_name",
                    "source_alias": "s",
                    "target_alias": "t",
                },
            )
            .when_matched_update(updates={c: f"s.{c}" for c in ATTRIBUTES})
            .when_not_matched_insert_all()
            # Gone from silver altogether: keep the row and its key, mark it inactive
            .when_not_matched_by_source_update(
                predicate=f"t.facility_key <> {UNKNOWN_KEY}", updates={"is_active": "false"}
            )
            .execute()
        )
    return pl.read_delta(path, columns=["facility_key"], storage_options=storage_options).height


def _read_or_empty(path: str, schema: dict, storage_options) -> pl.DataFrame:
    if DeltaTable.is_deltatable(path, storage_options=storage_options):
        return pl.read_delta(path, storage_options=storage_options)
    return pl.DataFrame(schema=schema)


def _overwrite(frame: pl.DataFrame, path: str, storage_options) -> None:
    frame.write_delta(
        path,
        mode="overwrite",
        storage_options=storage_options,
        delta_write_options={"schema_mode": "overwrite"},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.gold.build")
    parser.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    args = parser.parse_args(argv)

    try:
        written = build(args.tables_root, args.mapping)
    except BuildError as e:
        print(e, file=sys.stderr)
        return 1
    for name, rows in written.items():
        print(f"{name}: {rows} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
