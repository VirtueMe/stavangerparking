# Stavanger Parkering on Microsoft Fabric

Case solution: ingestion, transformation and modelling of open parking data from Stavanger kommune ([opencom.no/dataset/stavanger-parkering](https://opencom.no/dataset/stavanger-parkering)) using a medallion architecture (bronze → silver → gold) on Microsoft Fabric.

Work is planned and tracked in [GitHub Issues](https://github.com/VirtueMe/stavangerparking/issues) and on the project board [The Stavanger Parking Case](https://github.com/users/VirtueMe/projects/3). The [initial plan](https://github.com/VirtueMe/stavangerparking/blob/27c5e3337b7e2341de13154f83ac222038971b3e/backlog.md) the issues were created from is kept in the history.

## Status

Access to a Fabric capacity is pending. Until then, raw snapshots are collected outside the platform and the transformations are built and tested locally on the same stack (Polars and delta-rs), so they can be moved to the platform unchanged.

## Architecture

_To be written in #2: data flow, the Fabric items used for each step, and the star schema._

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
| `docs/adr/` | [Architecture decision records](docs/adr/README.md) |
| `docs/weaknesses.md` | [Known weaknesses](docs/weaknesses.md), recorded as they appear |

## Data source and licence

_To be written in #32: attribution of Stavanger kommune's data under NLOD 2.0._
