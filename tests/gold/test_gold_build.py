import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from stavanger_parking.bronze.collector import render_raw_path, sidecar_path
from stavanger_parking.bronze.load import load_source
from stavanger_parking.config import load_sources
from stavanger_parking.gold import build
from stavanger_parking.gold.build import BuildError
from stavanger_parking.gold.calendar import dim_date, dim_time
from stavanger_parking.gold.facility import UNKNOWN_KEY
from stavanger_parking.silver import build as silver
from stavanger_parking.tables import (
    AVAILABILITY_TABLE,
    DATE_TABLE,
    FACILITY_TABLE,
    HOURLY_TABLE,
    SUGGESTED_PRICE_TABLE,
    TIME_TABLE,
    table_path,
)

ROOT = Path(__file__).parent.parent.parent
REPO_CONFIG = ROOT / "config" / "sources.json"
MAPPING = ROOT / "config" / "facility_mapping.json"
REGISTER = ROOT / "tests" / "fixtures" / "parkeringsregisteret_stavanger_parkering.json"
T0 = datetime(2026, 9, 28, 12, 3, tzinfo=UTC)
NINE = ["Jernbanen", "Valberget", "Posten", "Jorenholmen", "St Olav", "Siddis", "Forum"]
NINE += ["Kyrre", "Parketten"]


def record(sted: str, klokkeslett: str = "14:00") -> dict:
    return {
        "Dato": "28.09.2026",
        "Klokkeslett": klokkeslett,
        "Sted": sted,
        "Latitude": "58.966341",
        "Longitude": "5.732047",
        "Antall_ledige_plasser": "100",
    }


class Pipeline:
    """Raw snapshots in, gold tables out, the way the real runs go."""

    def __init__(self, tmp_path):
        self.raw_root, self.tables = tmp_path / "raw", str(tmp_path / "tables")
        sources = {s.id: s for s in load_sources(REPO_CONFIG)}
        self.parking, self.register = sources["stavanger_parking"], sources["parkeringsregisteret"]

    def collect(self, source, at: datetime, payload: bytes) -> None:
        raw_file = render_raw_path(source.raw_path, at)
        (self.raw_root / raw_file).parent.mkdir(parents=True, exist_ok=True)
        (self.raw_root / raw_file).write_bytes(payload)
        meta = {"ingested_at": at.isoformat()}
        (self.raw_root / sidecar_path(raw_file)).write_text(json.dumps(meta))

    def snapshot(self, at: datetime, facilities: list[str]) -> None:
        self.collect(self.parking, at, json.dumps([record(f) for f in facilities]).encode())

    def run(self, mapping=MAPPING) -> dict[str, int]:
        for source in (self.parking, self.register):
            load_source(source, self.raw_root, self.tables, datetime.now(UTC))
        silver.build(self.parking, self.tables)
        silver.build_register(self.register, self.tables)
        return build.build(self.tables, mapping, REPO_CONFIG)

    def facilities(self) -> pl.DataFrame:
        return pl.read_delta(table_path(self.tables, FACILITY_TABLE)).sort("facility_key")


@pytest.fixture
def pipeline(tmp_path):
    p = Pipeline(tmp_path)
    p.collect(p.register, T0 - timedelta(days=1), REGISTER.read_bytes())
    return p


def by_name(frame: pl.DataFrame) -> dict[str, dict]:
    return {r["facility_name"]: r for r in frame.iter_rows(named=True)}


def test_build_writes_the_date_and_time_dimensions(pipeline):
    pipeline.snapshot(T0, NINE)

    written = pipeline.run()

    assert pl.read_delta(table_path(pipeline.tables, DATE_TABLE)).equals(dim_date())
    assert pl.read_delta(table_path(pipeline.tables, TIME_TABLE)).equals(dim_time())
    assert written == {
        DATE_TABLE: dim_date().height,
        TIME_TABLE: 1440,
        FACILITY_TABLE: 10,
        AVAILABILITY_TABLE: 9,
        HOURLY_TABLE: 9,
        SUGGESTED_PRICE_TABLE: 9,
    }


def test_every_facility_in_the_feed_gets_a_key_and_its_capacity(pipeline):
    pipeline.snapshot(T0, NINE)

    pipeline.run()

    facilities = by_name(pipeline.facilities())
    assert sorted(r["facility_key"] for r in facilities.values()) == [-1, *range(1, 10)]
    jernbanen = facilities["Jernbanen"]
    assert (jernbanen["register_id"], jernbanen["capacity"]) == (3650, 390)
    assert jernbanen["capacity_changed_at"] == datetime(2024, 2, 16, 10, 53, 13, tzinfo=UTC)
    assert (jernbanen["first_seen"], jernbanen["last_seen"], jernbanen["is_active"]) == (
        T0,
        T0,
        True,
    )
    assert facilities["Forum"]["capacity"] == 289


def test_the_unknown_member_is_always_there(pipeline):
    pipeline.snapshot(T0, NINE)

    pipeline.run()

    unknown = pipeline.facilities().row(0, named=True)
    assert (unknown["facility_key"], unknown["facility_name"]) == (UNKNOWN_KEY, "(unknown)")
    assert unknown["is_active"] is False


def test_a_new_facility_appears_on_the_next_run_with_unknown_capacity(pipeline):
    pipeline.snapshot(T0, NINE)
    pipeline.run()
    before = {
        r["facility_name"]: r["facility_key"] for r in pipeline.facilities().iter_rows(named=True)
    }

    pipeline.snapshot(T0 + timedelta(minutes=5), [*NINE, "Lervig"])
    pipeline.run()

    facilities = by_name(pipeline.facilities())
    assert facilities["Lervig"]["facility_key"] == 10
    assert (facilities["Lervig"]["capacity"], facilities["Lervig"]["register_id"]) == (None, None)
    assert facilities["Lervig"]["is_active"] is True
    assert {n: facilities[n]["facility_key"] for n in before} == before


def test_a_facility_missing_from_the_feed_is_inactive_not_deleted(pipeline):
    pipeline.snapshot(T0, NINE)
    pipeline.run()

    pipeline.snapshot(T0 + timedelta(minutes=5), [f for f in NINE if f != "Posten"])
    pipeline.run()

    posten = by_name(pipeline.facilities())["Posten"]
    assert posten["is_active"] is False
    assert posten["last_seen"] == T0
    assert pipeline.facilities().height == 10


def test_a_facility_that_comes_back_is_active_again_with_its_key(pipeline):
    pipeline.snapshot(T0, NINE)
    pipeline.run()
    key = by_name(pipeline.facilities())["Posten"]["facility_key"]
    pipeline.snapshot(T0 + timedelta(minutes=5), [f for f in NINE if f != "Posten"])
    pipeline.run()

    pipeline.snapshot(T0 + timedelta(minutes=10), NINE)
    pipeline.run()

    posten = by_name(pipeline.facilities())["Posten"]
    assert (posten["facility_key"], posten["is_active"]) == (key, True)


def test_a_facility_gone_from_silver_is_kept_and_marked_inactive(pipeline):
    pipeline.snapshot(T0, NINE)
    pipeline.run()
    key = by_name(pipeline.facilities())["Kyrre"]["facility_key"]

    # Silver rebuilt without the history that named Kyrre
    fetch = table_path(pipeline.tables, silver.FETCH_TABLE)
    remaining = pl.read_delta(fetch).filter(pl.col("facility") != "Kyrre")
    remaining.write_delta(fetch, mode="overwrite")
    build.build_facilities(pipeline.tables, MAPPING)

    kyrre = by_name(pipeline.facilities())["Kyrre"]
    assert (kyrre["facility_key"], kyrre["is_active"]) == (key, False)


def test_rerunning_changes_nothing(pipeline):
    pipeline.snapshot(T0, NINE)
    pipeline.run()
    before = pipeline.facilities()

    pipeline.run()

    assert pipeline.facilities().equals(before)


def test_without_a_register_snapshot_capacity_is_unknown(tmp_path):
    p = Pipeline(tmp_path)
    p.snapshot(T0, NINE)

    p.run()

    assert p.facilities()["capacity"].null_count() == 10


def test_the_availability_fact_has_one_row_per_reading(pipeline):
    # Three fetches of one frozen reading, then a new reading: 2 readings per facility
    for m in (0, 20, 40):
        pipeline.snapshot(T0 + timedelta(minutes=m), NINE)
    later = T0 + timedelta(minutes=45)
    pipeline.collect(
        pipeline.parking, later, json.dumps([record(f, "14:44") for f in NINE]).encode()
    )

    pipeline.run()

    fact = pl.read_delta(table_path(pipeline.tables, AVAILABILITY_TABLE))
    jernbanen = fact.filter(
        pl.col("facility_key") == by_name(pipeline.facilities())["Jernbanen"]["facility_key"]
    ).sort("valid_from")
    assert fact.height == 18
    assert jernbanen["time_key"].to_list() == [1400, 1444]
    assert jernbanen["duration_minutes"].to_list() == [44.0, None]
    assert jernbanen["last_fetched_at"].to_list() == [T0 + timedelta(minutes=40), later]
    # 14:00 was 43 minutes old when fetched at 12:43 UTC: the source was stale
    assert jernbanen["is_stale"].to_list() == [True, False]
    assert jernbanen["occupied_spaces"].to_list() == [290, 290]


def test_a_reading_outside_dim_date_stops_the_build(pipeline):
    frozen = [dict(record(f), Dato="01.01.2036") for f in NINE]
    pipeline.collect(
        pipeline.parking, datetime(2036, 1, 1, 0, 5, tzinfo=UTC), json.dumps(frozen).encode()
    )

    with pytest.raises(BuildError, match="outside dim_date"):
        pipeline.run()


def test_build_without_silver_fails_clearly(tmp_path):
    with pytest.raises(BuildError, match="build silver first"):
        build.build(str(tmp_path))


def test_silver_readings_find_their_date_and_time_keys():
    # Silver's local parts map onto the keys (ADR 009): 2026-09-23 19:16 Oslo
    reading = pl.DataFrame(
        {"reading_date": [date(2026, 9, 23)], "reading_minute_of_day": [19 * 60 + 16]},
        schema_overrides={"reading_minute_of_day": pl.Int16},
    )

    joined = reading.join(
        dim_date().select("date", "date_key"), left_on="reading_date", right_on="date"
    ).join(
        dim_time().select("minute_of_day", "time_key"),
        left_on="reading_minute_of_day",
        right_on="minute_of_day",
    )

    assert joined.select("date_key", "time_key").rows() == [(20260923, 1916)]


def test_cli_reports_rows(pipeline, capsys):
    pipeline.snapshot(T0, NINE)
    pipeline.run()

    assert build.main(["--tables-root", pipeline.tables, "--mapping", str(MAPPING)]) == 0

    out = capsys.readouterr().out
    assert "dim_time: 1440 row(s)" in out
    assert "dim_parking_facility: 10 row(s)" in out


def test_cli_without_silver_exits_with_an_error(tmp_path, capsys):
    assert build.main(["--tables-root", str(tmp_path)]) == 1
    assert "build silver first" in capsys.readouterr().err
