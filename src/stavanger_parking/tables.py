"""Where tables live, and what they are called: the contract between the layers.

Every layer's tables sit directly under one tables root.

The root describes the environment (a local folder, `/lakehouse/default/Tables`, or an
`abfss://…/Tables` URI), not the source, so it is a parameter of every build.
"""


def table_path(tables_root: str, name: str) -> str:
    return f"{str(tables_root).rstrip('/')}/{name}"


# The source the model is built on; its bronze table is named in the source configuration
PARKING_SOURCE_ID = "stavanger_parking"

# Silver
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
HOURLY_TABLE = "fact_parking_hourly"
SUGGESTED_PRICE_TABLE = "fact_suggested_price"

# Quality
QUALITY_TABLE = "quality_check_results"

# Every table the pipeline writes besides the bronze tables (those are named in the source
# configuration). Maintenance works through this list; a test checks it against a full run.
PIPELINE_TABLES = (
    FETCH_TABLE,
    READING_TABLE,
    QUARANTINE_TABLE,
    FRESHNESS_TABLE,
    STALE_PERIOD_TABLE,
    AREA_TABLE,
    DATE_TABLE,
    TIME_TABLE,
    FACILITY_TABLE,
    AVAILABILITY_TABLE,
    HOURLY_TABLE,
    SUGGESTED_PRICE_TABLE,
    QUALITY_TABLE,
)
