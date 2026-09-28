# Architecture decision records

Each significant decision is recorded as an ADR: the context, the decision, the alternatives and the consequences.

- One file per decision: `NNN-short-title.md`, numbered in order (`001`, `002`, …)
- Start from [`000-template.md`](000-template.md)
- Decisions are not edited after they are accepted. A changed decision gets a new ADR, and the old one is marked `Superseded by ADR NNN`

| ADR | Title | Status |
|---|---|---|
| [001](001-polars-over-pyspark.md) | Polars on pure Python notebooks, not PySpark | Accepted |
| [002](002-json-over-csv.md) | Use the JSON resource, not the CSV | Accepted |
| [003](003-polling-interval.md) | Adaptive polling — every 5 minutes while values change, every 20 minutes while they don't | Accepted |
| [007](007-collect-outside-the-platform.md) | Collect raw snapshots outside the data platform while access is pending | Accepted |

Numbers 004–006 are reserved for decisions already planned: data modelling (#15) and the scalability assessment (#22).
