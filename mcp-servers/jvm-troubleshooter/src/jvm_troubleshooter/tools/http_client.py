"""Minimal read-only HTTP client for the Prometheus HTTP API.

Only `GET /api/v1/query` and `GET /api/v1/query_range` are ever called by
this project -- there is no code path here that can mutate Prometheus or
anything it monitors.
"""

from __future__ import annotations

from typing import Any, Iterable

import httpx

from jvm_troubleshooter.errors import ToolError, ok


def _redact(text: str, secrets: Iterable[str] | None) -> str:
    for secret in secrets or ():
        if secret:
            text = text.replace(secret, "***REDACTED***")
    return text


def request_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    redact: list[str] | None = None,
) -> dict[str, Any]:
    """Make a read-only HTTP request and return `ok(json)` or a structured ToolError dict.

    `redact` lists credential values (a Grafana token/session cookie) that must
    never appear in a returned error's `message` or `partialResults`.
    """
    result = _request_json(method, url, params=params, headers=headers, timeout=timeout)
    if result.get("isError") and redact:
        result["message"] = _redact(result["message"], redact)
        if isinstance(result.get("partialResults"), str):
            result["partialResults"] = _redact(result["partialResults"], redact)
    return result


def _request_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None,
    timeout: float,
) -> dict[str, Any]:
    try:
        resp = httpx.request(method, url, params=params, headers=headers, timeout=timeout)
    except httpx.TimeoutException:
        return ToolError(
            "transient",
            True,
            f"Request to {url} timed out after {timeout}s",
            attempted={"url": url, "method": method, "params": params},
            alternatives=["Retry", "Narrow the query or time window"],
        ).to_dict()
    except httpx.RequestError as exc:
        return ToolError(
            "transient",
            True,
            f"Network error calling {url}: {exc}",
            attempted={"url": url, "method": method, "params": params},
            alternatives=[
                "Retry",
                "Check PROMETHEUS_URL configuration and network reachability from wherever this server runs",
            ],
        ).to_dict()

    if resp.status_code in (401, 403):
        return ToolError(
            "permission",
            False,
            f"Authentication/authorization failed ({resp.status_code}) calling {url}",
            attempted={"url": url, "method": method, "params": params},
            partialResults=resp.text[:500],
            alternatives=[
                "Verify this Prometheus endpoint doesn't require auth this client isn't configured for",
                "To query through Grafana, set GRAFANA_URL/GRAFANA_DATASOURCE_UID plus GRAFANA_API_TOKEN or "
                "GRAFANA_SESSION_COOKIE rather than pointing PROMETHEUS_URL at a Grafana URL; "
                "if already in Grafana mode, the credential may have expired",
            ],
        ).to_dict()

    if resp.status_code == 429:
        return ToolError(
            "transient",
            True,
            f"Request to {url} was rate limited (HTTP 429)",
            attempted={"url": url, "method": method, "params": params},
            partialResults=resp.text[:500],
            alternatives=["Wait and retry with backoff", "Narrow the query or time window"],
        ).to_dict()

    if resp.status_code >= 400:
        is_server_error = resp.status_code >= 500
        return ToolError(
            "transient" if is_server_error else "validation",
            is_server_error,
            f"Request to {url} failed with HTTP {resp.status_code}",
            attempted={"url": url, "method": method, "params": params},
            partialResults=resp.text[:500],
            alternatives=(
                ["Retry after a short delay", "Treat as a gap, not zero/normal data, if this persists"]
                if is_server_error
                else ["Check PromQL syntax and label names -- see partialResults for Prometheus's parse error"]
            ),
        ).to_dict()

    try:
        return ok(resp.json())
    except Exception as exc:  # noqa: BLE001 - surfacing as a structured tool error, not raising
        return ToolError(
            "unknown",
            False,
            f"Failed to parse JSON response from {url}: {exc}",
            attempted={"url": url, "method": method, "params": params},
            partialResults=resp.text[:500],
            alternatives=[
                "A non-JSON response usually means a login page or proxy answered instead of Prometheus -- "
                "in Grafana mode, refresh GRAFANA_SESSION_COOKIE or switch to GRAFANA_API_TOKEN",
            ],
        ).to_dict()
