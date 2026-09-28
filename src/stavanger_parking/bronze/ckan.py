"""Resolve a dataset's download URL through the CKAN API.

The download link is looked up with `package_show` on every run instead of being hardcoded, so a
re-created resource with a new id does not break ingestion.
"""

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

DEFAULT_TIMEOUT = 10.0


def make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """HTTP client with the policy for talking to the source: a timeout and following redirects.

    Failed calls are not retried; the next scheduled run is the retry.
    """
    return httpx.Client(timeout=timeout, follow_redirects=True)


class CkanError(RuntimeError):
    """The CKAN API could not be used to find the resource."""


@dataclass(frozen=True)
class Resource:
    id: str
    name: str
    format: str
    url: str


def select_resource(package: dict, fmt: str) -> Resource:
    """Pick the single resource of the given format from a `package_show` result."""
    package_name = package.get("name", "<unknown>")
    resources = package.get("resources")
    if not isinstance(resources, list):
        raise CkanError(f"Package '{package_name}' has no resource list in the API response")

    matches = [r for r in resources if str(r.get("format", "")).strip().lower() == fmt.lower()]
    if not matches:
        available = ", ".join(sorted({str(r.get("format", "?")) for r in resources})) or "none"
        raise CkanError(
            f"Package '{package_name}' has no {fmt.upper()} resource "
            f"(available formats: {available})"
        )
    if len(matches) > 1:
        found = "; ".join(f"{r.get('name')} ({r.get('id')})" for r in matches)
        raise CkanError(
            f"Package '{package_name}' has {len(matches)} {fmt.upper()} resources, "
            f"expected exactly one: {found}"
        )

    resource = matches[0]
    url = resource.get("url") or ""
    if urlparse(url).scheme != "https":
        raise CkanError(f"Resource '{resource.get('id')}' has no https download URL: {url!r}")

    return Resource(
        id=resource["id"],
        name=resource.get("name", ""),
        format=str(resource["format"]).strip().upper(),
        url=url,
    )


def fetch_package(base_url: str, package_id: str, client: httpx.Client) -> dict:
    """Call `package_show` and return the package, failing clearly on any API problem."""
    endpoint = f"{base_url.rstrip('/')}/api/3/action/package_show"
    try:
        response = client.get(endpoint, params={"id": package_id})
        response.raise_for_status()
        body = response.json()
    except httpx.TimeoutException as e:
        raise CkanError(f"CKAN request timed out: {endpoint}?id={package_id}") from e
    except httpx.HTTPStatusError as e:
        raise CkanError(
            f"CKAN returned HTTP {e.response.status_code} for {endpoint}?id={package_id}"
        ) from e
    except httpx.HTTPError as e:
        raise CkanError(f"CKAN request failed: {endpoint}?id={package_id}: {e}") from e
    except ValueError as e:
        raise CkanError(f"CKAN returned a response that is not JSON: {endpoint}") from e

    if not isinstance(body, dict) or body.get("success") is not True:
        error = body.get("error") if isinstance(body, dict) else None
        raise CkanError(f"CKAN package_show failed for '{package_id}': {error or body!r}")
    if not isinstance(body.get("result"), dict):
        raise CkanError(f"CKAN package_show for '{package_id}' returned no result object")
    return body["result"]


def resolve_resource(base_url: str, package_id: str, fmt: str, client: httpx.Client) -> Resource:
    """Find the package's single resource in the given format, including its download URL."""
    return select_resource(fetch_package(base_url, package_id, client), fmt)
