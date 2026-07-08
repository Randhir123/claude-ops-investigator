"""Read-only Prometheus tools.

Environment variables:
  PROMETHEUS_URL   base URL of the Prometheus server, e.g. http://prometheus:9090

Only the `/api/v1/query` and `/api/v1/query_range` read endpoints are used.
There is no code path in this module that can mutate Prometheus or cluster
state.

Prefer the typed `prom_get_*` tools, which build bounded PromQL internally.
`prom_query_instant` exists for cases the typed tools don't cover, and is
guarded against obviously huge/unbounded queries.
"""

from __future__ import annotations

import os
import re
from typing import Any

from claude_ops.errors import ToolError, ok
from claude_ops.evidence.raw_store import store_raw_evidence
from claude_ops.evidence.summarizers import summarize_prometheus_result
from claude_ops.tools.http_client import request_json

_QUERY_PATH = "/api/v1/query"
_QUERY_RANGE_PATH = "/api/v1/query_range"
_DEFAULT_TIMEOUT_SECONDS = 20.0

_MAX_PROMQL_LENGTH = 1000
_MAX_RANGE_DAYS = 7
_MAX_SINCE_MINUTES = 1440  # cap the rate windows built by typed tools at 24h

# Matches range-vector/offset durations like [30d], [4w], [400h] embedded in a
# PromQL expression. This is intentionally simple: PromQL grammar validation
# is Prometheus's job, this is only a guard against obviously
# unbounded/expensive queries before we send them out.
_DURATION_RE = re.compile(r"\[(\d+)([smhdwy])\]")
_SECONDS_PER_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}


def _prometheus_base_url() -> str | None:
    url = os.environ.get("PROMETHEUS_URL", "").strip()
    return url.rstrip("/") or None


_PREFLIGHT_HINT = (
    "Call prom_ensure_connection to check Prometheus reachability "
    "(and optionally start a local kubectl port-forward if PROMETHEUS_AUTO_PORT_FORWARD=true)"
)


def _missing_config_error(attempted: dict[str, Any]) -> dict[str, Any]:
    return ToolError(
        "validation",
        False,
        "PROMETHEUS_URL is not set.",
        attempted=attempted,
        alternatives=[
            "Set the PROMETHEUS_URL environment variable to the Prometheus base URL, e.g. http://prometheus:9090",
            _PREFLIGHT_HINT,
        ],
    ).to_dict()


def _with_preflight_hint(result: dict[str, Any]) -> dict[str, Any]:
    """Append a prom_ensure_connection hint to transient (likely connectivity) errors.

    This only ever *suggests* the preflight tool — prometheus_tools.py never
    calls ensure_prometheus()/starts a port-forward itself.
    """
    if result.get("isError") and result.get("errorCategory") == "transient":
        alternatives = list(result.get("alternatives") or [])
        if _PREFLIGHT_HINT not in alternatives:
            alternatives.append(_PREFLIGHT_HINT)
        result = {**result, "alternatives": alternatives}
    return result


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _validate_promql(promql: str) -> dict[str, Any] | None:
    if not promql or not promql.strip():
        return ToolError("validation", False, "promql cannot be empty").to_dict()

    if len(promql) > _MAX_PROMQL_LENGTH:
        return ToolError(
            "validation",
            False,
            f"promql exceeds max length of {_MAX_PROMQL_LENGTH} characters",
            attempted={"promql_length": len(promql)},
            alternatives=["Use a narrower/shorter query", "Use one of the typed prom_get_* tools instead"],
        ).to_dict()

    for value, unit in _DURATION_RE.findall(promql):
        seconds = int(value) * _SECONDS_PER_UNIT[unit]
        if seconds > _MAX_RANGE_DAYS * 86400:
            return ToolError(
                "validation",
                False,
                f"promql range duration [{value}{unit}] exceeds the {_MAX_RANGE_DAYS}-day cap",
                attempted={"promql": promql},
                alternatives=[f"Narrow the range to {_MAX_RANGE_DAYS}d or less"],
            ).to_dict()

    return None


def _instant_query(promql: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    base_url = _prometheus_base_url()
    if not base_url:
        return _missing_config_error({"promql": promql})

    invalid = _validate_promql(promql)
    if invalid is not None:
        return invalid

    return _with_preflight_hint(
        request_json("GET", f"{base_url}{_QUERY_PATH}", params={"query": promql}, timeout=timeout)
    )


def _range_query(
    promql: str,
    *,
    start: str,
    end: str,
    step: str = "60s",
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    base_url = _prometheus_base_url()
    if not base_url:
        return _missing_config_error({"promql": promql, "start": start, "end": end})

    invalid = _validate_promql(promql)
    if invalid is not None:
        return invalid

    return _with_preflight_hint(
        request_json(
            "GET",
            f"{base_url}{_QUERY_RANGE_PATH}",
            params={"query": promql, "start": start, "end": end, "step": step},
            timeout=timeout,
        )
    )


def _clamp_since_minutes(since_minutes: int) -> int:
    return max(1, min(int(since_minutes), _MAX_SINCE_MINUTES))


def _store_prometheus_evidence(*, promql: str, raw: Any, label: str, metadata: dict[str, Any]) -> dict[str, Any]:
    summary = summarize_prometheus_result(raw, label=label)
    record = store_raw_evidence(
        content_type="prometheus.query_result",
        raw=raw,
        summary=summary,
        metadata={**metadata, "promql": promql},
    )
    return ok(record.to_dict())


def prom_query_instant(promql: str) -> dict[str, Any]:
    """Run a read-only, bounded instant PromQL query and store the raw result as evidence."""
    result = _instant_query(promql)
    if result.get("isError"):
        return result
    return _store_prometheus_evidence(
        promql=promql,
        raw=result["data"],
        label=f"PromQL instant query: {promql}",
        metadata={"query_type": "instant"},
    )


def prom_get_pod_restart_counts(namespace: str, service: str) -> dict[str, Any]:
    """Get current pod container restart counts for a service."""
    ns = _escape_label_value(namespace)
    svc = _escape_label_value(service)
    promql = f'sum by (pod) (kube_pod_container_status_restarts_total{{namespace="{ns}", pod=~"{svc}.*"}})'
    result = _instant_query(promql)
    if result.get("isError"):
        return result
    return _store_prometheus_evidence(
        promql=promql,
        raw=result["data"],
        label=f"pod restart counts for {namespace}/{service}",
        metadata={"namespace": namespace, "service": service, "metric": "pod_restart_counts"},
    )


def prom_get_pod_restart_increase(namespace: str, service: str, since_minutes: int = 60) -> dict[str, Any]:
    """Get per-pod restart increase for a service over a bounded incident window."""
    ns = _escape_label_value(namespace)
    svc = _escape_label_value(service)
    window = f"{_clamp_since_minutes(since_minutes)}m"
    promql = (
        f"sum by (pod) (increase(kube_pod_container_status_restarts_total"
        f'{{namespace="{ns}", pod=~"{svc}.*"}}[{window}]))'
    )
    result = _instant_query(promql)
    if result.get("isError"):
        return result
    return _store_prometheus_evidence(
        promql=promql,
        raw=result["data"],
        label=f"pod restart increase for {namespace}/{service} over last {window}",
        metadata={
            "namespace": namespace,
            "service": service,
            "metric": "pod_restart_increase",
            "since_minutes": since_minutes,
        },
    )


def prom_get_pod_cpu_usage(namespace: str, service: str) -> dict[str, Any]:
    """Get current per-pod CPU usage (5m rate) for a service."""
    ns = _escape_label_value(namespace)
    svc = _escape_label_value(service)
    promql = (
        f'sum by (pod) (rate(container_cpu_usage_seconds_total{{namespace="{ns}", pod=~"{svc}.*"}}[5m]))'
    )
    result = _instant_query(promql)
    if result.get("isError"):
        return result
    return _store_prometheus_evidence(
        promql=promql,
        raw=result["data"],
        label=f"pod CPU usage for {namespace}/{service}",
        metadata={"namespace": namespace, "service": service, "metric": "pod_cpu_usage"},
    )


def prom_get_pod_memory_usage(namespace: str, service: str) -> dict[str, Any]:
    """Get current per-pod working-set memory usage for a service."""
    ns = _escape_label_value(namespace)
    svc = _escape_label_value(service)
    promql = f'sum by (pod) (container_memory_working_set_bytes{{namespace="{ns}", pod=~"{svc}.*"}})'
    result = _instant_query(promql)
    if result.get("isError"):
        return result
    return _store_prometheus_evidence(
        promql=promql,
        raw=result["data"],
        label=f"pod memory usage for {namespace}/{service}",
        metadata={"namespace": namespace, "service": service, "metric": "pod_memory_usage"},
    )


def prom_get_http_error_rate(namespace: str, service: str, since_minutes: int = 60) -> dict[str, Any]:
    """Get the HTTP 5xx error rate for a service over a bounded time window."""
    ns = _escape_label_value(namespace)
    svc = _escape_label_value(service)
    window = f"{_clamp_since_minutes(since_minutes)}m"
    promql = (
        f'sum(rate(http_requests_total{{namespace="{ns}", service="{svc}", status=~"5.."}}[{window}])) '
        f'/ sum(rate(http_requests_total{{namespace="{ns}", service="{svc}"}}[{window}]))'
    )
    result = _instant_query(promql)
    if result.get("isError"):
        return result
    return _store_prometheus_evidence(
        promql=promql,
        raw=result["data"],
        label=f"HTTP 5xx error rate for {namespace}/{service} over last {window}",
        metadata={"namespace": namespace, "service": service, "metric": "http_error_rate", "since_minutes": since_minutes},
    )


def prom_get_latency_p95(namespace: str, service: str, since_minutes: int = 60) -> dict[str, Any]:
    """Get p95 HTTP request latency for a service over a bounded time window."""
    ns = _escape_label_value(namespace)
    svc = _escape_label_value(service)
    window = f"{_clamp_since_minutes(since_minutes)}m"
    promql = (
        f"histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket"
        f'{{namespace="{ns}", service="{svc}"}}[{window}])) by (le))'
    )
    result = _instant_query(promql)
    if result.get("isError"):
        return result
    return _store_prometheus_evidence(
        promql=promql,
        raw=result["data"],
        label=f"p95 latency for {namespace}/{service} over last {window}",
        metadata={"namespace": namespace, "service": service, "metric": "latency_p95", "since_minutes": since_minutes},
    )
