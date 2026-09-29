# ADR 006: Scalability — the solution scales in facilities and years; the limits are one machine's memory and the per-run derivation

- **Status:** Accepted
- **Date:** 2026-09-29
- **Issue:** #22

## Context

The case asks whether the solution is scalable. This record answers it per axis, with numbers measured on the collected data and on a benchmark of the real code, not estimates. Each axis states the current limit, what breaks first, and the change that would be needed.

**What was measured (2026-09-29):**

- **Raw data.** A parking snapshot is **1.2 KB** of JSON (9 records) plus a **0.6 KB** sidecar. A register snapshot, filtered to the 9 mapped areas, is **40 KB** (the full response is 540 KB, ADR 010). The `data` branch held 109 files and 915 KB, which Git packs to **378 KB**, because identical snapshots (the frozen feed, the unchanged register) are stored once.
- **Files in the tables.** Replaying the real snapshots as 42 incremental runs left 88 active files and 434 files on disk; maintenance brings both to 12 (#20, [`docs/maintenance.md`](../maintenance.md)).
- **The transformations.** [`benchmarks/scaling.py`](../../benchmarks/scaling.py) generates bronze rows for a number of facilities and years in the worst case for the model: a fetch every 5 minutes, every fetch a new reading with changed values. It runs them through the real silver and gold code (parse, deduplicate, freshness, facility, availability, hourly) in memory, in its own process capped at 20 GB, on an 8-core machine with 31 GB. It leaves out reading and writing the Delta tables, so its **times are lower bounds**; memory, the binding limit, is measured as it is:

| Facilities | Years | Fetches | Time | Peak memory |
|---|---|---|---|---|
| 9 | 1 | 0.95 M | 1.1 s | 1.1 GB |
| 9 | 5 | 4.7 M | 9.5 s | 3.4 GB |
| 90 | 1 | 9.5 M | 22 s | 6.6 GB |
| 9 | 10 | 9.5 M | 12 s | 6.8 GB |
| 90 | 2 | 18.9 M | 24 s | 13.1 GB |
| 90 | 3 | 28.4 M | out of memory at the 20 GB cap | |
| 90 | 5 | 47 M | out of memory at the 20 GB cap | |
| 900 | 1 | 95 M | out of memory at the 20 GB cap | |

The cap limits the process's address space, which is stricter than the memory it actually uses, so a machine with 20 GB may manage somewhat more than these rows show. Rerun with `uv run python benchmarks/scaling.py`.

Today's volume, 9 facilities with adaptive polling (ADR 003), is at most the first row per year, and in practice less: a quiet or frozen feed is fetched every 20 minutes, not every 5.

## Decision

The solution scales in **facilities, history and polling frequency** within the limits below, and beyond them the changes are known and local. **More sources or municipalities need changes to the model**, not only configuration. Per axis:

### More facilities

- **What grows:** rows, linearly: one fetch row per facility per snapshot, and one row per facility in the facility dimension. Nothing else: the code has no list of facilities.
- **What stays unchanged:** a new facility needs no code or configuration change. It appears in the next snapshot, is inserted into the dimension with a new key (#12, tested with a synthetic tenth facility), and is included in every fact.
- **What needs a person:** the facility mapping (ADR 005). Until a new facility is mapped, it has no capacity, and the quality check stops the run (`facility_count`, #21). This is deliberate, and one line of configuration per facility.
- **First limit:** memory, as for longer history below: 90 facilities for a year is 9.5 M fetches, 6.6 GB and 22 s; for two years, 13.1 GB; for three, more than 20 GB.

### Longer history

- **What grows:** bronze and the silver fetch table, by up to 105,000 snapshots per year (one every 5 minutes), about 0.95 M fetch rows per year for 9 facilities; the raw files by up to 190 MB per year before Git's deduplication.
- **First thing that breaks: the per-run derivation.** Silver derives readings, conflicts and staleness from **all** fetches on every run, and gold rebuilds its facts from them (ADR 009). That is what makes incremental runs equal a rebuild, but it makes each run's time and memory grow with the whole history: at 5 years, every 5-minute run takes about 10 s and 3.4 GB. That is fine, but not at 50 years or 50 cities.
- **Change needed:** derive only the affected time window: the dates the new fetches touch, plus the previous reading per facility, merged into the existing tables. Partitioning bronze, the fetch table and the facts by date makes that window cheap to read and write. The derivation is written as pure functions over frames, so the window can be introduced without changing the rules.
- **Also growing, and already handled:** small files (maintenance, #20); finding what is new reads the row keys of bronze and silver (noted in `docs/weaknesses.md`); the collector reads the latest sidecars of the `data` branch, whose clone grows with the number of files.

### More sources or municipalities

- **What carries:** the source configuration is declarative (#3). A new CKAN or HTTP source is a config entry with its own raw path and bronze table; collection, sidecars, adaptive polling, bronze loading, table maintenance and the quality checks' framework need no change.
- **First thing that breaks: the model is written for one parking source.** Parsing knows this feed's fields (`Dato`, `Klokkeslett`, `Sted`, `Antall_ledige_plasser`), silver and gold read the one source named `stavanger_parking`, and a facility is identified by its name alone (ADR 004). A second municipality with a facility called "Sentrum" would collide with this one's.
- **Change needed:** a mapping of each source's fields onto the silver schema (the parser keyed by source), `source_id` in the facility's natural key and in the facts, and the facility mapping per source. Each source then runs the same silver and gold code, independently, which is also where running sources in parallel (and Spark, below) starts to pay.

### Higher polling frequency

- **Today:** at most every 5 minutes, the cadence of the trigger (ADR 008), and adaptively every 20 minutes while values do not change (ADR 003). The source publishes every 2 minutes, so a faster cadence would at most see 2.5 times as many readings.
- **What grows:** runs, files and rows, linearly with the frequency. Per run, the cost does not change with frequency but with history (above): polling every minute multiplies the daily work by five, because every run derives from all fetches.
- **Storage:** the parking feed is small (1.8 KB per snapshot with its sidecar). The **register** is now the larger raw source: 40 KB every hour is about 350 MB per year in raw bytes. Git stores identical snapshots once, so unchanged hours cost little in the repository; on the platform's storage they would not be deduplicated. Storing a register snapshot only when its content changes would make it negligible.
- **On the platform:** each run costs capacity (Fabric capacity units, Databricks compute). A platform scheduler has no 5-minute floor, but faster polling only pays if the analyses need a finer grain than the 15-minute buckets they use (ADR 003).

### Engine: Polars on one machine, or Spark

- **Today:** Polars on one node reads and writes the Delta tables (ADR 001). A year of the current feed takes about a second and 1 GB for the whole derivation, in the worst case.
- **The limit is memory, not time.** The derivation holds all fetches in memory. Peak memory grows by about 0.7 GB per million fetches; the time stays under half a minute up to the largest scale that fits (18.9 M fetches in 24 s).
- **Switch to Spark** (or introduce the windowed derivation first) when one of these holds:
  - the fetch table approaches **20 million rows**: 18.9 M needed 13.1 GB, and 28.4 M no longer fitted in 20 GB (for example 90 facilities over 3 years, or several cities);
  - many sources must be processed in parallel within one run;
  - the platform is Databricks and its Spark-based managed features (Auto Loader, declarative pipelines) are wanted (ADR 001).
- **What a migration involves:** the Delta tables stay as they are, and every reader (SQL endpoints, Power BI) is unaffected. The transformations are pure functions over frames with one rule each; they would be rewritten in PySpark function by function, with the existing tests as the specification. The windowed derivation, if done first, postpones the switch by making each run small, whatever the history.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Switch to Spark now | At today's volume a full derivation takes a second on one node; Spark would add cluster start-up time and cost to every run for no gain (ADR 001) |
| Windowed (incremental) derivation now | Adds merge logic and partitioning that pay off only at many millions of rows; the full derivation is what guarantees that an incremental run equals a rebuild (ADR 009), and it is fast enough |
| Partition the tables by date now | Tables of a few megabytes gain nothing from partitions, and small partitions make the small-file problem worse; partitioning belongs with the windowed derivation |

## Consequences

- The solution handles the case's data with a large margin: the current feed, in the worst case, takes a second a year.
- The first limits are known and measured: the per-run derivation over all history, and one machine's memory between 19 and 28 M fetch rows on 20 GB. That is about 20 years of today's feed in the worst case, or 90 facilities for two years. The benchmark can be rerun to check them as the code changes.
- A second municipality needs model changes (a per-source parser, `source_id` in the facility key), not just configuration; this is the largest gap between "scalable" and "scalable to other cities".
- The register's hourly raw snapshots are the largest raw growth; storing them on change would remove it.
- Revisit when a second source is added, when the fetch table passes 10 M rows, or when the platform decision (#67) lands on Databricks with Spark-based ingestion.
