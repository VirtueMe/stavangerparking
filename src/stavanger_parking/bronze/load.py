"""Load collected raw snapshots into the bronze Delta table.

    python -m stavanger_parking.bronze.load --raw-root DIR --tables-root DIR

For every source, raw files under `--raw-root` that are not yet in bronze are appended to the
source's `bronze_table` under `--tables-root`: one row per record, every source field as a string
(nested objects and lists as JSON text), plus the sidecar metadata and the raw file path. The
roots are local folders or Lakehouse paths (for example `/lakehouse/default/Files` and
`/lakehouse/default/Tables`), so the same code runs locally and on the platform.

Bronze is append-only. A raw file is loaded once: files already in bronze, or already recorded as
a load issue, are skipped. Files that cannot be loaded (no sidecar, or a payload that is not a
JSON list of records) are recorded in `<bronze_table>_load_issues` and reported, never loaded or
skipped silently. All new rows of a run go into one Delta commit.

The records of failed collection attempts (`bronze.collect_issues`) are loaded the same way, once
each, into `bronze_collect_issues`: one row per record, for every source.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from deltalake import DeltaTable

from stavanger_parking.bronze.collect_issues import read_issues
from stavanger_parking.bronze.collector import SIDECAR_SUFFIX, raw_glob, sidecar_path
from stavanger_parking.config import DEFAULT_CONFIG, Source, load_sources
from stavanger_parking.tables import COLLECT_ISSUES_TABLE, table_path

# Metadata columns added to every bronze row; source fields keep their own names
METADATA_SCHEMA = {
    "source_id": pl.String,
    "raw_file": pl.String,
    "record_index": pl.Int32,
    "ingested_at": pl.Datetime("us", "UTC"),
    "source_url": pl.String,
    "resource_id": pl.String,
    "content_hash": pl.String,
    "values_fingerprint": pl.String,
    "run_id": pl.String,
    "loaded_at": pl.Datetime("us", "UTC"),
}

ISSUE_SCHEMA = {
    "source_id": pl.String,
    "raw_file": pl.String,
    "issue": pl.String,
    "detail": pl.String,
    "detected_at": pl.Datetime("us", "UTC"),
}

MISSING_SIDECAR = "missing_sidecar"
UNREADABLE_SIDECAR = "unreadable_sidecar"
UNREADABLE_PAYLOAD = "unreadable_payload"
NO_RECORDS = "no_records"


class LoadError(RuntimeError):
    """Raw files could not be loaded into bronze."""


@dataclass(frozen=True)
class Issue:
    raw_file: str
    issue: str
    detail: str


@dataclass
class LoadResult:
    source_id: str
    loaded_files: list[str] = field(default_factory=list)
    rows: int = 0
    issues: list[Issue] = field(default_factory=list)
    skipped: int = 0


def bronze_path(tables_root: str, source: Source) -> str:
    return table_path(tables_root, source.bronze_table)


def issues_path(tables_root: str, source: Source) -> str:
    return f"{bronze_path(tables_root, source)}_load_issues"


def raw_files(raw_root: Path, source: Source) -> list[str]:
    """Raw files of a source, relative to the root, oldest first (paths sort by time)."""
    return sorted(
        p.relative_to(raw_root).as_posix()
        for p in raw_root.glob(raw_glob(source.raw_path))
        if not p.name.endswith(SIDECAR_SUFFIX)
    )


def handled_files(tables_root: str, source: Source, storage_options=None) -> set[str]:
    """Raw files already in bronze or already recorded as a load issue."""
    handled: set[str] = set()
    for path in (bronze_path(tables_root, source), issues_path(tables_root, source)):
        if DeltaTable.is_deltatable(path, storage_options=storage_options):
            table = pl.read_delta(path, columns=["raw_file"], storage_options=storage_options)
            handled.update(table["raw_file"].unique().to_list())
    return handled


def parse_records(payload: bytes) -> list[dict] | None:
    """The records of a snapshot, or None if it is not a JSON list of objects."""
    try:
        records = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        return None
    return records


def bronze_rows(
    source: Source, raw_file: str, sidecar: dict, records: list[dict], loaded_at: datetime
) -> list[dict]:
    """One row per record: every source field as a string, plus the metadata columns."""
    clashes = sorted(set().union(*records) & METADATA_SCHEMA.keys()) if records else []
    if clashes:
        raise LoadError(f"{raw_file}: source fields clash with metadata columns: {clashes}")
    meta = {
        "source_id": source.id,
        "raw_file": raw_file,
        "ingested_at": datetime.fromisoformat(sidecar["ingested_at"]),
        "source_url": sidecar.get("source_url"),
        "resource_id": sidecar.get("resource_id"),
        "content_hash": sidecar.get("content_hash"),
        "values_fingerprint": sidecar.get("values_fingerprint"),
        "run_id": sidecar.get("run_id"),
        "loaded_at": loaded_at,
    }
    return [
        {**{k: _as_text(v) for k, v in record.items()}, **meta, "record_index": i}
        for i, record in enumerate(records)
    ]


def _as_text(value) -> str | None:
    """A field value as text: strings unchanged, anything else as JSON (`true`, `3650`, `{...}`)."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def read_raw_file(raw_root: Path, raw_file: str) -> Issue | tuple[dict, list[dict]]:
    """The sidecar and records of a raw file, or the issue that prevents loading it."""
    sidecar_file = raw_root / sidecar_path(raw_file)
    if not sidecar_file.exists():
        return Issue(raw_file, MISSING_SIDECAR, f"no {sidecar_path(raw_file)}")
    try:
        sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
        datetime.fromisoformat(sidecar["ingested_at"])
    except (ValueError, KeyError, TypeError) as e:
        return Issue(raw_file, UNREADABLE_SIDECAR, f"{type(e).__name__}: {e}")
    records = parse_records((raw_root / raw_file).read_bytes())
    if records is None:
        return Issue(raw_file, UNREADABLE_PAYLOAD, "not a JSON list of objects")
    if not records:
        return Issue(raw_file, NO_RECORDS, "the snapshot contains no records")
    return sidecar, records


def load_source(
    source: Source, raw_root: Path, tables_root: str, now: datetime, storage_options=None
) -> LoadResult:
    """Append a source's new raw files to bronze; record the ones that cannot be loaded."""
    result = LoadResult(source.id)
    handled = handled_files(tables_root, source, storage_options)
    rows: list[dict] = []
    for raw_file in raw_files(raw_root, source):
        if raw_file in handled:
            result.skipped += 1
            continue
        snapshot = read_raw_file(raw_root, raw_file)
        if isinstance(snapshot, Issue):
            result.issues.append(snapshot)
            continue
        sidecar, records = snapshot
        rows.extend(bronze_rows(source, raw_file, sidecar, records, now))
        result.loaded_files.append(raw_file)

    # Rows first, issues second: if a run stops in between, the issues are found again next time
    if rows:
        source_fields = sorted(set().union(*rows) - METADATA_SCHEMA.keys())
        schema = {**dict.fromkeys(source_fields, pl.String), **METADATA_SCHEMA}
        frame = pl.DataFrame(rows, schema=schema)
        _append(frame, bronze_path(tables_root, source), storage_options)
        result.rows = frame.height
    if result.issues:
        issues = pl.DataFrame(
            [
                {
                    "source_id": source.id,
                    "raw_file": i.raw_file,
                    "issue": i.issue,
                    "detail": i.detail,
                    "detected_at": now,
                }
                for i in result.issues
            ],
            schema=ISSUE_SCHEMA,
        )
        _append(issues, issues_path(tables_root, source), storage_options)
    return result


COLLECT_ISSUE_SCHEMA = {
    "record_file": pl.String,
    "issue": pl.String,
    "run_id": pl.String,
    "source_id": pl.String,
    "occurred_at": pl.Datetime("us", "UTC"),
    "detail": pl.String,
    "url": pl.String,
    "recorded_at": pl.Datetime("us", "UTC"),
    "loaded_at": pl.Datetime("us", "UTC"),
}


def load_collect_issues(raw_root: Path, tables_root: str, now: datetime, storage_options=None):
    """Append the records of failed collection attempts not yet in bronze; returns how many."""
    path = table_path(tables_root, COLLECT_ISSUES_TABLE)
    handled = set()
    if DeltaTable.is_deltatable(path, storage_options=storage_options):
        handled = set(
            pl.read_delta(path, columns=["record_file"], storage_options=storage_options)[
                "record_file"
            ]
        )
    rows = [
        {
            **{k: record.get(k) for k in COLLECT_ISSUE_SCHEMA},
            "record_file": record_file,
            "occurred_at": datetime.fromisoformat(record["occurred_at"]),
            "recorded_at": datetime.fromisoformat(record["recorded_at"]),
            "loaded_at": now,
        }
        for record_file, record in read_issues(raw_root)
        if record_file not in handled
    ]
    frame = pl.DataFrame(rows, schema=COLLECT_ISSUE_SCHEMA)
    # Written even when empty on the first run, so the table exists for the checks and the report
    if rows or not DeltaTable.is_deltatable(path, storage_options=storage_options):
        _append(frame, path, storage_options)
    return frame.height


def _append(frame: pl.DataFrame, path: str, storage_options) -> None:
    # Append only; new source fields become new columns instead of failing the load
    frame.write_delta(
        path,
        mode="append",
        storage_options=storage_options,
        delta_write_options={"schema_mode": "merge"},
    )


def run(config_path, raw_root: Path, tables_root: str, now: datetime, storage_options=None):
    """Load every source's new raw files; returns the report, a line per source and per issue."""
    lines = []
    for source in load_sources(config_path):
        r = load_source(source, raw_root, tables_root, now, storage_options)
        lines.append(
            f"{source.id}: loaded {len(r.loaded_files)} file(s), {r.rows} row(s); "
            f"skipped {r.skipped} already handled; {len(r.issues)} issue(s)"
        )
        lines += [f"  {i.issue}: {i.raw_file} ({i.detail})" for i in r.issues]
    loaded = load_collect_issues(raw_root, tables_root, now, storage_options)
    lines.append(f"collect issues: loaded {loaded} record(s) of failed collection attempts")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m stavanger_parking.bronze.load")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--raw-root", type=Path, required=True, help="root of the raw files")
    parser.add_argument("--tables-root", required=True, help="folder or URI of the tables")
    args = parser.parse_args(argv)

    for line in run(args.config, args.raw_root, args.tables_root, datetime.now(UTC)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
