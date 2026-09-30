"""Build the gold tables.

    python -m stavanger_parking.gold.build --tables-root DIR [--mapping FILE] [--config FILE]

Under `--tables-root`:

- `dim_date` and `dim_time` are generated (`gold.calendar`) and replaced on every run.
- `dim_parking_facility` is maintained with MERGE (`gold.facility`): facilities keep their
  surrogate keys, new facilities are inserted with the next free key, attributes are overwritten,
  and a facility no longer in silver is marked inactive, never deleted. It needs silver's fetch
  table; capacities come from `silver_parking_area` through the facility mapping, and are unknown
  until the register has been collected.
- `fact_parking_availability` is rebuilt from silver's readings and the dimensions
  (`gold.availability`); a reading whose date is outside `dim_date` stops the build.
- `fact_suggested_price` is rebuilt from the hourly fact, the tariffs and the pricing rules
  (`gold.pricing`).
- `fact_parking_hourly` is rebuilt from the availability fact and silver's stale periods
  (`gold.hourly`); a reading covers at most the parking source's slow polling interval past its
  last fetch.
"""

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import polars as pl
from deltalake import DeltaTable

from stavanger_parking.config import DEFAULT_CONFIG, load_sources
from stavanger_parking.facilities import DEFAULT_MAPPING, load_facility_mapping
from stavanger_parking.gold.availability import availability
from stavanger_parking.gold.calendar import FIRST_DATE, LAST_DATE, dim_date, dim_time
from stavanger_parking.gold.facility import (
    ATTRIBUTES,
    FACILITY_SCHEMA,
    UNKNOWN_KEY,
    assign_keys,
    facility_attributes,
)
from stavanger_parking.gold.hourly import hourly
from stavanger_parking.gold.pricing import (
    DEFAULT_RULES,
    DEFAULT_TARIFFS,
    load_rules,
    load_tariffs,
    suggested_prices,
)
from stavanger_parking.silver.register import AREA_SCHEMA
from stavanger_parking.tables import (
    AREA_TABLE,
    AVAILABILITY_TABLE,
    DATE_TABLE,
    FACILITY_TABLE,
    FETCH_TABLE,
    HOURLY_TABLE,
    PARKING_SOURCE_ID,
    READING_TABLE,
    STALE_PERIOD_TABLE,
    SUGGESTED_PRICE_TABLE,
    TIME_TABLE,
    table_path,
)


class BuildError(RuntimeError):
    """The gold tables could not be built."""


def build(
    tables_root: str,
    mapping_path=DEFAULT_MAPPING,
    config_path=DEFAULT_CONFIG,
    tariffs_path=DEFAULT_TARIFFS,
    rules_path=DEFAULT_RULES,
    storage_options=None,
) -> dict[str, int]:
    """Write the gold tables; returns the number of rows per table."""
    written = {}
    for name, frame in ((DATE_TABLE, dim_date()), (TIME_TABLE, dim_time())):
        _overwrite(frame, table_path(tables_root, name), storage_options)
        written[name] = frame.height
    written[FACILITY_TABLE] = build_facilities(tables_root, mapping_path, storage_options)
    written[AVAILABILITY_TABLE] = build_availability(tables_root, storage_options)
    parking = next(s for s in load_sources(config_path) if s.id == PARKING_SOURCE_ID)
    max_gap = timedelta(minutes=parking.polling.slow_interval_minutes)
    written[HOURLY_TABLE] = build_hourly(tables_root, max_gap, storage_options)
    written[SUGGESTED_PRICE_TABLE] = build_prices(
        tables_root, tariffs_path, rules_path, storage_options
    )
    return written


def build_prices(tables_root: str, tariffs_path, rules_path, storage_options=None) -> int:
    """Replace `fact_suggested_price`; returns its number of rows."""
    rows = suggested_prices(
        pl.read_delta(table_path(tables_root, HOURLY_TABLE), storage_options=storage_options),
        pl.read_delta(table_path(tables_root, FACILITY_TABLE), storage_options=storage_options),
        load_tariffs(tariffs_path),
        load_rules(rules_path),
    )
    _overwrite(rows, table_path(tables_root, SUGGESTED_PRICE_TABLE), storage_options)
    return rows.height


def build_hourly(tables_root: str, max_gap: timedelta, storage_options=None) -> int:
    """Replace `fact_parking_hourly`; returns its number of rows."""
    fact = pl.read_delta(
        table_path(tables_root, AVAILABILITY_TABLE), storage_options=storage_options
    )
    periods = pl.read_delta(
        table_path(tables_root, STALE_PERIOD_TABLE), storage_options=storage_options
    ).filter(pl.col("source_id") == PARKING_SOURCE_ID)
    rows = hourly(fact, periods, max_gap)
    _overwrite(rows, table_path(tables_root, HOURLY_TABLE), storage_options)
    return rows.height


def build_availability(tables_root: str, storage_options=None) -> int:
    """Replace `fact_parking_availability`; returns its number of rows."""

    def read(name: str) -> pl.DataFrame:
        return pl.read_delta(table_path(tables_root, name), storage_options=storage_options)

    fact = availability(
        read(READING_TABLE), read(FETCH_TABLE), read(FACILITY_TABLE), read(STALE_PERIOD_TABLE)
    )
    first, last = (int(d.strftime("%Y%m%d")) for d in (FIRST_DATE, LAST_DATE))
    outside = fact.filter(~pl.col("date_key").is_between(first, last))
    if outside.height:
        raise BuildError(
            f"{outside.height} reading(s) fall outside dim_date ({FIRST_DATE} to {LAST_DATE}), "
            f"e.g. date_key {outside['date_key'][0]}; extend the range in gold/calendar.py"
        )
    _overwrite(fact, table_path(tables_root, AVAILABILITY_TABLE), storage_options)
    return fact.height


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


def run(
    tables_root: str,
    mapping_path=DEFAULT_MAPPING,
    config_path=DEFAULT_CONFIG,
    tariffs_path=DEFAULT_TARIFFS,
    rules_path=DEFAULT_RULES,
    storage_options=None,
) -> list[str]:
    """Write the gold tables; returns the report, a line per table."""
    written = build(
        tables_root, mapping_path, config_path, tariffs_path, rules_path, storage_options
    )
    return [f"{name}: {rows} row(s)" for name, rows in written.items()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.gold.build")
    parser.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--tariffs", type=Path, default=DEFAULT_TARIFFS)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    args = parser.parse_args(argv)

    try:
        lines = run(args.tables_root, args.mapping, args.config, args.tariffs, args.rules)
    except BuildError as e:
        print(e, file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
