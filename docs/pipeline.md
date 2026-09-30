# Pipeline: one entry point

The pipeline runs bronze load, silver, gold and the quality checks, in that order. It is the one thing a platform calls ([ADR 011](adr/011-one-repository-two-platforms.md)): a Fabric notebook, a Databricks job task, or you, locally.

```sh
uv run python -m stavanger_parking.pipeline run --raw-root <raw files> --tables-root <tables>
```

Installed from the wheel, the same command is the console script `stavanger-parking-pipeline run …`, which the Databricks job calls ([`docs/databricks.md`](databricks.md)).

From a notebook:

```python
from stavanger_parking.pipeline import run_pipeline

result = run_pipeline("/lakehouse/default/Files", "/lakehouse/default/Tables")
print(result.report())
if result.exit_code:
    raise RuntimeError(f"pipeline failed with exit code {result.exit_code}")
```

`run_pipeline` returns what each step did (`result.steps`, each with its report lines and error), whether a critical check failed (`result.critical`), and the exit code the command line would give. It raises nothing for a step that cannot run or a critical check: the caller decides how the platform hears about it, which is why the notebook above raises.

## Parameters

| Parameter | Meaning |
|---|---|
| `--raw-root` | The raw files, as a local path: a folder, the `data` branch, `/lakehouse/default/Files`, or a Unity Catalog volume (`/Volumes/<catalog>/<schema>/raw`) |
| `--tables-root` | Where the tables are: a folder, `/lakehouse/default/Tables`, or a URI delta-rs writes to, such as `abfss://…/Tables` |
| `--storage-option KEY=VALUE` | A delta-rs storage option for the tables root, such as credentials for an `abfss://` URI; repeat for more. `storage_options={...}` in `run_pipeline` |
| `--config`, `--mapping`, `--tariffs`, `--rules` | The configuration files; by default those shipped in the package (`stavanger_parking/config/`) |

## Order, and what stops a run

1. **bronze**: every source's new raw files ([`docs/bronze.md`](bronze.md)). No new files is not a failure.
2. **silver**: incremental; the register's areas once it is collected ([`docs/silver.md`](silver.md)).
3. **gold**: the dimensions and facts ([`docs/gold.md`](gold.md)).
4. **quality**: the checks, with their results appended to `quality_check_results` ([`docs/quality.md`](quality.md)).

A step that **cannot run** (no bronze table to build silver from, a reading outside the date dimension, no snapshot to check) stops the pipeline, and the steps after it do not run. Each step leaves its tables consistent, so the next run continues where this one stopped. Running the pipeline again on the same raw files adds nothing but a new set of quality results, so an orchestrator can retry it.

A **critical check** fails the run after every step has run and the results are stored (#21). Any other error is a bug, and is raised with its traceback.

Collection is not part of the pipeline. It has one collector of record, scheduled on its own ([ADR 011](adr/011-one-repository-two-platforms.md#collection-one-collector-of-record-and-a-handover)). Nor is [table maintenance](maintenance.md), which runs daily rather than on every run.

## Exit codes

| Code | Meaning | The orchestrator should |
|---|---|---|
| 0 | Every step ran; no critical check failed | Carry on |
| 1 | A step could not run; the report names it and why (or an unexpected error, with a traceback) | Alert; the tables up to the failed step are current |
| 2 | The command line was wrong | Fix the job definition |
| 3 | A critical check failed; the results are in `quality_check_results` | Alert; the tables are built, but the data needs a look before it is trusted |

## The per-step commands

Each step still has its own command, for running or debugging one layer: `bronze.load`, `silver.build` (with `--rebuild`), `gold.build` and `quality.check`. The pipeline calls the same functions they do.
