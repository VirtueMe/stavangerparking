# ADR 003: Adaptive polling — every 5 minutes while values change, every 20 minutes while they don't

- **Status:** Accepted
- **Date:** 2026-09-28
- **Issue:** #5

## Context

The source only exposes the current state of the 9 parking facilities. History exists only if we collect it, so the polling interval decides the resolution of everything downstream: the periodic snapshot fact (#13), the 15-minute buckets in `dim_time` (#11) and the hourly aggregates (#14).

The initial plan was a fixed interval of 5 minutes. Measuring the source before deciding changed that.

What we measured on 2026-09-28:

- **The feed is re-published every 2 minutes.** Over 20 minutes of sampling, the CKAN `last_modified` of the JSON resource, the HTTP `Last-Modified` header and the ETag moved every 120 seconds (±1 s), 11 times.
- **Historically, the data was 2–4 minutes old when fetched.** Nine Wayback Machine captures between 2019 and May 2026 show the data timestamp 2–4 minutes behind the capture time (one outlier at 17 minutes). The data timestamps are Oslo local time; only that reading gives consistent delays.
- **Anomaly: right now the content is frozen.** All 20 samples, and every check since 2026-09-23 19:16, returned identical content: the same availability values and the same data timestamp. The file is re-published, but not refreshed. File and metadata dates cannot tell a live feed from a frozen one.
- **A snapshot is about 1.2 KB** (9 rows).

The anomaly shows that polling at a fixed rate spends most of its runs, files and commits on data that does not change, whether because the feed is frozen or, in normal operation, because little happens at night. We changed tactics accordingly.

Constraints:

- GitHub Actions schedules are fixed, run at most every 5 minutes, and can be delayed or dropped under load (ADR 007).
- On the data platform, every run costs capacity, and every stored snapshot adds a small file and a Delta commit to bronze.

## Decision

**Adaptive polling, driven by whether the availability values change:**

- The schedule fires every 5 minutes. Each run first decides whether to fetch.
- **Fast mode (fetch every 5 minutes)** is the default, and applies as long as the values keep changing.
- **Slow mode (fetch every 20 minutes)** applies when the last **5 stored snapshots** have identical availability values. A scheduled run in slow mode skips fetching unless 20 minutes have passed since the last stored snapshot.
- **Back to fast mode immediately** when a fetched snapshot's values differ from the previous one.
- **What is compared:** a *values fingerprint* over each facility's name and available spaces (`Sted`, `Antall_ledige_plasser`), excluding the data timestamp (`Dato`, `Klokkeslett`), which advances even when nothing else changes. A whole-file hash is kept as well (`content_hash`), which also detects a frozen feed.
- **State lives in the data.** Each sidecar records `values_fingerprint` and `content_hash`. The collector derives its mode from the latest sidecars on the `data` branch, so there is no separate state to lose, and every decision can be replayed.
- **Every fetched snapshot is stored**, changed or not, and every skipped run is logged with its reason, so a skip can be told apart from a failed run.
- The parameters (5 and 20 minutes, 5 unchanged snapshots) are configuration, not code (#6).

## Alternatives considered

| Alternative | Why not |
|---|---|
| Fixed every 5 minutes (the initial plan) | Spends most runs, files and commits on unchanged data during quiet nights and outages like the current one |
| Fixed every 2 minutes, matching the publication cadence | Not possible on GitHub Actions (minimum 5 minutes); more runs and small files for little gain, since the finest grain used is 15 minutes |
| Fixed every 10 or 15 minutes | Fewer runs, but also during busy periods: 1–2 or exactly 1 observation per 15-minute bucket, and a single skipped run leaves a bucket empty |
| Compare whole-file hashes only | The data timestamp advances every 2 minutes in normal operation, so the hash changes even when no value does; only a frozen feed would slow down, a quiet night never would |
| Use CKAN or HTTP metadata to detect change | `last_modified`, `Last-Modified` and ETag move every 2 minutes even while the data is frozen |
| Store only snapshots whose content changed | Hides *when* we looked and saw nothing new, which is the evidence for staleness; deduplication belongs in silver, where it is replayable |

## Consequences

- **Irregular observations.** Intervals are 5 minutes while values change and 20 minutes while they don't. Downstream facts must treat each observation as valid until the next one: hourly averages are time-weighted, not plain averages of observations (#13, #14). Because slow mode is only entered when values have been identical for 5 snapshots, a longer gap represents unchanged values.
- **Detection delay.** A change after a quiet period is seen up to 20 minutes late, typically at the start of the morning rush. Changes that start and end within one 20-minute gap are not seen.
- **Volume.** At most 288 snapshots (2,592 rows, about 350 KB) per day in fast mode, 72 per day in slow mode; about 0.95 million rows per year at most. This feeds the engine choice in ADR 001.
- **Small files.** Up to 288 small files and bronze commits per day: table maintenance (#20) is required.
- **Staleness stays visible.** While the source is frozen, snapshots are stored every 20 minutes; silver deduplicates them, and staleness is detected by comparing the data's own timestamp with `ingested_at` (#10), never by file or metadata dates.
- Revisit if the source's publication cadence changes, if the detection delay proves too long for the analyses, or on the platform if the capacity cost of a 5-minute schedule matters (#18).
