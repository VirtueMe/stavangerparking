"""Publish the tables the report reads as Unity Catalog tables. Databricks only.

    publish.py --catalog CATALOG --schema SCHEMA --tables-root /Volumes/CATALOG/SCHEMA/tables

The pipeline writes every table with delta-rs to a volume. Delta files in a volume are not Unity
Catalog tables, and Power BI's Databricks connector reads tables (#72), so this task replaces each
published table with the current content of its Delta table. The tables are small (ADR 006); a
full copy on every run is simpler than tracking what changed.

Platform code: it uses Spark, which the `stavanger_parking` package never does (ADR 011).
"""

import argparse

from deltalake import DeltaTable
from pyspark.sql import SparkSession

from stavanger_parking.tables import (
    AVAILABILITY_TABLE,
    DATE_TABLE,
    FACILITY_TABLE,
    HOURLY_TABLE,
    QUALITY_TABLE,
    SOURCE_STALE_PERIOD_TABLE,
    SUGGESTED_PRICE_TABLE,
    TIME_TABLE,
    table_path,
)

# The star schema, the suggested prices, and the source's stale periods and the quality results
# for the report's freshness page
PUBLISHED = (
    DATE_TABLE,
    TIME_TABLE,
    FACILITY_TABLE,
    AVAILABILITY_TABLE,
    HOURLY_TABLE,
    SUGGESTED_PRICE_TABLE,
    SOURCE_STALE_PERIOD_TABLE,
    QUALITY_TABLE,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="publish.py")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--tables-root", required=True)
    args = parser.parse_args()

    spark = SparkSession.builder.getOrCreate()
    for name in PUBLISHED:
        path = table_path(args.tables_root, name)
        # A pipeline that stopped early has not written every table yet
        if not DeltaTable.is_deltatable(path):
            print(f"{name}: no Delta table at {path}; not published")
            continue
        target = f"{args.catalog}.{args.schema}.{name}"
        frame = spark.read.format("delta").load(path)
        frame.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
        print(f"{name}: published {frame.count()} row(s) to {target}")


if __name__ == "__main__":
    main()
