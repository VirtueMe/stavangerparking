# Databricks deployment

The pipeline runs on Databricks from this repository, as a [Databricks Asset Bundle](https://docs.databricks.com/aws/en/dev-tools/bundles/) in [`platforms/databricks/`](../platforms/databricks/databricks.yml). Only this folder is Databricks-specific: the jobs install the package's wheel (#70) and call its entry points (#69), as [ADR 011](adr/011-one-repository-two-platforms.md) describes.

## What the bundle deploys

| Resource | What it is |
|---|---|
| Schema `workspace.stavanger_parking` | Holds the volumes and the published tables. For the `dev` target, development mode prefixes it with the deploying user: `dev_<user>_stavanger_parking` |
| Volume `raw` | The raw files, in the same layout as the `data` branch (`raw_path` in the [source configuration](config.md)) |
| Volume `tables` | Every layer's Delta tables, written by delta-rs through the pipeline entry point, with `allow_unsafe_rename` (below) |
| Job `stavanger_parking_pipeline` | `pipeline`: [`stavanger-parking-pipeline run`](pipeline.md) on the two volumes. Then `publish`: copies the tables the report reads into the schema as Unity Catalog tables ([`publish.py`](../platforms/databricks/publish.py)). Every 15 minutes, **paused** |
| Job `stavanger_parking_collect` | `stavanger-parking-collect run` into the raw volume. Every 5 minutes, **paused** |

Both jobs run on serverless compute with the wheel installed, email the deploying user when they fail, and have serverless auto-optimization turned off: it retries a failed task, and a retry of a critical check repeats its verdict and stores its results twice. The next scheduled run is the retry; while the schedules are paused, run the job again by hand. The pipeline's exit codes ([`docs/pipeline.md`](pipeline.md#exit-codes)) fail the `pipeline` task; `publish` still runs, so a run stopped by a critical check (exit 3), which has built every table, still publishes them.

**Writing Delta to a volume.** A volume is a FUSE mount, and it cannot rename a file without replacing an existing one, which delta-rs uses to commit safely: the first run failed with `Unable to rename file: Operation not permitted`. delta-rs does not plan to support volumes ([delta-rs#2540](https://github.com/delta-io/delta-rs/issues/2540)). The pipeline therefore passes the storage option `allow_unsafe_rename=true`, which commits with a plain rename. That is safe only with one writer, and `max_concurrent_runs: 1` on the job makes sure there is one. **Anything else that writes to the `tables` volume must be a task of the pipeline job, never a job of its own**, or two runs could commit at once and one commit would be lost. [Table maintenance](maintenance.md) is the first such writer: it is not scheduled here yet, and when it is, it becomes a task after `publish`.

**Why tables are published by a separate task.** delta-rs writes Delta tables to a path, and on this workspace the only writable paths are volumes. Delta files in a volume are not Unity Catalog tables, and Power BI's Databricks connector reads tables (#72). `publish.py` uses Spark to replace each published table with its Delta table's content: the star schema, `fact_suggested_price` and `quality_check_results`. Spark stays in the platform folder; the package never imports it.

## Deploying

Needs the [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/install), `uv`, and for `prod` the GitHub CLI.

```sh
databricks auth login --host https://<workspace>.cloud.databricks.com   # once; stores a profile
platforms/databricks/deploy.sh dev                                      # builds the wheel from this checkout
platforms/databricks/deploy.sh prod v0.17.0                             # installs a released wheel
```

- **`dev`** (the default target) is for trying a change: the bundle's development mode prefixes the jobs with your name and keeps every schedule paused.
- **`prod`** installs the wheel attached to a GitHub release, so what runs is a released version.

The workspace is not in the repository: the CLI's profile says which one. `databricks bundle validate -t <target>` checks the bundle without deploying.

## Secrets

None today. The data is open and needs no credentials, the pipeline reads and writes volumes with the job's own identity, and deploying uses your CLI login. If deploying moves to CI, it needs a service principal and its OAuth secret as GitHub secrets; if a source ever needs a key, it goes in a Databricks secret scope, read by the platform layer and passed to the package, never read by the package itself.

## Backfill

```sh
platforms/databricks/backfill.sh dev
```

Copies the raw files from the `data` branch into the raw volume and runs the pipeline job once, which loads every file bronze does not have yet and rebuilds silver and gold. Raw files never change, so running it again copies the same files and loads only new ones. Until Databricks collects, this is how its data stays current: run it when you want the platform's tables up to date.

## Next to GitHub Actions collection

GitHub Actions stays the collector of record ([`docs/collector.md`](collector.md)), and the Databricks collection job is deployed **paused**, so two collectors never write the history at once. The raw files reach Databricks only by backfill. When Databricks takes over collection, the handover in [ADR 011](adr/011-one-repository-two-platforms.md#collection-one-collector-of-record-and-a-handover) applies: backfill, an overlap of at least 24 hours with the collection job writing to a separate comparison volume (its `--storage` parameter), a comparison, and the switch.

### Free Edition

The workspace is Databricks Free Edition. It has serverless compute only, and at most five job tasks run at once per account, both fine for these jobs. **Outbound internet is limited to a set of trusted domains** until the account's identity is verified, so the collection job may not reach the source (opencom.no); verify the identity before the handover's overlap. Exceeding the fair-use quota shuts compute down for the rest of the day, which is one more reason the schedules stay paused until they are needed.
