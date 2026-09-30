# Walkthrough: 22 October 2026

A 10–15 minute walkthrough of the case, following its requirements: the architecture and data model, fetching the data, the platform, the report, a new facility, scalability, PySpark or Polars, and the weaknesses. Each part says **what to show** and **what to say**; the links go to the detail, for questions.

About 14 minutes in all, then questions.

| # | Part | Minutes | Case requirement |
|---|---|---|---|
| 1 | [The case and where it stands](#1-the-case-and-where-it-stands) | 1 | |
| 2 | [Architecture and data flow](#2-architecture-and-data-flow) | 2 | Overview of the data model and data flow; medallion structure |
| 3 | [The data model](#3-the-data-model) | 1.5 | Suitable fact and dimension tables |
| 4 | [Fetching the data, outside the platform](#4-fetching-the-data-outside-the-platform) | 2 | Fetch data programmatically from opencom.no |
| 5 | [One repository, two platforms](#5-one-repository-two-platforms) | 2 | Lakehouse, notebooks, pipelines with dependencies and failure alerts |
| 6 | [The report, live](#6-the-report-live) | 2 | Optional: visualise in Power BI |
| 7 | [A new facility](#7-a-new-facility) | 1 | How the code handles a new parking facility |
| 8 | [Scalability, and PySpark or Polars](#8-scalability-and-pyspark-or-polars) | 1.5 | Is the solution scalable? PySpark vs. Polars |
| 9 | [Known weaknesses](#9-known-weaknesses) | 1 | Potential weaknesses |

## Before the meeting

- **Bring Databricks up to date:** `tools/backfill -p databricks --prod` copies the new raw files from the `data` branch and runs the pipeline. It ends with exit code 3 while Forum's capacity is wrong; the tables are published all the same ([`docs/databricks.md`](databricks.md#backfill)).
- **Refresh the report** in the Power BI service (the semantic model's *Refresh now*), and check the freshness page shows today's data age.
- **Check collection is running:** the latest commit on the [`data` branch](https://github.com/VirtueMe/stavangerparking/tree/data) is minutes old, and `collect gaps` lists nothing new ([`docs/collector.md`](collector.md)).
- **Check the source:** is the feed still frozen at 23 September, 19:16? If it has recovered, parts 4 and 9 change: the stale period now has an end.
- **Open, in tabs:** the [README](../README.md), the report in the "Stavanger Parking Case" workspace, the Databricks jobs page, [`docs/weaknesses.md`](weaknesses.md) and the [ADR index](adr/README.md).
- The Databricks workspace is Free Edition and temporary; if it is gone, the report still shows its last refresh, and the rest runs locally.

## 1. The case and where it stands

**Show:** the README's [Status](../README.md#status).

**Say:**

- The case asked for a Fabric solution for Stavanger's open parking data: ingestion, transformation, a model, and optionally a report.
- Fabric needs a capacity, and the own tenant is too new for a trial. Rather than wait, the solution was built so that the platform is a deployment choice: it runs on Databricks today, and Fabric is written for.
- What runs now: collection every 5 minutes since 28 September, the whole pipeline on Databricks, and a Power BI report refreshed from it.
- The source itself stopped updating on 23 September. The solution notices that, and shows it rather than hiding it; that is a thread through the rest.

## 2. Architecture and data flow

**Show:** the [architecture diagram](../README.md#architecture), then the [data flow](../README.md#data-flow).

**Say:**

- **The raw files are the source of truth.** Every snapshot is stored as fetched, with a sidecar that says when, from where, and whether its values changed. Everything else is rebuilt from them, on any platform.
- **Medallion layers:** bronze loads each raw file once, as text; silver types it, converts Oslo time to UTC, deduplicates repeated fetches into readings and quarantines what it cannot parse; gold builds the star schema; a quality step checks the result, and a critical failure stops the run.
- **One entry point** runs all of it: `stavanger-parking-pipeline run --raw-root … --tables-root …`. A platform only has to call it ([`docs/pipeline.md`](pipeline.md)).
- Incremental runs give the same tables as a rebuild, and the tests check that ([ADR 009](adr/009-silver-model.md)).

## 3. The data model

**Show:** the [star schema](../README.md#star-schema); if asked, the columns in [`docs/architecture.md`](architecture.md#star-schema).

**Say:**

- **`fact_parking_availability`** is a periodic snapshot: one row per facility per source reading, valid until the next one.
- **Collection is irregular** (adaptive polling), so a plain average over rows would over-weight busy periods: every average over time is **weighted by how long each reading was valid**.
- **Free spaces are semi-additive:** they add up across facilities at one moment ("free spaces in the centre now"), never across time.
- **`fact_parking_hourly`** aggregates per facility and hour, and says how much of the hour is covered by readings and how much was stale, so gaps show instead of disappearing into an average.
- **Dimensions:** date with Norwegian public holidays, time per minute with 15-minute buckets, and facility, whose capacity comes from the national parking register ([ADR 005](adr/005-capacity-as-reference-data.md)).

## 4. Fetching the data, outside the platform

**Show:** [`collect.yml`](../.github/workflows/collect.yml) briefly, then the [`data` branch](https://github.com/VirtueMe/stavangerparking/tree/data) with a raw file and its sidecar.

**Say:**

- The source only has the **current state**: any period not collected is lost for good. So collection started on day one, outside any platform, on GitHub Actions ([ADR 007](adr/007-collect-outside-the-platform.md)).
- The download URL is resolved through the CKAN API on every run, so a moved resource is followed.
- **Adaptive polling:** every 5 minutes while values change, every 20 once they have stood still for 5 snapshots ([ADR 003](adr/003-polling-interval.md)). The register's capacities are collected hourly ([ADR 010](adr/010-collect-the-register-hourly.md)).
- **GitHub's own schedule started 2 of about 145 runs** in the first 12 hours, so an external scheduler, cron-job.org, now starts each run ([ADR 008](adr/008-trigger-collection-externally.md)).
- **How the frozen feed was found:** the file is re-uploaded every 2 minutes, and every "updated" signal moves with it: the dataset page, CKAN's timestamps, the HTTP headers, the ETag. Only comparing the content showed that the values have not changed since 23 September. Freshness is therefore judged from the data's own timestamp, never from metadata.

## 5. One repository, two platforms

**Show:** the [platform mapping](architecture.md#platform-mapping), the Databricks jobs page, and `tools/deploy -p databricks --prod --dry-run` (it plans and deploys nothing).

**Say:**

- **One repository, one package, thin platform folders** ([ADR 011](adr/011-one-repository-two-platforms.md)). The package imports nothing platform-specific, and a test keeps it that way. `platforms/databricks/` holds the Asset Bundle; `platforms/fabric/` is where Fabric's items go.
- **On Databricks:** the released wheel runs as a job task on serverless compute, into Unity Catalog volumes; a second task publishes the report's tables to Unity Catalog. The tasks depend on each other, the publish runs even when a critical check fails (so the tables with the failure are still there), and a failed run e-mails the owner.
- **On Fabric** the same entry point runs from a notebook in a Data pipeline, with the Lakehouse's `Files/` and `Tables/`: the mapping is written, and waits for a capacity (#16).
- `tools/deploy`, `tools/backfill` and `tools/report` do the same thing on either platform, with a dry run first.
- **Collection stays in one place** until a platform takes over with a handover, so every period of history has exactly one collector. Databricks' collector is deployed but paused: Free Edition blocks the sources.

## 6. The report, live

**Show:** the report in the service, page by page.

**Say:**

- **Availability over time:** free spaces per facility, time-weighted, by date and hour; the card is the latest reading, summed across facilities.
- **Weekday and hour patterns:** occupancy as a heat map; public holidays can be left out.
- **The map**, with a table of the same figures beside it.
- **Data freshness and quality:** the source is **Stale**, its data about 9,500 minutes old on the day the report was first published, and the latest quality run failed on **Forum: 292 free spaces against a registered capacity of 289**. That stops every pipeline run, on purpose: a capacity that is wrong makes occupancy wrong, and the report shows it rather than clamping it.
- **About:** both sources, the NLOD attribution (also in every page's footer), and what "stale" and "occupancy" mean.
- The model and report are files in the repository, published with `tools/report`, which gives each platform its own data source so the service can refresh it ([`docs/report.md`](report.md#on-a-platform)).

## 7. A new facility

**Show:** [A new facility](../README.md#a-new-facility), and the test [`test_a_new_facility_appears_on_the_next_run_with_unknown_capacity`](../tests/gold/test_gold_build.py).

**Say:**

- **No code or configuration change.** The facility appears in the next snapshot, and the facility MERGE gives it a new key and a `first_seen`. The test runs the whole pipeline with a synthetic tenth facility.
- **Its capacity is unknown until someone adds it to the facility mapping**, one line. Until then, the quality check stops the run, deliberately, so a new facility cannot go unnoticed.
- A facility that disappears is marked inactive, never deleted. A renamed one appears as new, because the feed has no id: the name is the key ([ADR 004](adr/004-facility-name-as-natural-key.md)).

## 8. Scalability, and PySpark or Polars

**Show:** [How it scales](../README.md#how-it-scales).

**Say:**

- **Polars and delta-rs, not Spark** ([ADR 001](adr/001-polars-over-pyspark.md)): the data fits on one machine many times over. A year of this feed is about a second and 1 GB for the whole derivation, and Spark would add a JVM and a slower start to every 5-minute run for no benefit.
- **Measured, not guessed** ([ADR 006](adr/006-scalability-assessment.md)): the limit is memory, about 13 GB at 19 million fetch rows. That is two decades of this feed, or 90 facilities for two years.
- **What changes first:** derive only the dates a run touches, or move to Spark, before the fetch table nears 20 million rows. The Delta tables are engine-neutral, so readers and the report are unaffected, and the transformations are pure functions with tests that serve as the specification for a rewrite.
- **What does not scale by configuration:** a second municipality. The model is written for one parking source; it needs a field mapping and the source in the facility's key.

## 9. Known weaknesses

**Show:** [Known weaknesses](../README.md#known-weaknesses), and mention that [`docs/weaknesses.md`](weaknesses.md) has been kept as each trade-off was made.

**Say** (pick three):

- **The source:** frozen since 23 September, with every metadata signal saying otherwise, and no support channel beyond an e-mail address.
- **Capacity:** a hand-maintained mapping to a register that can be wrong, as Forum shows.
- **Collection** depends on cron-job.org, and nothing alerts if its calls stop; gaps are listed afterwards from the sidecars.
- **The facility name as key:** a rename splits a facility's history.
- **The platform:** Databricks Free Edition cannot reach the sources, and deploying is manual (#83).

## Likely questions

| Question | Short answer | More |
|---|---|---|
| Why not Spark, since Databricks and Fabric are built around it? | The volume fits in memory with room to spare; Spark would cost a JVM and start-up on every run. The switch point is measured, and the tables stay the same | [ADR 001](adr/001-polars-over-pyspark.md), [ADR 006](adr/006-scalability-assessment.md) |
| Why collect on GitHub Actions and not on the platform? | History cannot be recovered, and platform access was not there on day one. The platform takes over with a handover that keeps one collector | [ADR 007](adr/007-collect-outside-the-platform.md), [ADR 011](adr/011-one-repository-two-platforms.md#collection-one-collector-of-record-and-a-handover) |
| What happens when the feed starts updating again? | Nothing to change: the stale period gets its end, and the next readings are fresh | [`docs/silver.md`](silver.md#source-staleness) |
| Why does every run fail? | A critical quality check: Forum's registered capacity (289) is below its reported free spaces (292). It fails on purpose until the capacity is corrected; the tables are still built and published | [`docs/quality.md`](quality.md) |
| Why a 5-minute interval, when the source publishes every 2? | 5 minutes is the scheduler's floor; the analyses use 15-minute buckets, so a finer grain would add runs and rows without adding insight | [ADR 003](adr/003-polling-interval.md) |
| Why not Direct Lake on Fabric? | It would be the natural choice there; it needs other partitions in the model, not another function, and is planned for when Fabric exists | [`docs/report.md`](report.md#on-a-platform) |
| How much of it is tested? | The package, the entry point, the wheel, the tools and the report's files, on every pull request; the deployments are checked by hand on the platform | [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| Could it suggest prices? | Yes, as an example: `fact_suggested_price` applies example rules to occupancy; real rules are the operator's decision | [`docs/pricing.md`](pricing.md) |
