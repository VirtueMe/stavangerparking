import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from stavanger_parking.gold.facility import FACILITY_SCHEMA
from stavanger_parking.gold.hourly import HOURLY_SCHEMA
from stavanger_parking.gold.pricing import (
    NO_DATA,
    NO_TARIFF,
    PRICE_SCHEMA,
    PRICED,
    STALE,
    PricingConfigError,
    load_rules,
    load_tariffs,
    suggested_prices,
)

ROOT = Path(__file__).parent.parent.parent
TARIFFS = ROOT / "config" / "tariffs.json"
RULES = ROOT / "config" / "pricing_rules.json"
# Wednesday 30 September 2026, 12:00 UTC is 14:00 in Oslo; 05:00 UTC is 07:00 (morning rush)
WEDNESDAY_14 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
WEDNESDAY_07 = datetime(2026, 9, 30, 5, 0, tzinfo=UTC)
FRIDAY_12 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)


def frame(rows: list[dict], schema: dict) -> pl.DataFrame:
    return pl.DataFrame({c: [r.get(c) for r in rows] for c in schema}, schema=schema)


FACILITIES = frame(
    [
        {"facility_key": 1, "facility_name": "Jernbanen", "capacity": 390},
        {"facility_key": 2, "facility_name": "Lervig", "capacity": None},
    ],
    FACILITY_SCHEMA,
)


def hour(start: datetime, free: float | None, key=1, covered=60.0, stale=0.0, most=None) -> dict:
    local = start + timedelta(hours=2)
    return {
        "facility_key": key,
        "date_key": int(local.strftime("%Y%m%d")),
        "hour": local.hour,
        "hour_start": start,
        "avg_available_spaces": free,
        "max_available_spaces": most if most is not None else free,
        "covered_minutes": covered,
        "counted_minutes": covered,
        "stale_minutes": stale,
    }


def price(*hours: dict, rules=None) -> pl.DataFrame:
    return suggested_prices(
        frame(list(hours), HOURLY_SCHEMA),
        FACILITIES,
        load_tariffs(TARIFFS),
        rules or load_rules(RULES),
    )


def only(prices: pl.DataFrame) -> dict:
    assert prices.height == 1
    return prices.row(0, named=True)


def test_the_repository_tariffs_cover_every_hour_of_every_facility():
    tariffs = load_tariffs(TARIFFS)

    assert tariffs.height == 9 * 7 * 24
    assert tariffs.group_by("facility").len()["len"].unique().to_list() == [168]


@pytest.mark.parametrize(
    ("weekday", "hour", "per_hour"),
    [(3, 12, 40.0), (5, 12, 60.0), (6, 20, 60.0), (5, 22, 40.0), (3, 3, 20.0)],
)
def test_weekday_exceptions_override_the_general_price(weekday, hour, per_hour):
    # Jernbanen: 20 per half hour 07-24, 10 at night, 30 on Fridays and Saturdays 11-21
    tariffs = load_tariffs(TARIFFS).filter(pl.col("facility") == "Jernbanen")

    row = tariffs.filter((pl.col("weekday_number") == weekday) & (pl.col("hour") == hour))
    assert row["base_price"].to_list() == [per_hour]


def test_kyrre_is_cheaper_on_weekend_afternoons():
    kyrre = load_tariffs(TARIFFS).filter((pl.col("facility") == "Kyrre") & (pl.col("hour") == 14))

    assert dict(kyrre.select("weekday_number", "base_price").rows())[7] == 16.0
    assert dict(kyrre.select("weekday_number", "base_price").rows())[3] == 32.0


def test_a_normal_hour_keeps_the_tariff():
    # 40 % occupied: the "low" band (below 50 %) lowers the price by 20 %
    row = only(price(hour(WEDNESDAY_14, free=234)))

    assert (row["occupancy"], row["occupancy_band"], row["base_price"]) == (0.4, "low", 40.0)
    assert (row["suggested_price"], row["applied_rule"], row["status"]) == (
        32.0,
        "occupancy:low",
        PRICED,
    )


def test_a_full_facility_in_the_rush_hour_costs_more_within_the_maximum():
    # 95 % occupied at 07:00 on a Wednesday: high (1.25) × morning rush (1.1) = 1.375
    row = only(price(hour(WEDNESDAY_07, free=19.5, most=20)))

    assert (row["occupancy_band"], row["rush_window"]) == ("high", "morning")
    assert row["multiplier"] == pytest.approx(1.375)
    assert row["suggested_price"] == 55.0  # 40 × 1.375
    assert row["applied_rule"] == "occupancy:high × rush:morning"


def test_a_price_above_the_maximum_is_capped_and_says_so(tmp_path):
    rules = json.loads(RULES.read_text()) | {}
    rules["occupancy_bands"][-1]["multiplier"] = 3.0
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules))

    row = only(price(hour(FRIDAY_12, free=0), rules=load_rules(path)))

    assert row["suggested_price"] == 90.0  # 60 × the 1.5 maximum, not 60 × 3
    assert row["applied_rule"] == "occupancy:high (capped at the maximum)"


def test_changing_a_multiplier_is_a_config_change_only(tmp_path):
    rules = json.loads(RULES.read_text())
    rules["occupancy_bands"][0]["multiplier"] = 0.5
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules))

    row = only(price(hour(WEDNESDAY_14, free=234), rules=load_rules(path)))

    assert row["suggested_price"] == 20.0  # 40 × 0.5, at the minimum


def test_a_missing_capacity_is_estimated_and_flagged():
    row = only(price(hour(WEDNESDAY_14, free=50, key=2, most=80)))

    assert (row["capacity"], row["capacity_is_estimated"]) == (80, True)
    assert row["occupancy"] == pytest.approx(1 - 50 / 80)


def test_a_known_capacity_is_not_flagged_as_estimated():
    row = only(price(hour(WEDNESDAY_14, free=234)))

    assert (row["capacity"], row["capacity_is_estimated"]) == (390, False)


def test_a_facility_without_a_tariff_is_not_priced():
    # Lervig has an estimated capacity but no tariff in the file
    row = only(price(hour(WEDNESDAY_14, free=50, key=2, most=80)))

    assert (row["status"], row["suggested_price"], row["applied_rule"]) == (
        NO_TARIFF,
        None,
        NO_TARIFF,
    )


def test_a_stale_hour_is_not_priced():
    row = only(price(hour(WEDNESDAY_14, free=234, stale=45.0)))

    assert (row["status"], row["suggested_price"]) == (STALE, None)
    assert row["occupancy"] == 0.4  # still shown, for the analysis


@pytest.mark.parametrize(("free", "covered"), [(None, 60.0), (234, 10.0)])
def test_an_hour_without_enough_data_is_not_priced(free, covered):
    row = only(price(hour(WEDNESDAY_14, free=free, covered=covered)))

    assert (row["status"], row["suggested_price"]) == (NO_DATA, None)


def test_the_output_schema_is_fixed():
    assert price(hour(WEDNESDAY_14, free=234)).schema == pl.Schema(PRICE_SCHEMA)


def write(tmp_path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def tariff(periods: list[dict]) -> dict:
    return {"unit": "half_hour", "facilities": [{"facility": "X", "periods": periods}]}


@pytest.mark.parametrize(
    ("periods", "message"),
    [
        ([{"from": "07:00", "to": "24:00", "price": 10}], "cover every hour"),
        (
            [
                {"from": "00:00", "to": "12:00", "price": 10},
                {"from": "11:00", "to": "24:00", "price": 10},
            ],
            "two prices at 11:00",
        ),
        ([{"from": "07:30", "to": "24:00", "price": 10}], "whole hours"),
        (
            [
                {"from": "00:00", "to": "24:00", "price": 10},
                {"weekdays": ["fredag"], "from": "11:00", "to": "12:00", "price": 5},
            ],
            "weekdays must be",
        ),
    ],
)
def test_invalid_tariffs_are_rejected(tmp_path, periods, message):
    with pytest.raises(PricingConfigError, match=message):
        load_tariffs(write(tmp_path, "tariffs.json", tariff(periods)))


def test_tariffs_in_another_unit_are_rejected(tmp_path):
    data = tariff([{"from": "00:00", "to": "24:00", "price": 10}]) | {"unit": "hour"}

    with pytest.raises(PricingConfigError, match="half_hour"):
        load_tariffs(write(tmp_path, "tariffs.json", data))


def test_bands_must_rise_and_end_open(tmp_path):
    rules = json.loads(RULES.read_text())
    rules["occupancy_bands"][-1]["below"] = 1.0

    with pytest.raises(PricingConfigError, match="bands must rise"):
        load_rules(write(tmp_path, "rules.json", rules))


def test_multipliers_must_be_positive(tmp_path):
    rules = json.loads(RULES.read_text())
    rules["rush_hours"][0]["multiplier"] = 0

    with pytest.raises(PricingConfigError, match="positive"):
        load_rules(write(tmp_path, "rules.json", rules))


def test_overlapping_rush_windows_are_rejected(tmp_path):
    rules = json.loads(RULES.read_text())
    rules["rush_hours"][1] |= {"from": "08:00", "to": "10:00"}

    with pytest.raises(PricingConfigError, match="morning and afternoon overlap"):
        load_rules(write(tmp_path, "rules.json", rules))


def test_a_missing_rules_field_is_named(tmp_path):
    rules = json.loads(RULES.read_text())
    del rules["limits"]["max_multiplier"]

    with pytest.raises(PricingConfigError, match="max_multiplier"):
        load_rules(write(tmp_path, "rules.json", rules))
