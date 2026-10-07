# Changelog

All notable changes to this project are documented in this file, generated from
Conventional Commit messages on `main`.

## [0.25.0] - 2026-10-07

### Features

- **bronze:** Record a source's server errors as outages, so they do not fail the run ([#125](https://github.com/VirtueMe/stavangerparking/pull/125))

## [0.24.1] - 2026-10-02

### Documentation

- **databricks:** Free Edition reaches the sources; the collector works ([#123](https://github.com/VirtueMe/stavangerparking/pull/123))

## [0.24.0] - 2026-10-02

### Features

- **bronze:** Read the collector's failed runs from its run history ([#120](https://github.com/VirtueMe/stavangerparking/pull/120))

## [0.23.2] - 2026-10-02

### Documentation

- **walkthrough:** Refresh order, and no assumed state in the meeting demo ([#118](https://github.com/VirtueMe/stavangerparking/pull/118))

## [0.23.1] - 2026-10-02

### Bug Fixes

- **report:** Occupancy visuals leave out the hours of a frozen source ([#117](https://github.com/VirtueMe/stavangerparking/pull/117))

## [0.23.0] - 2026-10-02

### Features

- **pipeline:** Rebuild silver on a platform with tools/rebuild ([#116](https://github.com/VirtueMe/stavangerparking/pull/116))

## [0.22.2] - 2026-10-02

### Bug Fixes

- **silver:** "Fullt" is 0 free, and a single misfit reading is a source glitch ([#115](https://github.com/VirtueMe/stavangerparking/pull/115))

## [0.22.1] - 2026-10-01

### Bug Fixes

- **databricks:** A clean run succeeds: exit only on a non-zero code ([#110](https://github.com/VirtueMe/stavangerparking/pull/110))

## [0.22.0] - 2026-10-01

### Features

- **gold:** The source's stale periods, and an incident timeline in the report ([#108](https://github.com/VirtueMe/stavangerparking/pull/108))

## [0.21.1] - 2026-10-01

### Documentation

- The source feed's freeze as a dated past incident ([#106](https://github.com/VirtueMe/stavangerparking/pull/106))

## [0.21.0] - 2026-10-01

### Features

- **fabric:** The Fabric platform folder, ready to deploy ([#102](https://github.com/VirtueMe/stavangerparking/pull/102))

## [0.20.4] - 2026-10-01

### Documentation

- Deploying in the README ([#100](https://github.com/VirtueMe/stavangerparking/pull/100))

## [0.20.3] - 2026-10-01

### Documentation

- **walkthrough:** The live change is Forum's reserved spaces ([#96](https://github.com/VirtueMe/stavangerparking/pull/96))

## [0.20.2] - 2026-09-30

### Documentation

- **walkthrough:** Merge a change live during the presentation ([#93](https://github.com/VirtueMe/stavangerparking/pull/93))

## [0.20.1] - 2026-09-30

### Documentation

- Walkthrough for the 22 October meeting ([#90](https://github.com/VirtueMe/stavangerparking/pull/90))

## [0.20.0] - 2026-09-30

### Features

- **report:** The visuals on the report's five pages ([#89](https://github.com/VirtueMe/stavangerparking/pull/89))

## [0.19.1] - 2026-09-30

### Documentation

- README with the architecture and known weaknesses ([#88](https://github.com/VirtueMe/stavangerparking/pull/88))

## [0.19.0] - 2026-09-30

### Features

- **report:** Generate and publish the Power BI model per platform ([#86](https://github.com/VirtueMe/stavangerparking/pull/86))

## [0.18.1] - 2026-09-30

### Bug Fixes

- **collect:** An on-demand snapshot starts no gap ([#85](https://github.com/VirtueMe/stavangerparking/pull/85))

## [0.18.0] - 2026-09-30

### Features

- **tools:** Run platform scripts with --platform or PLATFORM from .env ([#84](https://github.com/VirtueMe/stavangerparking/pull/84))

## [0.17.0] - 2026-09-30

### Features

- **databricks:** Deploy the pipeline with a Databricks Asset Bundle ([#81](https://github.com/VirtueMe/stavangerparking/pull/81))

## [0.16.0] - 2026-09-30

### Features

- **pipeline:** One entry point for the whole pipeline ([#79](https://github.com/VirtueMe/stavangerparking/pull/79))

## [0.15.2] - 2026-09-30

### Documentation

- **adr:** One repository for Fabric and Databricks ([#78](https://github.com/VirtueMe/stavangerparking/pull/78))

## [0.15.1] - 2026-09-29

### Documentation

- **pricing:** Requirements for operational price boards ([#76](https://github.com/VirtueMe/stavangerparking/pull/76))

## [0.15.0] - 2026-09-29

### Features

- **pricing:** Pricing rules and suggested price ([#75](https://github.com/VirtueMe/stavangerparking/pull/75))

## [0.14.2] - 2026-09-29

### Documentation

- **adr:** Scalability assessment ([#74](https://github.com/VirtueMe/stavangerparking/pull/74))

## [0.14.1] - 2026-09-29

### Performance

- **pipeline:** Delta table maintenance ([#73](https://github.com/VirtueMe/stavangerparking/pull/73))

## [0.14.0] - 2026-09-29

### Features

- **report:** Power BI semantic model and report ([#66](https://github.com/VirtueMe/stavangerparking/pull/66))

## [0.13.0] - 2026-09-29

### Features

- **quality:** Data quality and schema drift checks ([#65](https://github.com/VirtueMe/stavangerparking/pull/65))

## [0.12.1] - 2026-09-29

### Documentation

- **adr:** Record filtered raw files for reference data in ADR 007 ([#64](https://github.com/VirtueMe/stavangerparking/pull/64))

## [0.12.0] - 2026-09-29

### Features

- **bronze:** Collect Parkeringsregisteret hourly with the parking feed ([#63](https://github.com/VirtueMe/stavangerparking/pull/63))

## [0.11.0] - 2026-09-29

### Features

- **gold:** Hourly aggregate fact ([#61](https://github.com/VirtueMe/stavangerparking/pull/61))

## [0.10.0] - 2026-09-29

### Features

- **gold:** Availability snapshot fact ([#59](https://github.com/VirtueMe/stavangerparking/pull/59))

## [0.9.0] - 2026-09-29

### Features

- **gold:** Facility dimension with MERGE ([#58](https://github.com/VirtueMe/stavangerparking/pull/58))

## [0.8.0] - 2026-09-29

### Features

- **gold:** Date and time dimensions ([#57](https://github.com/VirtueMe/stavangerparking/pull/57))

## [0.7.0] - 2026-09-29

### Features

- **bronze:** Collect Parkeringsregisteret for facility capacities ([#56](https://github.com/VirtueMe/stavangerparking/pull/56))

## [0.6.1] - 2026-09-29

### Documentation

- **adr:** Data modelling decisions ([#55](https://github.com/VirtueMe/stavangerparking/pull/55))

## [0.6.0] - 2026-09-29

### Features

- **silver:** Source staleness detection ([#53](https://github.com/VirtueMe/stavangerparking/pull/53))

## [0.5.0] - 2026-09-29

### Features

- **silver:** Deduplication and replayable incremental builds ([#52](https://github.com/VirtueMe/stavangerparking/pull/52))

## [0.4.0] - 2026-09-29

### Features

- **silver:** Typed parsing of parking readings ([#51](https://github.com/VirtueMe/stavangerparking/pull/51))

## [0.3.0] - 2026-09-28

### Features

- **bronze:** Load raw snapshots into the bronze table ([#48](https://github.com/VirtueMe/stavangerparking/pull/48))

## [0.2.2] - 2026-09-28

### Documentation

- Architecture and data model sketch ([#47](https://github.com/VirtueMe/stavangerparking/pull/47))

## [0.2.1] - 2026-09-28

### Documentation

- Record source operations findings ([#46](https://github.com/VirtueMe/stavangerparking/pull/46))

## [0.2.0] - 2026-09-28

### Features

- **bronze:** Scheduled raw snapshot collector outside Fabric ([#44](https://github.com/VirtueMe/stavangerparking/pull/44))

## [0.1.1] - 2026-09-28

### Documentation

- **adr:** Initial architecture decisions ([#43](https://github.com/VirtueMe/stavangerparking/pull/43))

## [0.1.0] - 2026-09-28

### Features

- **config:** Declarative source configuration ([#39](https://github.com/VirtueMe/stavangerparking/pull/39))
- **bronze:** Resolve download URL through the CKAN API ([#38](https://github.com/VirtueMe/stavangerparking/pull/38))

### Documentation

- Data licence attribution (NLOD 2.0) ([#40](https://github.com/VirtueMe/stavangerparking/pull/40))
- Add backlog and README

