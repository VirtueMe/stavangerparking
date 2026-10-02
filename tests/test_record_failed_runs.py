"""The Collect workflow's script that records earlier failed runs (#119).

The script runs for real, with the real collector, and a fake `gh` on PATH that answers the two
GitHub API calls (already filtered, as `--jq` would) and records every call.
"""

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from stavanger_parking.bronze.collect_issues import (
    FETCH_FAILED,
    CollectIssue,
    read_issues,
    write_issue,
)

REPO = Path(__file__).parents[1]
SCRIPT = REPO / ".github" / "scripts" / "record-failed-runs.sh"

RUNS = [
    {
        "run_id": "36890842735-1",
        "started_at": "2026-10-01T16:17:14Z",
        "url": "https://github.com/VirtueMe/stavangerparking/actions/runs/36890842735",
    },
    {
        "run_id": "36642529839-1",
        "started_at": "2026-09-29T22:57:12Z",
        "url": "https://github.com/VirtueMe/stavangerparking/actions/runs/36642529839",
    },
]

FAKE_GH = """#!/usr/bin/env bash
echo "gh $*" >> "$CALLS"
case "$2" in
  */actions/workflows/collect.yml/runs*) cat "$RUNS_FILE" ;;
  */jobs) echo "Commit and push snapshots" ;;
esac
"""

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None or shutil.which("jq") is None, reason="needs uv and jq"
)


def run(tmp_path: Path, storage: Path) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    (tmp_path / "runs.json").write_text(json.dumps(RUNS))
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(tmp_path / "calls.log"),
        "RUNS_FILE": str(tmp_path / "runs.json"),
        "GITHUB_REPOSITORY": "VirtueMe/stavangerparking",
    }
    return subprocess.run(
        [SCRIPT, storage], cwd=REPO, env=env, capture_output=True, text=True, check=False
    )


def gh_calls(tmp_path: Path) -> list[str]:
    return [c for c in (tmp_path / "calls.log").read_text().splitlines() if c.startswith("gh")]


def test_failed_runs_are_recorded_with_their_failed_step(tmp_path):
    storage = tmp_path / "data"

    result = run(tmp_path, storage)

    assert result.returncode == 0, result.stderr
    records = sorted((r["run_id"], r["detail"]) for _, r in read_issues(storage))
    assert records == [
        ("36642529839-1", "failed step: Commit and push snapshots"),
        ("36890842735-1", "failed step: Commit and push snapshots"),
    ]
    # One call for the list, and one per new run for its failed step
    assert len(gh_calls(tmp_path)) == 3


def test_recorded_runs_cost_no_further_calls(tmp_path):
    storage = tmp_path / "data"
    run(tmp_path, storage)
    (tmp_path / "calls.log").unlink()

    result = run(tmp_path, storage)

    assert result.returncode == 0, result.stderr
    assert "no failed runs to record" in result.stdout
    assert len(gh_calls(tmp_path)) == 1


def test_a_run_that_recorded_its_own_failure_is_skipped(tmp_path):
    storage = tmp_path / "data"
    at = datetime.fromisoformat("2026-09-29T22:57:30+00:00")
    issue = CollectIssue(FETCH_FAILED, "36642529839-1", at, "Download failed", "stavanger_parking")
    write_issue(storage, issue, issue.occurred_at)

    result = run(tmp_path, storage)

    assert result.returncode == 0, result.stderr
    jobs = [c.split()[2] for c in gh_calls(tmp_path) if "/jobs" in c]
    assert jobs == ["repos/VirtueMe/stavangerparking/actions/runs/36890842735/attempts/1/jobs"]


def test_the_workflow_records_failed_runs_without_blocking_collection():
    workflow = (REPO / ".github" / "workflows" / "collect.yml").read_text()

    assert "  actions: read\n" in workflow
    step = workflow.split("- name: Record earlier failed runs", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error: true" in step
    assert "run: .github/scripts/record-failed-runs.sh data" in step
    # Before the collection, so the records go into this run's commit
    assert workflow.index("Record earlier failed runs") < workflow.index("- name: Collect\n")


def test_the_workflow_retries_a_failed_push():
    workflow = (REPO / ".github" / "workflows" / "collect.yml").read_text()

    assert "for attempt in 1 2 3; do\n            git push origin data && exit 0" in workflow
