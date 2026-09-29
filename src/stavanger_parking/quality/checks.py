"""Data quality checks on the latest data.

Each check looks at the latest parking snapshot (the newest raw file, loaded or not), the latest
register snapshot, or the last day of the hourly fact, and returns results: one per check, or one
per facility for checks about facilities. A result is **critical** or a **warning**; a failed
critical check stops the run (`quality.check`), a failed warning is only recorded.

Critical, because what follows would be wrong or incomplete:

- `schema_drift`: an expected source field is missing, or a new one appears
- `empty_snapshot`: the latest snapshot could not be loaded, or none of its readings survived
- `facility_count`: the snapshot has a different number of facilities than the mapping
- `free_exceeds_capacity`: a facility reports more free spaces than its capacity

Warnings, to be looked at:

- `unmapped_facility`: a facility in the snapshot is not in the facility mapping
- `coordinates_outside_area`: a facility's coordinates are missing or outside the Stavanger area
- `negative_available_spaces`: the source reported a negative number of free spaces
- `quarantined_values`: other values of the snapshot were quarantined
- `register_missing_areas`: mapped areas are missing from the latest register snapshot
- `source_stale`: the latest snapshot's data is older than the staleness threshold
- `low_coverage`: hours in the last day that the readings cover for less than half
"""

from dataclasses import dataclass
from datetime import timedelta

import polars as pl

from stavanger_parking.facilities import FacilityMapping
from stavanger_parking.silver.parse import SOURCE_FIELDS

CRITICAL = "critical"
WARNING = "warning"

# The Stavanger area: every facility lies well inside it
LATITUDE = (58.85, 59.05)
LONGITUDE = (5.55, 5.85)
LOW_COVERAGE_MINUTES = 30

# Bronze columns that are not source fields
BRONZE_METADATA = {
    "source_id",
    "raw_file",
    "record_index",
    "ingested_at",
    "source_url",
    "resource_id",
    "content_hash",
    "values_fingerprint",
    "run_id",
    "loaded_at",
}


@dataclass(frozen=True)
class Result:
    check: str
    severity: str
    passed: bool
    detail: str
    subject: str | None = None


def _result(check: str, severity: str, passed: bool, detail: str, subject=None) -> Result:
    return Result(check, severity, passed, detail, subject)


def latest_snapshot(bronze: pl.DataFrame, load_issues: pl.DataFrame) -> str | None:
    """The newest raw file of the source, whether it was loaded or recorded as a load issue."""
    files = [*bronze["raw_file"].to_list(), *load_issues["raw_file"].to_list()]
    return max(files) if files else None


def schema_drift(bronze: pl.DataFrame, snapshot: str) -> list[Result]:
    rows = bronze.filter(pl.col("raw_file") == snapshot)
    if rows.is_empty():
        return []  # nothing loaded: `empty_snapshot` reports it
    present = {
        c for c in rows.columns if c not in BRONZE_METADATA and rows[c].null_count() < rows.height
    }
    missing, new = sorted(set(SOURCE_FIELDS) - present), sorted(present - set(SOURCE_FIELDS))
    detail = "; ".join(
        [f"missing: {', '.join(missing)}"] * bool(missing) + [f"new: {', '.join(new)}"] * bool(new)
    )
    return [_result("schema_drift", CRITICAL, not (missing or new), detail or "fields as expected")]


def empty_snapshot(snapshot: str, load_issues: pl.DataFrame, fetches: pl.DataFrame) -> Result:
    issue = load_issues.filter(pl.col("raw_file") == snapshot)
    if issue.height:
        return _result("empty_snapshot", CRITICAL, False, f"not loaded: {issue['issue'][0]}")
    readings = fetches.filter(pl.col("raw_file") == snapshot).height
    detail = f"{readings} reading(s)" if readings else "no reading survived parsing"
    return _result("empty_snapshot", CRITICAL, readings > 0, detail)


def facility_count(snapshot_fetches: pl.DataFrame, mapping: tuple[FacilityMapping, ...]) -> Result:
    seen = set(snapshot_fetches["facility"].to_list())
    mapped = {m.facility for m in mapping}
    detail = f"{len(seen)} facilities, {len(mapped)} mapped"
    extra, absent = sorted(seen - mapped), sorted(mapped - seen)
    if extra:
        detail += f"; not mapped: {', '.join(extra)}"
    if absent:
        detail += f"; not in the snapshot: {', '.join(absent)}"
    return _result("facility_count", CRITICAL, len(seen) == len(mapped), detail)


def facility_checks(
    snapshot_fetches: pl.DataFrame, capacities: dict[str, int | None], mapping
) -> list[Result]:
    """Checks per facility in the snapshot: capacity, mapping and coordinates."""
    mapped = {m.facility for m in mapping}
    results = []
    for row in snapshot_fetches.sort("facility").iter_rows(named=True):
        name, free = row["facility"], row["available_spaces"]
        capacity = capacities.get(name)
        if free is not None and capacity is not None:
            results.append(
                _result(
                    "free_exceeds_capacity",
                    CRITICAL,
                    free <= capacity,
                    f"{free} free of {capacity}",
                    name,
                )
            )
        results.append(
            _result(
                "unmapped_facility",
                WARNING,
                name in mapped,
                "mapped" if name in mapped else "not in config/facility_mapping.json",
                name,
            )
        )
        lat, lon = row["latitude"], row["longitude"]
        inside = (
            lat is not None
            and lon is not None
            and LATITUDE[0] <= lat <= LATITUDE[1]
            and LONGITUDE[0] <= lon <= LONGITUDE[1]
        )
        results.append(_result("coordinates_outside_area", WARNING, inside, f"{lat}, {lon}", name))
    return results


def quarantine_checks(quarantine: pl.DataFrame, snapshot: str) -> list[Result]:
    rows = quarantine.filter(pl.col("raw_file") == snapshot)
    negative = rows.filter(
        (pl.col("field") == "Antall_ledige_plasser") & pl.col("raw_value").str.contains(r"^-\d+$")
    )
    others = rows.height - negative.height
    return [
        _result(
            "negative_available_spaces",
            WARNING,
            negative.is_empty(),
            f"{negative.height} negative value(s)",
        ),
        _result("quarantined_values", WARNING, others == 0, f"{others} other value(s)"),
    ]


def register_missing_areas(areas: pl.DataFrame, mapping) -> Result:
    if areas.is_empty():
        return _result("register_missing_areas", WARNING, False, "no register snapshot")
    latest = areas.filter(pl.col("ingested_at") == pl.col("ingested_at").max())
    present = set(latest["register_id"].to_list())
    missing = sorted(m.register_id for m in mapping if m.register_id not in present)
    detail = f"missing: {', '.join(map(str, missing))}" if missing else "all mapped areas present"
    return _result("register_missing_areas", WARNING, not missing, detail)


def source_stale(freshness: pl.DataFrame, snapshot: str) -> list[Result]:
    row = freshness.filter(pl.col("raw_file") == snapshot)
    if row.is_empty():
        return []
    age = row["source_age_minutes"][0]
    stale = bool(row["is_stale"][0])
    return [_result("source_stale", WARNING, not stale, f"data {age:.0f} minutes old")]


def low_coverage(hourly: pl.DataFrame, facility_keys: list[int], until) -> Result:
    """Facility-hours in the last 24 full hours before `until` covered for less than half.

    An hour without any reading has no row in the hourly fact, so the hours are laid out for every
    active facility first: a gap in collection counts as zero coverage, not as no data.
    """
    last = (until - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    hours = [last - timedelta(hours=h) for h in range(24)]
    grid = pl.DataFrame(
        {
            "facility_key": [k for k in facility_keys for _ in hours],
            "hour_start": [h for _ in facility_keys for h in hours],
        },
        schema={"facility_key": pl.Int32, "hour_start": pl.Datetime("us", "UTC")},
    )
    covered = grid.join(
        hourly.select("facility_key", "hour_start", "covered_minutes"),
        on=["facility_key", "hour_start"],
        how="left",
    ).with_columns(pl.col("covered_minutes").fill_null(0.0))
    low = covered.filter(pl.col("covered_minutes") < LOW_COVERAGE_MINUTES)
    detail = (
        f"{low.height} of {covered.height} facility-hour(s) under {LOW_COVERAGE_MINUTES} minutes"
    )
    return _result("low_coverage", WARNING, low.is_empty(), detail)
