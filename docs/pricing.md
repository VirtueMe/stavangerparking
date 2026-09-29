# Pricing: suggested prices

`fact_suggested_price` suggests an hourly price per facility: today's tariff, adjusted by how full the facility was and whether it was a rush hour. It is built with the other gold tables (`python -m stavanger_parking.gold.build`), from the hourly fact and two configuration files. Changing a price, a threshold or a multiplier is a change to those files, never to code.

## Tariffs: `config/tariffs.json`

The prices Stavanger Parkering publishes ([stavanger-parkering.no](https://stavanger-parkering.no/en/parkering/p-hus/), checked 2026-09-29), entered by hand in the unit they are published in: **NOK per half hour**. Each facility has periods covering the day; a period that names weekdays overrides the general period on those days. The file is validated on load: every hour of every day must have exactly one general price, periods start on the hour, and weekdays are `mon` to `sun`.

| Facility (feed) | Operator | 07–24 (NOK per half hour) | 00–07 | Weekday exceptions | Max per day |
|---|---|---|---|---|---|
| Jernbanen | P-Jernbanen | 20 | 10 | Fri–Sat 11–21: 30 | 310 |
| Valberget | P-Valberghallen | 23 (07–11, 21–24), 30 (11–21) | 10 | | 310 |
| Posten | P-Posten | 18 | 9 | | 310 |
| Jorenholmen | P-Jorenholmen | 23 | 10 | Fri–Sat 11–18: 30 | none |
| St Olav | P-St. Olav | 18 | 9 | | 310 |
| Siddis | P-Siddis | 13 | 8 | | 161 |
| Forum | P-Forum | 13 | 8 | | 310 |
| Kyrre | P-Kyrre | 16 | 8 | Sat–Sun 11–18: 8 | 310 |
| Parketten | P-Arketten | 13 (07–16), 20 (16–24) | 8 | | 310 |

The operator already prices by time of day and weekday: Jernbanen and Jorenholmen cost more on Friday and Saturday middays, and Kyrre less on weekend afternoons. The daily maximum is recorded but not used, since suggestions are per hour.

## Rules: `config/pricing_rules.json`

**An example policy for the analysis, not Stavanger Parkering's.** Setting real thresholds and multipliers is the operator's decision (#27).

| Rule | Example setting |
|---|---|
| Occupancy bands | `low` below 50 %: × 0.8; `normal` below 85 %: × 1.0; `high` from 85 %: × 1.25 |
| Rush hours | `morning` Mon–Fri 07–09: × 1.1; `afternoon` Mon–Fri 15–17: × 1.1 |
| Limits | between 0.5 and 1.5 times the tariff; a facility can have its own minimum and maximum in NOK per hour |
| Data | an hour is priced only if it has at least 30 covered minutes, and at most half of them are stale |

Rush hours are local hours and weekdays, the same as `fact_parking_hourly.hour` and `dim_date.weekday_number`.

## `fact_suggested_price`

One row per facility per hour of `fact_parking_hourly`.

| Column | Meaning |
|---|---|
| `facility_key`, `date_key`, `hour`, `hour_start` | As in the hourly fact |
| `occupancy` | 1 − time-weighted average free spaces / capacity |
| `capacity`, `capacity_is_estimated` | The register's capacity; where there is none, the highest number of free spaces ever observed, flagged `true` |
| `base_price` | The tariff for that local hour and weekday, NOK per hour |
| `occupancy_band`, `rush_window`, `multiplier` | The rules that applied, and their combined multiplier |
| `suggested_price` | `base_price × multiplier`, kept within the limits and rounded to whole kroner; blank when the hour is not priced |
| `applied_rule` | How the price came about, e.g. `occupancy:high × rush:morning`, `occupancy:high (capped at the maximum)`; or why it was not priced |
| `status` | `priced`, `stale` (mostly stale data), `no_data` (no count, or under 30 covered minutes) or `no_tariff` |

**An estimated capacity is never presented as actual capacity:** it is only a lower bound (the facility may never have been empty while we watched), so occupancy computed from it is too high, and `capacity_is_estimated` marks every such row.

## On the current data

Every hour since 23 September rests on the frozen feed, so **no hour is priced**: 1,001 hours are `stale`, and the rest `no_data` (Posten and Kyrre report only "Open"). Pricing on data that is days old would be pricing on a fiction; the table says so instead.

What it would take to show such prices to drivers on boards at the entrance, including the legal requirements, is discussed in [`docs/price-boards.md`](price-boards.md).
