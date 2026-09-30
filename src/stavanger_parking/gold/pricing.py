"""Suggested prices: the tariffs adjusted by occupancy and rush hours, per facility per hour.

Two configuration files drive it, so that changing a price or a rule never needs a code change:

- `config/tariffs.json`: the prices Stavanger Parkering publishes, per half hour, per facility, with
  their time windows. A window that names weekdays overrides the general window on those days.
- `config/pricing_rules.json`: occupancy bands with multipliers, rush-hour windows with
  multipliers, the limits of a suggested price relative to the tariff, and how much stale or
  missing data an hour may rest on before it is not priced.

`fact_suggested_price` has one row per facility per hour of `fact_parking_hourly`. The suggested
price is the hourly tariff times the multipliers of the hour's occupancy band and rush window,
kept within the limits and rounded to whole kroner; `applied_rule` says which rules made it.

Occupancy is time-weighted: 1 − average free spaces / capacity. Where the register gives no
capacity, the highest number of free spaces ever observed stands in for it, flagged in
`capacity_is_estimated`: it is only a lower bound of the real capacity (ADR 005).

An hour is not priced (`suggested_price` is null) when it has too little data (`no_data`), rests
mostly on stale data (`stale`), or its facility has no tariff (`no_tariff`).
"""

import json
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from stavanger_parking.config import CONFIG_DIR

DEFAULT_TARIFFS = CONFIG_DIR / "tariffs.json"
DEFAULT_RULES = CONFIG_DIR / "pricing_rules.json"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
OSLO = "Europe/Oslo"

PRICED = "priced"
STALE = "stale"
NO_DATA = "no_data"
NO_TARIFF = "no_tariff"

PRICE_SCHEMA = {
    "facility_key": pl.Int32,
    "date_key": pl.Int32,
    "hour": pl.Int8,
    "hour_start": pl.Datetime("us", "UTC"),
    "occupancy": pl.Float64,
    "capacity": pl.Int32,
    "capacity_is_estimated": pl.Boolean,
    "base_price": pl.Float64,
    "occupancy_band": pl.String,
    "rush_window": pl.String,
    "multiplier": pl.Float64,
    "suggested_price": pl.Float64,
    "applied_rule": pl.String,
    "status": pl.String,
}


class PricingConfigError(ValueError):
    """A tariff or pricing rule file is missing or invalid."""


@dataclass(frozen=True)
class Band:
    name: str
    below: float | None  # the band applies below this occupancy; None for the last band
    multiplier: float


@dataclass(frozen=True)
class RushWindow:
    name: str
    weekdays: tuple[int, ...]  # 1 (Monday) to 7 (Sunday), as dim_date.weekday_number
    hours: tuple[int, ...]  # local hours, as fact_parking_hourly.hour
    multiplier: float


@dataclass(frozen=True)
class Rules:
    bands: tuple[Band, ...]
    rush: tuple[RushWindow, ...]
    min_multiplier: float
    max_multiplier: float
    limits: dict  # facility -> {"min": NOK per hour, "max": NOK per hour}
    max_stale_share: float
    min_covered_minutes: float


def _read(path, what: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise PricingConfigError(f"{what} not found: {path}") from e
    except json.JSONDecodeError as e:
        raise PricingConfigError(f"{what} {path} is not valid JSON: {e}") from e


def _hours(frm: str, to: str, where: str) -> tuple[int, ...]:
    """The whole local hours from `frm` to `to` ("07:00" to "24:00"); windows start on the hour."""
    try:
        start_h, start_m = map(int, frm.split(":"))
        end_h, end_m = map(int, to.split(":"))
    except (AttributeError, ValueError) as e:
        raise PricingConfigError(f"{where}: times must be HH:MM, got {frm!r}-{to!r}") from e
    if start_m or end_m or not 0 <= start_h < end_h <= 24:
        raise PricingConfigError(f"{where}: {frm}-{to} must be whole hours within one day")
    return tuple(range(start_h, end_h))


def _weekdays(names, where: str) -> tuple[int, ...]:
    if not names or any(n not in WEEKDAYS for n in names):
        raise PricingConfigError(f"{where}: weekdays must be some of {', '.join(WEEKDAYS)}")
    return tuple(WEEKDAYS.index(n) + 1 for n in names)


def load_tariffs(path=DEFAULT_TARIFFS) -> pl.DataFrame:
    """The hourly tariff of every facility, weekday and local hour: NOK per hour."""
    data = _read(path, "Tariffs")
    if data.get("unit") != "half_hour":
        raise PricingConfigError(f"{path}: unit must be half_hour, the unit the operator publishes")
    rows = []
    for entry in data.get("facilities") or []:
        name = entry.get("facility")
        grid: dict[tuple[int, int], float] = {}
        general = [p for p in entry.get("periods", []) if "weekdays" not in p]
        special = [p for p in entry.get("periods", []) if "weekdays" in p]
        for period in general:
            for hour in _hours(period.get("from"), period.get("to"), f"{path}: {name}"):
                for day in range(1, 8):
                    if (day, hour) in grid:
                        raise PricingConfigError(f"{path}: {name} has two prices at {hour:02d}:00")
                    grid[(day, hour)] = float(period["price"])
        if len(grid) != 7 * 24:
            raise PricingConfigError(f"{path}: {name}'s periods must cover every hour of the day")
        for period in special:
            days = _weekdays(period["weekdays"], f"{path}: {name}")
            for hour in _hours(period.get("from"), period.get("to"), f"{path}: {name}"):
                for day in days:
                    grid[(day, hour)] = float(period["price"])
        rows += [
            {"facility": name, "weekday_number": d, "hour": h, "base_price": 2 * price}
            for (d, h), price in grid.items()
        ]
    if not rows:
        raise PricingConfigError(f"{path}: no facilities")
    return pl.DataFrame(
        rows,
        schema={
            "facility": pl.String,
            "weekday_number": pl.Int8,
            "hour": pl.Int8,
            "base_price": pl.Float64,
        },
    )


def load_rules(path=DEFAULT_RULES) -> Rules:
    data = _read(path, "Pricing rules")
    try:
        rules = _rules(data, path)
    except (KeyError, TypeError, ValueError) as e:
        if isinstance(e, PricingConfigError):
            raise
        raise PricingConfigError(f"{path}: missing or invalid field {e}") from e
    seen: dict[tuple[int, int], str] = {}
    for window in rules.rush:
        for cell in ((d, h) for d in window.weekdays for h in window.hours):
            if cell in seen:
                raise PricingConfigError(
                    f"{path}: rush windows {seen[cell]} and {window.name} overlap; "
                    "which one applies would depend on their order"
                )
            seen[cell] = window.name
    return rules


def _rules(data: dict, path) -> Rules:
    bands = tuple(
        Band(b["name"], b.get("below"), float(b["multiplier"])) for b in data["occupancy_bands"]
    )
    limits = [b.below for b in bands[:-1]]
    if not bands or bands[-1].below is not None or None in limits or limits != sorted(limits):
        raise PricingConfigError(
            f"{path}: occupancy bands must rise, and only the last may have no upper bound"
        )
    rush = tuple(
        RushWindow(
            w["name"],
            _weekdays(w["weekdays"], f"{path}: {w['name']}"),
            _hours(w["from"], w["to"], f"{path}: {w['name']}"),
            float(w["multiplier"]),
        )
        for w in data.get("rush_hours", [])
    )
    limit = data["limits"]
    rules = Rules(
        bands,
        rush,
        float(limit["min_multiplier"]),
        float(limit["max_multiplier"]),
        dict(limit.get("facilities", {})),
        float(data["data"]["max_stale_share"]),
        float(data["data"]["min_covered_minutes"]),
    )
    multipliers = [b.multiplier for b in bands] + [w.multiplier for w in rush]
    if any(m <= 0 for m in multipliers) or not 0 < rules.min_multiplier <= rules.max_multiplier:
        raise PricingConfigError(f"{path}: multipliers must be positive, and min ≤ max")
    return rules


def suggested_prices(
    hourly: pl.DataFrame,
    facilities: pl.DataFrame,
    tariffs: pl.DataFrame,
    rules: Rules,
) -> pl.DataFrame:
    """One suggested price per facility per hour of the hourly fact."""
    estimated = hourly.group_by("facility_key").agg(
        pl.col("max_available_spaces").max().alias("_estimated_capacity")
    )
    capacity = facilities.select(
        "facility_key", pl.col("facility_name").alias("facility"), "capacity"
    ).join(estimated, on="facility_key", how="left")
    rush = pl.DataFrame(
        [
            {"weekday_number": d, "hour": h, "rush_window": w.name, "_rush": w.multiplier}
            for w in rules.rush
            for d in w.weekdays
            for h in w.hours
        ],
        schema={
            "weekday_number": pl.Int8,
            "hour": pl.Int8,
            "rush_window": pl.String,
            "_rush": pl.Float64,
        },
    )

    band = pl.lit(None, pl.String)
    band_multiplier = pl.lit(None, pl.Float64)
    for b in reversed(rules.bands):
        applies = pl.lit(True) if b.below is None else pl.col("occupancy") < b.below
        band = pl.when(applies).then(pl.lit(b.name)).otherwise(band)
        band_multiplier = pl.when(applies).then(pl.lit(b.multiplier)).otherwise(band_multiplier)

    limits = pl.DataFrame(
        [
            {"facility": f, "_min": v.get("min"), "_max": v.get("max")}
            for f, v in rules.limits.items()
        ],
        schema={"facility": pl.String, "_min": pl.Float64, "_max": pl.Float64},
    )
    local = pl.col("hour_start").dt.convert_time_zone(OSLO)
    frame = (
        hourly.join(capacity, on="facility_key", how="left")
        .with_columns(
            local.dt.weekday().cast(pl.Int8).alias("weekday_number"),
            pl.col("hour").cast(pl.Int8),
            pl.col("capacity").is_null().alias("capacity_is_estimated"),
            pl.coalesce("capacity", "_estimated_capacity").alias("_capacity"),
        )
        .join(tariffs, on=["facility", "weekday_number", "hour"], how="left")
        .join(rush, on=["weekday_number", "hour"], how="left")
        .join(limits, on="facility", how="left")
        .with_columns(
            (1 - pl.col("avg_available_spaces") / pl.col("_capacity")).alias("occupancy"),
            pl.col("_capacity").alias("capacity"),
        )
        .with_columns(
            band.alias("occupancy_band"),
            (band_multiplier * pl.col("_rush").fill_null(1.0)).alias("multiplier"),
            pl.coalesce("_min", pl.col("base_price") * rules.min_multiplier).alias("_low"),
            pl.coalesce("_max", pl.col("base_price") * rules.max_multiplier).alias("_high"),
        )
        .with_columns((pl.col("base_price") * pl.col("multiplier")).alias("_raw"))
        .with_columns(
            pl.when(
                pl.col("avg_available_spaces").is_null()
                | pl.col("_capacity").is_null()
                | (pl.col("covered_minutes") < rules.min_covered_minutes)
            )
            .then(pl.lit(NO_DATA))
            .when(pl.col("stale_minutes") > rules.max_stale_share * pl.col("covered_minutes"))
            .then(pl.lit(STALE))
            .when(pl.col("base_price").is_null())
            .then(pl.lit(NO_TARIFF))
            .otherwise(pl.lit(PRICED))
            .alias("status")
        )
    )
    priced = pl.col("status") == PRICED
    clamped = (
        pl.when(pl.col("_raw") > pl.col("_high"))
        .then(pl.lit(" (capped at the maximum)"))
        .when(pl.col("_raw") < pl.col("_low"))
        .then(pl.lit(" (raised to the minimum)"))
        .otherwise(pl.lit(""))
    )
    rule = pl.concat_str(
        pl.lit("occupancy:"),
        pl.col("occupancy_band"),
        pl.when(pl.col("rush_window").is_not_null())
        .then(pl.concat_str(pl.lit(" × rush:"), pl.col("rush_window")))
        .otherwise(pl.lit("")),
        clamped,
    )
    return (
        frame.with_columns(
            pl.when(priced)
            .then(pl.col("_raw").clip(pl.col("_low"), pl.col("_high")).round(0))
            .alias("suggested_price"),
            pl.when(priced).then(rule).otherwise(pl.col("status")).alias("applied_rule"),
        )
        .select(pl.col(c).cast(t) for c, t in PRICE_SCHEMA.items())
        .sort("facility_key", "hour_start")
    )
