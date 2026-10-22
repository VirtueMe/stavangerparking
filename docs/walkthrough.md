# Walkthrough: 22 October 2026

A 10–15 minute walkthrough of the case, following its requirements: the architecture and data model, fetching the data, the platform, a change made live, the report, a new facility, scalability, PySpark or Polars, and the weaknesses. Each part says **what to show** and **what to say**; the links go to the detail, for questions.

About 15 minutes in all, then questions.

| # | Part | Minutes | Case requirement |
|---|---|---|---|
| 1 | [The case and where it stands](#1-the-case-and-where-it-stands) | 1 | |
| 2 | [Architecture and data flow](#2-architecture-and-data-flow) | 1.5 | Overview of the data model and data flow; medallion structure |
| 3 | [The data model](#3-the-data-model) | 1.5 | Suitable fact and dimension tables |
| 4 | [Fetching the data, outside the platform](#4-fetching-the-data-outside-the-platform) | 1.5 | Fetch data programmatically from opencom.no |
| 5 | [One repository, two platforms](#5-one-repository-two-platforms) | 2 | Lakehouse, notebooks, pipelines with dependencies and failure alerts |
| 6 | [A change, live](#6-a-change-live) | 1.5 | How changes reach the platform |
| 7 | [The report, live](#7-the-report-live) | 2 | Optional: visualise in Power BI |
| 8 | [A new facility](#8-a-new-facility) | 1 | How the code handles a new parking facility |
| 9 | [Scalability, and PySpark or Polars](#9-scalability-and-pyspark-or-polars) | 1.5 | Is the solution scalable? PySpark vs. Polars |
| 10 | [Known weaknesses, and the change landing](#10-known-weaknesses-and-the-change-landing) | 1.5 | Potential weaknesses |

## Before the meeting

- **The change for part 6:** the pull request for [#91](https://github.com/VirtueMe/stavangerparking/issues/91), [#97](https://github.com/VirtueMe/stavangerparking/pull/97) (Forum's 26 reserved spaces), is open, its checks are green, and it merges without conflicts; rebase it if `main` has moved. Do not merge it before the meeting.
- **Logins for part 6:** `gh auth status`, `databricks auth describe` and `uv run --only-group powerbi fab auth status` all say you are signed in, and `tools/deploy -p databricks --prod --dry-run` plans without errors.
- **Bring Databricks and the report up to date, in this order:**
  1. `tools/deploy -p databricks --prod`, if a release came out since the last deploy (`--dry-run` shows which release it would install).
  2. `tools/backfill -p databricks --prod` copies the new raw files from the `data` branch and runs the pipeline job ([`docs/databricks.md`](databricks.md#backfill)).
  3. **Wait until the run has ended and its `publish` task succeeded**: the Databricks jobs page, or `databricks jobs list-runs`. The job's overall state is not the signal: a failed critical check ends the `pipeline` task with exit code 3 and the job with `SUCCESS_WITH_FAILURES`, and `publish` runs all the same.
  4. `tools/report -p databricks --prod`, only if the report changed since it was last published.
  5. Refresh the semantic model in the Power BI service (*Refresh now*, under two minutes), and check the freshness page shows today's data age.

  **Never refresh while a run is publishing.** `publish` replaces every table, so a refresh that reads them meanwhile can mix tables from before and after the run, or find one missing: on 1 October a refresh started 25 seconds before a run and hung for many minutes.
- **Whether a check fails today depends on the hour,** so nothing below promises either way: Forum exceeds its registered 289 only when it is nearly empty, and Jernbanen its 390 at night ([#112](https://github.com/VirtueMe/stavangerparking/issues/112)).
- **Check collection is running:** the latest commit on the [`data` branch](https://github.com/VirtueMe/stavangerparking/tree/data) is minutes old, and `collect gaps` lists nothing new ([`docs/collector.md`](collector.md)).
- **Check the source:** note the data's own timestamp and whether the feed is live or frozen today. Parts 1, 4 and 10 tell the 23 September – 1 October freeze as a past incident either way. If it is frozen again, say so, and show the open stale period in the report.
- **Open, in tabs:** the [README](../README.md), the report in the "Stavanger Parking Case" workspace, the Databricks jobs page, the Databricks SQL editor with [the query for part 6](#the-reading-for-part-6), [`docs/weaknesses.md`](weaknesses.md) and the [ADR index](adr/README.md).
- The Databricks workspace is Free Edition and temporary; if it is gone, the report still shows its last refresh, and the rest runs locally.

## 1. The case and where it stands

**Show:** the README's [Status](../README.md#status).

**Say:**

- The case asked for a Fabric solution for Stavanger's open parking data: ingestion, transformation, a model, and optionally a report.
- Fabric needs a capacity, and the own tenant is too new for a trial. Rather than wait, the solution was built so that the platform is a deployment choice: it runs on Databricks today, and Fabric is written for.
- What runs now: collection every 5 minutes since 28 September, the whole pipeline on Databricks, and a Power BI report refreshed from it.
- The source itself can stop updating without notice: it was frozen from 23 September to 1 October, then recovered on its own. The solution notices that, and shows stale data as stale rather than hiding it; that is a thread through the rest.

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
- **How the frozen feed was found:** the file is re-uploaded every 2 minutes, and every "updated" signal moves with it: the dataset page, CKAN's timestamps, the HTTP headers, the ETag. Only comparing the content showed that the values had not changed since 23 September. It recovered on 1 October, unreported and unannounced, and the metadata looked just as fresh before and after. Freshness is therefore judged from the data's own timestamp, never from metadata.

## 5. One repository, two platforms

**Show:** the [platform mapping](architecture.md#platform-mapping), the Databricks jobs page, and `tools/deploy -p databricks --prod --dry-run` (it plans and deploys nothing).

**Say:**

- **One repository, one package, thin platform folders** ([ADR 011](adr/011-one-repository-two-platforms.md)). The package imports nothing platform-specific, and a test keeps it that way. `platforms/databricks/` holds the Asset Bundle; `platforms/fabric/` is where Fabric's items go.
- **On Databricks:** the released wheel runs as a job task on serverless compute, into Unity Catalog volumes; a second task publishes the report's tables to Unity Catalog. The tasks depend on each other, the publish runs even when a critical check fails (so the tables with the failure are still there), and a failed run e-mails the owner.
- **On Fabric** the same entry point runs from a notebook in a Data pipeline, with the Lakehouse's `Files/` and `Tables/`: the mapping is written, and waits for a capacity (#16).
- `tools/deploy`, `tools/backfill` and `tools/report` do the same thing on either platform, with a dry run first.
- **Collection stays in one place** until a platform takes over with a handover, so every period of history has exactly one collector. Databricks' collector is deployed and works (tested on 2 October), but stays paused until such a handover.

## 6. A change, live

**Show:** the pull request for [#91](https://github.com/VirtueMe/stavangerparking/issues/91), the query below in the Databricks SQL editor (Forum's occupied spaces: −3), then GitHub Actions, then a terminal.

**Say, while doing it:**

- **The finding:** on 23 September at 19:16, Forum reported 292 free spaces, and the register says 289: occupancy −3. While that was the newest reading, through the freeze, the quality check stopped every run. With live data it depends on the hour: Forum exceeds 289 only when it is nearly empty. The register records *public* parking; the garage also has **26 reserved spaces**, for Madla and Tjensvoll HBT (the municipality's home care), Kolumbus and one private holder. The feed counts them, the register does not: 289 + 26 = 315.
- **How it was found:** the register links each area to the sign plan the operator filed, and Forum's floor 1 has 26 reserved-space signs. The first guess, that charging and accessible spaces come on top of the paid ones, was wrong: the same sign plan says they are ordinary paid spaces. The check did its job: two sources count different spaces, and it said so.
- **The change:** `reserved_spaces: 26` in the facility mapping, with its source in the note; gold adds it to the register's count; an ADR 005 addendum and tests. Merge it (squash).
- **What happens next, without anyone deploying by hand from a branch:**
  1. The merge makes a release, about 20 seconds: a tag, the changelog, and the wheel on the GitHub Release.
  2. `tools/deploy -p databricks --prod` installs that release, the latest by default (about a minute; `--dry-run` first shows the plan).
  3. `tools/backfill -p databricks --prod` runs the pipeline with the new wheel (about 3–4 minutes). **Leave it running and go on to part 7.**
- **If anything goes wrong:** `tools/deploy -p databricks --prod <tag>`, with the release before the merge (the Releases page lists it), puts it back.

### The reading for part 6

The 23 September 19:16 reading (17:16 UTC) stays in `fact_parking_availability` whatever the feed does on the day, so it shows the change the same way every time: before the merge, capacity 289 and **−3** occupied; after it, 315 and **23**.

```sql
SELECT f.facility_name, f.capacity, a.available_spaces, a.occupied_spaces
FROM workspace.stavanger_parking.fact_parking_availability a
JOIN workspace.stavanger_parking.dim_parking_facility f USING (facility_key)
WHERE f.facility_name = 'Forum' AND a.valid_from = TIMESTAMP '2026-09-23 17:16:00 UTC'
```

## 7. The report, live

**Show:** the report in the service, page by page. It still shows the data from before the change: the backfill from part 6 is running.

**Say:**

- **Availability over time:** free spaces per facility, time-weighted, by date and hour; the card is the latest reading, summed across facilities.
- **Weekday and hour patterns:** occupancy as a heat map, leaving out the hours when the source was frozen; public holidays can be left out.
- **The map**: each facility's latest reading, its occupancy and how old it is, with a table of the same figures beside it.
- **Data freshness and quality:** the source's status and data age today, and its **stale periods**: the 23 September – 1 October freeze is one closed period of about 7.75 days, and it shows as a run of days where the stale column is at 100 % and there is no occupancy column beside it: a frozen value is not shown as if it were occupancy. A new freeze would appear as a period with no end yet. The failed checks of the latest run are listed, if there are any: a capacity that is wrong makes occupancy wrong, so more free spaces than capacity is a critical check that fails the run, and the report shows it rather than clamping it.
- **About:** both sources, the NLOD attribution (also in every page's footer), and what "stale" and "occupancy" mean.
- The model and report are files in the repository, published with `tools/report`, which gives each platform its own data source so the service can refresh it ([`docs/report.md`](report.md#on-a-platform)).

## 8. A new facility

**Show:** [A new facility](../README.md#a-new-facility), and the test [`test_a_new_facility_appears_on_the_next_run_with_unknown_capacity`](../tests/gold/test_gold_build.py).

**Say:**

- **No code or configuration change.** The facility appears in the next snapshot, and the facility MERGE gives it a new key and a `first_seen`. The test runs the whole pipeline with a synthetic tenth facility.
- **Its capacity is unknown until someone adds it to the facility mapping**, one line. Until then, the quality check stops the run, deliberately, so a new facility cannot go unnoticed.
- A facility that disappears is marked inactive, never deleted. A renamed one appears as new, because the feed has no id: the name is the key ([ADR 004](adr/004-facility-name-as-natural-key.md)).

## 9. Scalability, and PySpark or Polars

**Show:** [How it scales](../README.md#how-it-scales).

**Say:**

- **Polars and delta-rs, not Spark** ([ADR 001](adr/001-polars-over-pyspark.md)): the data fits on one machine many times over. A year of this feed is about a second and 1 GB for the whole derivation, and Spark would add a JVM and a slower start to every 5-minute run for no benefit.
- **Measured, not guessed** ([ADR 006](adr/006-scalability-assessment.md)): the limit is memory, about 13 GB at 19 million fetch rows. That is two decades of this feed, or 90 facilities for two years.
- **What changes first:** derive only the dates a run touches, or move to Spark, before the fetch table nears 20 million rows. The Delta tables are engine-neutral, so readers and the report are unaffected, and the transformations are pure functions with tests that serve as the specification for a rewrite.
- **What does not scale by configuration:** a second municipality. The model is written for one parking source; it needs a field mapping and the source in the facility's key.

## 10. Known weaknesses, and the change landing

**Show:** [Known weaknesses](../README.md#known-weaknesses), and mention that [`docs/weaknesses.md`](weaknesses.md) has been kept as each trade-off was made.

**Say** (pick three):

- **The source:** it froze for about 8 days (23 September – 1 October) with every metadata signal saying otherwise, recovered without notice, and has no support channel beyond an e-mail address.
- **Capacity:** a hand-maintained mapping to a register that can be wrong, as Forum shows.
- **Collection** depends on cron-job.org, and nothing alerts if its calls stop; gaps are listed afterwards from the sidecars.
- **The facility name as key:** a rename splits a facility's history.
- **The platform:** Databricks is Free Edition, temporary and with a daily quota; its collector stays paused until a handover, and deploying is manual (#83).

**Then show the change landing:** check that the run from part 6 has ended and its `publish` task succeeded (a refresh before that reads half-replaced tables), then run [the query](#the-reading-for-part-6) again: Forum's capacity is 315 and the same reading has **23** occupied spaces, not −3. Refresh the semantic model in the service (under two minutes): the report has the new capacity. Forum no longer fails the check; the latest run's failed checks are not a promise either way, since Jernbanen can still exceed its 390 at night ([#112](https://github.com/VirtueMe/stavangerparking/issues/112)). The whole change, from merge to report, took about five minutes, and every step was a command anyone on the team can run.

After the meeting, `tools/report -p databricks --prod` publishes the About page's new wording on capacity; the figures are already right after the refresh.

## Likely questions

| Question | Short answer | More |
|---|---|---|
| Why not Spark, since Databricks and Fabric are built around it? | The volume fits in memory with room to spare; Spark would cost a JVM and start-up on every run. The switch point is measured, and the tables stay the same | [ADR 001](adr/001-polars-over-pyspark.md), [ADR 006](adr/006-scalability-assessment.md) |
| Why collect on GitHub Actions and not on the platform? | History cannot be recovered, and platform access was not there on day one. The platform takes over with a handover that keeps one collector | [ADR 007](adr/007-collect-outside-the-platform.md), [ADR 011](adr/011-one-repository-two-platforms.md#collection-one-collector-of-record-and-a-handover) |
| What happens when the feed freezes, or starts updating again? | Nothing to change. When it recovered on 1 October, the stale period got its end and the next readings were fresh, with no change to the code | [`docs/silver.md`](silver.md#source-staleness) |
| Why did runs fail? | A critical quality check: a facility reported more free spaces than its registered capacity, Forum at 292 against 289 on 23 September, because the register leaves out its 26 reserved spaces and the feed counts them. The facility mapping now adds them (315, #91). A run fails on purpose while such a reading is the latest; the tables were built and published all along | [`docs/quality.md`](quality.md), [ADR 005](adr/005-capacity-as-reference-data.md#addendum-2026-09-30-reserved-spaces-the-register-leaves-out) |
| Why a 5-minute interval, when the source publishes every 2? | 5 minutes is the scheduler's floor; the analyses use 15-minute buckets, so a finer grain would add runs and rows without adding insight | [ADR 003](adr/003-polling-interval.md) |
| Why not Direct Lake on Fabric? | It would be the natural choice there; it needs other partitions in the model, not another function, and is planned for when Fabric exists | [`docs/report.md`](report.md#on-a-platform) |
| How much of it is tested? | The package, the entry point, the wheel, the tools and the report's files, on every pull request; the deployments are checked by hand on the platform | [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| Could it suggest prices? | Yes, as an example: `fact_suggested_price` applies example rules to occupancy; real rules are the operator's decision | [`docs/pricing.md`](pricing.md) |
