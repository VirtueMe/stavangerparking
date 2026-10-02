import json
from datetime import UTC, datetime

import httpx
import polars as pl
import pytest

from stavanger_parking.bronze import collect_runs
from stavanger_parking.bronze.collect_runs import (
    DEFAULT_COLLECTOR,
    RUNS_SCHEMA,
    GitHubActions,
    RunsError,
    load_collector,
)
from stavanger_parking.tables import COLLECT_RUNS_TABLE, table_path

NOW = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
COLLECTOR = GitHubActions("VirtueMe/stavangerparking", "collect.yml")
RUNS = "/repos/VirtueMe/stavangerparking/actions/workflows/collect.yml/runs"


def listed_run(run_id: int, started: str, attempt: int = 1) -> dict:
    return {
        "id": run_id,
        "run_attempt": attempt,
        "run_started_at": started,
        "html_url": f"https://github.com/VirtueMe/stavangerparking/actions/runs/{run_id}",
    }


def jobs(*steps: tuple[str, str]) -> dict:
    return {"jobs": [{"steps": [{"name": n, "conclusion": c} for n, c in steps]}]}


class FakeGitHub:
    """The two API calls the reader makes, with the calls recorded."""

    def __init__(self, runs: list[dict], steps: dict[int, str] | None = None, status: int = 200):
        self.runs, self.steps, self.status, self.calls = runs, steps or {}, status, []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "API rate limit exceeded"})
        if request.url.path == RUNS:
            page = int(request.url.params["page"])
            size = int(request.url.params["per_page"])
            return httpx.Response(
                200, json={"workflow_runs": self.runs[(page - 1) * size :][:size]}
            )
        run_id = int(request.url.path.split("/runs/")[1].split("/")[0])
        step = self.steps.get(run_id)
        return httpx.Response(
            200, json=jobs(("Collect", "success"), *[(step, "failure")] * bool(step))
        )

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))


def table(tables_root: str) -> pl.DataFrame:
    return pl.read_delta(table_path(tables_root, COLLECT_RUNS_TABLE))


def test_the_repository_collector_is_the_collect_workflow():
    assert load_collector(DEFAULT_COLLECTOR) == COLLECTOR


def test_failed_runs_are_read_with_their_failed_step(tmp_path):
    fake = FakeGitHub(
        [
            listed_run(36890842735, "2026-10-01T16:17:14Z"),
            listed_run(36642529839, "2026-09-29T22:57:12Z"),
        ],
        {36890842735: "Commit and push snapshots", 36642529839: "Fail the run if a source failed"},
    )

    count = collect_runs.read(fake.client(), COLLECTOR, str(tmp_path), NOW)

    assert count == 2
    rows = table(str(tmp_path))
    assert rows.schema == pl.Schema(RUNS_SCHEMA)
    assert rows.select("run_id", "failed_step").rows() == [
        (36642529839, "Fail the run if a source failed"),
        (36890842735, "Commit and push snapshots"),
    ]
    assert rows["read_at"].unique().to_list() == [NOW]


def test_a_known_runs_step_is_not_asked_again(tmp_path):
    fake = FakeGitHub([listed_run(1, "2026-10-01T16:17:14Z")], {1: "Commit and push snapshots"})
    collect_runs.read(fake.client(), COLLECTOR, str(tmp_path), NOW)
    fake.runs.append(listed_run(2, "2026-10-01T18:00:00Z"))
    fake.calls.clear()

    collect_runs.read(fake.client(), COLLECTOR, str(tmp_path), NOW)

    # One call for the list, one for the new run's step; run 1's step comes from the table
    assert [c.path for c in fake.calls] == [
        RUNS,
        f"{RUNS.split('/workflows')[0]}/runs/2/attempts/1/jobs",
    ]
    assert table(str(tmp_path))["failed_step"].to_list() == ["Commit and push snapshots", None]


def test_the_table_is_the_current_list(tmp_path):
    """A run the history no longer lists (GitHub's retention) drops out."""
    fake = FakeGitHub(
        [listed_run(1, "2025-09-01T00:00:00Z"), listed_run(2, "2026-10-01T16:17:14Z")]
    )
    collect_runs.read(fake.client(), COLLECTOR, str(tmp_path), NOW)
    fake.runs = fake.runs[1:]

    collect_runs.read(fake.client(), COLLECTOR, str(tmp_path), NOW)

    assert table(str(tmp_path))["run_id"].to_list() == [2]


def test_every_page_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(collect_runs, "PAGE_SIZE", 2)
    fake = FakeGitHub([listed_run(i, f"2026-10-01T0{i}:00:00Z") for i in range(1, 6)])

    assert collect_runs.read(fake.client(), COLLECTOR, str(tmp_path), NOW) == 5


def test_a_run_without_a_failed_step_has_none(tmp_path):
    fake = FakeGitHub([listed_run(1, "2026-10-01T16:17:14Z")])

    collect_runs.read(fake.client(), COLLECTOR, str(tmp_path), NOW)

    assert table(str(tmp_path))["failed_step"].to_list() == [None]


def test_an_unreadable_history_keeps_the_previous_table(tmp_path, monkeypatch):
    fake = FakeGitHub([listed_run(1, "2026-10-01T16:17:14Z")], {1: "Commit and push snapshots"})
    collect_runs.read(fake.client(), COLLECTOR, str(tmp_path), NOW)
    fake.status = 403
    monkeypatch.setattr(collect_runs, "make_client", fake.client)

    lines = collect_runs.run(DEFAULT_COLLECTOR, str(tmp_path), NOW)

    assert lines[0].startswith("collect runs: not read, the previous table is kept (")
    assert "403" in lines[0]
    assert table(str(tmp_path))["run_id"].to_list() == [1]


def test_a_token_is_sent_when_set(monkeypatch):
    seen = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: (
                seen.append(r.headers.get("Authorization"))
                or httpx.Response(200, json={"workflow_runs": []})
            )
        )
    )
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")

    collect_runs.failed_runs(client, COLLECTOR, {})

    assert seen == ["Bearer t0ken"]


@pytest.mark.parametrize("content", ["{}", '{"github_actions": {"repository": "a/b"}}', "not json"])
def test_an_invalid_collector_config_is_named(tmp_path, content):
    path = tmp_path / "collector.json"
    path.write_text(content)

    with pytest.raises(RunsError, match="no valid github_actions collector"):
        load_collector(path)


def test_cli_reads_and_reports(tmp_path, capsys):
    collector = tmp_path / "collector.json"
    collector.write_text(json.dumps({"github_actions": {"repository": "a/b", "workflow": "c.yml"}}))

    assert (
        collect_runs.main(["--tables-root", str(tmp_path / "t"), "--collector", str(collector)])
        == 0
    )
    assert capsys.readouterr().out.strip() == "collect runs: 0 failed run(s) of a/b c.yml"
