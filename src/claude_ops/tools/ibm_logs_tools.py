"""Read-only IBM Cloud Logs (DataPrime) tools.

Environment variables:
  IBM_CLOUD_API_KEY   IBM Cloud API key, exchanged for a short-lived IAM token
  IBM_LOGS_ENDPOINT   IBM Cloud Logs API endpoint, e.g.
                       https://<guid>.api.us-south.logs.cloud.ibm.com
                       (an `.ingress.` endpoint is accepted and rewritten to
                       `.api.` automatically)

Only DataPrime `source logs` read queries are issued; there is no ingestion,
tagging, or mutation code path. The API key and bearer token are never
included in returned tool results or error messages.

Prefer these tools over live pod logs (`k8s_get_pod_logs`) for historical
analysis: IBM Cloud Logs entries survive pod restarts, deployments, and
scale-downs, and span all pod incarnations of a service.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from claude_ops.errors import ToolError, ok
from claude_ops.evidence.raw_store import store_raw_evidence
from claude_ops.evidence.summarizers import summarize_log_matches
from claude_ops.tools.http_client import redact_secrets, request_json

_IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
_QUERY_PATH = "/v1/query"
_DEFAULT_QUERY_TIMEOUT_SECONDS = 60.0
_MAX_LIMIT = 500

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _missing_config_error(missing_var: str, attempted: dict[str, Any]) -> dict[str, Any]:
    return ToolError(
        "validation",
        False,
        f"{missing_var} is not set.",
        attempted=attempted,
        alternatives=[
            f"Set the {missing_var} environment variable before querying IBM Cloud Logs",
            "Record this as an unknowns/gap — do not report 'no matching logs' because IBM Cloud Logs could not be queried",
        ],
    ).to_dict()


def _resolve_endpoint() -> str | None:
    raw = os.environ.get("IBM_LOGS_ENDPOINT", "").strip().rstrip("/")
    if not raw:
        return None
    return raw.replace(".ingress.", ".api.")


def _get_iam_token() -> dict[str, Any]:
    """Return ok({"token": str}) or a structured ToolError dict. Caches the token in-process."""
    now = time.time()
    cached = _token_cache["token"]
    if cached and now < float(_token_cache["expires_at"]) - 60:
        return ok({"token": cached})

    api_key = os.environ.get("IBM_CLOUD_API_KEY", "").strip()
    if not api_key:
        return _missing_config_error("IBM_CLOUD_API_KEY", {})

    result = request_json(
        "POST",
        _IAM_TOKEN_URL,
        data={
            "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
            "apikey": api_key,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        timeout=30.0,
        # IBM's IAM endpoint could in principle echo a bad apikey back in an
        # error body; strip it so it never reaches a tool result either way.
        redact=[api_key],
    )
    if result.get("isError"):
        return result

    body = result["data"]
    token = body.get("access_token")
    if not token:
        return ToolError("unknown", False, "IAM token response did not include an access_token").to_dict()

    _token_cache["token"] = token
    _token_cache["expires_at"] = now + int(body.get("expires_in", 3600))
    return ok({"token": token})


def _escape_dp_string(value: str) -> str:
    """Escape a string for use inside a DataPrime single-quoted string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _build_dataprime_query(*, text_query: str, namespace: str, app: str | None, limit: int) -> str:
    lines = [
        "source logs",
        f"| filter $l.applicationname == '{_escape_dp_string(namespace)}'",
    ]
    if app:
        lines.append(f"| filter $l.subsystemname == '{_escape_dp_string(app)}'")
    lines.append(f"| wildfind '{_escape_dp_string(text_query)}'")
    lines.append(f"| limit {int(limit)}")
    return "\n".join(lines)


def _iso8601(ts_seconds: float) -> str:
    return datetime.fromtimestamp(ts_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _read_userdata(entry: dict[str, Any]) -> Any:
    raw = entry.get("user_data") or entry.get("userdata") or entry.get("userData") or entry.get("data") or "{}"
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _extract_text(entry: dict[str, Any]) -> str:
    ud = _read_userdata(entry)
    if isinstance(ud, str):
        return ud
    if not isinstance(ud, dict):
        return str(ud)

    text_obj = ud.get("text")
    if isinstance(text_obj, dict):
        for field in ("message", "msg", "log"):
            if text_obj.get(field):
                return str(text_obj[field])
        return json.dumps(text_obj, separators=(",", ":"))
    if isinstance(text_obj, str) and text_obj:
        return text_obj

    for field in ("message", "msg", "log", "textPayload", "MESSAGE", "short_message"):
        if field in ud and ud[field]:
            return str(ud[field])

    return json.dumps(ud, separators=(",", ":"))


def _extract_timestamp(entry: dict[str, Any]) -> str:
    metadata = entry.get("metadata", [])
    if isinstance(metadata, list):
        meta = {m.get("key"): m.get("value") for m in metadata if isinstance(m, dict) and "key" in m and "value" in m}
        for key in ("timestamp", "Timestamp", "time"):
            if meta.get(key):
                return str(meta[key])

    ud = _read_userdata(entry)
    if isinstance(ud, dict):
        ts = ud.get("timestamp") or ud.get("@timestamp") or ud.get("time")
        if ts:
            return str(ts)

    return ""


def _parse_streaming_lines(lines: Any, limit: int) -> tuple[list[dict[str, str]], str | None]:
    """Parse IBM Cloud Logs `text/event-stream` lines into (entries, api_error).

    Possible lines:
      : success
      data: {"query_id": {...}}
      data: {"warning": {...}}
      data: {"result": {"results": [...]}}
      data: {"error": {...}}
    """
    entries: list[dict[str, str]] = []

    for raw_line in lines:
        if not raw_line:
            continue

        line = raw_line.strip()
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].lstrip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if "error" in obj:
            return entries, str(obj["error"])
        if "warning" in obj:
            continue

        result = obj.get("result")
        if not isinstance(result, dict):
            continue

        results = result.get("results", [])
        if not isinstance(results, list):
            continue

        for result_entry in results:
            if not isinstance(result_entry, dict):
                continue
            entries.append({
                "timestamp": _extract_timestamp(result_entry),
                "text": _extract_text(result_entry),
            })
            if len(entries) >= limit:
                return entries, None

    return entries, None


def _run_dataprime_query(
    *, text_query: str, namespace: str, app: str | None, since_minutes: int, limit: int
) -> dict[str, Any]:
    endpoint = _resolve_endpoint()
    if not endpoint:
        return _missing_config_error("IBM_LOGS_ENDPOINT", {"namespace": namespace, "app": app})

    token_result = _get_iam_token()
    if token_result.get("isError"):
        return token_result
    token = token_result["data"]["token"]

    limit = max(1, min(int(limit), _MAX_LIMIT))
    now_ts = time.time()
    start_ts = now_ts - since_minutes * 60

    dataprime_query = _build_dataprime_query(text_query=text_query, namespace=namespace, app=app, limit=limit)

    payload = {
        "query": dataprime_query,
        "metadata": {
            "startDate": _iso8601(start_ts),
            "endDate": _iso8601(now_ts),
            "syntax": "dataprime",
            "limit": limit,
            "tier": "frequent_search",
        },
    }

    attempted = {
        "namespace": namespace,
        "app": app,
        "since_minutes": since_minutes,
        "limit": limit,
        "dataprime_query": dataprime_query,
    }

    # Values that must never surface in a returned message/partialResults,
    # even if IBM's API echoes request details back in an error body.
    secrets = [os.environ.get("IBM_CLOUD_API_KEY", "").strip(), token]

    def _scrub(text: str | None) -> str | None:
        return redact_secrets(text, secrets)

    try:
        with httpx.stream(
            "POST",
            f"{endpoint}{_QUERY_PATH}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json=payload,
            timeout=_DEFAULT_QUERY_TIMEOUT_SECONDS,
        ) as resp:
            if resp.status_code in (401, 403):
                return ToolError(
                    "permission",
                    False,
                    f"IBM Cloud Logs authorization failed ({resp.status_code})",
                    attempted=attempted,
                    alternatives=[
                        "Verify IBM_CLOUD_API_KEY has access to this IBM Cloud Logs instance",
                        "Verify IBM_LOGS_ENDPOINT points to the correct instance/region",
                        "Request human approval/access if this is expected to be restricted — do not attempt to work around it",
                    ],
                ).to_dict()
            if resp.status_code == 429:
                resp.read()
                return ToolError(
                    "transient",
                    True,
                    "IBM Cloud Logs rate limited the query (HTTP 429)",
                    attempted=attempted,
                    partialResults=_scrub(resp.text[:500]),
                    alternatives=[
                        "Wait and retry with backoff",
                        "Reduce since_minutes or limit to lower query cost",
                        "Avoid issuing many searches in rapid succession",
                    ],
                ).to_dict()
            if resp.status_code >= 400:
                resp.read()
                is_server_error = resp.status_code >= 500
                alternatives = (
                    [
                        "Retry after a short delay",
                        "If this persists, treat IBM Cloud Logs as temporarily unavailable — this is a gap, not zero matching logs",
                    ]
                    if is_server_error
                    else [
                        "Check namespace/app/query parameters",
                        "Narrow since_minutes or limit if the request was rejected as too large",
                    ]
                )
                return ToolError(
                    "transient" if is_server_error else "validation",
                    is_server_error,
                    f"IBM Cloud Logs query failed with HTTP {resp.status_code}",
                    attempted=attempted,
                    partialResults=_scrub(resp.text[:500]),
                    alternatives=alternatives,
                ).to_dict()
            entries, api_error = _parse_streaming_lines(resp.iter_lines(), limit)
    except httpx.TimeoutException:
        return ToolError(
            "transient",
            True,
            "IBM Cloud Logs query timed out",
            attempted=attempted,
            alternatives=["Retry", "Narrow since_minutes or limit"],
        ).to_dict()
    except httpx.RequestError as exc:
        return ToolError(
            "transient",
            True,
            _scrub(f"Network error querying IBM Cloud Logs: {exc}"),
            attempted=attempted,
            alternatives=["Retry", "Check IBM_LOGS_ENDPOINT configuration"],
        ).to_dict()

    if api_error:
        return ToolError(
            "unknown", False, _scrub(f"IBM Cloud Logs API error: {api_error}"), attempted=attempted
        ).to_dict()

    entries.sort(key=lambda entry: entry["timestamp"])
    return ok({"entries": entries, "dataprime_query": dataprime_query})


def _store_log_evidence(
    *, entries: list[dict[str, str]], dataprime_query: str, label: str, metadata: dict[str, Any]
) -> dict[str, Any]:
    summary = summarize_log_matches(entries, label=label)
    record = store_raw_evidence(
        content_type="ibm_logs.search_result",
        raw={"entries": entries, "dataprime_query": dataprime_query},
        summary=summary,
        metadata=metadata,
    )
    return ok(record.to_dict())


def _search(
    *, namespace: str, text_query: str, app: str | None, since_minutes: int, limit: int, search_kind: str
) -> dict[str, Any]:
    result = _run_dataprime_query(text_query=text_query, namespace=namespace, app=app, since_minutes=since_minutes, limit=limit)
    if result.get("isError"):
        return result

    scope = f"{namespace}/{app}" if app else namespace
    return _store_log_evidence(
        entries=result["data"]["entries"],
        dataprime_query=result["data"]["dataprime_query"],
        label=f"IBM Cloud Logs {search_kind} search '{text_query}' in {scope} over last {since_minutes}m",
        metadata={
            "namespace": namespace,
            "app": app,
            "query": text_query,
            "since_minutes": since_minutes,
            "search_kind": search_kind,
        },
    )


def ibm_logs_search(namespace: str, query: str, app: str | None = None, since_minutes: int = 60, limit: int = 200) -> dict[str, Any]:
    """Search IBM Cloud Logs for a plain-text keyword within a namespace/app/time window."""
    return _search(namespace=namespace, text_query=query, app=app, since_minutes=since_minutes, limit=limit, search_kind="keyword")


def ibm_logs_search_errors(namespace: str, app: str, since_minutes: int = 60, limit: int = 200) -> dict[str, Any]:
    """Search IBM Cloud Logs for ERROR-level log lines for a specific app."""
    return _search(namespace=namespace, text_query="ERROR", app=app, since_minutes=since_minutes, limit=limit, search_kind="errors")


def ibm_logs_search_probe_failures(namespace: str, app: str, since_minutes: int = 60, limit: int = 200) -> dict[str, Any]:
    """Search IBM Cloud Logs for liveness/readiness probe failure log lines for a specific app."""
    return _search(
        namespace=namespace, text_query="probe failed", app=app, since_minutes=since_minutes, limit=limit, search_kind="probe_failures"
    )


def ibm_logs_search_text(namespace: str, app: str, text: str, since_minutes: int = 60, limit: int = 200) -> dict[str, Any]:
    """Search IBM Cloud Logs for an arbitrary plain-text pattern scoped to a specific app."""
    return _search(namespace=namespace, text_query=text, app=app, since_minutes=since_minutes, limit=limit, search_kind="text")
