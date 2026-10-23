"""The Databricks backfill script, against a fake CLI whose raw volume is a folder (#126)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="backfill.sh needs jq")

RAW_FILES = [
    "bronze/parking/2026/09/30/1.json",
    "bronze/parking/2026/09/30/1.meta.json",
    "bronze/parkeringsregisteret/2026/10/01/2.json",
]
VOLUME = "/Volumes/workspace/stavanger_parking/raw"

FAKE = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALLS"
RAW_PREFIX=dbfs:/Volumes/workspace/stavanger_parking/raw/
case "$(basename "$0") $1 $2" in
  "databricks bundle validate")
    echo '{"resources": {"schemas": {"stavanger_parking":
      {"catalog_name": "workspace", "name": "stavanger_parking"}}}}' ;;
  "databricks fs ls") ls "$VOLUME_DIR/${3#$RAW_PREFIX}" 2>/dev/null || exit 1 ;;
  "databricks fs mkdir") mkdir -p "$VOLUME_DIR/${3#$RAW_PREFIX}" ;;
  "databricks fs cp")
    # Like the real CLI, a copy into a folder that is not there fails
    dest="$VOLUME_DIR/${5#$RAW_PREFIX}"
    if [ ! -d "$(dirname "$dest")" ]; then
      echo "Error: no such directory: /${5#dbfs:/}" >&2
      exit 1
    fi
    cp "$4" "$dest" ;;
esac
# git archive: the raw files of the data branch
if [ "$(basename "$0")" = git ] && [[ " $* " == *" archive "* ]]; then
  tar -C "$RAW" -c bronze
fi
if [ "$(basename "$0")" = git ] && [ "$1" = rev-parse ]; then
  echo "$REPO_ROOT"
fi
"""


@pytest.fixture
def repo(tmp_path):
    """platforms/databricks/backfill.sh with a fake databricks and git, a folder as the volume."""
    script = tmp_path / "platforms" / "databricks" / "backfill.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(REPO / "platforms" / "databricks" / "backfill.sh", script)
    for cli in ["databricks", "git"]:
        path = tmp_path / "bin" / cli
        path.parent.mkdir(exist_ok=True)
        path.write_text(FAKE)
        path.chmod(0o755)
    for name in RAW_FILES:
        (tmp_path / "data" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "data" / name).write_text("{}")
    (tmp_path / "volume").mkdir()
    return tmp_path


def run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [repo / "platforms" / "databricks" / "backfill.sh", *args],
        env={
            **os.environ,
            "PATH": f"{repo / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "CALLS": str(repo / "calls.log"),
            "RAW": str(repo / "data"),
            "VOLUME_DIR": str(repo / "volume"),
            "REPO_ROOT": str(repo),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def volume(repo: Path) -> list[str]:
    return sorted(str(p.relative_to(repo / "volume")) for p in (repo / "volume").rglob("*.json"))


def cp_calls(repo: Path) -> list[str]:
    log = repo / "calls.log"
    lines = log.read_text().splitlines() if log.exists() else []
    return [c for c in lines if c.startswith("databricks fs cp")]


def test_a_first_backfill_copies_every_raw_file_and_runs_the_pipeline(repo):
    result = run(repo, "prod")

    assert result.returncode == 0, result.stderr
    assert "copying 3 of 3 raw file(s)" in result.stdout
    assert volume(repo) == sorted(RAW_FILES)
    assert (repo / "calls.log").read_text().splitlines()[
        -1
    ] == "databricks bundle run --target prod pipeline"


def test_a_backfill_copies_only_the_files_the_volume_lacks(repo):
    for name in RAW_FILES[:2]:
        (repo / "volume" / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / "volume" / name).write_text("{}")

    result = run(repo, "prod")

    assert result.returncode == 0, result.stderr
    assert "copying 1 of 3 raw file(s)" in result.stdout
    assert [c.split()[-1] for c in cp_calls(repo)] == [f"dbfs:{VOLUME}/{RAW_FILES[2]}"]
    assert volume(repo) == sorted(RAW_FILES)


def test_a_backfill_makes_the_folder_of_a_day_the_volume_lacks(repo):
    """The first backfill of a new day: the volume has the older days' folders, not this one's."""
    for name in RAW_FILES[:2]:
        (repo / "volume" / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / "volume" / name).write_text("{}")
    assert not (repo / "volume" / RAW_FILES[2]).parent.exists()

    result = run(repo, "prod")

    assert result.returncode == 0, result.stderr
    assert volume(repo) == sorted(RAW_FILES)
    assert (repo / "calls.log").read_text().splitlines()[
        -1
    ] == "databricks bundle run --target prod pipeline"


def test_a_second_backfill_copies_nothing_but_still_runs_the_pipeline(repo):
    run(repo, "prod")
    (repo / "calls.log").unlink()

    result = run(repo, "prod")

    assert result.returncode == 0, result.stderr
    assert "copying 0 of 3 raw file(s)" in result.stdout
    assert cp_calls(repo) == []
    assert (repo / "calls.log").read_text().splitlines()[
        -1
    ] == "databricks bundle run --target prod pipeline"


def test_a_dry_run_backfill_copies_and_runs_nothing(repo):
    result = run(repo, "--dry-run", "prod")

    assert result.returncode == 0, result.stderr
    assert f"would copy 3 of 3 raw file(s) to {VOLUME}" in result.stdout
    assert cp_calls(repo) == []
    assert volume(repo) == []
    assert "bundle run" not in (repo / "calls.log").read_text()
