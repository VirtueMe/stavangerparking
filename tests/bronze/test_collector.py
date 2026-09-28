import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from stavanger_parking.bronze import collect as cli
from stavanger_parking.bronze.collector import (
    FAST,
    SLOW,
    CollectError,
    collect,
    decide,
    find_gaps,
    mode_after,
    read_sidecars,
    render_raw_path,
    sidecar_path,
    values_fingerprint,
)
from stavanger_parking.config import Polling, load_sources

REPO_CONFIG = Path(__file__).parent.parent.parent / "config" / "sources.json"
PACKAGE_SHOW = (
    Path(__file__).parent.parent / "fixtures" / "ckan_package_show_stavanger_parkering.json"
)
T0 = datetime(2026, 9, 28, 14, 0, 0, tzinfo=UTC)
POLLING = Polling(5, 20, 5, ("Dato", "Klokkeslett"))


def snapshot(spaces: dict[str, str], time: str = "14:00") -> bytes:
    rows = [
        {"Dato": "28.09.2026", "Klokkeslett": time, "Sted": name, "Antall_ledige_plasser": free}
        for name, free in spaces.items()
    ]
    return json.dumps(rows).encode()


QUIET = {"Jernbanen": "285", "Posten": "Open"}


def sidecars(fingerprints: list[str | None], start=T0, step=5) -> list[dict]:
    return [
        {"ingested_at": (start + timedelta(minutes=i * step)).isoformat(), "values_fingerprint": fp}
        for i, fp in enumerate(fingerprints)
    ]


# --- fingerprint -------------------------------------------------------------------------------


def test_fingerprint_ignores_the_timestamp_fields():
    assert values_fingerprint(snapshot(QUIET, "14:00"), POLLING.change_ignores_fields) == (
        values_fingerprint(snapshot(QUIET, "14:02"), POLLING.change_ignores_fields)
    )


def test_fingerprint_changes_when_a_value_changes():
    changed = dict(QUIET, Jernbanen="284")
    assert values_fingerprint(snapshot(QUIET), ()) != values_fingerprint(snapshot(changed), ())


def test_fingerprint_does_not_depend_on_record_order():
    reordered = dict(reversed(list(QUIET.items())))
    assert values_fingerprint(snapshot(QUIET), ()) == values_fingerprint(snapshot(reordered), ())


@pytest.mark.parametrize("payload", [b"<html>", b'{"not": "a list"}', b"[1, 2]"])
def test_fingerprint_is_none_for_unexpected_payloads(payload):
    assert values_fingerprint(payload, ()) is None


def test_paths_are_rendered_from_the_template():
    raw = render_raw_path("bronze/parking/{yyyy}/{mm}/{dd}/{HHmmss}.json", T0)
    assert raw == "bronze/parking/2026/09/28/140000.json"
    assert sidecar_path(raw) == "bronze/parking/2026/09/28/140000.meta.json"


# --- mode and decision -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fingerprints, mode",
    [
        (["a"] * 4, FAST),  # not enough history yet
        (["a"] * 5, SLOW),
        (["b", "a", "a", "a", "a"] + ["a"], SLOW),  # only the last 5 count
        (["a", "a", "a", "a", "b"], FAST),  # the latest changed
        ([None] * 5, FAST),  # unreadable snapshots never slow down
    ],
)
def test_mode_follows_the_last_snapshots(fingerprints, mode):
    assert mode_after(fingerprints, POLLING) == mode


def test_first_run_fetches():
    assert decide(T0, [], POLLING).fetch


def test_fast_mode_fetches_on_every_run():
    history = sidecars(["a", "b", "c"])
    decision = decide(T0 + timedelta(minutes=15), history, POLLING)
    assert (decision.fetch, decision.mode) == (True, FAST)
    assert decision.reason == "3 of 5 snapshots needed to compare"

    changed = decide(T0 + timedelta(minutes=30), sidecars(["a", "a", "a", "a", "b"]), POLLING)
    assert changed.reason == "values changed within the last 5 snapshots"


def test_slow_mode_skips_until_the_slow_interval_is_nearly_over():
    history = sidecars(["a"] * 5)
    last = datetime.fromisoformat(history[-1]["ingested_at"])

    early = decide(last + timedelta(minutes=17), history, POLLING)
    due = decide(last + timedelta(minutes=17.5), history, POLLING)

    assert (early.fetch, early.mode) == (False, SLOW)
    assert "unchanged for 5 snapshots" in early.reason
    assert (due.fetch, due.mode) == (True, SLOW)


# --- collect -----------------------------------------------------------------------------------


class FakeSource:
    """Serves package_show and a snapshot whose content the test can change."""

    def __init__(self, payload: bytes, status: int = 200):
        self.payload, self.status, self.downloads = payload, status, 0
        self.package_show = json.loads(PACKAGE_SHOW.read_text(encoding="utf-8"))

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/package_show"):
                return httpx.Response(200, json=self.package_show)
            self.downloads += 1
            return httpx.Response(self.status, content=self.payload)

        return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def source():
    return load_sources(REPO_CONFIG)[0]


def run(source, storage, fake, at, run_id="r"):
    return collect(source, storage, fake.client(), at, run_id)


def test_first_collection_stores_raw_file_unchanged_with_sidecar(source, tmp_path):
    fake = FakeSource(snapshot(QUIET))

    outcome = run(source, tmp_path, fake, T0, run_id="123-1")

    assert outcome.raw_file == "bronze/parking/2026/09/28/140000.json"
    assert (tmp_path / outcome.raw_file).read_bytes() == fake.payload
    meta = json.loads((tmp_path / sidecar_path(outcome.raw_file)).read_text())
    assert meta["source_id"] == "stavanger_parking"
    assert meta["run_id"] == "123-1"
    assert meta["ingested_at"] == T0.isoformat()
    assert meta["resource_id"] == "d1bdc6eb-9b49-4f24-89c2-ab9f5ce2acce"
    assert meta["source_url"].endswith("/download/parking.json")
    assert meta["content_hash"].startswith("sha256:")
    assert meta["values_fingerprint"].startswith("sha256:")
    assert (meta["polling_mode"], meta["next_due"]) == (
        FAST,
        (T0 + timedelta(minutes=5)).isoformat(),
    )


def test_adaptive_polling_slows_down_and_speeds_up_again(source, tmp_path):
    fake = FakeSource(snapshot(QUIET))
    at = T0
    for _ in range(5):  # five unchanged snapshots, every 5 minutes
        run(source, tmp_path, fake, at)
        at += timedelta(minutes=5)
    assert read_sidecars(tmp_path, source)[-1]["polling_mode"] == SLOW

    # Slow mode: runs at +5, +10 and +15 minutes skip without fetching
    downloads = fake.downloads
    last = at - timedelta(minutes=5)
    for minutes in (5, 10, 15):
        assert not run(source, tmp_path, fake, last + timedelta(minutes=minutes)).decision.fetch
    assert fake.downloads == downloads

    # The run 20 minutes later fetches; the values changed, so the next mode is fast again
    fake.payload = snapshot(dict(QUIET, Jernbanen="250"), "14:40")
    outcome = run(source, tmp_path, fake, last + timedelta(minutes=20))
    assert outcome.decision.fetch
    assert read_sidecars(tmp_path, source)[-1]["polling_mode"] == FAST


def test_frozen_feed_also_slows_down(source, tmp_path):
    fake = FakeSource(snapshot(QUIET, "19:16"))  # identical content, timestamp included
    for i in range(5):
        run(source, tmp_path, fake, T0 + timedelta(minutes=5 * i))
    assert read_sidecars(tmp_path, source)[-1]["polling_mode"] == SLOW


def test_existing_files_are_never_overwritten(source, tmp_path):
    fake = FakeSource(snapshot(QUIET))
    run(source, tmp_path, fake, T0)

    with pytest.raises(CollectError, match="Refusing to overwrite"):
        run(source, tmp_path, fake, T0)


def test_download_failure_fails_the_run_and_stores_nothing(source, tmp_path):
    fake = FakeSource(b"", status=502)

    with pytest.raises(CollectError, match="Download failed"):
        run(source, tmp_path, fake, T0)
    assert not any(tmp_path.rglob("*.json"))


def test_unexpected_payload_is_still_stored_unchanged(source, tmp_path):
    fake = FakeSource(b"<html>maintenance</html>")

    outcome = run(source, tmp_path, fake, T0)

    assert (tmp_path / outcome.raw_file).read_bytes() == b"<html>maintenance</html>"
    assert read_sidecars(tmp_path, source)[-1]["values_fingerprint"] is None


# --- gaps --------------------------------------------------------------------------------------


def sidecar(at: datetime, next_minutes: int) -> dict:
    return {
        "ingested_at": at.isoformat(),
        "next_due": (at + timedelta(minutes=next_minutes)).isoformat(),
    }


def test_intended_slow_intervals_are_not_gaps():
    history = [sidecar(T0, 20), sidecar(T0 + timedelta(minutes=20), 20)]
    assert list(find_gaps(history, tolerance=timedelta(minutes=10))) == []


def test_missed_runs_are_gaps():
    history = [sidecar(T0, 5), sidecar(T0 + timedelta(minutes=45), 5)]

    (gap,) = find_gaps(history, tolerance=timedelta(minutes=10))

    assert gap.after == T0
    assert gap.expected_by == T0 + timedelta(minutes=5)
    assert gap.next_snapshot == T0 + timedelta(minutes=45)


def test_an_overdue_latest_snapshot_is_an_open_gap():
    history = [sidecar(T0, 5)]
    now = T0 + timedelta(hours=1)

    (gap,) = find_gaps(history, tolerance=timedelta(minutes=10), now=now)

    assert gap.next_snapshot is None


# --- command line ------------------------------------------------------------------------------


def test_cli_run_reports_fetches_and_skips(monkeypatch, tmp_path, capsys):
    fake = FakeSource(snapshot(QUIET))
    monkeypatch.setattr(cli, "make_client", fake.client)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    code = cli.main(
        ["--config", str(REPO_CONFIG), "run", "--storage", str(tmp_path), "--run-id", "1"]
    )

    assert code == 0
    assert "stavanger_parking: fetched -> bronze/parking/" in capsys.readouterr().out
    assert "fast mode" in summary.read_text()


def test_cli_run_fails_when_a_source_fails(monkeypatch, tmp_path, capsys):
    fake = FakeSource(b"", status=503)
    monkeypatch.setattr(cli, "make_client", fake.client)

    code = cli.main(
        ["--config", str(REPO_CONFIG), "run", "--storage", str(tmp_path), "--run-id", "1"]
    )

    assert code == 1
    assert "FAILED" in capsys.readouterr().out


def test_cli_gaps_lists_gaps(tmp_path, capsys):
    code = cli.main(["--config", str(REPO_CONFIG), "gaps", "--storage", str(tmp_path)])

    assert code == 0
    assert "stavanger_parking: 0 gap(s)" in capsys.readouterr().out
