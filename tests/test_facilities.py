import copy
import json
from pathlib import Path

import pytest

from stavanger_parking.config import CONFIG_DIR
from stavanger_parking.facilities import (
    DEFAULT_MAPPING,
    MappingError,
    load_facility_mapping,
    parse_facility_mapping,
)

MAPPING = DEFAULT_MAPPING
REGISTER = Path(__file__).parent / "fixtures" / "parkeringsregisteret_stavanger_parkering.json"
# The facilities in the feed, as collected since 2026-09-28
FEED_FACILITIES = {
    "Jernbanen",
    "Valberget",
    "Posten",
    "Jorenholmen",
    "St Olav",
    "Siddis",
    "Forum",
    "Kyrre",
    "Parketten",
}


@pytest.fixture
def data() -> dict:
    return json.loads(MAPPING.read_text(encoding="utf-8"))


def problems_of(data) -> str:
    with pytest.raises(MappingError) as e:
        parse_facility_mapping(data)
    return str(e.value)


def test_repository_mapping_covers_every_facility_in_the_feed():
    mapping = load_facility_mapping(MAPPING)

    assert {m.facility for m in mapping} == FEED_FACILITIES


def test_repository_mapping_points_at_active_register_areas_with_their_names():
    register = {a["id"]: a for a in json.loads(REGISTER.read_text(encoding="utf-8"))}

    for m in load_facility_mapping(MAPPING):
        area = register[m.register_id]
        assert area["deaktivert"] is None, m.facility
        assert area["aktivVersjon"]["navn"] == m.register_name, m.facility


def test_discrepancies_between_sources_are_noted():
    notes = {m.facility: m.note for m in load_facility_mapping(MAPPING) if m.note}

    assert set(notes) == {"Jernbanen", "Forum"}
    assert "390" in notes["Jernbanen"] and "500" in notes["Jernbanen"]


@pytest.mark.parametrize("field", ["facility", "register_name", "operator_name"])
def test_missing_text_field_is_named(data, field):
    del data["facilities"][0][field]

    assert "facilities[0]" in problems_of(data)
    assert f"{field} is required and must be a non-empty string" in problems_of(data)


@pytest.mark.parametrize("value", [None, 0, "3650", True])
def test_register_id_must_be_a_positive_integer(data, value):
    data["facilities"][0]["register_id"] = value

    assert "register_id is required and must be a positive integer" in problems_of(data)


@pytest.mark.parametrize("key", ["facility", "register_id"])
def test_duplicates_are_rejected(data, key):
    data["facilities"][1][key] = data["facilities"][0][key]

    assert f"{key} {data['facilities'][0][key]!r} is mapped more than once" in problems_of(data)


def test_unknown_field_is_rejected_so_typos_are_caught(data):
    data["facilities"][0]["registerid"] = 1

    assert "unknown field 'registerid'" in problems_of(data)


def test_all_problems_are_reported_at_once(data):
    del data["facilities"][0]["operator_name"]
    data["facilities"][1]["register_id"] = -1

    message = problems_of(data)
    assert "operator_name is required" in message
    assert "register_id is required" in message


@pytest.mark.parametrize("data", [[], {}, {"facilities": []}, {"facilities": [], "x": 1}])
def test_wrong_top_level_shape_fails_clearly(data):
    with pytest.raises(MappingError):
        parse_facility_mapping(copy.deepcopy(data))


def test_missing_file_fails_clearly(tmp_path):
    with pytest.raises(MappingError, match="not found"):
        load_facility_mapping(tmp_path / "facility_mapping.json")


def test_the_default_mapping_is_in_the_config_package():
    """`facilities` finds the folder by path, since `config` imports it; both must agree."""
    assert DEFAULT_MAPPING.parent == CONFIG_DIR
    assert DEFAULT_MAPPING.is_file()
