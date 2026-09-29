"""Build the gold tables.

    python -m stavanger_parking.gold.build --tables-root DIR

Writes `dim_date` and `dim_time` under `--tables-root`, replacing them. Both are generated, not
derived from the data (`gold.calendar`), so every run writes the same tables. The facility
dimension and the facts come later (#12, #13, #14).
"""

import argparse
import sys

import polars as pl

from stavanger_parking.gold.calendar import dim_date, dim_time
from stavanger_parking.tables import table_path

DATE_TABLE = "dim_date"
TIME_TABLE = "dim_time"


def build(tables_root: str, storage_options=None) -> dict[str, int]:
    """Write the gold tables; returns the number of rows per table."""
    written = {}
    for name, frame in ((DATE_TABLE, dim_date()), (TIME_TABLE, dim_time())):
        _overwrite(frame, table_path(tables_root, name), storage_options)
        written[name] = frame.height
    return written


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
    args = parser.parse_args(argv)

    for name, rows in build(args.tables_root).items():
        print(f"{name}: {rows} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
