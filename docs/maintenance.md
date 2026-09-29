# Table maintenance

Every run of the pipeline adds files to its Delta tables. The maintenance command compacts them and removes the ones no longer needed.

```sh
uv run python -m stavanger_parking.maintenance --tables-root <tables>             # compact and vacuum
uv run python -m stavanger_parking.maintenance --tables-root <tables> --dry-run   # report only
```

`--tables-root` is the same folder or URI as for the builds, so the command runs locally and on either platform. It maintains every table the pipeline writes: each source's bronze table and load issues, silver, gold and the quality results. Tables that do not exist yet are skipped.

## What it does

For each table:

1. **`OPTIMIZE`** compacts the table's small files into fewer, larger ones. This is a new table version with the same rows; readers of older versions are unaffected.
2. **`VACUUM`** deletes the files that no version within the **retention period** refers to.

The retention defaults to **7 days**, Delta's default: time travel, and readers that started before a rewrite, keep working for that long. A shorter retention (`--retention-days`) is refused unless `--force-short-retention` is given, because a reader that is still running could lose its files.

## Why it is needed

Replaying the real snapshots of 28–29 September 2026 through the pipeline, one incremental run per snapshot (42 runs), left **88 active files and 434 files on disk**:

| Kind of table | Tables | What grows | Fixed by |
|---|---|---|---|
| Append-only | `bronze_parking`, `bronze_parkeringsregisteret`, `silver_parking_fetch`, `quality_check_results` | One new small file per run: 37 active files after 37 snapshots | `OPTIMIZE`: 37 → 1 |
| Rewritten every run | the derived silver tables, the dimensions, the facts | One active file, and every earlier file left on disk: 42 | `VACUUM`, once the files are older than the retention |

On those tables, maintenance with the default retention took the **active files from 88 to 12**; with the retention forced to zero on a copy, the **files on disk went from 437 to 12**. The rows were identical before and after, and incremental runs continued normally.

At up to 288 runs a day, the pipeline would otherwise add about **3,000 files a day**.

## When to run it

Once a day is enough: compaction keeps reads fast, and vacuum can only remove files older than the retention anyway. Scheduling it belongs to the pipeline entry point (#69) and the platform's scheduler (#71 on Databricks, #19 on Fabric). Both Databricks and Fabric offer their own table maintenance features, which may take over this job there; whether to use them is part of the platform work (#67). This command works the same everywhere.
