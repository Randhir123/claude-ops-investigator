from __future__ import annotations

from typing import Any

import httpx

from claude_ops.errors import ToolError, ok


def request_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Make a read-only HTTP request and return `ok(json)` or a structured ToolError dict.

    Shared by prometheus_tools and ibm_logs_tools so both map transport and
    HTTP failures onto the same errorCategory conventions used elsewhere in
    this project instead of each reimplementing it.
    """
    try:
        resp = httpx.request(
            method,
            url,
            params=params,
            json=json_body,
            data=data,
            headers=headers,
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return ToolError(
            "transient",
            True,
            f"Request to {url} timed out after {timeout}s",
            attempted={"url": url, "method": method},
            alternatives=["Retry", "Narrow the query or time window"],
        ).to_dict()
    except httpx.RequestError as exc:
        return ToolError(
            "transient",
            True,
            f"Network error calling {url}: {exc}",
            attempted={"url": url, "method": method},
            alternatives=["Retry", "Check network/endpoint configuration"],
        ).to_dict()

    if resp.status_code in (401, 403):
        return ToolError(
            "permission",
            False,
            f"Authentication/authorization failed ({resp.status_code}) calling {url}",
            attempted={"url": url, "method": method},
            partialResults=resp.text[:500],
            alternatives=["Verify API credentials", "Request human approval if this is expected to be restricted"],
        ).to_dict()

    if resp.status_code >= 400:
        is_server_error = resp.status_code >= 500
        return ToolError(
            "transient" if is_server_error else "validation",
            is_server_error,
            f"Request to {url} failed with HTTP {resp.status_code}",
            attempted={"url": url, "method": method},
            partialResults=resp.text[:500],
            alternatives=["Check request parameters", "Retry later if this is a server-side error"],
        ).to_dict()

    try:
        return ok(resp.json())
    except Exception as exc:
        return ToolError(
            "unknown",
            False,
            f"Failed to parse JSON response from {url}: {exc}",
            attempted={"url": url, "method": method},
            partialResults=resp.text[:500],
        ).to_dict()
