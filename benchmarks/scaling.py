"""How the transformations scale with facilities and history (ADR 006).

    uv run python benchmarks/scaling.py [--scale FACILITIES:YEARS ...] [--timeout SECONDS]

Generates bronze rows for a number of facilities over a number of years, in the worst case for the
model: a fetch every 5 minutes, every fetch a new reading with changed values. Each scale runs in
its own process, through the real silver and gold code, and reports its time and peak memory. A
scale that runs out of memory or time is reported as such.

The silver and gold steps derive their tables from **all** fetches on every run (ADR 009), so a
full derivation at a scale is also what every incremental run does at that history. Reading and
writing the Delta tables is left out, so the times are lower bounds; memory is measured as it is.
"""

import argparse
import json
import resource
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

FETCHES_PER_DAY = 24 * 12
DEFAULT_SCALES = ["9:1", "9:5", "90:1", "9:10", "90:5", "900:1"]


def bronze(facilities: int, years: float):
    """Bronze rows: every facility in every snapshot, 5 minutes apart, values always changing."""
    import polars as pl

    snapshots = int(years * 365 * FETCHES_PER_DAY)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    snap = pl.DataFrame({"s": pl.int_range(0, snapshots, eager=True)}).with_columns(
        (pl.lit(start) + pl.duration(minutes=pl.col("s") * 5)).alias("ingested_at")
    )
    local = (pl.col("ingested_at") - pl.duration(minutes=2)).dt.convert_time_zone("Europe/Oslo")
    facility = pl.DataFrame({"f": pl.int_range(0, facilities, eager=True)})
    return (
        snap.join(facility, how="cross")
        .with_columns(
            pl.lit("stavanger_parking").alias("source_id"),
            pl.format("bronze/parking/{}.json", pl.col("s")).alias("raw_file"),
            pl.col("f").cast(pl.Int32).alias("record_index"),
            local.dt.strftime("%d.%m.%Y").alias("Dato"),
            local.dt.strftime("%H:%M").alias("Klokkeslett"),
            pl.format("Facility {}", pl.col("f")).alias("Sted"),
            pl.lit("58.966341").alias("Latitude"),
            pl.lit("5.732047").alias("Longitude"),
            ((pl.col("s") * 7 + pl.col("f") * 13) % 500)
            .cast(pl.String)
            .alias("Antall_ledige_plasser"),
        )
        .drop("s", "f")
    )


def run_scale(facilities: int, years: float) -> dict:
    """Silver and gold on one scale, in this process; returns timings and peak memory."""
    import polars as pl

    from stavanger_parking.gold.availability import availability
    from stavanger_parking.gold.facility import assign_keys, facility_attributes
    from stavanger_parking.gold.hourly import hourly
    from stavanger_parking.silver.dedup import deduplicate
    from stavanger_parking.silver.freshness import freshness
    from stavanger_parking.silver.parse import parse_readings
    from stavanger_parking.silver.register import AREA_SCHEMA

    timings = {}

    def step(name, fn):
        t = time.perf_counter()
        out = fn()
        timings[name] = round(time.perf_counter() - t, 2)
        return out

    rows = step("generate", lambda: bronze(facilities, years))
    fetches, _ = step("parse", lambda: parse_readings(rows))
    rows = None  # free the generated bronze rows before the next steps
    readings, _ = step("deduplicate", lambda: deduplicate(fetches))
    _, periods = step("freshness", lambda: freshness(fetches, timedelta(minutes=15)))
    facilities_dim = step(
        "facility",
        lambda: assign_keys(
            facility_attributes(fetches, pl.DataFrame(schema=AREA_SCHEMA), ()),
            pl.DataFrame(schema={"facility_name": pl.String, "facility_key": pl.Int32}),
        ),
    )
    fact = step("availability", lambda: availability(readings, fetches, facilities_dim, periods))
    hours = step("hourly", lambda: hourly(fact, periods, timedelta(minutes=20)))
    return {
        "facilities": facilities,
        "years": years,
        "fetches": fetches.height,
        "hourly_rows": hours.height,
        "seconds": round(sum(v for k, v in timings.items() if k != "generate"), 1),
        "steps": timings,
        # ru_maxrss is in kilobytes on Linux
        "peak_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", nargs="*", default=DEFAULT_SCALES, help="FACILITIES:YEARS")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--memory-gb", type=int, default=20, help="memory cap per scale")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.child:
        # Cap the child's memory, so a scale that does not fit fails instead of swapping the machine
        limit = args.memory_gb * 1024**3
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        facilities, years = args.child.split(":")
        print(json.dumps(run_scale(int(facilities), float(years))))
        return 0

    for scale in args.scale:
        try:
            done = subprocess.run(
                [sys.executable, __file__, "--child", scale, "--memory-gb", str(args.memory_gb)],
                capture_output=True,
                text=True,
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            print(json.dumps({"scale": scale, "result": f"timeout after {args.timeout} s"}))
            continue
        if done.returncode != 0:
            memory = done.returncode < 0 or "MemoryError" in done.stderr or "memory" in done.stderr
            reason = f"out of memory (cap {args.memory_gb} GB)" if memory else done.stderr[-300:]
            print(json.dumps({"scale": scale, "result": reason}))
            continue
        print(done.stdout.strip(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
