from datetime import date

import polars as pl
import pytest

from stavanger_parking.gold.calendar import (
    DATE_SCHEMA,
    FIRST_DATE,
    LAST_DATE,
    TIME_SCHEMA,
    dim_date,
    dim_time,
    easter_sunday,
    norwegian_public_holidays,
)


@pytest.mark.parametrize(
    ("year", "easter"),
    [
        (2019, date(2019, 4, 21)),
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),  # the latest possible date
        (2285, date(2285, 3, 22)),  # the earliest possible date
    ],
)
def test_easter_sunday(year, easter):
    assert easter_sunday(year) == easter


def test_norwegian_public_holidays_2026():
    assert norwegian_public_holidays(2026) == {
        date(2026, 1, 1): "Første nyttårsdag",
        date(2026, 3, 29): "Palmesøndag",
        date(2026, 4, 2): "Skjærtorsdag",
        date(2026, 4, 3): "Langfredag",
        date(2026, 4, 5): "Første påskedag",
        date(2026, 4, 6): "Andre påskedag",
        date(2026, 5, 1): "Offentlig høytidsdag",
        date(2026, 5, 14): "Kristi himmelfartsdag",
        date(2026, 5, 17): "Grunnlovsdag",
        date(2026, 5, 24): "Første pinsedag",
        date(2026, 5, 25): "Andre pinsedag",
        date(2026, 12, 25): "Første juledag",
        date(2026, 12, 26): "Andre juledag",
    }


def test_coinciding_holidays_keep_both_names():
    assert norwegian_public_holidays(2027)[date(2027, 5, 17)] == "Grunnlovsdag, Andre pinsedag"
    assert len(norwegian_public_holidays(2027)) == 12


def test_dim_date_has_one_row_per_day_in_the_fixed_range():
    dates = dim_date()

    assert dates.schema == pl.Schema(DATE_SCHEMA)
    assert dates["date"].to_list()[0] == FIRST_DATE
    assert dates["date"].to_list()[-1] == LAST_DATE
    assert dates.height == (LAST_DATE - FIRST_DATE).days + 1
    assert dates["date_key"].is_unique().all()


def test_dim_date_columns():
    row = dim_date(date(2026, 5, 17), date(2026, 5, 17)).row(0, named=True)

    assert row == {
        "date_key": 20260517,
        "date": date(2026, 5, 17),
        "year": 2026,
        "quarter": 2,
        "month": 5,
        "month_name": "May",
        "day_of_month": 17,
        "iso_year": 2026,
        "iso_week": 20,
        "weekday_number": 7,
        "weekday": "Sunday",
        "is_weekend": True,
        "is_public_holiday": True,
        "holiday_name": "Grunnlovsdag",
    }


def test_iso_weeks_at_year_boundaries():
    dates = dim_date(date(2020, 12, 31), date(2021, 1, 4))

    assert dates.select("date", "iso_year", "iso_week").rows() == [
        (date(2020, 12, 31), 2020, 53),
        (date(2021, 1, 1), 2020, 53),
        (date(2021, 1, 2), 2020, 53),
        (date(2021, 1, 3), 2020, 53),
        (date(2021, 1, 4), 2021, 1),
    ]


def test_an_ordinary_weekday_is_neither_weekend_nor_holiday():
    row = dim_date(date(2026, 9, 29), date(2026, 9, 29)).row(0, named=True)

    assert (row["weekday"], row["is_weekend"], row["is_public_holiday"]) == (
        "Tuesday",
        False,
        False,
    )
    assert row["holiday_name"] is None


def test_every_year_in_the_range_has_its_holidays():
    counts = dim_date().group_by("year").agg(pl.col("is_public_holiday").sum()).sort("year")

    # 13 a year, one fewer when a moving holiday falls on 1 or 17 May (2027, 2032)
    assert dict(counts.rows()) == {
        y: 12 if y in (2027, 2032) else 13 for y in range(FIRST_DATE.year, LAST_DATE.year + 1)
    }


def test_dim_time_has_one_row_per_minute():
    times = dim_time()

    assert times.schema == pl.Schema(TIME_SCHEMA)
    assert times["minute_of_day"].to_list() == list(range(1440))
    assert times["time_key"].is_unique().all()


@pytest.mark.parametrize(
    ("minute_of_day", "time_key", "time", "quarter_hour", "day_part"),
    [
        (0, 0, "00:00", "00:00", "night"),
        (359, 559, "05:59", "05:45", "night"),
        (360, 600, "06:00", "06:00", "morning"),
        (599, 959, "09:59", "09:45", "morning"),
        (600, 1000, "10:00", "10:00", "midday"),
        (854, 1414, "14:14", "14:00", "afternoon"),
        (855, 1415, "14:15", "14:15", "afternoon"),
        (1080, 1800, "18:00", "18:00", "evening"),
        (1439, 2359, "23:59", "23:45", "evening"),
    ],
)
def test_dim_time_columns(minute_of_day, time_key, time, quarter_hour, day_part):
    row = dim_time().row(minute_of_day, named=True)

    assert (row["time_key"], row["time"], row["quarter_hour"], row["day_part"]) == (
        time_key,
        time,
        quarter_hour,
        day_part,
    )
    assert (row["hour"], row["minute"]) == divmod(minute_of_day, 60)


def test_every_quarter_hour_has_fifteen_minutes():
    counts = dim_time().group_by("quarter_hour").len()

    assert counts.height == 96
    assert counts["len"].unique().to_list() == [15]
