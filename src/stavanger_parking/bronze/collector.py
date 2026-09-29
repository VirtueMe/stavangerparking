"""Collect raw snapshots with adaptive polling (ADR 003) into a storage root (ADR 007).

Each fetched snapshot is stored unchanged at the source's `raw_path`, next to a sidecar
(`<name>.meta.json`) describing it. The polling mode is derived from the latest sidecars, so the
storage itself is the only state:

- fast: fetch on every scheduled run (every `fast_interval_minutes`)
- slow: once the last `unchanged_snapshots_for_slow` snapshots have identical values, fetch only
  every `slow_interval_minutes`; the first changed snapshot switches back to fast

A source without `polling`, such as monthly reference data (ADR 005), is fetched whenever it is
collected (`on_demand`); its sidecars have no fingerprint or next due time.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path, PurePosixPath

import httpx

from stavanger_parking.bronze.ckan import resolve_resource
from stavanger_parking.config import CkanLocation, HttpLocation, Polling, Source

SIDECAR_SUFFIX = ".meta.json"
FAST = "fast"
SLOW = "slow"
ON_DEMAND = "on_demand"


class CollectError(RuntimeError):
    """A snapshot could not be fetched or stored."""


@dataclass(frozen=True)
class Decision:
    fetch: bool
    mode: str
    reason: str


@dataclass(frozen=True)
class Outcome:
    source_id: str
    decision: Decision
    raw_file: str | None = None


@dataclass(frozen=True)
class Gap:
    after: datetime
    expected_by: datetime
    next_snapshot: datetime | None


def content_hash(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def values_fingerprint(payload: bytes, ignored_fields: tuple[str, ...]) -> str | None:
    """Hash of the snapshot's values, leaving out fields that change regardless (the timestamp).

    Record order does not matter. Returns None for a payload that is not a JSON list of objects;
    such a snapshot is still stored, and counts as changed.
    """
    try:
        records = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        return None
    canonical = sorted(
        json.dumps({k: v for k, v in r.items() if k not in ignored_fields}, sort_keys=True)
        for r in records
    )
    return "sha256:" + hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()


def render_raw_path(template: str, at: datetime) -> str:
    return template.format(yyyy=f"{at:%Y}", mm=f"{at:%m}", dd=f"{at:%d}", HHmmss=f"{at:%H%M%S}")


def sidecar_path(raw_file: str) -> str:
    path = PurePosixPath(raw_file)
    return str(path.with_name(path.stem + SIDECAR_SUFFIX))


def mode_after(fingerprints: list[str | None], polling: Polling) -> str:
    """The polling mode that follows snapshots with these values fingerprints (oldest first)."""
    window = fingerprints[-polling.unchanged_snapshots_for_slow :]
    unchanged = len(window) == polling.unchanged_snapshots_for_slow and len(set(window)) == 1
    return SLOW if unchanged and window[0] is not None else FAST


def slow_fetch_due_after(polling: Polling) -> timedelta:
    # Scheduled runs start late; fetching from half a fast step early keeps the effective
    # slow interval close to slow_interval_minutes instead of drifting a full step beyond it
    return timedelta(minutes=polling.slow_interval_minutes - polling.fast_interval_minutes / 2)


def decide(now: datetime, sidecars: list[dict], polling: Polling) -> Decision:
    """Whether a scheduled run at `now` should fetch, given the latest snapshots (oldest first)."""
    if not sidecars:
        return Decision(True, FAST, "no earlier snapshot")
    needed = polling.unchanged_snapshots_for_slow
    if len(sidecars) < needed:
        return Decision(True, FAST, f"{len(sidecars)} of {needed} snapshots needed to compare")
    if mode_after(_fingerprints(sidecars), polling) == FAST:
        return Decision(True, FAST, f"values changed within the last {needed} snapshots")
    last = datetime.fromisoformat(sidecars[-1]["ingested_at"])
    elapsed = now - last
    unchanged = f"values unchanged for {polling.unchanged_snapshots_for_slow} snapshots"
    if elapsed >= slow_fetch_due_after(polling):
        return Decision(True, SLOW, f"{unchanged}; {_minutes(elapsed)} since the last fetch")
    due = _minutes(slow_fetch_due_after(polling))
    return Decision(
        False, SLOW, f"{unchanged}; last fetch {_minutes(elapsed)} ago, next after {due}"
    )


def next_due(ingested_at: datetime, mode: str, polling: Polling) -> datetime:
    minutes = polling.slow_interval_minutes if mode == SLOW else polling.fast_interval_minutes
    return ingested_at + timedelta(minutes=minutes)


def find_gaps(
    sidecars: list[dict], tolerance: timedelta, now: datetime | None = None
) -> Iterator[Gap]:
    """Periods where no snapshot arrived by the time the previous one said the next was due.

    Intended slow-mode intervals are not gaps: each sidecar records when the next was due. With
    `now`, a latest snapshot whose successor is overdue is reported as an open gap.
    """
    for previous, following in pairwise(sidecars):
        expected = datetime.fromisoformat(previous["next_due"])
        arrived = datetime.fromisoformat(following["ingested_at"])
        if arrived > expected + tolerance:
            after = datetime.fromisoformat(previous["ingested_at"])
            yield Gap(after=after, expected_by=expected, next_snapshot=arrived)
    if sidecars and now is not None:
        expected = datetime.fromisoformat(sidecars[-1]["next_due"])
        if now > expected + tolerance:
            after = datetime.fromisoformat(sidecars[-1]["ingested_at"])
            yield Gap(after=after, expected_by=expected, next_snapshot=None)


def read_sidecars(storage: Path, source: Source, limit: int | None = None) -> list[dict]:
    """The source's sidecars in storage, oldest first (paths sort by time)."""
    files = sorted(storage.glob(sidecar_path(raw_glob(source.raw_path))))
    if limit is not None:
        files = files[-limit:]
    return [json.loads(f.read_text(encoding="utf-8")) for f in files]


def collect(
    source: Source,
    storage: Path,
    client: httpx.Client,
    now: datetime,
    run_id: str,
) -> Outcome:
    """Collect a source once: decide (if it is polled), and fetch and store if due."""
    polling = source.polling
    if polling is None:
        recent = []
        decision = Decision(True, ON_DEMAND, "not polled; fetched whenever collected")
    else:
        recent = read_sidecars(storage, source, limit=polling.unchanged_snapshots_for_slow)
        decision = decide(now, recent, polling)
    if not decision.fetch:
        return Outcome(source.id, decision)

    match source.location:
        case CkanLocation(base_url, package_id, fmt):
            resource = resolve_resource(base_url, package_id, fmt, client)
            url, resource_id = resource.url, resource.id
        case HttpLocation(url):
            resource_id = None
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise CollectError(f"Download failed for {source.id}: {url}: {e}") from e
    payload = response.content

    raw_file = render_raw_path(source.raw_path, now)
    fingerprint = mode = due = None
    if polling is None:
        mode = ON_DEMAND
    else:
        fingerprint = values_fingerprint(payload, polling.change_ignores_fields)
        mode = mode_after([*_fingerprints(recent), fingerprint], polling)
        due = next_due(now, mode, polling).isoformat()
    sidecar = {
        "source_id": source.id,
        "ingested_at": now.isoformat(),
        "source_url": url,
        "resource_id": resource_id,
        "run_id": run_id,
        "content_hash": content_hash(payload),
        "values_fingerprint": fingerprint,
        "polling_mode": mode,
        "next_due": due,
    }

    _write_new(storage / raw_file, payload)
    _write_new(
        storage / sidecar_path(raw_file),
        (json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    return Outcome(source.id, decision, raw_file)


def _write_new(path: Path, content: bytes) -> None:
    # Raw data is immutable: an existing file is never overwritten
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as f:
            f.write(content)
    except FileExistsError as e:
        raise CollectError(f"Refusing to overwrite existing file: {path}") from e


def _fingerprints(sidecars: list[dict]) -> list[str | None]:
    return [s.get("values_fingerprint") for s in sidecars]


def raw_glob(template: str) -> str:
    """Glob pattern matching every raw file a `raw_path` template can produce."""
    return template.format(yyyy="*", mm="*", dd="*", HHmmss="*")


def _minutes(delta: timedelta) -> str:
    return f"{delta.total_seconds() / 60:.1f} min"
