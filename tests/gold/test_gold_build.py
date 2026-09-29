from datetime import date

import polars as pl

from stavanger_parking.gold import build
from stavanger_parking.gold.build import DATE_TABLE, TIME_TABLE
from stavanger_parking.gold.calendar import dim_date, dim_time
from stavanger_parking.tables import table_path


def test_build_writes_the_date_and_time_dimensions(tmp_path):
    written = build.build(str(tmp_path))

    assert pl.read_delta(table_path(str(tmp_path), DATE_TABLE)).equals(dim_date())
    assert pl.read_delta(table_path(str(tmp_path), TIME_TABLE)).equals(dim_time())
    assert written == {DATE_TABLE: dim_date().height, TIME_TABLE: 1440}


def test_a_rebuild_replaces_the_tables_with_the_same_rows(tmp_path):
    build.build(str(tmp_path))
    build.build(str(tmp_path))

    assert pl.read_delta(table_path(str(tmp_path), TIME_TABLE)).height == 1440


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


def test_cli_reports_rows(tmp_path, capsys):
    assert build.main(["--tables-root", str(tmp_path)]) == 0

    assert "dim_time: 1440 row(s)" in capsys.readouterr().out
