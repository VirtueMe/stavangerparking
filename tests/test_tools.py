"""The platform tools (tools/) choose a platform and target and run that platform's script (#82).

The scripts run for real, in a copy of the repository layout under tmp_path: fake platforms record
their arguments, and fake CLIs on PATH record their calls, so nothing reaches a workspace.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]

# A platform script that records how it was called, and exits with EXIT_CODE if set
RECORDER = """#!/usr/bin/env bash
echo "$(basename "$(dirname "$0")")/$(basename "$0") $*" >> "$CALLS"
exit "${EXIT_CODE:-0}"
"""

# A CLI that records its calls; FAKE_OUTPUT_<name> is printed when set
FAKE_CLI = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALLS"
var="FAKE_OUTPUT_$(basename "$0")"
[ -n "${!var:-}" ] && echo "${!var}"
exit 0
"""


def write_script(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def repo(tmp_path):
    """The tools, next to two fake platforms: alpha has deploy and backfill, beta only deploy."""
    shutil.copytree(REPO / "tools", tmp_path / "tools")
    for script in ["alpha/deploy.sh", "alpha/backfill.sh", "beta/deploy.sh"]:
        write_script(tmp_path / "platforms" / script, RECORDER)
    return tmp_path


def environment(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "PLATFORM"}
    return {**env, "CALLS": str(tmp_path / "calls.log"), **extra}


def run(repo: Path, tool: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [repo / "tools" / tool, *args],
        env=environment(repo, **env),
        capture_output=True,
        text=True,
        check=False,
    )


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


# --- choosing the platform ---


@pytest.mark.parametrize("args", [["-p", "alpha"], ["--platform", "alpha"], ["--platform=alpha"]])
def test_the_flag_chooses_the_platform(repo, args):
    result = run(repo, "deploy", *args)

    assert result.returncode == 0, result.stderr
    assert calls(repo) == ["alpha/deploy.sh dev"]


def test_the_environment_chooses_the_platform(repo):
    run(repo, "deploy", PLATFORM="beta")

    assert calls(repo) == ["beta/deploy.sh dev"]


@pytest.mark.parametrize(
    "line", ["PLATFORM=beta", 'PLATFORM="beta"', "PLATFORM='beta'", "export PLATFORM=beta"]
)
def test_dotenv_chooses_the_platform(repo, line):
    (repo / ".env").write_text(f"SOME_TOKEN=secret\n{line}\n")

    run(repo, "deploy")

    assert calls(repo) == ["beta/deploy.sh dev"]


def test_dotenv_is_read_not_sourced(repo):
    (repo / ".env").write_text(f"PLATFORM=beta\ntouch {repo}/sourced\n")

    run(repo, "deploy")

    assert calls(repo) == ["beta/deploy.sh dev"]
    assert not (repo / "sourced").exists()


def test_the_flag_wins_over_the_environment_and_dotenv(repo):
    (repo / ".env").write_text("PLATFORM=beta\n")

    run(repo, "deploy", "-p", "alpha", PLATFORM="beta")

    assert calls(repo) == ["alpha/deploy.sh dev"]


def test_the_environment_wins_over_dotenv(repo):
    (repo / ".env").write_text("PLATFORM=beta\n")

    run(repo, "deploy", PLATFORM="alpha")

    assert calls(repo) == ["alpha/deploy.sh dev"]


def test_no_platform_fails_and_lists_the_platforms(repo):
    result = run(repo, "deploy")

    assert result.returncode == 2
    assert "platforms with deploy: alpha, beta" in result.stderr
    assert calls(repo) == []


@pytest.mark.parametrize("platform", ["gamma", "../platforms/alpha"])
def test_an_unknown_platform_fails(repo, platform):
    result = run(repo, "deploy", "-p", platform)

    assert result.returncode == 2
    assert f"platform '{platform}' has no deploy (platforms with deploy: alpha, beta)" in (
        result.stderr
    )
    assert calls(repo) == []


def test_a_platform_without_the_operation_fails(repo):
    result = run(repo, "backfill", "-p", "beta")

    assert result.returncode == 2
    assert "platform 'beta' has no backfill (platforms with backfill: alpha)" in result.stderr


# --- target, arguments, dry run, exit code ---


def test_the_target_is_dev_unless_prod(repo):
    run(repo, "backfill", "-p", "alpha")
    run(repo, "backfill", "-p", "alpha", "--prod")

    assert calls(repo) == ["alpha/backfill.sh dev", "alpha/backfill.sh prod"]


def test_other_arguments_go_to_the_script(repo):
    run(repo, "deploy", "--prod", "v0.16.0", "-p", "alpha")

    assert calls(repo) == ["alpha/deploy.sh prod v0.16.0"]


@pytest.mark.parametrize("flag", ["-n", "--dry-run"])
def test_the_dry_run_is_passed_on(repo, flag):
    result = run(repo, "deploy", "-p", "alpha", "--prod", flag)

    assert calls(repo) == ["alpha/deploy.sh --dry-run prod"]
    assert "alpha prod (dry run): platforms/alpha/deploy.sh --dry-run prod" in result.stderr


@pytest.mark.parametrize("option", ["--dryrun", "--prd", "-x"])
def test_an_unknown_option_fails_and_runs_nothing(repo, option):
    result = run(repo, "deploy", "-p", "alpha", option)

    assert result.returncode == 2
    assert f"unknown option: {option}" in result.stderr
    assert calls(repo) == []


def test_arguments_after_a_double_dash_are_passed_on_unread(repo):
    run(repo, "deploy", "-p", "alpha", "--", "--prod", "-x")

    assert calls(repo) == ["alpha/deploy.sh dev --prod -x"]


def test_the_exit_code_is_passed_through(repo):
    result = run(repo, "backfill", "-p", "alpha", EXIT_CODE="3")

    assert result.returncode == 3


# --- the Databricks scripts ---


@pytest.fixture
def databricks(tmp_path):
    """The Databricks scripts in a copy of their folder, with fake uv, gh, databricks and git."""
    folder = tmp_path / "platforms" / "databricks"
    folder.mkdir(parents=True)
    for script in ["deploy.sh", "backfill.sh"]:
        shutil.copy2(REPO / "platforms" / "databricks" / script, folder / script)
    (tmp_path / "tools").mkdir()
    shutil.copy2(REPO / "tools" / "requirements.sh", tmp_path / "tools" / "requirements.sh")
    bin_dir = tmp_path / "bin"
    for cli in ["uv", "gh", "databricks"]:
        write_script(bin_dir / cli, FAKE_CLI)
    # git archive yields a tar of two raw files; every other git call only records
    raw = tmp_path / "data"
    for name in ["bronze/parking/a.json", "bronze/parking/b.json"]:
        (raw / name).parent.mkdir(parents=True, exist_ok=True)
        (raw / name).write_text("{}")
    write_script(
        bin_dir / "git",
        f"""#!/usr/bin/env bash
echo "git $*" >> "$CALLS"
case "$*" in
  *archive*) tar -C {raw} -c bronze ;;
  *rev-parse*) echo {tmp_path} ;;
esac
""",
    )
    return folder, bin_dir


def run_databricks(tmp_path, databricks, script, *args):
    folder, bin_dir = databricks
    return subprocess.run(
        [folder / script, *args],
        env=environment(
            tmp_path,
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            FAKE_OUTPUT_gh="v0.17.0",
            FAKE_OUTPUT_databricks='{"resources": {"schemas": {"stavanger_parking": '
            '{"catalog_name": "workspace", "name": "stavanger_parking"}}}}',
        ),
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_prod_deploy_installs_the_latest_release(tmp_path, databricks):
    result = run_databricks(tmp_path, databricks, "deploy.sh", "prod")

    assert result.returncode == 0, result.stderr
    assert "release v0.17.0" in result.stdout
    assert [call for call in calls(tmp_path) if call.startswith(("gh", "databricks"))] == [
        "gh release view --repo VirtueMe/stavangerparking --json tagName --jq .tagName",
        "gh release download v0.17.0 --repo VirtueMe/stavangerparking --pattern *.whl --dir dist",
        "databricks bundle deploy --target prod",
    ]


def test_a_prod_deploy_installs_the_release_given(tmp_path, databricks):
    run_databricks(tmp_path, databricks, "deploy.sh", "prod", "v0.16.0")

    assert [call for call in calls(tmp_path) if call.startswith(("gh", "databricks"))] == [
        "gh release download v0.16.0 --repo VirtueMe/stavangerparking --pattern *.whl --dir dist",
        "databricks bundle deploy --target prod",
    ]


def export_call(tmp_path) -> str:
    return next(call for call in calls(tmp_path) if call.startswith("uv export"))


def test_a_prod_deploy_pins_the_dependencies_to_the_releases_own_lock(tmp_path, databricks):
    """ADR 012: the versions in that release's uv.lock, so a revert gets its versions too."""
    run_databricks(tmp_path, databricks, "deploy.sh", "prod", "v0.16.0")

    assert f"git -C {tmp_path} archive v0.16.0 pyproject.toml uv.lock" in calls(tmp_path)
    export = export_call(tmp_path)
    project = export.split("--project ")[1].split()[0]
    assert project != str(tmp_path), "prod exported the checkout's lock, not the release's"
    # One pip run installs the requirements and the wheel, which has no hash to check
    assert "--frozen" in export and "--no-dev" in export and "--no-hashes" in export
    assert export.endswith("-o dist/requirements.txt")


def test_a_dev_deploy_pins_the_dependencies_to_the_checkouts_lock(tmp_path, databricks):
    run_databricks(tmp_path, databricks, "deploy.sh", "dev")

    assert f"--project {tmp_path} -o dist/requirements.txt" in export_call(tmp_path)


def test_the_jobs_install_the_locked_requirements_before_the_wheel():
    bundle = (REPO / "platforms" / "databricks" / "databricks.yml").read_text()

    assert (
        bundle.count(
            "              - -r ${workspace.file_path}/dist/requirements.txt\n"
            "              - ./dist/*.whl\n"
        )
        == 2
    )
    assert "sync:\n  include:\n    - dist/requirements.txt\n" in bundle


def test_a_dry_run_deploy_plans_and_deploys_nothing(tmp_path, databricks):
    result = run_databricks(tmp_path, databricks, "deploy.sh", "--dry-run", "dev")

    assert result.returncode == 0, result.stderr
    assert [call for call in calls(tmp_path) if call.startswith("databricks")] == [
        "databricks bundle validate --target dev",
        "databricks bundle plan --target dev",
    ]


def test_a_backfill_copies_and_runs_the_pipeline(tmp_path, databricks):
    result = run_databricks(tmp_path, databricks, "backfill.sh", "prod")

    assert result.returncode == 0, result.stderr
    assert "copying 2 raw file(s) to /Volumes/workspace/stavanger_parking/raw" in result.stdout
    databricks_calls = [call for call in calls(tmp_path) if call.startswith("databricks")]
    assert databricks_calls[1].startswith("databricks fs cp --recursive --overwrite")
    assert databricks_calls[2] == "databricks bundle run --target prod pipeline"


def test_a_dry_run_backfill_copies_and_runs_nothing(tmp_path, databricks):
    result = run_databricks(tmp_path, databricks, "backfill.sh", "--dry-run", "prod")

    assert result.returncode == 0, result.stderr
    assert (
        "dry run: would copy 2 raw file(s) to /Volumes/workspace/stavanger_parking/raw"
        " and run the pipeline job (prod)"
    ) in result.stdout
    assert [call for call in calls(tmp_path) if call.startswith("databricks")] == [
        "databricks bundle validate --target prod --output json"
    ]
