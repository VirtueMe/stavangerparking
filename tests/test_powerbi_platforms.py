"""The Power BI project per platform (#72): tools/powerbi.py and platforms/<platform>/report.sh.

Each platform replaces only the model's data source, DeltaTable(name) in expressions.tmdl, so the
tables, measures and report stay one definition. Publishing runs against a fake `fab`.
"""

import importlib.util
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
SCHEMAS = Path(__file__).parent / "fixtures" / "powerbi_schemas"
LOCAL = REPO / "powerbi" / "StavangerParking.SemanticModel" / "definition" / "expressions.tmdl"
SOURCES = sorted(REPO.glob("platforms/*/report/expressions.tmdl"))
FUNCTION = "expression DeltaTable =\n\t\t(name as text) as table =>\n"
DATABRICKS = {"host": "dbc-1.cloud.databricks.com", "http_path": "/sql/1.0/warehouses/abc"}
DATABRICKS |= {"catalog": "workspace", "schema": "stavanger_parking"}

spec = importlib.util.spec_from_file_location("powerbi", REPO / "tools" / "powerbi.py")
powerbi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(powerbi)


def test_there_is_a_source_for_databricks_and_fabric():
    assert {p.parts[-3] for p in SOURCES} == {"databricks", "fabric"}


@pytest.mark.parametrize("path", [LOCAL, *SOURCES], ids=lambda p: p.parts[-3])
def test_every_source_defines_the_same_function(path):
    text = path.read_text(encoding="utf-8")

    assert text.count("expression DeltaTable =") == 1
    assert FUNCTION in text
    assert not any(line.startswith(" ") for line in text.splitlines())


def test_a_build_replaces_only_the_data_source(tmp_path):
    model = powerbi.build("databricks", tmp_path / "out", DATABRICKS)

    built = {p.relative_to(tmp_path / "out") for p in (tmp_path / "out").rglob("*") if p.is_file()}
    tracked = {
        p.relative_to(REPO / "powerbi") for p in (REPO / "powerbi").rglob("*") if p.is_file()
    }
    tracked -= {Path(".gitignore")}
    assert built == tracked
    changed = {
        path
        for path in built
        if (tmp_path / "out" / path).read_bytes() != (REPO / "powerbi" / path).read_bytes()
    }
    assert changed == {Path("StavangerParking.SemanticModel/definition/expressions.tmdl")}
    expressions = (model / "definition" / "expressions.tmdl").read_text(encoding="utf-8")
    assert "{{" not in expressions
    assert 'expression DatabricksHost = "dbc-1.cloud.databricks.com"' in expressions


def test_a_build_needs_every_value_and_no_other():
    with pytest.raises(powerbi.PowerBIError, match="no value for schema"):
        powerbi.render("{{host}} {{schema}}", {"host": "h"})
    with pytest.raises(powerbi.PowerBIError, match="unknown value extra"):
        powerbi.render("{{host}}", {"host": "h", "extra": "x"})


@pytest.mark.parametrize("value", ["", 'a"b', "a\nb"])
def test_a_value_must_be_safe_inside_an_m_text(value):
    with pytest.raises(powerbi.PowerBIError, match="non-empty single line"):
        powerbi.render("{{host}}", {"host": value})


def test_a_platform_without_a_source_fails(tmp_path):
    with pytest.raises(powerbi.PowerBIError, match="platform 'gamma' has no report source"):
        powerbi.build("gamma", tmp_path / "out", {})


def test_a_bound_report_points_at_the_published_model_by_id(tmp_path):
    report = tmp_path / "StavangerParking.Report"
    shutil.copytree(REPO / "powerbi" / "StavangerParking.Report", report)

    powerbi.bind_report(report, "1234")

    definition = json.loads((report / "definition.pbir").read_text(encoding="utf-8"))
    schema = json.loads((SCHEMAS / "report_definitionProperties_2.0.0.json").read_text())
    assert [e.message for e in Draft7Validator(schema).iter_errors(definition)] == []
    assert definition["datasetReference"] == {
        "byConnection": {"connectionString": "semanticmodelid=1234"}
    }


# --- publishing, with a fake fab ---

FAKE_FAB = """#!/usr/bin/env bash
echo "fab $*" >> "$CALLS"
case "$1" in
  get) echo 1234 ;;
  import) cp "$4/definition.pbir" "$CALLS.pbir" 2>/dev/null || true ;;
esac
"""


def write_script(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def fake_fab(tmp_path, monkeypatch):
    write_script(tmp_path / "bin" / "fab", FAKE_FAB)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CALLS", str(tmp_path / "calls.log"))
    return tmp_path / "calls.log"


def test_publishing_imports_the_model_then_the_report_bound_to_it(tmp_path, fake_fab):
    out = tmp_path / "out"
    powerbi.build("databricks", out, DATABRICKS)

    model_id = powerbi.publish(out, "Case.Workspace")

    assert model_id == "1234"
    model, report = (
        "Case.Workspace/StavangerParking.SemanticModel",
        "Case.Workspace/StavangerParking.Report",
    )
    calls = fake_fab.read_text().splitlines()
    assert calls[:2] == [
        f"fab import {model} -i {out}/StavangerParking.SemanticModel -f",
        f"fab get {model} -q id",
    ]
    assert re.fullmatch(rf"fab import {report} -i \S+/StavangerParking.Report -f", calls[2])
    imported = json.loads(Path(f"{fake_fab}.pbir").read_text())
    assert (
        imported["datasetReference"]["byConnection"]["connectionString"] == "semanticmodelid=1234"
    )
    # The generated project still opens in Desktop with the model next to it
    kept = json.loads((out / "StavangerParking.Report" / "definition.pbir").read_text())
    assert "byPath" in kept["datasetReference"]
    assert sorted(p.name for p in out.iterdir()) == [
        "StavangerParking.Report",
        "StavangerParking.SemanticModel",
        "StavangerParking.pbip",
    ]


def test_a_failed_import_stops_publishing(tmp_path, monkeypatch):
    write_script(tmp_path / "bin" / "fab", "#!/usr/bin/env bash\necho denied; exit 1\n")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    out = tmp_path / "out"
    powerbi.build("databricks", out, DATABRICKS)

    with pytest.raises(powerbi.PowerBIError, match="denied"):
        powerbi.publish(out, "Case.Workspace")


# --- platforms/databricks/report.sh, with fake CLIs ---

FAKE_DATABRICKS = """#!/usr/bin/env bash
echo "databricks $*" >> "$CALLS"
case "$1 $2" in
  "auth describe") echo '{"details": {"host": "https://dbc-1.cloud.databricks.com"}}' ;;
  "warehouses list") echo "$WAREHOUSES" ;;
  "bundle validate") echo "$BUNDLE" ;;
esac
"""

# uv runs the helper with this interpreter; only the call is recorded
FAKE_UV = f"""#!/usr/bin/env bash
echo "uv $*" >> "$CALLS"
while [ "$1" != python ]; do shift; done
shift
exec {sys.executable} "$@"
"""

SCHEMA = {"catalog_name": "workspace", "name": "stavanger_parking"}
BUNDLE = json.dumps({"resources": {"schemas": {"stavanger_parking": SCHEMA}}})
ONE_WAREHOUSE = json.dumps(
    [{"name": "Starter", "odbc_params": {"path": "/sql/1.0/warehouses/abc"}}]
)


@pytest.fixture
def repo(tmp_path):
    """The script, the helper, the project and the sources, in a copy of the repository layout."""
    shutil.copytree(REPO / "powerbi", tmp_path / "powerbi")
    (tmp_path / "tools").mkdir()
    shutil.copy2(REPO / "tools" / "powerbi.py", tmp_path / "tools" / "powerbi.py")
    shutil.copytree(
        REPO / "platforms" / "databricks" / "report",
        tmp_path / "platforms" / "databricks" / "report",
    )
    shutil.copy2(
        REPO / "platforms" / "databricks" / "report.sh", tmp_path / "platforms" / "databricks"
    )
    for name, content in [("databricks", FAKE_DATABRICKS), ("uv", FAKE_UV), ("fab", FAKE_FAB)]:
        write_script(tmp_path / "bin" / name, content)
    return tmp_path


def run_report(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    base = {
        k: v
        for k, v in os.environ.items()
        if k not in ("POWERBI_WORKSPACE", "DATABRICKS_WAREHOUSE")
    }
    return subprocess.run(
        [repo / "platforms" / "databricks" / "report.sh", *args],
        env={
            **base,
            "PATH": f"{repo / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "CALLS": str(repo / "calls.log"),
            "WAREHOUSES": ONE_WAREHOUSE,
            "BUNDLE": BUNDLE,
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def fab_calls(repo: Path) -> list[str]:
    return [
        line for line in (repo / "calls.log").read_text().splitlines() if line.startswith("fab")
    ]


def test_a_dry_run_builds_and_publishes_nothing(repo):
    result = run_report(repo, "--dry-run", "prod")

    assert result.returncode == 0, result.stderr
    assert (
        "would publish to Stavanger Parking Case, reading workspace.stavanger_parking"
        in result.stdout
    )
    assert fab_calls(repo) == []
    expressions = (
        repo
        / "dist/powerbi-databricks-prod/StavangerParking.SemanticModel/definition/expressions.tmdl"
    )
    text = expressions.read_text()
    assert '"dbc-1.cloud.databricks.com"' in text
    assert '"/sql/1.0/warehouses/abc"' in text


@pytest.mark.parametrize(
    ("target", "env", "workspace"),
    [
        ("prod", {}, "Stavanger Parking Case.Workspace"),
        ("dev", {}, "My workspace.Personal"),
        ("prod", {"POWERBI_WORKSPACE": "Other"}, "Other.Workspace"),
    ],
)
def test_the_target_chooses_the_workspace(repo, target, env, workspace):
    result = run_report(repo, target, **env)

    assert result.returncode == 0, result.stderr
    assert fab_calls(repo)[0].startswith(f"fab import {workspace}/StavangerParking.SemanticModel")
    assert fab_calls(repo)[2].startswith(f"fab import {workspace}/StavangerParking.Report")


def test_several_warehouses_need_a_name(repo):
    two = json.dumps(
        [
            {"name": "Starter", "odbc_params": {"path": "/sql/1.0/warehouses/abc"}},
            {"name": "Big", "odbc_params": {"path": "/sql/1.0/warehouses/big"}},
        ]
    )

    refused = run_report(repo, "--dry-run", "prod", WAREHOUSES=two)
    chosen = run_report(repo, "--dry-run", "prod", WAREHOUSES=two, DATABRICKS_WAREHOUSE="Big")

    assert refused.returncode == 2
    assert "set DATABRICKS_WAREHOUSE to one of: Starter, Big" in refused.stderr
    assert chosen.returncode == 0, chosen.stderr
    assert "through /sql/1.0/warehouses/big" in chosen.stdout
