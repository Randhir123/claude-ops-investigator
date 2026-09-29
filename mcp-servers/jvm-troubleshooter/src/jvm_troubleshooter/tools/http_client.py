"""Minimal read-only HTTP client for the Prometheus HTTP API.

Only `GET /api/v1/query` and `GET /api/v1/query_range` are ever called by
this project -- there is no code path here that can mutate Prometheus or
anything it monitors.
"""

from __future__ import annotations

from typing import Any

import httpx

from jvm_troubleshooter.errors import ToolError, ok


def request_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Make a read-only HTTP request and return `ok(json)` or a structured ToolError dict."""

    try:
        resp = httpx.request(method, url, params=params, timeout=timeout)
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
                "If it's a Grafana-proxied datasource URL rather than a direct Prometheus endpoint, "
                "point PROMETHEUS_URL at the real Prometheus/Thanos API instead",
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
        ).to_dict()
