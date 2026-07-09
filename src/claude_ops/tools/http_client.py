from __future__ import annotations

from typing import Any, Iterable

import httpx

from claude_ops.errors import ToolError, ok


def redact_secrets(text: str | None, secrets: Iterable[str] | None) -> str | None:
    """Replace any occurrence of a known secret value in `text` with a marker.

    Upstream APIs occasionally echo request parameters — including a bad API
    key or token — back in an error body. Callers pass the credential
    values they sent (e.g. an API key, a bearer token) so those exact
    strings never reach a tool result, even indirectly via `partialResults`.
    """
    if text is None or not secrets:
        return text
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***REDACTED***")
    return text


def request_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
    redact: list[str] | None = None,
) -> dict[str, Any]:
    """Make a read-only HTTP request and return `ok(json)` or a structured ToolError dict.

    Shared by prometheus_tools and ibm_logs_tools so both map transport and
    HTTP failures onto the same errorCategory conventions used elsewhere in
    this project instead of each reimplementing it.

    `redact`, if given, is a list of secret values (API keys, tokens) to
    strip from any error `message`/`partialResults` before it's returned —
    see `redact_secrets`.
    """

    def _error(category: str, is_retryable: bool, message: str, **kwargs: Any) -> dict[str, Any]:
        safe_message = redact_secrets(message, redact) or message
        err = ToolError(category, is_retryable, safe_message, **kwargs).to_dict()
        if isinstance(err.get("partialResults"), str):
            err["partialResults"] = redact_secrets(err["partialResults"], redact)
        return err

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
        return _error(
            "transient",
            True,
            f"Request to {url} timed out after {timeout}s",
            attempted={"url": url, "method": method},
            alternatives=["Retry", "Narrow the query or time window"],
        )
    except httpx.RequestError as exc:
        return _error(
            "transient",
            True,
            f"Network error calling {url}: {exc}",
            attempted={"url": url, "method": method},
            alternatives=["Retry", "Check network/endpoint configuration"],
        )

    if resp.status_code in (401, 403):
        return _error(
            "permission",
            False,
            f"Authentication/authorization failed ({resp.status_code}) calling {url}",
            attempted={"url": url, "method": method},
            partialResults=resp.text[:500],
            alternatives=["Verify API credentials", "Request human approval if this is expected to be restricted"],
        )

    if resp.status_code == 429:
        headers_obj = getattr(resp, "headers", None) or {}
        retry_after = headers_obj.get("Retry-After") if hasattr(headers_obj, "get") else None
        alternatives = [
            "Wait and retry with backoff",
            "Reduce query frequency or narrow the request (time window, result count) to lower load",
        ]
        if retry_after:
            alternatives.insert(0, f"Retry after {retry_after}s (Retry-After header)")
        return _error(
            "transient",
            True,
            f"Request to {url} was rate limited (HTTP 429)",
            attempted={"url": url, "method": method},
            partialResults=resp.text[:500],
            alternatives=alternatives,
        )

    if resp.status_code >= 400:
        is_server_error = resp.status_code >= 500
        alternatives = (
            [
                "Retry after a short delay",
                "If this persists, treat the backend as temporarily unavailable — this is a gap, not zero/normal data",
            ]
            if is_server_error
            else ["Check request parameters"]
        )
        return _error(
            "transient" if is_server_error else "validation",
            is_server_error,
            f"Request to {url} failed with HTTP {resp.status_code}",
            attempted={"url": url, "method": method},
            partialResults=resp.text[:500],
            alternatives=alternatives,
        )

    try:
        return ok(resp.json())
    except Exception as exc:
        return _error(
            "unknown",
            False,
            f"Failed to parse JSON response from {url}: {exc}",
            attempted={"url": url, "method": method},
            partialResults=resp.text[:500],
        )
