# Data quality checks

The checks run after silver and gold, on the latest data, and store every result.

```sh
uv run python -m stavanger_parking.quality.check --tables-root <tables>
```

Each run appends its results to `quality_check_results`, prints the failures, and adds them to the GitHub Actions run summary. It exits with **1 if a critical check failed**. In the [pipeline](pipeline.md), which runs the checks last, a critical failure gives exit code 3, for the orchestration to alert on (#19). The results are stored before the exit, so a failed run keeps its evidence.

The checks **never stop collection**. The source only shows its current state, so a collector that stopped on a quality problem would lose history for good; collection and quality are separate steps.

## What is checked

The checks look at the **latest parking snapshot** (the newest raw file, whether it could be loaded or not), the latest register snapshot, and the last 24 full hours of the hourly fact.

| Check | Severity | Fails when |
|---|---|---|
| `schema_drift` | critical | An expected field of the source (`Dato`, `Klokkeslett`, `Sted`, `Latitude`, `Longitude`, `Antall_ledige_plasser`) is missing from the snapshot, or a new field appears |
| `empty_snapshot` | critical | The snapshot could not be loaded (a bronze load issue), or none of its readings survived parsing |
| `facility_count` | critical | The snapshot has a different number of facilities than the [facility mapping](config.md#facility-mapping) |
| `free_exceeds_capacity` | critical | A facility reports more free spaces than its capacity; one result per facility with both |
| `unmapped_facility` | warning | A facility in the snapshot is not in the mapping; one result per facility |
| `coordinates_outside_area` | warning | A facility's coordinates are missing or outside the Stavanger area (latitude 58.85–59.05, longitude 5.55–5.85); one result per facility |
| `negative_available_spaces` | warning | The source reported a negative number of free spaces (quarantined in silver) |
| `quarantined_values` | warning | Other values of the snapshot were quarantined |
| `register_missing_areas` | warning | Mapped areas are missing from the latest register snapshot, or there is none |
| `source_stale` | warning | The snapshot's data is older than the staleness threshold ([source staleness](silver.md#source-staleness)) |
| `low_coverage` | warning | Facility-hours in the last 24 full hours covered for less than 30 minutes; an hour with no reading at all counts as uncovered |
| `collect_failures` | warning | Collector runs that started in the last 24 hours and failed, by failed step ([`docs/collector.md`](collector.md#failed-runs)); runs after the latest fetch count too, since a broken collector stops the fetches |
| `source_glitches` | warning | Readings of the last 24 hours that were [glitches in the source](gold.md#source-glitches), per facility; a reading is judged when the next one arrives, so a glitch in the latest snapshot is reported by the next run |

**Why these are critical:** each means that what follows is wrong or incomplete, not just unusual. Schema drift or an empty snapshot means readings are missing or silently null; a facility count that differs from the mapping means a facility was added, renamed or removed, and its capacity and history need attention; and more free spaces than capacity means the capacity is wrong, so occupancy is wrong.

**Resolving a critical failure** usually means changing the data the model relies on, not the code: a new or renamed facility is added to the mapping, and a wrong capacity is corrected at its source or noted in the mapping.

## Results

`quality_check_results`, append-only, one row per result:

| Column | Meaning |
|---|---|
| `checked_at` | When the run happened (UTC) |
| `snapshot` | The parking snapshot the run checked |
| `check`, `severity` | The check and whether it is `critical` or a `warning` |
| `passed` | Whether it passed |
| `subject` | The facility, for checks per facility |
| `detail` | What was found, e.g. `292 free of 289` |

## Example run (2026-09-29)

On the real data on 29 September 2026, the checks failed on one critical and one warning:

- **`free_exceeds_capacity`, Forum: 292 free of 289.** The register's capacity for Forum is too low; the operator's website says 325. The run stops until the capacity is corrected ([ADR 005](adr/005-capacity-as-reference-data.md), noted in the facility mapping).
- **`source_stale`: the data was about 5.8 days old**, because the feed was frozen from 23 September (it recovered on 1 October). The warning appears whenever the source's own timestamp is too old, and goes away by itself when the feed updates again.

Everything else passed: the schema was as expected, the snapshot had its 9 facilities, all mapped and inside the Stavanger area, and the register had every mapped area.
