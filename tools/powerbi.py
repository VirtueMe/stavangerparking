"""Build the Power BI project for a platform, and publish it to a Power BI workspace (#72).

    python tools/powerbi.py build databricks <out> host=... http_path=... catalog=... schema=...
    python tools/powerbi.py publish <out> "Stavanger Parking Case.Workspace"

The model in powerbi/ reads local Delta tables through one function, DeltaTable(name), in
expressions.tmdl. `build` copies the project to <out> and replaces that file with the platform's
own (platforms/<platform>/report/expressions.tmdl), its {{placeholders}} filled with the values
given, so each generated model has exactly one data source and refreshes in the Power BI service.

`publish` imports the semantic model with the Fabric CLI (`fab`, dependency group "powerbi"), then
the report, pointed at the published model by its id instead of the folder next to it.

Standard library only, and not part of the package: it runs where Power BI is published from.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "powerbi"
NAME = "StavangerParking"
MODEL = f"{NAME}.SemanticModel"
REPORT = f"{NAME}.Report"
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class PowerBIError(RuntimeError):
    """The project could not be built or published."""


def template(platform: str, root: Path = ROOT) -> Path:
    return root / "platforms" / platform / "report" / "expressions.tmdl"


def render(text: str, values: dict[str, str]) -> str:
    """Fill every {{placeholder}}; a missing, unused or unsafe value is an error."""
    wanted = set(PLACEHOLDER.findall(text))
    if missing := sorted(wanted - values.keys()):
        raise PowerBIError(f"no value for {', '.join(missing)}")
    if unused := sorted(values.keys() - wanted):
        raise PowerBIError(f"unknown value {', '.join(unused)}")
    for key, value in values.items():
        # Values land inside M text literals
        if not value or '"' in value or "\n" in value:
            raise PowerBIError(f"{key} must be a non-empty single line without quotes: {value!r}")
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], text)


def build(platform: str, out: Path, values: dict[str, str], root: Path = ROOT) -> Path:
    """Copy the project to `out` with the platform's data source; returns the model folder."""
    source = template(platform, root)
    if not source.is_file():
        raise PowerBIError(f"platform '{platform}' has no report source ({source})")
    expressions = render(source.read_text(encoding="utf-8"), values)
    if out.exists():
        shutil.rmtree(out)
    # Desktop's local settings and cache stay behind: they belong to one machine
    ignore = shutil.ignore_patterns("localSettings.json", "cache.abf", ".gitignore")
    shutil.copytree(root / "powerbi", out, ignore=ignore)
    model = out / MODEL
    (model / "definition" / "expressions.tmdl").write_text(expressions, encoding="utf-8")
    return model


def bind_report(report: Path, model_id: str) -> None:
    """Point the report at a published semantic model instead of the folder next to it."""
    pbir = report / "definition.pbir"
    definition = json.loads(pbir.read_text(encoding="utf-8"))
    definition["datasetReference"] = {
        "byConnection": {"connectionString": f"semanticmodelid={model_id}"}
    }
    pbir.write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")


def fab(*args: str) -> str:
    result = subprocess.run(["fab", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise PowerBIError(f"fab {' '.join(args)} failed:\n{result.stdout}{result.stderr}")
    return result.stdout.strip()


def publish(out: Path, workspace: str) -> str:
    """Import the model, then the report bound to it; returns the model's id.

    Both replace what is published: the repository is the definition, not the service.
    """
    fab("import", f"{workspace}/{MODEL}", "-i", str(out / MODEL), "-f")
    model_id = fab("get", f"{workspace}/{MODEL}", "-q", "id")
    # A bound copy, so the generated project still opens in Desktop with the model next to it
    with tempfile.TemporaryDirectory() as folder:
        bound = Path(folder) / REPORT
        shutil.copytree(out / REPORT, bound)
        bind_report(bound, model_id)
        fab("import", f"{workspace}/{REPORT}", "-i", str(bound), "-f")
    return model_id


def main(argv: list[str]) -> int:
    usage = (
        "usage: powerbi.py build <platform> <out> key=value..."
        " | powerbi.py publish <out> <workspace>"
    )
    try:
        match argv:
            case ["build", platform, out, *pairs]:
                if not all("=" in pair for pair in pairs):
                    raise PowerBIError(f"values are key=value: {pairs}")
                values = dict(pair.split("=", 1) for pair in pairs)
                model = build(platform, Path(out), values)
                print(f"built {model.parent} with the {platform} data source")
            case ["publish", out, workspace]:
                model_id = publish(Path(out), workspace)
                print(f"published {MODEL} ({model_id}) and {REPORT} to {workspace}")
            case _:
                print(usage, file=sys.stderr)
                return 2
    except PowerBIError as e:
        print(f"powerbi: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
