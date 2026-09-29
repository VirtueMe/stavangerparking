# ADR 009: Silver keeps every fetch and derives readings, conflicts and staleness from them

- **Status:** Accepted
- **Date:** 2026-09-29
- **Issues:** #8, #9, #10

## Context

Silver turns bronze, where every field is the string the source delivered, into typed data for the gold facts (#13, #14). Several properties of the source shape it:

- **Time without an offset.** `Dato` and `Klokkeslett` are Oslo local time to the minute. At the autumn DST change, 02:00–02:59 happens twice; in spring, it does not exist.
- **Repeats.** The file is re-published every 2 minutes and fetched every 5 or 20 (ADR 003); a frozen feed repeats one reading for days. On 2026-09-29, 108 fetches held 9 distinct readings.
- **Non-numeric values.** `Antall_ledige_plasser` is sometimes `"Open"` instead of a count.
- **Staleness is invisible in the metadata.** Every "updated" signal moves with the re-upload, not with the data (docs/weaknesses.md).
- **Replayability.** Bronze can be rebuilt from the raw files, and silver must give the same result whether it is built incrementally, in any order, or from scratch. Late files are expected: the backfill onto the platform (#17) delivers old fetches after new ones.
- **Readers.** The tables are read by Polars and delta-rs today, and by the Fabric SQL endpoint and Power BI (or Databricks) later.

## Decision

**Time.** `reading_at` is the instant in UTC. The local `reading_date`, `reading_minute_of_day` and `utc_offset_minutes` are stored next to it, so gold builds its date and time keys from local time (#11) without a timezone-less timestamp column. An ambiguous autumn time takes the later candidate unless that lies after `ingested_at`, and is flagged `dst_resolved`; a time skipped in spring is an error in the data and is quarantined.

**Values.** Every value that cannot be parsed is quarantined with its field, raw value and reason; the reading stays with that value null. A reading is left out only when its identity is lost: no valid time or no facility. `"Open"` is a known state of the source, `status = open`, not an error.

**Two grains.**

- `silver_parking_fetch`: every parsed bronze row, one per facility per fetch. **Append-only**: a run parses only the bronze rows not yet handled, tracked per row.
- `silver_parking_reading`: one row per **(`facility`, `reading_at`)**. The **first fetch wins**: the earliest `ingested_at`, then `raw_file` and `record_index`, so the winner does not depend on processing order. A later fetch of the same reading whose values differ is a conflict, and each differing value is quarantined as `conflicting_duplicate`.

**Derived, not updated.** Readings, conflicts and the freshness tables are recomputed from all fetches on every run and replace their tables. Only the fetch table and the parse quarantine accumulate. Quarantine rows are inserted, if not already there, before the fetches are appended, so a run that stops halfway is completed by the next.

**Staleness.** Each fetched snapshot is stale when its newest source timestamp is older than `freshness.stale_after_minutes` (15) at `ingested_at`. A stale period is a run of consecutive stale snapshots in fetch order with one timestamp, from `stale_from` (the timestamp plus the threshold) to the last fetch that saw it stale. It is evidence-based: a gap in collection does not extend it.

Details, column by column: [`docs/silver.md`](../silver.md).

## Alternatives considered

| Alternative | Why not |
|---|---|
| A local timestamp without timezone (`timestamp_ntz`) | The cleanest model, but it needs the Delta `timestampNtz` feature (reader version 3, writer version 7), and it is uncertain whether the Fabric SQL endpoint and Power BI read it. The tables keep protocol 1/2 |
| Always the earlier (or later) candidate for an ambiguous time | The second pass of 02:00–02:59 would land an hour off and collide with the first pass in deduplication |
| Quarantine rows, not values | A bad coordinate would remove a reading whose count is fine |
| Deduplicate with a MERGE into the reading table ("insert if new, replace if fetched earlier") | Which fetch wins, and so which fetches are conflicts, can change when an earlier file arrives late; the fetches that were not kept would be needed to recompute that. Keeping every fetch and deriving is simpler, and costs well under a second per million fetches |
| Last fetch wins | Treats a later value as a correction without evidence; the first fetch is closest to when the source published the reading |
| Track processed files, not rows | A file whose readings were partly left out could be marked done before its kept readings were written |
| Staleness from HTTP or CKAN dates, or the ETag | They move with every re-upload of an unchanged file |
| Stale periods grouped by timestamp | If the source served an old file again after recovering, the fresh time in between would count as stale |
| Stale until the next newer reading | Claims staleness without evidence across gaps in collection |

## Consequences

- An incremental run and a rebuild give identical tables; the tests check it with batches out of order and a run that stops halfway (#9).
- How often a reading was fetched, and when, stays available in the fetch table for freshness and for the collection analysis.
- Every run reads all fetches to derive the other tables: build time grows with history, which is negligible at about 1 million fetches per year (docs/weaknesses.md).
- Gold uses the local parts for its keys, `reading_at` for validity intervals, and the stale periods for `is_stale` and `stale_minutes` (#13, #14).
- The ambiguous-hour rule relies on the source being fresh; during an outage across the autumn change it can place a reading an hour late. `dst_resolved` marks the readings it applies to.
- Revisit if a platform reader turns out to support `timestamp_ntz` well, or if the fetch table grows enough that deriving from all of it matters.
- This record holds several related decisions. A change to one of them gets a new ADR that names the section it replaces (for example *Time*); the other sections stay in force.
