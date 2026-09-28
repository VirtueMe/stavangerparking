import copy
import json
from pathlib import Path

import httpx
import pytest

from stavanger_parking.bronze.ckan import (
    DEFAULT_TIMEOUT,
    CkanError,
    make_client,
    resolve_resource,
    select_resource,
)

BASE_URL = "https://opencom.no"
PACKAGE_ID = "stavanger-parkering"
FIXTURE = Path(__file__).parent.parent / "fixtures" / "ckan_package_show_stavanger_parkering.json"


@pytest.fixture
def package_show() -> dict:
    # Real package_show response from opencom.no, trimmed to the fields in use
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def client_returning(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def json_client(body, status=200) -> httpx.Client:
    return client_returning(lambda request: httpx.Response(status, json=body))


def test_resolves_json_resource_from_real_response(package_show):
    resource = resolve_resource(BASE_URL, PACKAGE_ID, "json", json_client(package_show))

    assert resource.id == "d1bdc6eb-9b49-4f24-89c2-ab9f5ce2acce"
    assert resource.format == "JSON"
    assert resource.url.endswith("/download/parking.json")


def test_calls_package_show_with_package_id(package_show):
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json=package_show)

    resolve_resource(BASE_URL + "/", PACKAGE_ID, "json", client_returning(handler))

    assert str(seen[0]) == "https://opencom.no/api/3/action/package_show?id=stavanger-parkering"


def test_changed_resource_id_is_followed(package_show):
    changed = copy.deepcopy(package_show)
    json_resource = changed["result"]["resources"][0]
    json_resource["id"] = "00000000-new-resource-id"
    json_resource["url"] = (
        "https://opencom.no/dataset/x/resource/00000000-new-resource-id/download/p.json"
    )

    resource = resolve_resource(BASE_URL, PACKAGE_ID, "json", json_client(changed))

    assert resource.id == "00000000-new-resource-id"
    assert "00000000-new-resource-id" in resource.url


def test_format_matching_ignores_case_and_records_the_resource_format(package_show):
    package_show["result"]["resources"][0]["format"] = " json "

    resource = select_resource(package_show["result"], "Json")

    assert resource.id.startswith("d1bdc6eb")
    assert resource.format == "JSON"


def test_client_policy_has_timeout_and_follows_redirects():
    with make_client() as client:
        assert client.timeout == httpx.Timeout(DEFAULT_TIMEOUT)
        assert client.follow_redirects is True


def test_missing_json_resource_fails_clearly(package_show):
    package_show["result"]["resources"] = [
        r for r in package_show["result"]["resources"] if r["format"] != "JSON"
    ]

    with pytest.raises(CkanError, match=r"no JSON resource \(available formats: CSV\)"):
        resolve_resource(BASE_URL, PACKAGE_ID, "json", json_client(package_show))


def test_ambiguous_json_resources_fail_clearly(package_show):
    duplicate = dict(package_show["result"]["resources"][0], id="second-json", name="Copy")
    package_show["result"]["resources"].append(duplicate)

    with pytest.raises(CkanError, match=r"2 JSON resources, expected exactly one.*second-json"):
        resolve_resource(BASE_URL, PACKAGE_ID, "json", json_client(package_show))


def test_non_https_url_fails(package_show):
    package_show["result"]["resources"][0]["url"] = "http://opencom.no/parking.json"

    with pytest.raises(CkanError, match="no https download URL"):
        select_resource(package_show["result"], "json")


def test_http_error_fails_clearly():
    with pytest.raises(CkanError, match="HTTP 503"):
        resolve_resource(BASE_URL, PACKAGE_ID, "json", json_client({}, status=503))


def test_timeout_fails_clearly():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(CkanError, match="timed out"):
        resolve_resource(BASE_URL, PACKAGE_ID, "json", client_returning(handler))


def test_connection_error_fails_clearly():
    def handler(request):
        raise httpx.ConnectError("name resolution failed", request=request)

    with pytest.raises(CkanError, match="request failed"):
        resolve_resource(BASE_URL, PACKAGE_ID, "json", client_returning(handler))


def test_success_false_fails_with_api_error():
    body = {"success": False, "error": {"message": "Not found", "__type": "Not Found Error"}}

    with pytest.raises(CkanError, match="package_show failed.*Not found"):
        resolve_resource(BASE_URL, PACKAGE_ID, "json", json_client(body))


def test_non_json_response_fails_clearly():
    client = client_returning(lambda request: httpx.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(CkanError, match="not JSON"):
        resolve_resource(BASE_URL, PACKAGE_ID, "json", client)


@pytest.mark.parametrize(
    "body",
    [{"success": True}, {"success": True, "result": {"name": "stavanger-parkering"}}, []],
)
def test_unexpected_response_shape_fails_clearly(body):
    with pytest.raises(CkanError):
        resolve_resource(BASE_URL, PACKAGE_ID, "json", json_client(body))
