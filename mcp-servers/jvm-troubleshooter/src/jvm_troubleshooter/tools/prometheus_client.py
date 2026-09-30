"""Shared, bounded Prometheus query helpers for the JVM tool modules.

Environment variables:
  PROMETHEUS_URL   base URL of the Prometheus/Thanos-query server, e.g.
                   http://prometheus:9090 (no trailing slash needed)
  GRAFANA_*        alternatively, query through Grafana's datasource proxy
                   (takes precedence when GRAFANA_URL is set) -- see
                   `prometheus_endpoint.py`
  JVM_LABEL_KEY    the Prometheus label that identifies which
                   service/job a JVM metric series belongs to. Defaults to
                   "job". This MUST match your actual scrape config -- if
                   it's wrong, queries return empty results, not an error.

Only `/api/v1/query` and `/api/v1/query_range` are used. Nothing in this
module can mutate Prometheus or JVM/cluster state.
"""

from __future__ import annotations

import os
import re
from typing import Any

from jvm_troubleshooter.errors import ToolError
from jvm_troubleshooter.tools.http_client import request_json
from jvm_troubleshooter.tools.prometheus_endpoint import PrometheusEndpoint, resolve_prometheus_endpoint

_QUERY_PATH = "/api/v1/query"
_QUERY_RANGE_PATH = "/api/v1/query_range"
_DEFAULT_TIMEOUT_SECONDS = 20.0

_MAX_PROMQL_LENGTH = 2000
_MAX_RANGE_DAYS = 7

_DURATION_RE = re.compile(r"\[(\d+)([smhdwy])")
_SECONDS_PER_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}


def prometheus_base_url() -> str | None:
    endpoint = resolve_prometheus_endpoint()
    return endpoint.base_url if isinstance(endpoint, PrometheusEndpoint) else None


def label_key() -> str:
    return os.environ.get("JVM_LABEL_KEY", "job").strip() or "job"


def escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def namespace_service_selector(namespace: str, service: str) -> str:
    """Build the `namespace=..., <label_key>=~"(service)"` selector fragment shared by every JVM query.

    `service` is treated as a regex alternation-friendly pattern (matching
    the convention used throughout this project's companion Grafana
    dashboard) -- pass a plain service name for an exact-ish match, or a
    `|`-joined list to cover multiple services/pods at once.
    """
    ns = escape_label_value(namespace)
    svc = escape_label_value(service)
    return f'namespace="{ns}", {label_key()}=~"({svc})"'


def _missing_config_error(attempted: dict[str, Any]) -> dict[str, Any]:
    return ToolError(
        "validation",
        False,
        "PROMETHEUS_URL is not set.",
        attempted=attempted,
        alternatives=[
            "Set the PROMETHEUS_URL environment variable to the Prometheus/Thanos-query base URL",
            "Or query through Grafana instead: set GRAFANA_URL, GRAFANA_DATASOURCE_UID and "
            "GRAFANA_API_TOKEN/GRAFANA_SESSION_COOKIE in .env",
            "Record this as an unknowns/gap -- do not report zero/normal JVM metrics because they could not be retrieved",
        ],
    ).to_dict()


def _validate_promql(promql: str) -> dict[str, Any] | None:
    if not promql or not promql.strip():
        return ToolError("validation", False, "promql cannot be empty").to_dict()

    if len(promql) > _MAX_PROMQL_LENGTH:
        return ToolError(
            "validation",
            False,
            f"promql exceeds max length of {_MAX_PROMQL_LENGTH} characters",
            attempted={"promql_length": len(promql)},
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


def _resolve_endpoint(attempted: dict[str, Any]) -> PrometheusEndpoint | dict[str, Any]:
    endpoint = resolve_prometheus_endpoint()
    if endpoint is None:
        return _missing_config_error(attempted)
    if isinstance(endpoint, dict):
        return {**endpoint, "attempted": {**(endpoint.get("attempted") or {}), **attempted}}
    return endpoint


def _query(endpoint: PrometheusEndpoint, path: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    return request_json(
        "GET",
        f"{endpoint.base_url}{path}",
        params=params,
        headers=endpoint.headers or None,
        timeout=timeout,
        redact=endpoint.secrets or None,
    )


def instant_query(promql: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    endpoint = _resolve_endpoint({"promql": promql})
    if isinstance(endpoint, dict):
        return endpoint

    invalid = _validate_promql(promql)
    if invalid is not None:
        return invalid

    return _query(endpoint, _QUERY_PATH, {"query": promql}, timeout)


def range_query(
    promql: str,
    *,
    start: str,
    end: str,
    step: str = "60s",
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    endpoint = _resolve_endpoint({"promql": promql, "start": start, "end": end})
    if isinstance(endpoint, dict):
        return endpoint

    invalid = _validate_promql(promql)
    if invalid is not None:
        return invalid

    return _query(endpoint, _QUERY_RANGE_PATH, {"query": promql, "start": start, "end": end, "step": step}, timeout)


def clamp_lookback_minutes(lookback_minutes: int, *, max_minutes: int = 1440) -> int:
    return max(1, min(int(lookback_minutes), max_minutes))
