"""The Fabric deployment (#98): the items in platforms/fabric/workspace, and the scripts.

There is no Fabric capacity to run them on yet (#16), so these tests check what can be checked
without one: every item is valid against Microsoft's published `.platform` schema and refers only to
items that exist, the notebooks call nothing but the package's entry points, collection is never
scheduled, and the scripts make the right `fab` calls (against a fake), changing nothing in a
dry run.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

REPO = Path(__file__).parents[1]
FABRIC = REPO / "platforms" / "fabric"
WORKSPACE = FABRIC / "workspace"
SCHEMA = Path(__file__).parent / "fixtures" / "powerbi_schemas"
ITEMS = sorted(p.parent for p in WORKSPACE.glob("*/.platform"))


def platform(item: Path) -> dict:
    return json.loads((item / ".platform").read_text(encoding="utf-8"))


def logical_ids() -> dict[str, Path]:
    return {platform(item)["config"]["logicalId"]: item for item in ITEMS}


def code_cells(notebook: Path) -> list[str]:
    """The code of each cell of a notebook in Fabric's Git format (`# CELL` to `# METADATA`)."""
    text = (notebook / "notebook-content.py").read_text(encoding="utf-8")
    return [
        block.split("# METADATA ****")[0].strip()
        for block in re.split(r"^# CELL \*+$", text, flags=re.M)[1:]
    ]


# --- the items ---


def test_the_workspace_has_the_lakehouse_the_notebooks_and_the_pipeline():
    assert sorted(item.name for item in ITEMS) == [
        "Collect.Notebook",
        "RunPipeline.Notebook",
        "StavangerParking.Lakehouse",
        "StavangerParkingPipeline.DataPipeline",
    ]


@pytest.mark.parametrize("item", ITEMS, ids=lambda p: p.name)
def test_every_item_is_valid_against_the_published_platform_schema(item):
    schema = json.loads((SCHEMA / "gitIntegration_platformProperties_2.0.0.json").read_text())
    document = platform(item)

    assert [e.message for e in Draft7Validator(schema).iter_errors(document)] == []
    name, kind = item.name.rsplit(".", 1)
    assert (document["metadata"]["displayName"], document["metadata"]["type"]) == (name, kind)


def test_logical_ids_are_unique():
    assert len(logical_ids()) == len(ITEMS)


@pytest.mark.parametrize("name", ["RunPipeline", "Collect"])
def test_the_notebooks_are_pure_python_on_the_lakehouse(name):
    text = (WORKSPACE / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")

    assert text.startswith("# Fabric notebook source\n")
    assert '"name": "jupyter"' in text and '"jupyter_kernel_name": "python3.11"' in text
    assert '"default_lakehouse_name": "StavangerParking"' in text
    assert "synapse_pyspark" not in text


@pytest.mark.parametrize("name", ["RunPipeline", "Collect"])
def test_the_notebooks_install_the_deployed_wheel_and_use_only_the_package(name):
    install, *code = code_cells(WORKSPACE / f"{name}.Notebook")

    # The locked dependencies first, checked by hash, then the wheel, which adds nothing to them
    assert "%pip install -r /lakehouse/default/Files/wheels/requirements.txt" in install
    assert "%pip install --no-deps /lakehouse/default/Files/wheels/{{wheel}}" in install
    imports = set(re.findall(r"^(?:from|import) (\w+)", "\n".join(code), re.M))
    assert imports <= {"stavanger_parking", "datetime"}
    assert all("raise RuntimeError" in cell for cell in code[-1:])


def test_the_run_notebook_calls_the_pipeline_on_the_lakehouse():
    _, code = code_cells(WORKSPACE / "RunPipeline.Notebook")

    assert 'run_pipeline("/lakehouse/default/Files", "/lakehouse/default/Tables")' in code
    assert "if result.exit_code:" in code


def test_the_pipeline_runs_the_notebook_and_alerts_when_it_fails():
    content = WORKSPACE / "StavangerParkingPipeline.DataPipeline" / "pipeline-content.json"
    activities = {a["name"]: a for a in json.loads(content.read_text())["properties"]["activities"]}

    run = activities["Run the pipeline"]
    assert run["type"] == "TridentNotebook"
    assert logical_ids()[run["typeProperties"]["notebookId"]].name == "RunPipeline.Notebook"
    # Pipeline runs turn %pip install off unless the activity asks for it, and the notebook needs it
    assert run["typeProperties"]["parameters"]["_inlineInstallationEnabled"] == {
        "value": "True",
        "type": "bool",
    }
    alert = activities["E-mail on failure"]
    assert alert["type"] == "Office365Outlook"
    assert alert["dependsOn"] == [
        {"activity": "Run the pipeline", "dependencyConditions": ["Failed"]}
    ]
    assert alert["typeProperties"]["inputs"]["body"]["To"] == "{{alert_email}}"


def test_collection_is_never_scheduled():
    """Fabric collects only after the handover (ADR 011): nothing runs Collect, nothing is timed."""
    collect = platform(WORKSPACE / "Collect.Notebook")["config"]["logicalId"]

    for path in WORKSPACE.rglob("*"):
        if path.is_file() and path.parent.name != "Collect.Notebook":
            assert collect not in path.read_text(encoding="utf-8"), path
    assert not list(WORKSPACE.rglob("*schedule*"))


def test_the_parameters_point_at_the_lakehouse_that_is_deployed():
    text = (WORKSPACE / "parameter.yml").read_text(encoding="utf-8")

    lakehouses = set(re.findall(r"\$items\.Lakehouse\.(\w+)\.\$id", text))
    assert lakehouses == {"StavangerParking"}
    assert set(re.findall(r"^\s+(dev|prod):", text, re.M)) == {"dev", "prod"}


# --- the scripts, with fake CLIs ---

FAKE = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALLS"
case "$(basename "$0") $1 $2" in
  "fab auth status") echo "Account: owner@example.com" ;;
  "fab exists"*) echo "* true" ;;
  "fab get"*) echo "$SQL_ENDPOINT" ;;
  "gh release view") echo v0.20.0 ;;
  "gh release download") touch "${@: -1}/stavanger_parking-0.20.0-py3-none-any.whl" ;;
  "uv build"*) touch "$4/stavanger_parking-0.21.0.dev0-py3-none-any.whl" ;;
  "uv export"*) for a in "$@"; do [ "$prev" = -o ] && echo "polars==1.44.2" > "$a"; prev=$a; done ;;
  "git rev-parse"*) echo "$REPO_ROOT" ;;
esac
# git -C <root> archive …: the raw files for backfill.sh, a tag's lock file for deploy.sh
if [ "$(basename "$0")" = git ] && [[ " $* " == *" archive "* ]]; then
  if [[ " $* " == *" bronze "* ]]; then
    tar -C "$RAW" -c bronze
  else
    tar -C "$LOCK" -c pyproject.toml uv.lock
  fi
fi
# uv runs the report's helper with this interpreter
if [ "$(basename "$0")" = uv ] && [ "$1" = run ]; then
  while [ "$1" != python ]; do shift; done
  shift
  exec PYTHON "$@"
fi
""".replace("PYTHON", sys.executable)


@pytest.fixture
def repo(tmp_path):
    """platforms/fabric, the report's project and helper, in a copy of the repository layout."""
    shutil.copytree(FABRIC, tmp_path / "platforms" / "fabric")
    shutil.copytree(REPO / "powerbi", tmp_path / "powerbi")
    (tmp_path / "tools").mkdir()
    for tool in ["powerbi.py", "requirements.sh"]:
        shutil.copy2(REPO / "tools" / tool, tmp_path / "tools" / tool)
    for cli in ["fab", "uv", "gh", "git"]:
        path = tmp_path / "bin" / cli
        path.parent.mkdir(exist_ok=True)
        path.write_text(FAKE)
        path.chmod(0o755)
    lock = tmp_path / "lock"
    lock.mkdir()
    (lock / "pyproject.toml").write_text("[project]\n")
    (lock / "uv.lock").write_text("version = 1\n")
    raw = tmp_path / "data"
    for name in ["bronze/parking/2026/09/30/1.json", "bronze/parking/2026/09/30/1.meta.json"]:
        (raw / name).parent.mkdir(parents=True, exist_ok=True)
        (raw / name).write_text("{}")
    return tmp_path


def run(repo: Path, script: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("FABRIC_", "POWERBI_"))}
    return subprocess.run(
        [repo / "platforms" / "fabric" / script, *args],
        env={
            **base,
            "PATH": f"{repo / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "CALLS": str(repo / "calls.log"),
            "RAW": str(repo / "data"),
            "LOCK": str(repo / "lock"),
            "REPO_ROOT": str(repo),
            "SQL_ENDPOINT": "abc.datawarehouse.fabric.microsoft.com",
            "FABRIC_WORKSPACE": "Parking",
            "FABRIC_DEV_WORKSPACE": "Parking dev",
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def calls(repo: Path) -> str:
    return (repo / "calls.log").read_text()


def fab_calls(repo: Path) -> list[str]:
    log = repo / "calls.log"
    return [c for c in log.read_text().splitlines() if c.startswith("fab ")] if log.exists() else []


WHEEL = "stavanger_parking-0.20.0-py3-none-any.whl"
LAKEHOUSE = "Parking.Workspace/StavangerParking.Lakehouse"


def test_a_prod_deploy_installs_the_latest_release_with_fabric_cicd(repo):
    result = run(repo, "deploy.sh", "prod")

    assert result.returncode == 0, result.stderr
    out = repo / "dist" / "fabric-prod"
    config = (out / "workspace" / "config.yml").read_text()
    assert 'prod: "Parking"' in config and 'parameter: "parameter.yml"' in config
    notebook = (out / "workspace" / "RunPipeline.Notebook" / "notebook-content.py").read_text()
    assert "Files/wheels/stavanger_parking-0.20.0-py3-none-any.whl" in notebook
    assert (
        "{{"
        not in (
            out / "workspace" / "StavangerParkingPipeline.DataPipeline" / "pipeline-content.json"
        ).read_text()
    )
    assert fab_calls(repo)[1:] == [
        f"fab deploy --config {out}/workspace/config.yml --target_env prod -f",
        f"fab cp {out}/wheel/requirements.txt {LAKEHOUSE}/Files/wheels/requirements.txt -f",
        f"fab cp {out}/wheel/{WHEEL} {LAKEHOUSE}/Files/wheels/{WHEEL} -f",
    ]
    # The dependencies come from the release's own uv.lock, not this checkout's
    assert f"git -C {repo} archive v0.20.0 pyproject.toml uv.lock" in calls(repo)
    export = next(c for c in calls(repo).splitlines() if c.startswith("uv export"))
    project = export.split("--project ")[1].split()[0]
    assert "--frozen" in export and "--no-dev" in export
    assert project not in (str(repo), str(REPO)), "prod exported the checkout's lock, not the tag's"
    assert (out / "wheel" / "requirements.txt").read_text() == "polars==1.44.2\n"
    # The repository keeps its placeholders
    assert "{{wheel}}" in (WORKSPACE / "RunPipeline.Notebook" / "notebook-content.py").read_text()


def test_a_dev_deploy_builds_the_wheel_and_uses_the_dev_workspace(repo):
    result = run(repo, "deploy.sh", "dev", FABRIC_ALERT_EMAIL="team@example.com")

    assert result.returncode == 0, result.stderr
    pipeline = (
        repo
        / "dist/fabric-dev/workspace/StavangerParkingPipeline.DataPipeline/pipeline-content.json"
    )
    assert '"To": "team@example.com"' in pipeline.read_text()
    export = next(c for c in calls(repo).splitlines() if c.startswith("uv export"))
    assert export.endswith(f"--project {repo} -o {repo}/dist/fabric-dev/wheel/requirements.txt")
    assert "--no-hashes" not in export  # two pip runs: the requirements are checked by hash
    assert any(c.startswith("fab deploy") and "--target_env dev" in c for c in fab_calls(repo))
    assert any("Parking dev.Workspace/StavangerParking.Lakehouse" in c for c in fab_calls(repo))


def test_a_dry_run_deploy_changes_nothing_in_the_workspace(repo):
    result = run(repo, "deploy.sh", "--dry-run", "prod")

    assert result.returncode == 0, result.stderr
    assert "would deploy 4 item(s) to Parking" in result.stdout
    assert [c.split()[1] for c in fab_calls(repo)] == ["auth", "exists"]


def test_a_deploy_needs_its_workspace(repo):
    result = run(repo, "deploy.sh", "prod", FABRIC_WORKSPACE="")

    assert result.returncode != 0
    assert "set FABRIC_WORKSPACE" in result.stderr


def test_a_backfill_copies_every_raw_file_and_runs_the_pipeline(repo):
    result = run(repo, "backfill.sh", "prod")

    assert result.returncode == 0, result.stderr
    lakehouse = "Parking.Workspace/StavangerParking.Lakehouse/Files"
    assert sorted(c.split()[-2] for c in fab_calls(repo) if c.startswith("fab cp")) == [
        f"{lakehouse}/bronze/parking/2026/09/30/1.json",
        f"{lakehouse}/bronze/parking/2026/09/30/1.meta.json",
    ]
    assert (
        fab_calls(repo)[-1] == "fab job run Parking.Workspace/StavangerParkingPipeline.DataPipeline"
    )


def test_a_dry_run_backfill_copies_and_runs_nothing(repo):
    result = run(repo, "backfill.sh", "--dry-run", "prod")

    assert result.returncode == 0, result.stderr
    assert (
        "would copy 2 raw file(s) to Parking.Workspace/StavangerParking.Lakehouse/Files"
        in result.stdout
    )
    assert fab_calls(repo) == []


def test_the_report_reads_the_lakehouse_through_its_sql_endpoint(repo):
    result = run(repo, "report.sh", "--dry-run", "prod")

    assert result.returncode == 0, result.stderr
    expressions = (
        repo / "dist/powerbi-fabric-prod/StavangerParking.SemanticModel/definition/expressions.tmdl"
    )
    text = expressions.read_text()
    assert 'expression FabricSqlEndpoint = "abc.datawarehouse.fabric.microsoft.com"' in text
    assert 'expression FabricLakehouse = "StavangerParking"' in text
    assert "would publish to Parking" in result.stdout
    assert [c.split()[1] for c in fab_calls(repo)] == ["get"]


def test_a_report_needs_the_lakehouse_deployed(repo):
    result = run(repo, "report.sh", "prod", SQL_ENDPOINT="")

    assert result.returncode == 2
    assert "no SQL analytics endpoint yet" in result.stderr


def test_a_placeholder_left_unfilled_stops_the_deploy(repo):
    notebook = repo / "platforms/fabric/workspace/RunPipeline.Notebook/notebook-content.py"
    notebook.write_text(notebook.read_text() + "\n# {{unknown}}\n")

    result = run(repo, "deploy.sh", "prod")

    assert result.returncode == 2
    assert "unfilled placeholders" in result.stderr
    assert not any(c.startswith("fab deploy") for c in fab_calls(repo))


@pytest.mark.parametrize("name", ["RunPipeline", "Collect"])
def test_the_notebook_kernel_is_the_python_the_project_tests_on(name):
    """Pinned, not Fabric's default (3.12): Fabric runs what CI tests and Databricks runs."""
    version = (REPO / ".python-version").read_text().strip()
    text = (WORKSPACE / f"{name}.Notebook" / "notebook-content.py").read_text(encoding="utf-8")

    assert f'"jupyter_kernel_name": "python{version}"' in text
