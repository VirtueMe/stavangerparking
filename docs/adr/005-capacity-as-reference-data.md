# ADR 005: Capacity comes from the national parking register, through a hand-maintained name mapping

- **Status:** Accepted
- **Date:** 2026-09-29
- **Issue:** #15

## Context

The feed gives the number of free spaces, not the capacity of a facility. Capacity is needed for occupancy (`occupied_spaces` and occupancy rates in gold, #13, #14) and for the pricing work (#25).

Two public sources give capacities, and on 2026-09-29 they disagreed for two of the nine facilities:

| Facility (feed) | [Parkeringsregisteret](https://www.vegvesen.no/fag/teknologi/apne-data/et-utvalg-apne-data/parkeringsregisteret-api/): paid spaces (last changed) | [Operator website](https://stavanger-parkering.no/en/parkering/p-hus/) |
|---|---|---|
| Jernbanen | 390 (2024-02-16) | 500 |
| Forum | 289 (2023-03-24) | 325 |
| Valberget, Parketten, Kyrre, Siddis, Jorenholmen, Posten, St Olav | 141, 290, 310, 360, 500, 150, 480 | the same |

- **Parkeringsregisteret** is the national register of parking areas, kept by Statens vegvesen and published as open data under NLOD 2.0 through a read API without authentication. Parking providers are required to register their areas and keep them up to date, and every change is versioned. For each area it gives paid, free, charging and accessible spaces, under its own names and ids. Stavanger Parkering's areas are listed under its organisation number (974782766).
- **The operator's website** is written for customers, has no date, and is not versioned.

The names differ between all three sources (ADR 004), so no source can be joined to the feed automatically. Capacity itself changes rarely: when a facility is rebuilt, or spaces are converted, for example to charging spaces.

## Decision

- **A mapping file in the repository** links each facility in the feed (the natural key, ADR 004) to its register id, with the register's and the operator's names for reference. It is maintained by hand and changes only when a facility is added, renamed or moved. Discrepancies between sources are noted in it.
- **Parkeringsregisteret is collected as a second source**, about once a month, by its own workflow with its own trigger (a second cron-job.org job, as for the parking feed in ADR 008). Its responses are stored unchanged, with sidecars, and loaded into bronze in the same way as the parking feed (ADR 007).
- **Capacity is the register's number of paid spaces**, taken from the latest register snapshot through the mapping; every space in these facilities is paid. A capacity change appears as a new snapshot, so capacity has a history.
- Capacity is **nullable**: a facility that is not in the mapping, such as a new facility, has an unknown capacity and null occupancy rather than a guess.
- The implementation (source configuration, collection workflow, bronze table and mapping file) is #54; the facility dimension (#12) reads capacity from it.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Capacities typed into a reference file by hand | Goes out of date without anyone noticing; the register already tracks changes, with dates |
| Match the register to the feed automatically, by name or coordinates | Names differ in every source, and coordinates by up to 280 m (ADR 004); a wrong match would silently attach the wrong capacity |
| Fetch the register as part of the 5-minute collection run | Mixes a monthly reference source into the parking collector's schedule and adaptive polling, for one call a month |
| GitHub's `schedule` trigger for the monthly run | It started 2 of about 145 scheduled runs (ADR 008); a monthly run could be skipped entirely |
| The operator's website | Written for customers, undated and unversioned; it disagrees with the register without saying which is newer |
| Estimate capacity from the highest number of free spaces ever observed | Only a lower bound, and it moves as history grows |

## Consequences

- Occupancy is computed from a dated, versioned, openly licensed source, and a capacity change reaches the model within about a month, without anyone editing a file.
- Because register snapshots are kept, occupancy for a past period can use the capacity that applied then, if the facility dimension keeps capacity with validity dates (#12 decides).
- A second source: the collector and configuration must handle a plain REST API as well as a CKAN resource, and a second workflow and cron-job.org job must be kept running.
- The mapping must be updated by hand when a facility is added or renamed; until then, that facility has no capacity. A facility in the feed without a mapping is easy to detect (#21).
- The register can be wrong or late. The discrepancies for Jernbanen and Forum are recorded, not resolved, and `free spaces > capacity` is an obvious symptom the quality checks can test (#21).
- Recorded in [`docs/weaknesses.md`](../weaknesses.md).
