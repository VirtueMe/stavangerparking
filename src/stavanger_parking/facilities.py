"""The facility mapping (`facility_mapping.json`): the feed's facilities in other sources.

The feed names a facility only by `Sted` (the natural key, ADR 004). The mapping links each name to
its parking area in the national parking register (Parkeringsregisteret), where its capacity comes
from, and records the names used by the register and the operator for reference (ADR 005). It is
maintained by hand: names differ between all three sources, so nothing is matched automatically.

The register records public parking only. Spaces reserved for others, which the feed still counts,
are added as `reserved_spaces`, with their source in the note (#91).

The mapping is validated on load, and every problem is reported at once.
"""

import json
from dataclasses import dataclass, fields
from pathlib import Path

# In the `stavanger_parking.config` package, found by path: that package imports this module
DEFAULT_MAPPING = Path(__file__).parent / "config" / "facility_mapping.json"


class MappingError(ValueError):
    """The facility mapping is missing, unreadable or invalid."""


@dataclass(frozen=True)
class FacilityMapping:
    facility: str
    register_id: int
    register_name: str
    operator_name: str
    # Anything a reader should know, such as sources that disagree
    note: str | None = None
    # Spaces the register leaves out because they are not public parking, such as spaces reserved
    # for the municipality's services, which the feed counts all the same; added to its capacity
    reserved_spaces: int = 0


REQUIRED_TEXT = ("facility", "register_name", "operator_name")


def load_facility_mapping(path) -> tuple[FacilityMapping, ...]:
    """Read and validate a facility mapping file."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise MappingError(f"Facility mapping not found: {path}") from e
    except json.JSONDecodeError as e:
        raise MappingError(f"Facility mapping {path} is not valid JSON: {e}") from e
    return parse_facility_mapping(data, origin=str(path))


def parse_facility_mapping(data, origin: str = "facility mapping") -> tuple[FacilityMapping, ...]:
    """Validate parsed mapping data, reporting every problem at once."""
    entries = data.get("facilities") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise MappingError(f"{origin}: expected an object with a non-empty 'facilities' list")
    if set(data) - {"facilities"}:
        raise MappingError(f"{origin}: unknown fields {sorted(set(data) - {'facilities'})}")

    problems: list[str] = []
    allowed = {f.name for f in fields(FacilityMapping)}
    mappings = []
    for index, entry in enumerate(entries):
        label = f"facilities[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{label}: must be an object")
            continue
        if isinstance(entry.get("facility"), str):
            label += f" ({entry['facility']})"
        before = len(problems)
        for key in sorted(set(entry) - allowed):
            problems.append(f"{label}: unknown field '{key}'")
        for key in REQUIRED_TEXT:
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{label}: {key} is required and must be a non-empty string")
        register_id = entry.get("register_id")
        if not isinstance(register_id, int) or isinstance(register_id, bool) or register_id < 1:
            problems.append(f"{label}: register_id is required and must be a positive integer")
        note = entry.get("note")
        if note is not None and not isinstance(note, str):
            problems.append(f"{label}: note must be a string")
        reserved = entry.get("reserved_spaces", 0)
        if not isinstance(reserved, int) or isinstance(reserved, bool) or reserved < 0:
            problems.append(f"{label}: reserved_spaces must be a non-negative integer")
        elif reserved and not (isinstance(note, str) and note.strip()):
            problems.append(f"{label}: reserved_spaces needs a note giving its source")
        if len(problems) == before:
            mappings.append(FacilityMapping(**entry))

    for key in ("facility", "register_id"):
        values = [getattr(m, key) for m in mappings]
        for value in sorted({v for v in values if values.count(v) > 1}, key=str):
            problems.append(f"{origin}: {key} {value!r} is mapped more than once")

    if problems:
        details = "\n".join(f"  - {p}" for p in problems)
        raise MappingError(f"{origin} is invalid:\n{details}")
    return tuple(mappings)
