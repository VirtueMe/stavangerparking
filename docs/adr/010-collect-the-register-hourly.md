# ADR 010: Collect the parking register hourly with the parking feed, keeping only the mapped areas

- **Status:** Accepted
- **Date:** 2026-09-29
- **Issue:** #60
- **Replaces:** the collection section of [ADR 005](005-capacity-as-reference-data.md) (a monthly run by its own workflow)

## Context

ADR 005 takes capacities from the national parking register and collected it once a month, by a workflow of its own (#54). The parking feed is collected every 5 minutes, so a capacity change could reach the model up to a month after the availability it applies to. The register does drift: on 2026-09-29 Forum reported 292 free spaces against the register's 289 (#13).

The register's response for Stavanger Parkering is about 540 KB: 178 areas, each with its full version history. The model uses 9 of them, the areas in the facility mapping. Stored on every hourly fetch, the response would add about 13 MB a day to the `data` branch.

The collector's polling rule fetched on every run while a source was in fast mode, which was right only for a source whose fast interval equals the run's 5 minutes.

## Decision

- **The register gets a `polling` section with an hourly interval**, and is collected by the same Collect run as the parking feed. Its own workflow is removed; manual runs use Collect's `workflow_dispatch`.
- **A polled source is due once its interval, less half a run, has passed** since its last snapshot, in fast as well as slow mode. The parking feed is unaffected: it is still fetched on every run while values change (5 − 2.5 minutes), and after 17.5 minutes in slow mode. The register is fetched once an hour.
- **Only the mapped areas are stored.** The source's `filter` keeps the records whose `id` is a `register_id` in the facility mapping. Each kept record is unchanged; the file is a filtered copy of the response. Its sidecar records the full response's hash and record count, the number kept, and any mapped id missing from the response, so the file stays traceable to what the source returned. A response that is not a JSON list of records is stored unchanged. This is an exception to ADR 007's rule that raw files are stored unchanged, recorded in #62.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Monthly, by its own workflow (ADR 005, #54) | A capacity change could go unnoticed for up to a month |
| The full response on every 5-minute run | About 155 MB a day on the `data` branch, and 288 calls a day to the register, for data that changes a few times a year |
| The full response hourly, stored every time | About 13 MB a day, nearly all of it repetition |
| The full response hourly, stored only when it changes | Keeps every area and the unchanged-raw rule, but needs a second kind of "skip" in the collector; the smaller filtered copy was preferred |
| One request per mapped area (`/parkeringsomraade/{id}`) | Keeps each response unchanged, but 9 calls and 18 files an hour, doubling the files on the `data` branch |

## Consequences

- A capacity change reaches the model within about an hour, and within the same Collect run as the availability it applies to.
- One trigger, one workflow and one concurrency group for all collection; the planned monthly cron-job.org job is not needed.
- The `data` branch grows by about 1 MB a day for the register (about 40 KB an hour).
- Areas that are not mapped are never stored: a new facility's register history before it is mapped cannot be recovered. It has no capacity until it is mapped anyway (ADR 005).
- Adding a facility to the mapping changes what the collector stores from the next run.
- A run started within half a run of the last fetch, such as a manual run right after a scheduled one, no longer fetches the parking feed a second time.
