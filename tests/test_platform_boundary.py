"""The package imports nothing platform-specific (ADR 011): it runs, and is tested, anywhere."""

import ast
from pathlib import Path

import stavanger_parking

PACKAGE = Path(stavanger_parking.__file__).parent
PLATFORM_MODULES = {"notebookutils", "mssparkutils", "dbutils", "pyspark", "databricks"}


def imported_modules(path: Path) -> set[str]:
    """The top-level names of every module a file imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_the_package_imports_nothing_platform_specific():
    offending = {
        str(path.relative_to(PACKAGE)): sorted(imported_modules(path) & PLATFORM_MODULES)
        for path in sorted(PACKAGE.rglob("*.py"))
        if imported_modules(path) & PLATFORM_MODULES
    }
    assert offending == {}


def test_the_check_finds_a_platform_import(tmp_path):
    notebook = tmp_path / "notebook.py"
    notebook.write_text("import os\nfrom pyspark.sql import SparkSession\nimport databricks.sdk\n")

    assert imported_modules(notebook) & PLATFORM_MODULES == {"pyspark", "databricks"}
