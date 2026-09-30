"""Run the whole pipeline: bronze load, silver, gold and quality, in that order.

    python -m stavanger_parking.pipeline run --raw-root DIR --tables-root DIR
        [--storage-option KEY=VALUE ...] [--config FILE] [--mapping FILE] [--tariffs FILE]
        [--rules FILE]

The one thing a platform calls (ADR 011). Notebooks call `run_pipeline(...)`; jobs and the command
line call `run`. The raw root is a local path (`/lakehouse/default/Files`, a Unity Catalog volume, a
folder); the tables root is anything delta-rs writes to, with `--storage-option` for credentials
and settings of an `abfss://` or other cloud URI.

A step that cannot run stops the pipeline: the steps after it do not run. A critical quality check
does not stop anything before it, since quality runs last, and its results are stored before the
run is reported as failed (#21). Collection is not part of the pipeline: it has one collector of
record, scheduled on its own (ADR 007, ADR 011).

Exit codes, for the orchestrator:

- 0: every step ran and no critical check failed
- 1: a step could not run; the report names it and why
- 2: the command line was wrong
- 3: a critical check failed; its results are in `quality_check_results`
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stavanger_parking.bronze import load
from stavanger_parking.config import DEFAULT_CONFIG
from stavanger_parking.facilities import DEFAULT_MAPPING
from stavanger_parking.gold import build as gold_build
from stavanger_parking.gold.pricing import DEFAULT_RULES, DEFAULT_TARIFFS
from stavanger_parking.quality import check
from stavanger_parking.silver import build as silver_build

OK, STEP_FAILED, CRITICAL_CHECK_FAILED = 0, 1, 3

# The errors that mean a step could not run. Anything else is a bug, and is raised.
STEP_ERRORS = (load.LoadError, silver_build.BuildError, gold_build.BuildError, check.CheckError)


@dataclass(frozen=True)
class Step:
    name: str
    report: list[str]
    error: str | None = None
    critical: bool = False


@dataclass(frozen=True)
class PipelineResult:
    steps: list[Step]

    @property
    def critical(self) -> bool:
        """A critical quality check failed."""
        return any(s.critical for s in self.steps)

    @property
    def failed_step(self) -> Step | None:
        return next((s for s in self.steps if s.error is not None), None)

    @property
    def exit_code(self) -> int:
        if self.failed_step is not None:
            return STEP_FAILED
        return CRITICAL_CHECK_FAILED if self.critical else OK

    def report(self) -> str:
        lines = []
        for step in self.steps:
            lines.append(f"== {step.name}" + (" FAILED" if step.error is not None else ""))
            lines += step.report
            if step.error is not None:
                lines.append(step.error)
        return "\n".join(lines)


def run_pipeline(
    raw_root,
    tables_root: str,
    storage_options: dict[str, str] | None = None,
    config_path=DEFAULT_CONFIG,
    mapping_path=DEFAULT_MAPPING,
    tariffs_path=DEFAULT_TARIFFS,
    rules_path=DEFAULT_RULES,
    now: datetime | None = None,
) -> PipelineResult:
    """Run every step in order, stopping at the first that cannot run; returns what each did."""
    raw_root, now = Path(raw_root), now or datetime.now(UTC)

    # Each step is named after its function
    def bronze() -> Step:
        return Step("bronze", load.run(config_path, raw_root, tables_root, now, storage_options))

    def silver() -> Step:
        return Step("silver", silver_build.run(config_path, tables_root, False, storage_options))

    def gold() -> Step:
        lines = gold_build.run(
            tables_root, mapping_path, config_path, tariffs_path, rules_path, storage_options
        )
        return Step("gold", lines)

    def quality() -> Step:
        outcome = check.run(tables_root, mapping_path, config_path, now, storage_options)
        return Step("quality", outcome.report.splitlines(), critical=outcome.critical)

    done: list[Step] = []
    for step in (bronze, silver, gold, quality):
        try:
            done.append(step())
        except STEP_ERRORS as e:
            done.append(Step(step.__name__, [], str(e)))
            break
    return PipelineResult(done)


def storage_option(text: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {text!r}")
    return key, value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="bronze load, silver, gold and quality, in order")
    run.add_argument("--raw-root", type=Path, required=True, help="root of the raw files")
    run.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    run.add_argument(
        "--storage-option",
        type=storage_option,
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="a delta-rs storage option for the tables root; repeat for more",
    )
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    run.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    run.add_argument("--tariffs", type=Path, default=DEFAULT_TARIFFS)
    run.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    args = parser.parse_args(argv)

    result = run_pipeline(
        args.raw_root,
        args.tables_root,
        dict(args.storage_option) or None,
        args.config,
        args.mapping,
        args.tariffs,
        args.rules,
    )
    print(result.report())
    if result.failed_step is not None:
        print(f"pipeline stopped: {result.failed_step.name} could not run", file=sys.stderr)
    elif result.critical:
        print("pipeline failed: a critical quality check failed", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
