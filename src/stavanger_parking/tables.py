"""Where tables live, and what they are called: the contract between the layers.

Every layer's tables sit directly under one tables root.

The root describes the environment (a local folder, `/lakehouse/default/Tables`, or an
`abfss://…/Tables` URI), not the source, so it is a parameter of every build.
"""


def table_path(tables_root: str, name: str) -> str:
    return f"{str(tables_root).rstrip('/')}/{name}"


# Silver (bronze tables are named in the source configuration)
FETCH_TABLE = "silver_parking_fetch"
READING_TABLE = "silver_parking_reading"
QUARANTINE_TABLE = "silver_quarantine"
FRESHNESS_TABLE = "silver_snapshot_freshness"
STALE_PERIOD_TABLE = "silver_stale_period"
AREA_TABLE = "silver_parking_area"

# Gold
DATE_TABLE = "dim_date"
TIME_TABLE = "dim_time"
FACILITY_TABLE = "dim_parking_facility"
AVAILABILITY_TABLE = "fact_parking_availability"
