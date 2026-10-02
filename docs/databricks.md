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

Both jobs run on serverless compute with the wheel installed, its dependencies at the versions in `uv.lock` (below), email the deploying user when they fail, and have serverless auto-optimization turned off: it retries a failed task, and a retry of a critical check repeats its verdict and stores its results twice. The next scheduled run is the retry; while the schedules are paused, run the job again by hand. The pipeline's non-zero exit codes ([`docs/pipeline.md`](pipeline.md#exit-codes)) fail the `pipeline` task, and a clean run (code 0) succeeds; `publish` still runs, so a run stopped by a critical check (exit 3), which has built every table, still publishes them.

**Writing Delta to a volume.** A volume is a FUSE mount, and it cannot rename a file without replacing an existing one, which delta-rs uses to commit safely: the first run failed with `Unable to rename file: Operation not permitted`. delta-rs does not plan to support volumes ([delta-rs#2540](https://github.com/delta-io/delta-rs/issues/2540)). The pipeline therefore passes the storage option `allow_unsafe_rename=true`, which commits with a plain rename. That is safe only with one writer, and `max_concurrent_runs: 1` on the job makes sure there is one. **Anything else that writes to the `tables` volume must be a task of the pipeline job, never a job of its own**, or two runs could commit at once and one commit would be lost. [Table maintenance](maintenance.md) is the first such writer: it is not scheduled here yet, and when it is, it becomes a task after `publish`.

**Why tables are published by a separate task.** delta-rs writes Delta tables to a path, and on this workspace the only writable paths are volumes. Delta files in a volume are not Unity Catalog tables, and Power BI's Databricks connector reads tables: `tools/report` generates and publishes the report against them ([`docs/report.md`](report.md#on-a-platform)). `publish.py` uses Spark to replace each published table with its Delta table's content: the star schema, `fact_suggested_price`, `fact_source_stale_period` and `quality_check_results`. Spark stays in the platform folder; the package never imports it.

## Deploying

Needs the [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/install), `uv`, and for `prod` the GitHub CLI.

```sh
databricks auth login --host https://<workspace>.cloud.databricks.com   # once; stores a profile
tools/deploy -p databricks                                              # dev: builds the wheel from this checkout
tools/deploy -p databricks --prod                                       # prod: installs the latest release's wheel
tools/deploy -p databricks --prod v0.16.0                               # prod: a given release, e.g. to revert
tools/deploy -p databricks --prod --dry-run                             # validate and show the plan, deploy nothing
```

- **`dev`** (the default target) is for trying a change: the bundle's development mode prefixes the jobs with your name and keeps every schedule paused.
- **`prod`** installs the wheel attached to a GitHub release, so what runs is a released version: the latest one, or the tag given, which is how a release is reverted.
- **The dependencies come from `uv.lock`** ([ADR 012](adr/012-uv-lock-everywhere.md)): `deploy.sh` writes `dist/requirements.txt` with [`tools/requirements.sh`](../tools/requirements.sh), from the checkout's lock for dev and from the release tag's own lock for prod, and the job environments install it before the wheel (`-r ${workspace.file_path}/dist/requirements.txt`; the bundle syncs that one file from the otherwise ignored `dist/`). The versions are pinned but not hash-checked: the environment installs the requirements and the wheel in one pip run, and the wheel has no hash.
- **`--dry-run`/`-n`** runs `bundle validate` and `bundle plan` instead of `bundle deploy`, and names the release a prod deploy would install. It still fetches or builds the wheel into `dist/` locally, because the plan needs it; the workspace is untouched.

The tools in `tools/` run the platform's own script, [`deploy.sh`](../platforms/databricks/deploy.sh) here, which also runs on its own (`platforms/databricks/deploy.sh [--dry-run] dev | prod [tag]`). The platform comes from `-p`/`--platform`, else `PLATFORM` in the environment, else a `PLATFORM=databricks` line in `.env` at the root of the checkout, so with that line `tools/deploy --prod` is enough. `.env` is read for that line only, never sourced.

The workspace is not in the repository: the CLI's profile says which one.

## Secrets

None today. The data is open and needs no credentials, the pipeline reads and writes volumes with the job's own identity, and deploying uses your CLI login. If deploying moves to CI, it needs a service principal and its OAuth secret as GitHub secrets; if a source ever needs a key, it goes in a Databricks secret scope, read by the platform layer and passed to the package, never read by the package itself.

## Backfill

```sh
tools/backfill -p databricks                     # dev
tools/backfill -p databricks --prod --dry-run    # count the files and name the volume, copy nothing
```

Copies the raw files from the `data` branch into the raw volume and runs the pipeline job once, which loads every file bronze does not have yet, brings silver up to date and rebuilds gold. Raw files never change, so running it again copies the same files and loads only new ones. Until Databricks collects, this is how its data stays current: run it when you want the platform's tables up to date.

A refresh of the Power BI model reads the published tables: start it after the run's `publish` task has succeeded, never while it runs ([`docs/report.md`](report.md#on-a-platform)).

## Rebuilding silver

```sh
tools/rebuild -p databricks --prod --dry-run    # name the job and the parameter, run nothing
tools/rebuild -p databricks --prod
```

Silver parses only the bronze rows it has not seen ([`docs/silver.md`](silver.md#runs-and-rebuilds)), so a change to the parsing applies only to rows that arrive after it. `tools/rebuild` runs the pipeline job once with the job parameter `rebuild_silver=true`: silver is rebuilt from all of bronze, then gold, quality and publish run as usual. It copies no raw files; run `tools/backfill` first if the volume is behind.

The parameter defaults to `false`, so the scheduled runs, and `tools/backfill`, stay incremental. After a release that changes the parsing: `tools/deploy --prod`, then `tools/rebuild --prod`. The first time was #113, when the source's `"Fullt"` became 0 free spaces.

Fabric has no rebuild yet: `tools/rebuild -p fabric` says so, until the Fabric deployment has run (#16).

## Next to GitHub Actions collection

GitHub Actions stays the collector of record ([`docs/collector.md`](collector.md)), and the Databricks collection job is deployed **paused**, so two collectors never write the history at once. The raw files reach Databricks only by backfill. When Databricks takes over collection, the handover in [ADR 011](adr/011-one-repository-two-platforms.md#collection-one-collector-of-record-and-a-handover) applies: backfill, an overlap of at least 24 hours with the collection job writing to a separate comparison volume (its `--storage` parameter), a comparison, and the switch.

### Free Edition

The workspace is Databricks Free Edition. It has serverless compute only, and at most five job tasks run at once per account, both fine for these jobs. **Outbound internet is limited to a set of trusted domains** until the account's identity is verified, so the collection job cannot reach the sources (opencom.no, parkreg-open.atlas.vegvesen.no). Identity verification is meant to lift that, but after verifying on 2026-09-30 both jobs and notebooks still could not resolve them, while `pypi.org` and `github.com` resolved: check again before the handover's overlap. Exceeding the fair-use quota shuts compute down for the rest of the day, which is one more reason the schedules stay paused until they are needed.
