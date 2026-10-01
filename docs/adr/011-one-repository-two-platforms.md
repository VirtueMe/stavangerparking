# ADR 011: One repository deploys to Fabric and Databricks; only a thin platform layer differs

- **Status:** Accepted
- **Date:** 2026-09-30
- **Issue:** #68

## Context

The case is to run on a data platform, and which one is still open. Fabric access is not settled: the own tenant is too new for a Fabric trial, and the workspace is Power BI Pro only. Databricks was offered as an equal alternative. The presentation is on 22 October, and we cannot wait for the answer before building the deployment.

Most of the solution is already platform-neutral, by earlier decisions:

- The logic lives in the `stavanger_parking` package, runs on Polars and delta-rs, and is tested locally ([ADR 001](001-polars-over-pyspark.md)). Nothing in `src/` imports Fabric, Databricks or Spark.
- Every build takes the tables root and storage options as parameters (`tables.py`). The raw files have one relative layout, set by `raw_path` in the [source configuration](../config.md).
- The raw files are the source of truth and are collected outside any platform, on the `data` branch ([ADR 007](007-collect-outside-the-platform.md), [ADR 008](008-trigger-collection-externally.md)). Bronze, silver and gold can be rebuilt from them anywhere.

What differs between the platforms is how code is installed and started, where storage is and how it is reached, how runs are scheduled and alerted, where secrets live, and how Power BI connects. Collection carries one more constraint. The collector keeps its state in the storage it writes to: each run reads the latest sidecars to decide whether to fetch (ADR 003). Two collectors writing to two storages cannot see each other. Each would build a separate, equally valid history, and neither would be the complete one.

## Decision

**One repository, one package, two thin deployments.** The same commit deploys to Fabric and to Databricks. Everything that describes *what* the solution does is shared. Only *where* and *when* it runs differs, and that part lives outside the package.

### What is shared, and what is platform-specific

| Shared (one copy) | Platform-specific (one per platform) |
|---|---|
| The `stavanger_parking` package: collection, bronze, silver, gold, quality, pricing | Runtime entry points: notebooks or job tasks that call the package |
| The pipeline entry point, `run_pipeline(...)` and `python -m stavanger_parking.pipeline run` (#69) | The values of the raw root, the tables root and the storage options |
| The configuration: sources, facility mapping, tariffs, pricing rules. It ships inside the wheel (#70) | Scheduling, retries and failure alerts |
| The table names and the raw-file layout (`tables.py`, `raw_path`) | Secrets and how they reach a run (Key Vault or a Fabric connection; a Databricks secret scope) |
| The tests, run in CI on every pull request | The deployment definition: Fabric Git integration items; a Databricks Asset Bundle |
| The Power BI semantic model and report (`powerbi/`) | The model's data source: Direct Lake or the SQL analytics endpoint on Fabric, the Databricks connector on Databricks, chosen by parameters in one model (#72) |

The platform layer's job is small by design. It finds the paths and credentials, calls the entry point, and reports the outcome to the platform's scheduler. If a platform folder starts to hold logic that the other platform would also need (filtering, ordering of steps, retries of a step), the logic moves into the package.

**Storage on both platforms uses the same shape.** The raw root is a POSIX path on both: `/lakehouse/default/Files` on Fabric, a Unity Catalog volume (`/Volumes/<catalog>/<schema>/raw`) on Databricks. So the collector and the bronze loader, which work on local paths, run unchanged. The tables root is whatever the platform's delta-rs can write to: `abfss://…/Tables` in the Lakehouse, or a cloud storage location registered as external tables in a Unity Catalog schema on Databricks. The exact Unity Catalog setup is decided in #71.

### Where the platform-specific parts live

```text
src/stavanger_parking/   shared package: imports nothing platform-specific
config/                  shared configuration (packaged into the wheel, #70)
powerbi/                 shared model and report; the data source is a parameter (#72)
platforms/
  fabric/                Fabric items synced by Git integration: notebooks, Data pipelines (#16–#19)
  databricks/            databricks.yml and job definitions (#71)
tests/                   shared tests; they never need a platform
```

- **`src/stavanger_parking` imports nothing platform-specific:** no `notebookutils` or `mssparkutils`, no `dbutils`, no `pyspark`, no `databricks` SDK. A test checks this. It is added with the first `platforms/` folder, by #71 or #17, whichever lands first.
- **Dependencies point one way.** A platform folder installs a released wheel (#70) and calls the package. The package never knows which platform runs it.
- **Each platform deploys only its own folder.** Fabric's workspace Git integration points at `platforms/fabric/`, and the bundle's root is `platforms/databricks/`. A change to one platform's folder does not redeploy the other.

### Collection: one collector of record, and a handover

- **Exactly one collector writes the history at any time: the collector of record.** Today that is the GitHub Actions workflow started by cron-job.org (ADR 008), writing to the `data` branch. The collector of record is named in [`docs/collector.md`](../collector.md).
- **A platform's collection schedule is off by default.** It is paused in the bundle's targets on Databricks and not enabled on the Data pipeline on Fabric. Deploying a platform never starts a second collector. Its pipeline runs on raw files that were copied into it (the backfill in #17 and #71).
- **Handover (refines ADR 007), for the one platform that takes over:**
  1. **Backfill:** copy the raw files from the `data` branch into the platform's raw root and rebuild bronze, silver and gold from them.
  2. **Overlap:** turn on the platform's collection, writing to a separate **comparison root**, never to its raw root. GitHub Actions stays the collector of record. The two run in parallel for at least 24 hours.
  3. **Compare:** every snapshot the collector of record stored in the overlap has a snapshot in the comparison root within one polling interval with the same `values_fingerprint`, and `collect gaps` is clean on the comparison root.
  4. **Switch:** turn off the cron-job.org job, then copy the `data` branch's files since the backfill into the raw root, so it holds GitHub's history up to the switch. Point the platform's collection at the raw root and discard the comparison root. Its first run continues adaptive polling from the last sidecar GitHub wrote. Then remove `collect.yml`, revoke the token (ADR 008), and name the platform as the collector of record. The `data` branch stays as a read-only archive.
- **So every period of the history has exactly one collector:** GitHub Actions up to the switch, the platform after it. The overlap's snapshots only prove that the platform collects correctly; they never enter the history, where near-duplicate snapshots minutes apart would count twice in the facts.
- **The other platform never collects from the source.** If both deployments are kept after the switch, the second receives raw files by copy from the collector of record. That copy is a job of its own, and gets its own issue if it is needed.

## Alternatives considered

| Alternative | Why not |
|---|---|
| One platform only: wait for Fabric | Blocks the deployment on the answer about Fabric access, with the presentation on 22 October |
| One platform only: build for Databricks now | The case asks for Fabric. If Fabric access arrives, the work is redone rather than added to |
| Two repositories, one per platform | The package, configuration, tests and model would be copied or vendored, and would drift. A fix would need two pull requests and two releases |
| A thin shim per notebook: each platform wraps each step (load, silver, gold, quality) with its own parameters | The order of the steps, the stop on failure and the parameter handling would be written twice, once per platform, and could differ. One entry point (#69) keeps them in the package, where they are tested |
| Platform detection inside the package (`if running on Fabric …`) | Complects the logic with its runtime. The package could then no longer be tested without imitating each platform, and a third platform would mean editing the package |
| Spark on Databricks, to use Auto Loader and Lakeflow declarative pipelines | A second engine and a second implementation of every layer, for data that fits in memory (ADR 001, ADR 006). The Polars package runs as a Python job on Databricks as it does on Fabric |

## Consequences

- The platform choice becomes a deployment decision, not a rewrite. Whichever way Fabric access is settled, the shared part is done, and one platform folder is used.
- Two deployments are more to maintain and document than one. Keeping the platform folders thin limits this, and an unused folder can be left unused without affecting the other.
- Neither deployment can be tested in CI without platform credentials. CI tests the package, the wheel (#70) and the entry point (#69); the deployments are checked by hand on each platform. On Databricks, `databricks bundle validate` can check the bundle without running it.
- Collection on a platform only starts by a deliberate switch, after an overlap. The cost is a period with two collectors running, and a comparison root that is thrown away afterwards.
- On Databricks, the solution does not use Spark-based features such as Auto Loader or Lakeflow declarative pipelines. How the tables are registered in Unity Catalog is decided in #71. This is ADR 001's revisit point: this decision keeps Polars, and ADR 001's triggers for switching to Spark still apply.
- ADR 008's cron-job.org trigger and token are retired at the switch, not when a platform is merely deployed.
- Revisit when the platform is decided: the other platform's folder can then be removed, or kept as proof that the solution is portable.

## Addendum 2026-09-30: as built on Databricks

The Databricks deployment (#71) and the report per platform (#72) settled two points this decision left open, in ways that differ from the text above:

- **Tables on Databricks are in a volume, not external tables.** Free Edition has no external locations, so the tables root is a managed Unity Catalog volume. delta-rs cannot commit to a volume with a rename that refuses to replace, so the pipeline commits with a plain rename (`allow_unsafe_rename`), which is safe only with one writer: the pipeline job allows one run at a time, and any other writer to the tables must be a task of that job. A `publish` task copies the tables the report reads into the Unity Catalog schema with Spark, because Power BI's Databricks connector reads tables, not files. Spark stays in the platform folder; the package still never imports it ([`docs/databricks.md`](../databricks.md)).
- **The Power BI data source is generated per platform, not chosen by a parameter.** A query that picks its source with an `if` on a parameter does not refresh in the Power BI service, which needs one data source per query. The model in `powerbi/` stays as it is, and `tools/report` replaces its one data source function, `DeltaTable(name)`, with the platform's (`platforms/<platform>/report/expressions.tmdl`) in a generated copy, then publishes it with the Fabric CLI. The tables, measures and pages are still one definition ([`docs/report.md`](../report.md#on-a-platform)).

The rest holds: one package and one entry point, thin platform folders (now with `tools/` to run the same operation on either), and collection only by a deliberate handover. The handover to Databricks is also blocked for now: Free Edition limits outbound internet to trusted domains, and the sources are not among them.

## Addendum 2026-10-01: as prepared for Fabric

The Fabric deployment (#98) is written before there is a capacity to run it on, and differs from the text above in one point: **it is deployed with fabric-cicd, not synced by Git integration.** `tools/deploy -p fabric` deploys the items in `platforms/fabric/workspace/` with `fab deploy`, as `tools/deploy -p databricks` deploys the bundle, so on both platforms a release is deployed, not a branch, and dev and prod differ only in where they go (two workspaces on Fabric). fabric-cicd fills in the IDs that exist only after deployment, such as the notebooks' default Lakehouse. The folder is in Git format, so Git integration remains possible. As on Databricks, the platform folder holds no logic: one pure Python notebook installs the wheel and calls `run_pipeline`. What has not been tried is listed in [`docs/fabric.md`](../fabric.md#not-tried-yet).
