import httpx
import pytest

from stavanger_parking.bronze import collect_runs


@pytest.fixture(autouse=True)
def no_collector_history(monkeypatch):
    """No test reads GitHub: the collector's run history is empty unless a test says otherwise."""

    def client():
        return httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"workflow_runs": []}))
        )

    monkeypatch.setattr(collect_runs, "make_client", client)
