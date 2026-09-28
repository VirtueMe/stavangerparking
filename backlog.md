# Backlog – Stavanger Parkering on Microsoft Fabric

Instructions for Claude Code: complete the setup steps below first, then create the labels and milestones, then one GitHub issue per entry, with the title, body and labels given. Link dependencies with "Depends on #N" once issue numbers exist. Add every issue to the project board. Do not start implementing anything beyond the setup steps.

## Case requirements and where they are covered

The case asks for a complete Fabric solution (ingestion, transformation and modelling, optionally visualisation) as the basis for a concrete discussion about data engineering at the next meeting.

| Case requirement | Issues |
|---|---|
| Overview/architecture of the data model and data flow | 2, 23 |
| Suitable fact and dimension tables | 2, 11, 12, 13, 14, 15 |
| Is the solution scalable? | 22 |
| How the code handles a new parking facility | 12, 23 |
| Fetch data programmatically from opencom.no | 3, 4, 6, 18 |
| Lakehouse, Notebooks, Data pipelines (dependencies and failure alerts) | 16, 17, 18, 19 |
| PySpark vs. Polars assessment | 5 |
| Medallion structure bronze → silver → gold | 7, 8, 9, 10, 11, 12, 13, 14 |
| Potential weaknesses of the solution | 1 (running log), 23 |
| Optional: visualise in Power BI | 24 |

This table doubles as the agenda for the presentation (issue 28).

## Fabric access and the local-first approach

A Fabric trial cannot currently be activated: the tenant is too new to be eligible. This has been flagged with Bouvet.

The plan does not wait for it:

- **Collection starts now, outside Fabric.** A scheduled GitHub Actions workflow saves the raw API responses unchanged (issue 6). The source only exposes the current state, so any day without collection is history that cannot be recovered.
- **Code is built locally on the same stack.** Fabric's pure Python notebooks run Polars and delta-rs, which run locally as `polars` and `deltalake`. Bronze, silver and gold are built and tested locally as plain Python modules, with notebooks as thin wrappers.
- **Fabric is added when access arrives.** The raw files are copied into the Lakehouse and bronze is rebuilt from them (issue 17). Because bronze is replayable, the result is the same as if Fabric had collected the data from the start.
- **Contingency:** if there is no Fabric access by 2026-10-14, the presentation shows the local solution and explains how each part maps to Lakehouse, Notebooks and Data pipelines.

## Conventions

- **Weaknesses are recorded as they appear.** Every issue that makes a trade-off or leaves a known limitation adds an entry to `docs/weaknesses.md` before it is closed. Issue 23 summarises the log; it does not reconstruct it.
- **Optional work never blocks the core.** Issues labelled `optional` are extras beyond the case requirements. They must not block or change the core solution, and the core is finished before optional work starts.
- **Conventional Commits via squash merge.** Every change reaches `main` through a squash-merged pull request whose title follows Conventional Commits. The PR title becomes the commit message on `main`, which is what the changelog is generated from.

## Setup (before any issues)

These steps produce the place the issues live in, so they cannot be issues themselves.

1. **Local repository:** `git init` in this folder with `main` as the default branch. First commit contains this `backlog.md` and a minimal `README.md`.
2. **GitHub repository:** create the public repository `VirtueMe/stavangerparking` and push `main`. The description says it is a Microsoft Fabric case solution for Stavanger parking data.
3. **Repository settings:**
   - Enable Issues, disable Wiki
   - Allow squash merging only, with the PR title as the default commit message
   - Protect `main` with a ruleset: changes only through pull requests, and the PR title check below must pass
4. **Changelog automation (GitHub Actions):**
   - `pr-title.yml`: on pull requests, validate that the title follows Conventional Commits (for example `amannn/action-semantic-pull-request`)
   - `changelog.yml`: on push to `main` (a merged PR), run git-cliff to regenerate `CHANGELOG.md` and commit it back to `main` as `chore(changelog): update CHANGELOG.md [skip ci]`
   - `cliff.toml` in the repository root, grouping commits by type (Features, Bug Fixes, Documentation, …) and skipping the changelog's own commits
   - The workflow's push must be allowed past the `main` ruleset without opening the rule for everyone: use a deploy key or GitHub App token that is on the ruleset's bypass list, and document the choice in `CONTRIBUTING.md`
5. **Labels and milestones:** create the labels and milestones listed below. Remove GitHub's default labels that are not used here.
6. **GitHub Project:** create a user-level project `The Stavanger Parking Case` owned by `VirtueMe` and link it to the repository.
   - Board view grouped by Status: `Backlog`, `Ready`, `In progress`, `In review`, `Done`
   - Table view grouped by milestone
   - Roadmap view on the milestone due dates
   - Custom field `Optional` (yes/no), so the extras can be filtered out of the core view
7. **Issues:** create the issues and add each one to the project with Status `Backlog`, and `Optional` set to match the label.

Acceptance criteria:
- The repository is public and reachable at `github.com/VirtueMe/stavangerparking`
- A test PR with a non-conventional title is blocked; after renaming and merging it, `CHANGELOG.md` on `main` contains the entry
- The changelog commit does not trigger another changelog run
- Every issue in this file exists in the repository, is on the project board and has the right milestone and labels
- Dependencies are linked with "Depends on #N"

## Labels

`infra`, `config`, `bronze`, `silver`, `gold`, `pipeline`, `fabric`, `quality`, `report`, `docs`, `adr`, `optional`, `pricing`

## Milestones

| Milestone | Due | Goal |
|---|---|---|
| M1 – Foundation & collection | 2026-10-04 | Architecture sketched and raw snapshots collected on a schedule |
| M2 – Silver & gold | 2026-10-11 | Star schema built locally from real collected history |
| M3 – Fabric & hardening | 2026-10-18 | Running in Fabric (if access), orchestration, quality checks, scalability assessment, docs |
| M4 – Buffer | 2026-10-21 | Review and rehearsal before the 22 Oct meeting |

The critical path is issues 3, 4, 5 (ADR 003) and 6: once the collector runs, everything else can catch up on the collected history.

---

## M1 – Foundation & collection

### 1. `chore: repository scaffolding`
Labels: `infra`, `docs`

- README skeleton, `docs/adr/` folder with an ADR template
- `docs/weaknesses.md` created with a short template (weakness, consequence, mitigation, related issue/ADR)
- Conventional Commits, squash-merge policy and the changelog workflow described in `CONTRIBUTING.md`
- Python project for the local stack (`polars`, `deltalake`, `pytest`), and a GitHub Actions workflow that runs the tests on pull requests

Acceptance criteria:
- A fresh clone can install the environment and run the (empty) test suite with one documented command

### 2. `docs: architecture and data model sketch`
Labels: `docs`

First version of the overview the case asks for, made before implementation so the rest of the work follows it:

- Data flow diagram: opencom.no CKAN API → raw files → bronze → silver → gold → (optional) Power BI, showing which Fabric item (Lakehouse, Notebook, Data pipeline) does each step
- The interim flow while Fabric is unavailable: GitHub Actions collector → raw files → local Delta tables
- Draft star schema: facts, dimensions, grain of each fact, keys
- Where configuration lives and how a new source or facility enters the flow

Acceptance criteria:
- Lives in `docs/architecture.md`, with diagrams as Mermaid so they render on GitHub
- Later issues that change the design update this document

### 3. `feat(config): declarative source configuration`
Labels: `config`

Add `config/sources.json` describing each source: CKAN base URL, package id (`stavanger-parkering`), preferred format (JSON), raw file path and bronze table. The collector and the ingestion code read this file; nothing source-specific is hardcoded.

Files in the repository are not automatically available in the Lakehouse. Document how the config will reach the Fabric notebooks (for example a Fabric environment resource, a deploy step to `Files/config/`, or a config notebook).

Acceptance criteria:
- Config is validated on load and fails with a clear message if a required field is missing
- Adding a second source requires only a new config entry
- The route from repository to Fabric runtime is documented

### 4. `feat(bronze): resolve download URL through the CKAN API`
Labels: `bronze`

Use `package_show` on the CKAN API to find the JSON resource instead of hardcoding the download link.

Acceptance criteria:
- A changed resource id does not break ingestion
- A missing JSON resource fails the run with a clear error

### 5. `docs(adr): initial architecture decisions`
Labels: `adr`, `docs`

- ADR 003: Polling interval vs. source update frequency, capacity cost and small-file growth. Written first, since the collector (issue 6) needs it
- ADR 007: Collecting outside Fabric while access is pending. Why raw files are the source of truth, why the later backfill gives the same result, and the limits of GitHub Actions scheduling (minimum 5-minute interval, delayed or skipped runs, schedules disabled after 60 days of repository inactivity)
- ADR 001: Polars (pure Python notebooks) over PySpark. Covers data volume (rows per day derived from facility count × the polling interval in ADR 003), startup time, capacity cost, local development on the same stack, and when Spark would become the right choice
- ADR 002: JSON over CSV as the source format

### 6. `feat(bronze): scheduled raw snapshot collector outside Fabric`
Labels: `bronze`, `pipeline`

Depends on: 3, 4, 5

A GitHub Actions workflow on a schedule (interval from ADR 003) that resolves the resource through CKAN (issue 4), downloads the JSON and saves it unchanged.

- Stored on a dedicated `data` branch, using the same layout the Lakehouse will use: `bronze/parking/yyyy/mm/dd/HHmmss.json`, with a small metadata sidecar per file (`ingested_at` UTC, `source_url`, `content_hash`, `run_id`)
- The `data` branch is excluded from the changelog workflow and from the `main` ruleset
- A failed run is visible (failed workflow run and GitHub notification), and gaps in collection can be listed afterwards

Acceptance criteria:
- Runs unattended and collects snapshots continuously from M1 onwards
- Files are never modified or deleted after they are written
- Keeps running until Fabric ingestion (issue 18) has been verified, so there is no gap during the switch

---

## M2 – Silver & gold

All of M2 is built and tested locally with `polars` and `deltalake`, as plain Python modules that Fabric notebooks later wrap.

### 7. `feat(bronze): load raw snapshots into the bronze table`
Labels: `bronze`

Depends on: 6

Append the collected raw files to a bronze Delta table: all source columns as strings, plus `ingested_at` (UTC), `source_url`, `content_hash`, `run_id` from the sidecar.

Acceptance criteria:
- Bronze is append-only; no step ever updates or deletes bronze data
- Loading is idempotent: files already loaded are skipped, tracked by `run_id`
- Raw files and table rows can be traced to each other through `run_id`
- The same code runs against local paths and Lakehouse paths, selected by configuration

### 8. `feat(silver): typed parsing of snapshots`
Labels: `silver`

Depends on: 7

- `Dato` + `Klokkeslett` → timestamp in Europe/Oslo, stored alongside UTC; ambiguous and non-existent times at DST transitions are handled explicitly
- Latitude/longitude → decimal
- `Antall_ledige_plasser` → nullable integer `available_spaces` plus `status` (`numeric`, `open`, `unknown`)

Acceptance criteria:
- Unparseable values are quarantined with the reason, never silently dropped
- Tests cover the `"Open"` value and a DST transition, and run automatically on pull requests

### 9. `feat(silver): deduplication and replayability`
Labels: `silver`

Depends on: 8

- Deduplicate on (facility name, source timestamp)
- A full rebuild of silver from bronze produces the same result as incremental runs

Acceptance criteria:
- Re-running the same batch does not create duplicates
- A rebuild mode exists and is documented

### 10. `feat(silver): source staleness detection`
Labels: `silver`, `quality`

Depends on: 8

Flag snapshots where the source timestamp has not advanced for longer than a configurable threshold relative to `ingested_at`.

Acceptance criteria:
- Stale periods are visible in silver and can be excluded or highlighted in gold

### 11. `feat(gold): date and time dimensions`
Labels: `gold`

- `dim_date` (weekday, week, month, Norwegian public holidays if feasible)
- `dim_time` at minute granularity with a 15-minute bucket column

### 12. `feat(gold): facility dimension with MERGE`
Labels: `gold`

Depends on: 9

`dim_parking_facility`: surrogate key, name as natural key, coordinates, capacity from a reference seed file, `first_seen`, `last_seen`, `is_active`.

Acceptance criteria:
- A new facility in the feed appears automatically on the next run with no code change (tested with a synthetic 10th facility)
- A facility missing from the feed is marked inactive, never deleted
- Missing capacity is allowed and shown as unknown

### 13. `feat(gold): availability snapshot fact`
Labels: `gold`

Depends on: 11, 12

`fact_parking_availability` as a periodic snapshot fact: facility key, date key, time key, `available_spaces`, `status`, source and ingestion timestamps.

Acceptance criteria:
- Rows with an unknown facility map to an unknown member instead of being lost
- The semi-additive nature of `available_spaces` is documented

### 14. `feat(gold): hourly aggregate fact`
Labels: `gold`

Depends on: 13

`fact_parking_hourly`: average, minimum and maximum available spaces plus observation count per facility per hour.

### 15. `docs(adr): data modelling decisions`
Labels: `adr`, `docs`

- ADR 004: Facility name as natural key, with the risks of renames
- ADR 005: Capacity as manually maintained reference data

---

## M3 – Fabric & hardening

Issues 16–19 need Fabric access. If access has not arrived by 2026-10-14, they are moved to the discussion in the presentation (see contingency above) and the rest of M3 continues.

### 16. `chore(infra): set up Fabric workspace with GitHub integration`
Labels: `infra`, `fabric`

Blocked until Fabric access is available (flagged with Bouvet).

Manual steps in the Fabric UI (done by Benny): activate the capacity, create the workspace and Lakehouse, connect the workspace to this repository.

Acceptance criteria:
- Workspace is connected to a `fabric` branch in `VirtueMe/stavangerparking`, and the first sync commit exists there
- Fabric's own sync commits reach `main` only through squash-merged pull requests with a conventional title
- Lakehouse name and workspace name are recorded in the README

### 17. `feat(fabric): notebooks and backfill from collected raw files`
Labels: `fabric`, `bronze`

Depends on: 7, 14, 16

- Copy the raw files from the `data` branch to `Files/bronze/parking/...` in the Lakehouse
- Notebooks wrapping the bronze, silver and gold modules, with the config available as documented in issue 3
- Rebuild bronze, silver and gold in the Lakehouse from the raw files

Acceptance criteria:
- Row counts and a sample of values in the Lakehouse gold tables match the local build from the same raw files

### 18. `feat(pipeline): scheduled ingestion in Fabric`
Labels: `pipeline`, `fabric`

Depends on: 16, 17

A Data pipeline that runs the ingestion notebook on the schedule from ADR 003, writing raw files and bronze in the Lakehouse.

Acceptance criteria:
- Runs unattended, and overlaps with the GitHub Actions collector until both produce matching snapshots
- The switch-over and the retirement of the collector are documented

### 19. `feat(pipeline): end-to-end orchestration with failure alerts`
Labels: `pipeline`, `fabric`

Depends on: 17, 18

Bronze → silver → gold with dependencies, parameters from the config, and a notification on failure.

Acceptance criteria:
- A failing step stops downstream steps and sends an alert

### 20. `perf(pipeline): Delta table maintenance`
Labels: `pipeline`

Depends on: 7

Scheduled `OPTIMIZE` and `VACUUM` to handle small-file growth from frequent polling. Implemented against local tables first, scheduled in Fabric once issue 19 exists.

### 21. `test(quality): data quality and schema drift checks`
Labels: `quality`

Depends on: 8

- Expected row count per snapshot, coordinates within the Stavanger area, non-negative available spaces
- Detection of new, renamed or missing columns in the source

Acceptance criteria:
- Check results are stored and visible
- A critical failure stops the run locally, and stops the pipeline once orchestration (issue 19) exists

### 22. `docs(adr): scalability assessment`
Labels: `adr`, `docs`

Depends on: 2, 5

ADR 006, answering the case question "is the solution scalable?" concretely along each axis:

- **More facilities:** what grows, and what stays unchanged (links to the new-facility handling in issue 12)
- **Longer history:** years of snapshots, partitioning of bronze and gold, small files and table maintenance
- **More sources or municipalities:** how far the declarative config (issue 3) carries, and what would have to change
- **Higher polling frequency:** capacity cost and storage growth
- **Engine:** the concrete volume or workload at which Polars should be replaced by Spark, and what the migration would involve

Acceptance criteria:
- Each axis states the current limit, the first thing that breaks, and the change needed
- Numbers are based on collected data volumes, not assumptions

### 23. `docs: README with architecture and known weaknesses`
Labels: `docs`

Depends on: 2, 22

Final version of the architecture from issue 2: diagram, data flow, star schema, how a new facility is handled, how to rebuild from bronze, a summary of the scalability assessment, and the known weaknesses from `docs/weaknesses.md` with links to the relevant ADRs.

### 24. `feat(report): Power BI semantic model and report`
Labels: `report`, `optional`

Depends on: 14, 16

Availability over time, typical weekday/hour patterns per facility, map of facilities, data freshness indicator.

---

## M3 – Optional: demand-responsive pricing

A separate extra on top of the core solution: simulate how prices could vary with occupancy and rush hours. Inspired by demand-responsive pricing schemes such as SFpark in San Francisco, which aimed to keep occupancy within a target band. These issues belong to milestone M3 and are only started once the core is done.

### 25. `feat(pricing): pricing rules and suggested price`
Labels: `gold`, `pricing`, `optional`

Depends on: 12, 14

- Tariff reference file with current hourly prices per facility, entered manually
- Pricing rules as config, not code: occupancy thresholds with multipliers, rush-hour windows defined against `dim_time`, and minimum and maximum price per facility
- Occupancy computed from capacity; where capacity is missing, an estimated capacity from the highest observed number of free spaces, clearly flagged as estimated
- Gold table `fact_suggested_price` per facility and time slot: occupancy, base price, applied rule, suggested price

Acceptance criteria:
- Changing a threshold or multiplier requires only a config change
- Every suggested price can be traced to the rule that produced it
- Estimated capacity is never presented as actual capacity

### 26. `feat(report): pricing simulation page`
Labels: `report`, `pricing`, `optional`

Depends on: 24, 25

- Heatmap of occupancy by weekday and hour per facility
- Current price vs. suggested price over time
- Hours per week each facility spends outside the target occupancy band
- What-if parameters so the viewer can adjust thresholds and multipliers with sliders and see the effect immediately

Acceptance criteria:
- The page states clearly that this is a policy simulation, not a demand forecast, since there is no data on how drivers react to price changes

### 27. `docs(pricing): requirements for operational price boards`
Labels: `docs`, `pricing`, `optional`

Discussion only. Not to be implemented in the case. A requirements document for what it would take to show the current hourly price to drivers on electronic boards at the entrance, as discussion material for the meeting.

Architecture:
- Fabric is an analytical platform; setting prices on physical boards is an operational process. The price decision should run in an operational service, with Fabric used for analysis, rule tuning and follow-up. An event-driven path (for example Eventstream and Activator) can be evaluated, but the boards must not depend on the analytics platform being available
- The price engine publishes price changes as events, which gives a complete, replayable log of which price applied where and when

Requirements to resolve:
- **Price shown before entry:** the driver must see the price before driving in. Check the Norwegian parking regulations (parkeringsforskriften) for requirements on signage and price information
- **Price locking:** decide which price applies to a car that entered at one price when the price later changes, for example locking the price at entry time
- **Integration with payment:** the displayed price must match what is charged in payment apps, ticket machines and plate-recognition (ANPR) systems
- **Stability:** hysteresis and a minimum interval between changes, so prices do not flip back and forth around a threshold
- **Caps and fairness:** maximum price and maximum change per step, set by the owner
- **Fallback:** a fixed default price when data is stale, missing or the facility reports `Open`
- **Latency:** a defined maximum time from occupancy change to updated board
- **Governance:** who may change rules and thresholds, with approval and an audit log of every rule change
- **Board operations:** monitoring that each board shows the intended price, and alerting when a board is offline or shows something else
- **Communication:** informing drivers that prices vary, and publishing the rules openly

---

## M4 – Buffer

### 28. `docs: presentation walkthrough`
Labels: `docs`

Depends on: 23

A 10–15 minute walkthrough prepared for the 22 October meeting, following the case requirements table at the top of this file: architecture and data model, how a new facility is handled, scalability, the PySpark vs. Polars choice, collecting outside Fabric, and the known weaknesses.
