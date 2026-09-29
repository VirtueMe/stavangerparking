"""The date and time dimensions: generated, not derived from the data.

Facts key on the source's local Oslo time (ADR 009): `date_key` (`yyyymmdd`) from `reading_date`,
and `time_key` (`hhmm`) from `reading_minute_of_day`, which `dim_time` also carries.

- `dim_date`: one row per day from `FIRST_DATE` to `LAST_DATE`, with ISO week, weekday, weekend
  and Norwegian public holidays. The range is fixed, so a rebuild never depends on the data or on
  today's date; it covers the source's known history (the Wayback Machine has it from 2019).
- `dim_time`: one row per minute of the day, with its 15-minute bucket and part of the day.
"""

from datetime import date, timedelta

import polars as pl

FIRST_DATE = date(2020, 1, 1)
LAST_DATE = date(2035, 12, 31)

DATE_SCHEMA = {
    "date_key": pl.Int32,
    "date": pl.Date,
    "year": pl.Int16,
    "quarter": pl.Int8,
    "month": pl.Int8,
    "month_name": pl.String,
    "day_of_month": pl.Int8,
    "iso_year": pl.Int16,
    "iso_week": pl.Int8,
    "weekday_number": pl.Int8,
    "weekday": pl.String,
    "is_weekend": pl.Boolean,
    "is_public_holiday": pl.Boolean,
    "holiday_name": pl.String,
}

TIME_SCHEMA = {
    "time_key": pl.Int16,
    "minute_of_day": pl.Int16,
    "hour": pl.Int8,
    "minute": pl.Int8,
    "time": pl.String,
    "quarter_hour": pl.String,
    "day_part": pl.String,
}

# (first hour, name): each part lasts until the next one starts
DAY_PARTS = ((0, "night"), (6, "morning"), (10, "midday"), (14, "afternoon"), (18, "evening"))


def easter_sunday(year: int) -> date:
    """Easter Sunday in the Gregorian calendar (the anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(year, month, day + 1)


def norwegian_public_holidays(year: int) -> dict[date, str]:
    """The public holidays (helligdager and høytidsdager) of a year, with their official names."""
    easter = easter_sunday(year)
    moving = {
        -7: "Palmesøndag",
        -3: "Skjærtorsdag",
        -2: "Langfredag",
        0: "Første påskedag",
        1: "Andre påskedag",
        39: "Kristi himmelfartsdag",
        49: "Første pinsedag",
        50: "Andre pinsedag",
    }
    holidays = {
        date(year, 1, 1): "Første nyttårsdag",
        date(year, 5, 1): "Offentlig høytidsdag",
        date(year, 5, 17): "Grunnlovsdag",
        date(year, 12, 25): "Første juledag",
        date(year, 12, 26): "Andre juledag",
    }
    # A moving holiday can fall on a fixed one (17 May 2027 is also Andre pinsedag): keep both
    for offset, name in moving.items():
        day = easter + timedelta(days=offset)
        holidays[day] = f"{holidays[day]}, {name}" if day in holidays else name
    return dict(sorted(holidays.items()))


def dim_date(first: date = FIRST_DATE, last: date = LAST_DATE) -> pl.DataFrame:
    """One row per day from `first` to `last`, both included."""
    holidays = {
        day: name
        for year in range(first.year, last.year + 1)
        for day, name in norwegian_public_holidays(year).items()
    }
    days = pl.date_range(first, last, "1d", eager=True).alias("date")
    holiday = pl.col("date").replace_strict(holidays, default=None, return_dtype=pl.String)
    return (
        days.to_frame()
        .with_columns(
            # Month and day are Int8 in Polars: widen before multiplying, or the key overflows
            (
                pl.col("date").dt.year().cast(pl.Int32) * 10000
                + pl.col("date").dt.month().cast(pl.Int32) * 100
                + pl.col("date").dt.day().cast(pl.Int32)
            ).alias("date_key"),
            pl.col("date").dt.year().alias("year"),
            pl.col("date").dt.quarter().alias("quarter"),
            pl.col("date").dt.month().alias("month"),
            pl.col("date").dt.strftime("%B").alias("month_name"),
            pl.col("date").dt.day().alias("day_of_month"),
            pl.col("date").dt.iso_year().alias("iso_year"),
            pl.col("date").dt.week().alias("iso_week"),
            pl.col("date").dt.weekday().alias("weekday_number"),
            pl.col("date").dt.strftime("%A").alias("weekday"),
            (pl.col("date").dt.weekday() >= 6).alias("is_weekend"),
            holiday.is_not_null().alias("is_public_holiday"),
            holiday.alias("holiday_name"),
        )
        .select(pl.col(c).cast(t) for c, t in DATE_SCHEMA.items())
    )


def dim_time() -> pl.DataFrame:
    """One row per minute of the day."""
    minutes = pl.int_range(0, 24 * 60, eager=True).alias("minute_of_day").to_frame()
    hour, minute = pl.col("minute_of_day") // 60, pl.col("minute_of_day") % 60
    day_part = pl.lit(DAY_PARTS[0][1])
    for first_hour, name in DAY_PARTS[1:]:
        day_part = pl.when(hour >= first_hour).then(pl.lit(name)).otherwise(day_part)
    return minutes.with_columns(
        (hour * 100 + minute).alias("time_key"),
        hour.alias("hour"),
        minute.alias("minute"),
        pl.format(
            "{}:{}", hour.cast(pl.String).str.zfill(2), minute.cast(pl.String).str.zfill(2)
        ).alias("time"),
        pl.format(
            "{}:{}",
            hour.cast(pl.String).str.zfill(2),
            (minute // 15 * 15).cast(pl.String).str.zfill(2),
        ).alias("quarter_hour"),
        day_part.alias("day_part"),
    ).select(pl.col(c).cast(t) for c, t in TIME_SCHEMA.items())
