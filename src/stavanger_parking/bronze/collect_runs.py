"""The collector's failed runs: a source of its own, read from the collector's run history (#119).

    python -m stavanger_parking.bronze.collect_runs --tables-root DIR [--collector FILE]

A snapshot that was never stored leaves no raw file; the run that failed to store it is the only
trace. Whoever collects keeps that history, so reading it is the collector's integration, named in
`config/collector.json`. Today that is GitHub Actions: the Collect workflow's failed runs, from the
GitHub REST API. A platform that takes over collection (ADR 011) adds its own reader here.

`bronze_collect_runs` holds the failed runs the collector's history currently lists, one row per
run, replaced on every read: a failure older than the history's retention (about 400 days on
GitHub) drops out, which is fine for a table about current failures. A run's failed step costs one
more call, so it is asked only for runs the table does not have yet; the rest keep theirs.

Reading never stops the pipeline: if the history cannot be read (no network, a rate limit), the
read is reported and the previous table is kept. Without a token, GitHub allows 60 calls an hour;
`GITHUB_TOKEN`, if set, raises that.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl
from deltalake import DeltaTable

from stavanger_parking.bronze.ckan import make_client
from stavanger_parking.config import CONFIG_DIR
from stavanger_parking.tables import COLLECT_RUNS_TABLE, table_path

DEFAULT_COLLECTOR = CONFIG_DIR / "collector.json"
GITHUB_API = "https://api.github.com"
PAGE_SIZE = 100

RUNS_SCHEMA = {
    "run_id": pl.Int64,
    "run_attempt": pl.Int32,
    "started_at": pl.Datetime("us", "UTC"),
    "failed_step": pl.String,
    "url": pl.String,
    "read_at": pl.Datetime("us", "UTC"),
}


class RunsError(RuntimeError):
    """The collector's run history could not be read."""


@dataclass(frozen=True)
class GitHubActions:
    """A collector that runs as a GitHub Actions workflow."""

    repository: str
    workflow: str


def load_collector(path=DEFAULT_COLLECTOR) -> GitHubActions:
    """The collector whose runs are read; only GitHub Actions exists today."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        section = data["github_actions"]
        return GitHubActions(str(section["repository"]), str(section["workflow"]))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RunsError(f"{path}: no valid github_actions collector: {e}") from e


def failed_runs(
    client: httpx.Client, collector: GitHubActions, steps: dict[tuple[int, int], str | None]
) -> list[dict]:
    """The workflow's failed runs, latest attempt each; `steps` are failed steps already known."""
    base = f"{GITHUB_API}/repos/{collector.repository}/actions"
    runs, page = [], 1
    while True:
        listed = _get(
            client,
            f"{base}/workflows/{collector.workflow}/runs",
            {"status": "failure", "per_page": PAGE_SIZE, "page": page},
        )["workflow_runs"]
        runs += listed
        if len(listed) < PAGE_SIZE:
            break
        page += 1
    rows = []
    for run in runs:
        key = (int(run["id"]), int(run["run_attempt"]))
        step = steps[key] if key in steps else _failed_step(client, base, *key)
        rows.append(
            {
                "run_id": key[0],
                "run_attempt": key[1],
                "started_at": datetime.fromisoformat(run["run_started_at"]),
                "failed_step": step,
                "url": run["html_url"],
            }
        )
    return rows


def _failed_step(client: httpx.Client, base: str, run_id: int, attempt: int) -> str | None:
    jobs = _get(client, f"{base}/runs/{run_id}/attempts/{attempt}/jobs", {})["jobs"]
    failed = [s["name"] for j in jobs for s in j.get("steps", []) if s["conclusion"] == "failure"]
    return failed[0] if failed else None


def _get(client: httpx.Client, url: str, params: dict) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = client.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as e:
        raise RunsError(f"{url}: {e}") from e


def read(
    client: httpx.Client,
    collector: GitHubActions,
    tables_root: str,
    now: datetime,
    storage_options=None,
) -> int:
    """Replace `bronze_collect_runs` with the failed runs listed now; returns how many."""
    path = table_path(tables_root, COLLECT_RUNS_TABLE)
    steps = {}
    if DeltaTable.is_deltatable(path, storage_options=storage_options):
        known = pl.read_delta(path, storage_options=storage_options)
        steps = {
            (i, a): s for i, a, s in known.select("run_id", "run_attempt", "failed_step").rows()
        }
    rows = [{**r, "read_at": now} for r in failed_runs(client, collector, steps)]
    frame = pl.DataFrame(rows, schema=RUNS_SCHEMA).sort("started_at")
    frame.write_delta(
        path,
        mode="overwrite",
        storage_options=storage_options,
        delta_write_options={"schema_mode": "overwrite"},
    )
    return frame.height


def run(collector_path, tables_root: str, now: datetime, storage_options=None) -> list[str]:
    """Read the collector's failed runs; returns the report. An unreadable history never raises."""
    try:
        collector = load_collector(collector_path)
        with make_client() as client:
            count = read(client, collector, tables_root, now, storage_options)
    except RunsError as e:
        return [f"collect runs: not read, the previous table is kept ({e})"]
    return [f"collect runs: {count} failed run(s) of {collector.repository} {collector.workflow}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.bronze.collect_runs")
    parser.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    parser.add_argument("--collector", type=Path, default=DEFAULT_COLLECTOR)
    args = parser.parse_args(argv)
    for line in run(args.collector, args.tables_root, datetime.now(UTC)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
