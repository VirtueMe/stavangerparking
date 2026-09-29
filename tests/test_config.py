import copy
import json
from pathlib import Path

import httpx
import pytest

from stavanger_parking.bronze.ckan import resolve_resource
from stavanger_parking.config import ConfigError, HttpLocation, load_sources, parse_sources

REPO_CONFIG = Path(__file__).parent.parent / "config" / "sources.json"
PACKAGE_SHOW = Path(__file__).parent / "fixtures" / "ckan_package_show_stavanger_parkering.json"


@pytest.fixture
def config() -> dict:
    return json.loads(REPO_CONFIG.read_text(encoding="utf-8"))


def parking():
    return next(s for s in load_sources(REPO_CONFIG) if s.id == "stavanger_parking")


def problems_of(data) -> str:
    with pytest.raises(ConfigError) as e:
        parse_sources(data)
    return str(e.value)


def test_repository_config_is_valid():
    assert [s.id for s in load_sources(REPO_CONFIG)] == [
        "stavanger_parking",
        "parkeringsregisteret",
    ]
    source = parking()

    assert source.id == "stavanger_parking"
    assert source.location.package_id == "stavanger-parkering"
    assert source.bronze_table == "bronze_parking"
    assert source.licence.publisher == "Stavanger kommune"


def test_config_feeds_the_ckan_resolver():
    source = parking()
    package_show = json.loads(PACKAGE_SHOW.read_text(encoding="utf-8"))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=package_show))
    )

    resource = resolve_resource(
        source.location.base_url, source.location.package_id, source.location.format, client
    )

    assert resource.url.endswith("/download/parking.json")


def test_second_source_needs_only_a_config_entry(config):
    second = copy.deepcopy(config["sources"][0])
    second.update(id="sandnes_parking", bronze_table="bronze_sandnes_parking")
    second["ckan"]["package_id"] = "sandnes-parkering"
    second["raw_path"] = "bronze/sandnes_parking/{yyyy}/{mm}/{dd}/{HHmmss}.json"
    config["sources"].append(second)

    sources = parse_sources(config)

    assert [s.id for s in sources][-1] == "sandnes_parking"
    assert sources[-1].location.package_id == "sandnes-parkering"


@pytest.mark.parametrize("field", ["id", "raw_path", "bronze_table", "licence"])
def test_missing_source_field_is_named(config, field):
    del config["sources"][0][field]

    assert "sources[0]" in problems_of(config)
    assert f"{field} is required" in problems_of(config)


@pytest.mark.parametrize(
    "section, field",
    [("ckan", "base_url"), ("ckan", "package_id"), ("ckan", "format"), ("licence", "url")],
)
def test_missing_nested_field_is_named_with_its_path(config, section, field):
    del config["sources"][0][section][field]

    assert f"sources[0] (stavanger_parking): {section}.{field} is required" in problems_of(config)


def test_empty_string_counts_as_missing(config):
    config["sources"][0]["ckan"]["package_id"] = "  "

    assert "ckan.package_id is required" in problems_of(config)


def test_unknown_field_is_rejected_so_typos_are_caught(config):
    config["sources"][0]["ckan"]["packge_id"] = "typo"

    assert "unknown field 'ckan.packge_id'" in problems_of(config)


@pytest.mark.parametrize("path", ["licence.url", "ckan.base_url"])
def test_urls_must_be_https(config, path):
    section, field = path.split(".")
    config["sources"][0][section][field] = "http://example.org"

    assert f"{path} must be an https URL" in problems_of(config)


@pytest.mark.parametrize("raw_path", ["/bronze/{yyyy}.json", "../outside/{yyyy}.json"])
def test_raw_path_must_stay_under_the_storage_root(config, raw_path):
    config["sources"][0]["raw_path"] = raw_path

    assert "raw_path must be relative to the storage root" in problems_of(config)


def test_raw_path_rejects_unknown_placeholders(config):
    config["sources"][0]["raw_path"] = "bronze/{year}/{HHmmss}.json"

    assert "unknown placeholders ['year']" in problems_of(config)


def test_identifiers_must_be_safe_names(config):
    config["sources"][0]["bronze_table"] = "bronze-parking"
    config["sources"][0]["id"] = "Stavanger"

    message = problems_of(config)
    assert "bronze_table must be lowercase letters" in message
    assert "id must be lowercase letters" in message


def test_duplicate_source_ids_are_rejected(config):
    config["sources"].append(copy.deepcopy(config["sources"][0]))

    assert "source id 'stavanger_parking' is used more than once" in problems_of(config)


def test_all_problems_are_reported_at_once(config):
    del config["sources"][0]["bronze_table"]
    config["sources"][0]["ckan"]["base_url"] = "http://opencom.no"

    message = problems_of(config)
    assert "bronze_table is required" in message
    assert "ckan.base_url must be an https URL" in message


@pytest.mark.parametrize("data", [[], {"sources": []}, {"sources": "x"}, {"sources": ["x"]}])
def test_wrong_top_level_shape_fails_clearly(data):
    with pytest.raises(ConfigError):
        parse_sources(data)


def test_missing_file_fails_clearly(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_sources(tmp_path / "sources.json")


def test_invalid_json_fails_with_position(tmp_path):
    broken = tmp_path / "sources.json"
    broken.write_text('{"sources": [', encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid JSON.*line 1"):
        load_sources(broken)


def test_repository_config_has_the_adr_003_polling_policy():
    source = parking()

    assert source.polling.fast_interval_minutes == 5
    assert source.polling.slow_interval_minutes == 20
    assert source.polling.unchanged_snapshots_for_slow == 5
    assert source.polling.change_ignores_fields == ("Dato", "Klokkeslett")


@pytest.mark.parametrize("value", [0, -5, "5", 2.5, True, None])
def test_polling_intervals_must_be_positive_integers(config, value):
    config["sources"][0]["polling"]["fast_interval_minutes"] = value

    assert "polling.fast_interval_minutes is required and must be a positive integer" in (
        problems_of(config)
    )


def test_slow_interval_cannot_be_shorter_than_fast(config):
    config["sources"][0]["polling"]["slow_interval_minutes"] = 2

    assert "slow_interval_minutes must not be shorter than" in problems_of(config)


@pytest.mark.parametrize("value", ["Dato", [""], [1], None])
def test_change_ignores_fields_must_be_a_list_of_names(config, value):
    config["sources"][0]["polling"]["change_ignores_fields"] = value

    assert "polling.change_ignores_fields is required and must be a list of names" in (
        problems_of(config)
    )


def test_polling_and_freshness_are_optional(config):
    del config["sources"][0]["polling"]
    del config["sources"][0]["freshness"]

    source = parse_sources(config)[0]

    assert (source.polling, source.freshness) == (None, None)


def test_a_polling_section_must_be_an_object(config):
    config["sources"][0]["polling"] = 5

    assert "polling is required and must be an object" in problems_of(config)


def test_repository_register_source_is_fetched_by_url_and_not_polled():
    register = next(s for s in load_sources(REPO_CONFIG) if s.id == "parkeringsregisteret")

    assert isinstance(register.location, HttpLocation)
    assert register.location.url.startswith("https://parkreg-open.atlas.vegvesen.no/")
    assert "orgnr=974782766" in register.location.url
    assert (register.polling, register.freshness) == (None, None)
    assert register.licence.publisher == "Statens vegvesen"


@pytest.mark.parametrize("sections", [(), ("ckan", "http")])
def test_a_source_has_exactly_one_of_ckan_and_http(config, sections):
    entry = config["sources"][0]
    del entry["ckan"]
    for section in sections:
        entry[section] = {"url": "https://example.org"} if section == "http" else {}

    assert "exactly one of ckan and http is required" in problems_of(config)


def test_an_http_url_must_be_https(config):
    config["sources"][1]["http"]["url"] = "http://example.org/data.json"

    assert "http.url must be an https URL" in problems_of(config)


def test_unknown_http_field_is_rejected(config):
    config["sources"][1]["http"]["uri"] = "https://example.org"

    assert "unknown field 'http.uri'" in problems_of(config)


def test_repository_config_has_a_staleness_threshold():
    source = parking()

    assert source.freshness.stale_after_minutes == 15


@pytest.mark.parametrize("value", [0, -1, "15", 1.5, False, None])
def test_stale_after_minutes_must_be_a_positive_integer(config, value):
    config["sources"][0]["freshness"]["stale_after_minutes"] = value

    assert "freshness.stale_after_minutes is required and must be a positive integer" in (
        problems_of(config)
    )


def test_unknown_freshness_field_is_named(config):
    config["sources"][0]["freshness"]["stale_after"] = 15

    assert "unknown field 'freshness.stale_after'" in problems_of(config)


@pytest.mark.parametrize(
    "raw_path",
    [
        "bronze/{dd}/{mm}/{yyyy}/{HHmmss}.json",  # paths would not sort by time
        "bronze/{yyyy}/{mm}/{dd}.json",  # snapshots on the same day would collide
        "bronze/{yyyy}/{mm}/{dd}/{HHmmss}-{HHmmss}.json",
    ],
)
def test_raw_path_placeholders_must_appear_once_in_time_order(config, raw_path):
    config["sources"][0]["raw_path"] = raw_path

    assert "once each and in that order, so that paths sort by time" in problems_of(config)
