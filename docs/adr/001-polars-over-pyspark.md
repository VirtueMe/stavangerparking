# ADR 001: Polars on pure Python notebooks, not PySpark

- **Status:** Accepted
- **Date:** 2026-09-28
- **Issue:** #5

## Context

The case asks for a considered choice between PySpark and Polars. The deciding factor is the size and shape of the data:

- 9 facilities, polled at most every 5 minutes (ADR 003): **at most 288 snapshots and 2,592 rows per day**, about **0.95 million rows per year**. Even ten years of history fits comfortably in memory on one machine.
- Every run processes one small batch: a single 1.2 KB snapshot for bronze, a few hundred rows for incremental silver and gold. A full rebuild from bronze (#9) touches the whole history, which is still small.
- Runs are frequent (up to 288 per day), so the fixed cost of starting a run matters more than throughput.

Fabric offers both Spark notebooks and pure Python notebooks with Polars and delta-rs (the `deltalake` package), which read and write the same Delta tables. Spark notebooks need a Spark session to start, which takes noticeably longer than starting a Python process and uses more capacity per run.

## Decision

Use **Polars with delta-rs** in pure Python notebooks. The transformation logic lives in the `stavanger_parking` package as plain Python modules; notebooks are thin wrappers that import it. The same code runs locally (`uv run pytest`) and on the platform.

## Alternatives considered

| Alternative | Why not |
|---|---|
| PySpark | A distributed engine for data that fits on one machine: slower start per run and more capacity per run, for no benefit at this volume. Local development needs a JVM and a Spark installation |
| pandas | Single-node like Polars, but slower, more memory hungry, and without a lazy API; no advantage here |
| DuckDB | A good fit as well (single node, SQL, reads Delta). Polars was chosen for its DataFrame API and native Delta writes via `write_delta`; DuckDB stays an option for ad hoc SQL |

## Consequences

- Fast, cheap runs, and the same code and tests locally and on the platform. This also made it possible to build before platform access (ADR 007).
- The Delta tables are standard, so Spark, SQL endpoints and Power BI can read them regardless of the engine that wrote them.
- **When Spark would become the right choice:**
  - the working set no longer fits in one machine's memory: tens of gigabytes rather than the current megabytes, for example hundreds of cities polled every minute over many years;
  - many sources need to be processed in parallel within one run;
  - streaming ingestion (for example Eventstream or Structured Streaming) replaces polling;
  - platform features that assume Spark become important, such as Auto Loader or declarative pipelines on Databricks.
- **Revisit if the platform becomes Databricks:** its managed features (Auto Loader, Lakeflow declarative pipelines, Unity Catalog managed tables) assume Spark, which shifts the balance even at this volume. The scalability assessment ([ADR 006](006-scalability-assessment.md)) measures where the limits are.
