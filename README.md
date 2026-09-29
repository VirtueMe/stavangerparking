# Stavanger Parkering on Microsoft Fabric

Case solution: ingestion, transformation and modelling of open parking data from Stavanger kommune ([opencom.no/dataset/stavanger-parkering](https://opencom.no/dataset/stavanger-parkering)) using a medallion architecture (bronze → silver → gold) on Microsoft Fabric.

Work is planned and tracked in [GitHub Issues](https://github.com/VirtueMe/stavangerparking/issues) and on the project board [The Stavanger Parking Case](https://github.com/users/VirtueMe/projects/3). The [initial plan](https://github.com/VirtueMe/stavangerparking/blob/27c5e3337b7e2341de13154f83ac222038971b3e/backlog.md) the issues were created from is kept in the history.

## Status

Access to a Fabric capacity is pending. Until then, raw snapshots are [collected](docs/collector.md) on GitHub Actions, started every 5 minutes by cron-job.org ([ADR 008](docs/adr/008-trigger-collection-externally.md)), onto the [`data` branch](https://github.com/VirtueMe/stavangerparking/tree/data) ([ADR 007](docs/adr/007-collect-outside-the-platform.md)), and the transformations are built and tested locally on the same stack (Polars and delta-rs), so they can be moved to the platform unchanged.

## Architecture

The data flows from the CKAN API through raw files, bronze, silver and gold into a star schema, with a periodic snapshot fact of availability per facility and an hourly aggregate. See [`docs/architecture.md`](docs/architecture.md) for the data flow, the Fabric items used for each step, the layers and the star schema.

## Getting started

Requires [uv](https://docs.astral.sh/uv/). From a fresh clone:

```sh
uv run pytest
```

This installs the pinned Python version and dependencies into `.venv` and runs the test suite. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the development workflow.

## Repository layout

| Path | Contents |
|---|---|
| `config/sources.json` | [Source configuration](docs/config.md): where each dataset comes from, where it lands, and its licence |
| `src/stavanger_parking/` | Transformation logic as plain Python modules; notebooks import from here |
| `tests/` | Tests, run on every pull request |
| `.github/workflows/collect.yml` | [Collector](docs/collector.md): snapshot collection onto the `data` branch, started by cron-job.org |
| `docs/bronze.md` | [Bronze loading](docs/bronze.md): raw files into the bronze Delta table |
| `docs/architecture.md` | [Architecture](docs/architecture.md): data flow, layers and star schema |
| `docs/adr/` | [Architecture decision records](docs/adr/README.md) |
| `docs/weaknesses.md` | [Known weaknesses](docs/weaknesses.md), recorded as they appear |
| `templates/DATA-LICENCE.md` | Data attribution placed at the root of the `data` branch |

## Data source and licence

The data is the open dataset [Stavanger parkering](https://opencom.no/dataset/stavanger-parkering), published by **Stavanger kommune** under the [Norwegian Licence for Open Government Data (NLOD) 2.0](https://data.norge.no/nlod/en/2.0). Raw snapshots are republished unchanged on the [`data` branch](https://github.com/VirtueMe/stavangerparking/tree/data), and derived tables and reports are built from them.

> Contains data under the Norwegian licence for Open Government data (NLOD) distributed by Stavanger kommune.

Each source's licence is recorded in [`config/sources.json`](config/sources.json), so a source cannot be added without one.

## Licence

The code in this repository is licensed under the [MIT licence](LICENSE). The data is not covered by it; it remains under NLOD 2.0 as described above.
