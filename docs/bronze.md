# Bronze loading

Collected raw snapshots ([`docs/collector.md`](collector.md)) are loaded into the bronze Delta table of their source (`bronze_table` in [`config/sources.json`](../config/sources.json)).

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

- **One row per record.** Every source field is stored as a string, exactly as delivered (`"285"`, `"Open"`); typing happens in silver (#8).
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
