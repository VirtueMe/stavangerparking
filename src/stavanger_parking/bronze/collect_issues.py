"""Records of failed collection attempts, stored next to the raw files (#119).

A snapshot that was never stored leaves no raw file, so without a record the only trace of why is
the orchestrator's log, which expires. Every failed attempt is therefore written as a small JSON
file under `ISSUES_DIR` in the storage root, and bronze loads them into `bronze_collect_issues`
(`bronze.load`):

- `fetch_failed`: a source could not be fetched or stored. The collector writes it in the same run,
  so it is stored with that run's other files, even when no source succeeded.
- `run_failed`: a run failed without leaving a record of its own, such as a failed push or a crash.
  The run cannot record that itself; a later run is given the orchestrator's failed runs and
  records those it has no record of yet (`record_failed_runs`). Finding them is the
  orchestrator's part (the Collect workflow asks the GitHub API), so the package stays
  platform-neutral.

A record is identified by its run and, for `fetch_failed`, its source. Records are immutable like
the raw files: writing one that exists is refused, and a run already recorded is skipped.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

ISSUES_DIR = "bronze/collect_issues"
FETCH_FAILED = "fetch_failed"
RUN_FAILED = "run_failed"


@dataclass(frozen=True)
class CollectIssue:
    issue: str
    run_id: str
    occurred_at: datetime
    detail: str
    source_id: str | None = None
    url: str | None = None


def issue_path(issue: CollectIssue) -> str:
    """Where a record is stored: by when it happened, then by run and source, so paths sort."""
    at = issue.occurred_at
    name = "-".join(
        filter(None, [at.strftime("%H%M%S"), issue.run_id, issue.issue, issue.source_id])
    )
    return f"{ISSUES_DIR}/{at:%Y}/{at:%m}/{at:%d}/{name}.json"


def write_issue(storage: Path, issue: CollectIssue, recorded_at: datetime) -> str:
    """Store a record; returns its path. An existing record is never overwritten."""
    path = issue_path(issue)
    record = {**asdict(issue), "occurred_at": issue.occurred_at.isoformat()}
    record["recorded_at"] = recorded_at.isoformat()
    target = storage / path
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "x", encoding="utf-8") as f:
        f.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return path


def read_issues(storage: Path) -> list[tuple[str, dict]]:
    """Every record in storage, as (path, record), oldest first."""
    return [
        (str(p.relative_to(storage)), json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(storage.glob(f"{ISSUES_DIR}/*/*/*/*.json"))
    ]


def recorded_runs(storage: Path) -> set[str]:
    return {record["run_id"] for _, record in read_issues(storage)}


def unrecorded_runs(storage: Path, run_ids: list[str]) -> list[str]:
    """The given runs that have no record yet, each once, in the given order."""
    known = recorded_runs(storage)
    return [r for r in dict.fromkeys(run_ids) if r not in known]


def record_failed_runs(storage: Path, runs: list[dict], now: datetime) -> list[str]:
    """Record the failed runs that have no record yet; returns the paths written.

    Each run is `{"run_id", "started_at", "failed_step", "url"}`; `failed_step` and `url` may be
    missing. A run that recorded its own failure, a `fetch_failed`, is not recorded again.
    """
    written = []
    for run_id in unrecorded_runs(storage, [r["run_id"] for r in runs]):
        run = next(r for r in runs if r["run_id"] == run_id)
        step = run.get("failed_step")
        issue = CollectIssue(
            RUN_FAILED,
            run_id,
            datetime.fromisoformat(run["started_at"]),
            f"failed step: {step}" if step else "failed; no step reported",
            url=run.get("url"),
        )
        written.append(write_issue(storage, issue, now))
    return written
