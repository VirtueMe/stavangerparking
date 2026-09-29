# Architecture decision records

Each significant decision is recorded as an ADR: the context, the decision, the alternatives and the consequences.

- One file per decision: `NNN-short-title.md`, numbered in order (`001`, `002`, …)
- Start from [`000-template.md`](000-template.md)
- Decisions are not edited after they are accepted. A changed decision gets a new ADR, and the old one is marked `Superseded by ADR NNN`. New evidence that does not change the decision is appended as a dated addendum

| ADR | Title | Status |
|---|---|---|
| [001](001-polars-over-pyspark.md) | Polars on pure Python notebooks, not PySpark | Accepted |
| [002](002-json-over-csv.md) | Use the JSON resource, not the CSV | Accepted |
| [003](003-polling-interval.md) | Adaptive polling — every 5 minutes while values change, every 20 minutes while they don't | Accepted |
| [004](004-facility-name-as-natural-key.md) | The facility name in the feed is the natural key of a facility | Accepted |
| [005](005-capacity-as-reference-data.md) | Capacity comes from the national parking register, through a hand-maintained name mapping | Accepted; collection replaced by 010 |
| [006](006-scalability-assessment.md) | Scalability: the solution scales in facilities and years; the limits are one machine's memory and the per-run derivation | Accepted |
| [007](007-collect-outside-the-platform.md) | Collect raw snapshots outside the data platform while access is pending | Accepted |
| [008](008-trigger-collection-externally.md) | Trigger collection externally from cron-job.org, not from GitHub's schedule | Accepted |
| [009](009-silver-model.md) | Silver keeps every fetch and derives readings, conflicts and staleness from them | Accepted |
| [010](010-collect-the-register-hourly.md) | Collect the parking register hourly with the parking feed, keeping only the mapped areas | Accepted |
