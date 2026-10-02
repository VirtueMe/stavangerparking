# Bronze loading

Collected raw snapshots ([`docs/collector.md`](collector.md)) are loaded into the bronze Delta table of their source (`bronze_table` in [`src/stavanger_parking/config/sources.json`](../src/stavanger_parking/config/sources.json)).

```sh
uv run python -m stavanger_parking.bronze.load --raw-root <raw files> --tables-root <tables>
```

| Where | `--raw-root` | `--tables-root` |
|---|---|---|
| Locally, from the `data` branch | a checkout of `data`, e.g. `git worktree add ../stavangerparking-data data` | any folder, e.g. `../stavangerparking-tables` |
| Fabric notebook | `/lakehouse/default/Files` | `/lakehouse/default/Tables`, or an `abfss://…/Tables` URI |

The roots describe the environment, not the source, so they are parameters and not part of the source configuration.

## What a load does

For every source, raw files not yet handled are appended to bronze in **one Delta commit**:

- **One row per record.** Every source field is stored as a string, exactly as delivered (`"285"`, `"Open"`); typing happens in [silver](silver.md). A value that is not a string is stored as JSON text: `3650`, `true`, or a nested object such as the register's `aktivVersjon`.
- **Metadata columns** from the sidecar: `source_id`, `raw_file`, `record_index`, `ingested_at` (UTC), `source_url`, `resource_id`, `content_hash`, `values_fingerprint`, `run_id`, and `loaded_at`. `raw_file` and `record_index` trace every row back to its file and position; `run_id` traces it to the collection run.
- **New source fields become new columns** (schema merge), with earlier rows null, instead of failing the load. Detecting such drift is the job of the quality checks (#21).

## Guarantees

- **Append-only.** Bronze is only ever appended to; nothing updates or deletes rows.
- **Idempotent.** A raw file is loaded once. Files already in bronze, or already recorded as a load issue, are skipped. The key is the raw file path, which is unique because raw files are immutable. `run_id` is not unique per file, since one collection run can write a file per source.
- **Nothing is lost silently.** A raw file that cannot be loaded is recorded in `<bronze_table>_load_issues` with its reason, reported in the output, and not retried:

| `issue` | Meaning |
|---|---|
| `missing_sidecar` | The raw file has no `.meta.json` (a run stopped between the two writes) |
| `unreadable_sidecar` | The sidecar is not valid JSON or lacks `ingested_at` |
| `unreadable_payload` | The response is not a JSON list of objects, e.g. an HTML error page |
| `no_records` | The response is an empty list |

Rows are written before issues; if a load stops in between, the issues are simply found again on the next run.

## The collector's failed runs

`bronze_collect_runs` holds the failed runs the collector's run history lists now, read by [`bronze/collect_runs.py`](../src/stavanger_parking/bronze/collect_runs.py) after the raw files are loaded (#119). Unlike the raw files, it is not built from the `data` branch: the history is a source of its own, and the table is its current state, replaced on every read. A failure older than the history's retention (about 400 days on GitHub) drops out.

| Column | Meaning |
|---|---|
| `run_id`, `run_attempt` | The run, and its latest attempt |
| `started_at` | When the run started (UTC) |
| `failed_step` | The step that failed, such as `Commit and push snapshots`; null if none was reported |
| `url` | The run's page |
| `read_at` | When the history was read |

- **Few calls.** One for the list (per 100 failed runs), plus one per run not yet in the table, for its failed step; the others keep theirs. Without a token GitHub allows 60 calls an hour; `GITHUB_TOKEN` raises that.
- **Never stops the pipeline.** If the history cannot be read (no network, a rate limit), the bronze report says so and the previous table is kept.
- **The collector is configuration**, [`config/collector.json`](../src/stavanger_parking/config/collector.json): the repository and the workflow. The pipeline on Databricks Free Edition reaches the GitHub API.
