# Power BI report

The report is a [Power BI project (PBIP)](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview) in [`powerbi/`](../powerbi): the semantic model as TMDL text, and a report with its pages. Both are plain text under version control. The model encodes the rules of the star schema ([`docs/gold.md`](gold.md)), so a report built on it cannot average over irregular readings or sum free spaces over time by accident.

```
powerbi/
├── StavangerParking.pbip                 # open this in Power BI Desktop
├── StavangerParking.SemanticModel/
│   ├── definition.pbism
│   └── definition/                       # TMDL: model, tables, relationships, expressions
└── StavangerParking.Report/
    ├── definition.pbir                   # points at ../StavangerParking.SemanticModel
    └── definition/                       # PBIR: report, pages
```

**Status:** written and checked without Power BI. The project files validate against Microsoft's published schemas, and the model's tables, columns and relationships are tested against the tables the gold build writes (`tests/test_powerbi.py`). Power BI Desktop runs only on Windows and there is no Fabric workspace yet (#16), so the model has **not been opened, refreshed or rendered**; the pages have no visuals yet, and the layout below is the specification for them.

## Opening it

1. Build the tables into a folder the Windows machine can read, for example `C:\stavangerparking\tables`:

    ```sh
    uv run python -m stavanger_parking.pipeline run --raw-root <data branch> --tables-root C:\stavangerparking\tables
    ```

    It also runs the quality checks. While Forum's capacity in the register is wrong, it ends with exit code 3; the tables are built all the same ([`docs/pipeline.md`](pipeline.md#exit-codes)).

2. Open `powerbi/StavangerParking.pbip` in Power BI Desktop.
3. Set the parameter **TablesRoot** (*Transform data → Edit parameters*) to that folder, and refresh.

The data source is defined once, in `expressions.tmdl`: `TablesRoot` and a `DeltaTable(name)` function that reads a Delta table with `DeltaLake.Table`. Every table's partition calls `DeltaTable("<table>")`. **On Fabric** (#16), the tables live in the Lakehouse: point `DeltaTable` at the Lakehouse, or switch the partitions to Direct Lake; nothing else in the model changes.

## The model

Three dimensions and two facts, as in the star schema, joined fact to dimension:

| Relationship | |
|---|---|
| `fact_parking_availability` → `dim_parking_facility`, `dim_date`, `dim_time` | by `facility_key`, `date_key`, `time_key` |
| `fact_parking_hourly` → `dim_parking_facility`, `dim_date` | by `facility_key`, `date_key`; the hour is a column of the fact |

Every column has a description (from `docs/gold.md`). Month and weekday names sort by their numbers; the facility's latitude and longitude are categorized for maps. Implicit measures are discouraged, so values are shown through the measures below, never by dragging a column in and letting Power BI sum it.

### Measures

| Measure | Table | What it does |
|---|---|---|
| `Free spaces (latest)` | availability | Each facility's latest reading in the selection, **summed across facilities**: "free spaces now" in the city, without summing over time |
| `Free spaces (avg)` | hourly | **Time-weighted** average per facility (hours weighted by their counted minutes, the minutes with a count), summed across facilities |
| `Free spaces (min)`, `Free spaces (max)` | hourly | The lowest and highest single reading in the selection |
| `Occupancy` | hourly | Share of capacity in use, over facilities with a known capacity; outside 0–100 % means the capacity is wrong |
| `Covered minutes`, `Stale minutes`, `Stale share` | hourly | How much of the time is covered by readings, and how much of it rests on stale data |
| `Readings` | availability | Number of source readings; never average over them unweighted |
| `Data age (minutes)` | availability | How old the newest reading was at the last fetch that saw it |
| `Source status` | availability | `Stale` if a current reading was seen stale, otherwise `Fresh` |
| `Attribution` | availability | The NLOD 2.0 attribution text for both sources |

These follow the rules in [`docs/gold.md`](gold.md): readings are irregular, so averages are weighted by time; free spaces are semi-additive, so they add up across facilities at one time, never over time.

## Pages

The report has five pages. Their layout is specified here and done in Power BI Desktop:

| Page | Visuals |
|---|---|
| **Availability over time** | Line chart of `Free spaces (avg)` by `dim_date[date]` and `fact_parking_hourly[hour]`, one line per `facility_name`; a card with `Free spaces (latest)`; slicers for date and facility |
| **Weekday and hour patterns** | Matrix of `Occupancy` (or `Free spaces (avg)`) with `dim_date[weekday]` in rows and `fact_parking_hourly[hour]` in columns, conditional formatting as a heat map; slicer for facility; a toggle to exclude public holidays (`dim_date[is_public_holiday]`) |
| **Facilities on the map** | Map with `dim_parking_facility[latitude]`, `[longitude]`, bubble size `Free spaces (latest)`, tooltip with `capacity` and `Occupancy` |
| **Data freshness and quality** | Cards for `Source status` and `Data age (minutes)`; column chart of `Stale share` and `Covered minutes` by date; table of the latest `quality_check_results` failures once that table is added to the model |
| **About the data** | A text box or card with `Attribution`, links to the licence and the sources, and a note on what the stale and occupancy figures mean |

The **attribution** is required by the NLOD 2.0 licences of both sources (#32, #54): *Contains data under the Norwegian licence for Open Government data (NLOD) distributed by Stavanger kommune* (Stavanger parkering) and *by Statens vegvesen* (Parkeringsregisteret), with a link to the licence, <https://data.norge.no/nlod/en/2.0>. It belongs on the About page and, if the report is published, in a footer on every page.

## Not verified yet

Until the project is opened in Power BI Desktop or Fabric:

- **The M data source.** `DeltaLake.Table(Folder.Contents(...))` for a local folder is the documented function with a folder listing; whether it reads the local Delta tables as written by delta-rs has not been tried.
- **The DAX.** The measures follow the documented functions and the model's rules, but have not been evaluated.
- **The theme** named in `report.json` (`CY24SU10`), a built-in theme name that may not match the installed version of Power BI Desktop; if Desktop objects, pick a theme there and save.

Whatever Power BI Desktop changes when the project is first saved should be committed, and `tests/test_powerbi.py` keeps the model's columns in step with the code from then on.
