"""The Collect workflow keeps a fetched snapshot when a push fails for a moment (#119)."""

from pathlib import Path

REPO = Path(__file__).parents[1]


def test_the_workflow_retries_a_failed_push():
    workflow = (REPO / ".github" / "workflows" / "collect.yml").read_text()

    assert "for attempt in 1 2 3; do\n            git push origin data && exit 0" in workflow
