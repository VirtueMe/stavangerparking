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

**Status:** written without Power BI Desktop and running in the Power BI service. The project files, the visuals included, validate against Microsoft's published schemas, and the model's tables, columns and relationships are tested against the tables the gold build and the quality checks write (`tests/test_powerbi.py`). **On Databricks it runs in the Power BI service** (2026-09-30): `tools/report -p databricks --prod` published it to the Pro workspace, a refresh loaded all five tables with the row counts the pipeline wrote (`dim_date` 5844, `dim_time` 1440, `dim_parking_facility` 10, `fact_parking_availability` 9, `fact_parking_hourly` 1431), and every measure evaluates. **All five pages render with data** in the service (checked by eye on 2026-09-30, #87); the report has not been opened in Power BI Desktop.

## Opening it

1. Build the tables into a folder the Windows machine can read, for example `C:\stavangerparking\tables`:

    ```sh
    uv run python -m stavanger_parking.pipeline run --raw-root <data branch> --tables-root C:\stavangerparking\tables
    ```

    It also runs the quality checks. If a critical check fails, it ends with exit code 3; the tables are built all the same ([`docs/pipeline.md`](pipeline.md#exit-codes)).

2. Open `powerbi/StavangerParking.pbip` in Power BI Desktop.
3. Set the parameter **TablesRoot** (*Transform data → Edit parameters*) to that folder, and refresh.

The data source is defined once, in `expressions.tmdl`: `TablesRoot` and a `DeltaTable(name)` function that reads a Delta table with `DeltaLake.Table`. Every table's partition calls `DeltaTable("<table>")`.

## On a platform

On Databricks and Fabric the model reads the platform's tables instead. The model in `powerbi/` is not edited for that: `tools/report` generates a copy whose `expressions.tmdl` is the platform's own, [`platforms/<platform>/report/expressions.tmdl`](../platforms/databricks/report/expressions.tmdl), and publishes it to Power BI. Only that file differs, and it defines the same `DeltaTable(name)`, so the tables, measures and pages stay one definition (`tests/test_powerbi_platforms.py`).

```sh
uv run --only-group powerbi fab auth login   # once: the Fabric CLI, signed in as you
tools/report -p databricks --prod --dry-run   # generate dist/powerbi-databricks-prod/ only
tools/report -p databricks --prod             # generate it and publish to "Stavanger Parking Case"
tools/report -p databricks                    # dev: the dev schema, published to "My workspace"
```

- **One data source per generated model.** A single model that picks its source with an `if` on a parameter is simpler to switch in Desktop, but the Power BI service does not refresh a query whose data source depends on such a switch. Generating the model per platform keeps the service's view to one source.
- **Databricks** reads the Unity Catalog tables that the pipeline job's `publish` task writes ([`docs/databricks.md`](databricks.md)), through a SQL warehouse, with the Databricks connector. [`report.sh`](../platforms/databricks/report.sh) takes the host from the CLI profile, the warehouse's HTTP path from the workspace (`DATABRICKS_WAREHOUSE` names one if there are several), and the catalog and schema from the bundle target, so the workspace stays out of the repository. `POWERBI_WORKSPACE` overrides the Power BI workspace.
- **Fabric** reads the Lakehouse tables through its SQL analytics endpoint in import mode. It is written but not tried (#16), and has no `report.sh` until Fabric has a platform folder. Direct Lake would need other partitions, not another function.
- **Publishing** imports the semantic model and then the report with the [Fabric CLI](https://aka.ms/fabric-cli) (dependency group `powerbi`), through the Fabric REST API; this works on a Pro workspace, no Fabric capacity needed. The published report points at the published model by its id. Publishing replaces both, so changes made in the service are lost at the next publish: the repository is the definition. On Linux without a keyring, the CLI's login needs `fab config set encryption_fallback_enabled true`, which stores its token unencrypted under `~/.config/fab/`.
- **After the first publish**, set the model's credentials once in the Power BI service (the semantic model's *Settings → Data source credentials*): for Databricks, a personal access token. A cloud source needs no gateway on Pro. Then refresh, or set a refresh schedule there.
- `--dry-run` only generates the project. It opens in Power BI Desktop as it is, with the model next to the report, which is also how to use it on a machine without the Fabric CLI.

## The model

Three dimensions and two facts, as in the star schema, joined fact to dimension, and the quality results (`quality_check_results`, [`docs/quality.md`](quality.md)) as a table of their own, related to nothing:

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
| `Capacity` | availability | The facilities' capacity from the national parking register, summed; blank without one |
| `Readings` | availability | Number of source readings; never average over them unweighted |
| `Data age (minutes)` | availability | How old the newest reading was at the last fetch that saw it |
| `Source status` | availability | `Stale` if a current reading was seen stale, otherwise `Fresh` |
| `Attribution` | availability | The NLOD 2.0 attribution text for both sources |
| `Failed checks (latest run)` | quality results | Failed checks in the latest quality run; blank for older runs, so a table of checks lists only the latest failures |

These follow the rules in [`docs/gold.md`](gold.md): readings are irregular, so averages are weighted by time; free spaces are semi-additive, so they add up across facilities at one time, never over time.

## Pages

The report has five pages, each with a title and the attribution footer. The visuals are PBIR files, one folder per visual in `powerbi/StavangerParking.Report/definition/pages/<page>/visuals/`, written as files rather than in Power BI Desktop, reviewed in pull requests and published with `tools/report` (#87):

| Page | Visuals |
|---|---|
| **Availability over time** | Line chart of `Free spaces (avg)` by `dim_date[date]` and `fact_parking_hourly[hour]`, one line per `facility_name`; a card with `Free spaces (latest)`; slicers for date and facility |
| **Weekday and hour patterns** | Matrix of `Occupancy` (or `Free spaces (avg)`) with `dim_date[weekday]` in rows and `fact_parking_hourly[hour]` in columns, conditional formatting as a heat map; slicer for facility; a toggle to exclude public holidays (`dim_date[is_public_holiday]`) |
| **Facilities on the map** | Azure Maps bubbles at `dim_parking_facility[latitude]`, `[longitude]`, sized by `Free spaces (latest)`, tooltip with `Capacity` and `Occupancy`; beside it a table of the same figures, which also stands in for the map if the tenant has map visuals turned off |
| **Data freshness and quality** | Cards for `Source status` and `Data age (minutes)`; column chart of `Stale share` and `Covered minutes` by date; table of the failed checks in the latest quality run (`quality_check_results`, with the measure `Failed checks (latest run)`) |
| **About the data** | A text box or card with `Attribution`, links to the licence and the sources, and a note on what the stale and occupancy figures mean |

The **attribution** is required by the NLOD 2.0 licences of both sources (#32, #54): *Contains data under the Norwegian licence for Open Government data (NLOD) distributed by Stavanger kommune* (Stavanger parkering) and *by Statens vegvesen* (Parkeringsregisteret), with a link to the licence, <https://data.norge.no/nlod/en/2.0>. It belongs on the About page and, if the report is published, in a footer on every page.

## Not verified yet

Until the project is opened in Power BI Desktop or on Fabric:

- **The M data source.** `DeltaLake.Table(Folder.Contents(...))` for a local folder is the documented function with a folder listing; whether it reads the local Delta tables as written by delta-rs has not been tried. The Databricks source refreshes in the service; the Fabric source follows the navigation its connector generates and has not been tried (#16).
- **Formatting in the visual files.** The published schemas type a visual's query and position, but leave its formatting (titles, colours, labels) loosely typed, so a mistake there passes the tests and shows only in the service. Text boxes need room for their text plus padding, or Power BI adds a scrollbar: check a changed page by eye after publishing.
- **The theme** named in `report.json` (`CY24SU10`), a built-in theme name that may not match the installed version of Power BI Desktop; if Desktop objects, pick a theme there and save.

Whatever Power BI Desktop changes when the project is first saved should be committed, and `tests/test_powerbi.py` keeps the model's columns in step with the code from then on.
