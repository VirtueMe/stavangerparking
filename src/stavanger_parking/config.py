"""Declarative source configuration (`config/sources.json`).

Everything source-specific lives in the config: where the data comes from, where raw files and the
bronze table go, and under which licence the data is published. Paths and table names are relative;
the storage root and the catalog or schema belong to the runtime environment, not to the source.

The config is validated on load, and every problem is reported at once with the source and field
it concerns.
"""

import json
import re
import string
from dataclasses import dataclass, fields
from pathlib import PurePosixPath
from urllib.parse import urlparse

IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
# In this order, so that sorting raw paths sorts snapshots by time (the collector relies on it)
RAW_PATH_PLACEHOLDERS = ("yyyy", "mm", "dd", "HHmmss")


class ConfigError(ValueError):
    """The source configuration is missing, unreadable or invalid."""


@dataclass(frozen=True)
class CkanLocation:
    base_url: str
    package_id: str
    format: str


@dataclass(frozen=True)
class Licence:
    id: str
    url: str
    publisher: str


@dataclass(frozen=True)
class Polling:
    """Adaptive polling (ADR 003): fast while values change, slow after a run of unchanged ones."""

    fast_interval_minutes: int
    slow_interval_minutes: int
    unchanged_snapshots_for_slow: int
    # Fields left out when comparing snapshots, e.g. the data timestamp that advances regardless
    change_ignores_fields: tuple[str, ...]


@dataclass(frozen=True)
class Freshness:
    """When the source counts as stale: its newest timestamp is older than this when fetched."""

    stale_after_minutes: int


@dataclass(frozen=True)
class Source:
    id: str
    ckan: CkanLocation
    raw_path: str
    bronze_table: str
    licence: Licence
    polling: Polling
    freshness: Freshness


def _field_names(cls) -> set[str]:
    # The dataclasses are the single definition of which fields exist
    return {f.name for f in fields(cls)}


def load_sources(path) -> tuple[Source, ...]:
    """Read and validate a sources config file."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"Source config not found: {path}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"Source config {path} is not valid JSON: {e}") from e
    return parse_sources(data, origin=str(path))


def parse_sources(data, origin: str = "source config") -> tuple[Source, ...]:
    """Validate parsed config data and build the sources, reporting every problem at once."""
    problems: list[str] = []

    if not isinstance(data, dict):
        raise ConfigError(f"{origin}: expected an object with a 'sources' list")
    _check_keys(data, {"sources"}, "config", problems)
    entries = data.get("sources")
    if not isinstance(entries, list) or not entries:
        problems.append("config: sources must be a non-empty list")
        entries = []

    sources = []
    for index, entry in enumerate(entries):
        label = f"sources[{index}]"
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            label += f" ({entry['id']})"
        source = _parse_source(entry, label, problems)
        if source is not None:
            sources.append(source)

    seen: set[str] = set()
    for source in sources:
        if source.id in seen:
            problems.append(f"config: source id '{source.id}' is used more than once")
        seen.add(source.id)

    if problems:
        details = "\n".join(f"  - {p}" for p in problems)
        raise ConfigError(f"{origin} is invalid:\n{details}")
    return tuple(sources)


def _parse_source(entry, label: str, problems: list[str]) -> Source | None:
    if not isinstance(entry, dict):
        problems.append(f"{label}: must be an object")
        return None
    before = len(problems)
    _check_keys(entry, _field_names(Source), label, problems)

    source_id = _text(entry, "id", label, problems)
    if source_id and not IDENTIFIER.match(source_id):
        problems.append(f"{label}: id must be lowercase letters, digits and underscores")

    ckan = _section(entry, "ckan", CkanLocation, label, problems)
    base_url = _https_url(ckan, "base_url", label, problems, section="ckan")
    package_id = _text(ckan, "package_id", label, problems, section="ckan")
    fmt = _text(ckan, "format", label, problems, section="ckan")

    raw_path = _text(entry, "raw_path", label, problems)
    if raw_path:
        _check_raw_path(raw_path, label, problems)

    bronze_table = _text(entry, "bronze_table", label, problems)
    if bronze_table and not IDENTIFIER.match(bronze_table):
        problems.append(f"{label}: bronze_table must be lowercase letters, digits and underscores")

    licence = _section(entry, "licence", Licence, label, problems)
    licence_id = _text(licence, "id", label, problems, section="licence")
    licence_url = _https_url(licence, "url", label, problems, section="licence")
    publisher = _text(licence, "publisher", label, problems, section="licence")

    polling_section = _section(entry, "polling", Polling, label, problems)
    fast = _positive_int(polling_section, "fast_interval_minutes", label, problems)
    slow = _positive_int(polling_section, "slow_interval_minutes", label, problems)
    unchanged = _positive_int(polling_section, "unchanged_snapshots_for_slow", label, problems)
    ignored = _text_list(polling_section, "change_ignores_fields", label, problems)
    if fast and slow and slow < fast:
        problems.append(
            f"{label}: polling.slow_interval_minutes must not be shorter than "
            "polling.fast_interval_minutes"
        )

    freshness_section = _section(entry, "freshness", Freshness, label, problems)
    stale_after = _positive_int(
        freshness_section, "stale_after_minutes", label, problems, section="freshness"
    )

    if len(problems) > before:
        return None
    return Source(
        id=source_id,
        ckan=CkanLocation(base_url=base_url, package_id=package_id, format=fmt),
        raw_path=raw_path,
        bronze_table=bronze_table,
        licence=Licence(id=licence_id, url=licence_url, publisher=publisher),
        polling=Polling(
            fast_interval_minutes=fast,
            slow_interval_minutes=slow,
            unchanged_snapshots_for_slow=unchanged,
            change_ignores_fields=ignored,
        ),
        freshness=Freshness(stale_after_minutes=stale_after),
    )


def _check_keys(obj: dict, allowed: set[str], label: str, problems: list[str], prefix="") -> None:
    # Unknown keys are errors, so a typo does not silently leave a field unset
    for key in sorted(set(obj) - allowed):
        problems.append(f"{label}: unknown field '{prefix}{key}'")


def _section(entry: dict, key: str, schema, label: str, problems: list[str]) -> dict | None:
    value = entry.get(key)
    if not isinstance(value, dict):
        problems.append(f"{label}: {key} is required and must be an object")
        return None
    _check_keys(value, _field_names(schema), label, problems, prefix=f"{key}.")
    return value


def _text(obj: dict | None, key: str, label: str, problems: list[str], section: str = "") -> str:
    # A missing section (None) is already reported; its fields are not repeated
    if obj is None:
        return ""
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        problems.append(
            f"{label}: {_path(section, key)} is required and must be a non-empty string"
        )
        return ""
    return value


def _https_url(
    obj: dict | None, key: str, label: str, problems: list[str], section: str = ""
) -> str:
    url = _text(obj, key, label, problems, section)
    if url and (urlparse(url).scheme != "https" or not urlparse(url).netloc):
        problems.append(f"{label}: {_path(section, key)} must be an https URL, got {url!r}")
    return url


def _positive_int(
    obj: dict | None, key: str, label: str, problems: list[str], section: str = "polling"
) -> int:
    if obj is None:
        return 0
    value = obj.get(key)
    # bool is an int subclass; true/false are not intervals
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        problems.append(
            f"{label}: {_path(section, key)} is required and must be a positive integer"
        )
        return 0
    return value


def _text_list(
    obj: dict | None, key: str, label: str, problems: list[str], section: str = "polling"
) -> tuple[str, ...]:
    if obj is None:
        return ()
    value = obj.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        problems.append(f"{label}: {_path(section, key)} is required and must be a list of names")
        return ()
    return tuple(value)


def _path(section: str, key: str) -> str:
    return f"{section}.{key}" if section else key


def _check_raw_path(raw_path: str, label: str, problems: list[str]) -> None:
    path = PurePosixPath(raw_path)
    if path.is_absolute() or ".." in path.parts:
        problems.append(f"{label}: raw_path must be relative to the storage root: {raw_path!r}")
    try:
        names = [name for _, name, _, _ in string.Formatter().parse(raw_path) if name is not None]
    except ValueError as e:
        problems.append(f"{label}: raw_path is not a valid template: {e}")
        return
    expected = ", ".join("{" + p + "}" for p in RAW_PATH_PLACEHOLDERS)
    unknown = sorted(set(names) - set(RAW_PATH_PLACEHOLDERS))
    if unknown:
        problems.append(
            f"{label}: raw_path has unknown placeholders {unknown} (allowed: {expected})"
        )
    elif tuple(names) != RAW_PATH_PLACEHOLDERS:
        problems.append(
            f"{label}: raw_path must contain {expected} once each and in that order, "
            "so that paths sort by time"
        )
