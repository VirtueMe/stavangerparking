# Fabric deployment

The pipeline is ready to run on Microsoft Fabric from this repository, from the items in [`platforms/fabric/workspace/`](../platforms/fabric/workspace/). As on Databricks ([`docs/databricks.md`](databricks.md)), only this folder is Fabric-specific: a notebook installs the package's wheel and calls its entry point (ADR 011).

**Status: written, not yet run.** There is no Fabric capacity (#16), so nothing here has been deployed. The item formats are copied from Microsoft's own examples (the [`fabric-cicd`](https://github.com/microsoft/fabric-cicd) sample workspace and [`fabric-toolbox`](https://github.com/microsoft/fabric-toolbox)), every `.platform` file is valid against Microsoft's published schema, and the scripts are tested against a fake Fabric CLI (`tests/test_fabric.py`). What only a real workspace can check is listed under [Not tried yet](#not-tried-yet).

## What is deployed

| Item | What it is |
|---|---|
| Lakehouse `StavangerParking` | `Files/` holds the raw files, in the same layout as the `data` branch, and the wheel (`Files/wheels/`); `Tables/` holds every layer's Delta tables |
| Notebook `RunPipeline` | A pure Python notebook (not Spark, ADR 001) with the Lakehouse attached: installs the wheel from `Files/wheels/`, then `run_pipeline("/lakehouse/default/Files", "/lakehouse/default/Tables")` ([`docs/pipeline.md`](pipeline.md)). A non-zero exit code raises, so the run fails. Its kernel is pinned to **Python 3.11**, the version CI tests on and Databricks runs, rather than Fabric's default 3.12; Fabric supports 3.11 until October 2027 ([kernel lifecycle](https://learn.microsoft.com/en-us/fabric/data-engineering/python-notebook-runtime-lifecycle)). `Collect` is pinned the same way |
| Data pipeline `StavangerParkingPipeline` | Runs `RunPipeline`, and e-mails when it fails (an Office 365 Outlook activity on the *Failed* path). The notebook activity passes `_inlineInstallationEnabled = True`: pipeline runs turn `%pip install` off by default, and the notebook installs the wheel with it ([library management](https://learn.microsoft.com/en-us/fabric/data-engineering/library-management#python-inline-installation)) |
| Notebook `Collect` | Collects a snapshot of every source that is due into `Files/`. **Not scheduled, and run by nothing**: GitHub Actions is the collector of record until the handover (ADR 011) |

The tables are written to `Tables/` and appear as Lakehouse tables, with no publishing step: the report reads them through the Lakehouse's SQL analytics endpoint ([`docs/report.md`](report.md#on-a-platform)).

## Deploying

Needs the Fabric CLI (`uv run --only-group powerbi fab`, the dependency group `powerbi`), signed in with `fab auth login`, `uv`, and for `prod` the GitHub CLI. The workspace is not in the repository:

```sh
export FABRIC_WORKSPACE="<prod workspace>"       # and FABRIC_DEV_WORKSPACE for dev
tools/deploy -p fabric --prod --dry-run          # prepare dist/fabric-prod/ and check the workspace
tools/deploy -p fabric --prod                    # the latest release (or give a tag, to revert)
tools/backfill -p fabric --prod                  # copy the raw files into Files/ and run the pipeline
tools/report -p fabric --prod                    # the Power BI model on the Lakehouse's SQL endpoint
```

[`deploy.sh`](../platforms/fabric/deploy.sh) gets the wheel (built from the checkout for dev, the release for prod), copies the items to `dist/fabric-<target>/` with the wheel's file name and the alert address filled in, writes the `config.yml` with the workspace, and deploys with **fabric-cicd** (`fab deploy`). fabric-cicd creates or updates every item and fills in the IDs [`parameter.yml`](../platforms/fabric/workspace/parameter.yml) names: the notebooks' default Lakehouse and workspace, which only exist once the Lakehouse does; the pipeline's reference to the notebook is resolved by fabric-cicd itself. Then the wheel is copied into `Files/wheels/`.

- **dev and prod are two workspaces**, `FABRIC_DEV_WORKSPACE` and `FABRIC_WORKSPACE`, the way fabric-cicd separates environments.
- **The failure e-mail** goes to `FABRIC_ALERT_EMAIL`, by default the signed-in account. The activity is deployed **inactive**: an Office 365 Outlook activity needs a connection, which is signed in by a person and cannot be kept in Git. Open the pipeline once, choose the connection, and activate the activity.
- **Not Git integration.** #16 planned the workspace's Git integration on a `fabric` branch. Deploying with fabric-cicd from the tools is the same as on Databricks: a release is deployed, not a branch, and dev and prod differ only in the workspace. The folder is in Git format, so Git integration can still sync it if that is wanted.

## Backfill

```sh
tools/backfill -p fabric --prod --dry-run   # count the files and name the Lakehouse, copy nothing
```

[`backfill.sh`](../platforms/fabric/backfill.sh) copies the raw files from the `data` branch into `Files/`, at the same relative paths, one file at a time with `fab cp`, then runs the pipeline with `fab job run`. Raw files never change, so a second backfill copies the same files and the pipeline loads only the new ones.

## Next to GitHub Actions collection

GitHub Actions stays the collector of record ([`docs/collector.md`](collector.md)). `Collect` is deployed but runs only if someone starts it, so Fabric never becomes a second collector by being deployed. When Fabric takes over collection, the handover in [ADR 011](adr/011-one-repository-two-platforms.md#collection-one-collector-of-record-and-a-handover) applies: backfill, an overlap of at least 24 hours with `Collect` writing to a separate comparison folder (its `--storage` argument), a comparison, and the switch. The overlap and the switch add a schedule to a pipeline that runs `Collect` every 5 minutes, with `_inlineInstallationEnabled` like `StavangerParkingPipeline`; that schedule is not in the repository until then.

## Not tried yet

Until there is a workspace (#16):

- **The item formats** as deployed by fabric-cicd: the notebooks' Lakehouse binding, the pipeline's notebook activity, and the Outlook activity's definition.
- **The Fabric CLI calls:** `fab deploy` with this config, `fab cp` from a local file into `Files/`, `fab job run` on the pipeline, and the SQL endpoint's connection string from `fab get`.
- **Reproducible dependencies.** `%pip install` resolves the wheel's dependencies on every run, and the package only sets minimum versions, so a run can get newer Polars or deltalake than CI tested; Microsoft recommends an Environment item for pipelines for that reason. Two ways to pin them, to choose on the first deployment: an Environment with the wheel as a custom library, or `%pip install -r` with requirements exported from `uv.lock`.
- **delta-rs on the Lakehouse mount.** Databricks' volumes could not commit without `allow_unsafe_rename` (ADR 011 addendum); whether `/lakehouse/default/Tables` can is unknown. If it cannot, the notebook passes the same storage option.
- **The report on the SQL endpoint**, and whether Direct Lake should replace import mode.

Each is checked once, on the first deployment, and this section shrinks to what remains.
