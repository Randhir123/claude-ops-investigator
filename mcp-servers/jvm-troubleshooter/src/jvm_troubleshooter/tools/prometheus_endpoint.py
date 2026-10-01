"""Resolve where Prometheus queries go: a direct Prometheus URL, or Grafana's
datasource proxy. Mirrors claude-ops-investigator's module of the same name
(intentionally duplicated, like `errors.py`, so this server stays standalone).

Two mutually exclusive modes, chosen from the environment:

  direct   (default) PROMETHEUS_URL, e.g. http://localhost:9090 behind a
           `kubectl port-forward`. No auth headers.

  grafana  Selected whenever GRAFANA_URL is set; takes precedence over
           PROMETHEUS_URL. Queries go through Grafana's datasource proxy,
           `<GRAFANA_URL>/api/datasources/proxy/uid/<GRAFANA_DATASOURCE_UID>`,
           which forwards the same `/api/v1/query` and `/api/v1/query_range`
           calls to the Prometheus datasource — so no port-forward or cluster
           access is needed, only a Grafana login.

             GRAFANA_URL              e.g. https://grafana.example.com
             GRAFANA_DATASOURCE_UID   uid of the Prometheus datasource
             GRAFANA_API_TOKEN        service account token (sent as Bearer), or
             GRAFANA_SESSION_COOKIE   the browser's `grafana_session` cookie value
                                      (used only if no API token is set)

Credentials belong in a gitignored `.env`, never in `.mcp.json`. They are
returned in `secrets` so callers can redact them from any error text.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from jvm_troubleshooter.errors import ToolError

_DATASOURCE_UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class PrometheusEndpoint:
    mode: str  # "direct" | "grafana"
    base_url: str
    headers: dict[str, str] = field(default_factory=dict)
    secrets: list[str] = field(default_factory=list)


def grafana_mode_enabled() -> bool:
    return bool(os.environ.get("GRAFANA_URL", "").strip())


def _config_error(message: str, attempted: dict[str, Any], alternatives: list[str]) -> dict[str, Any]:
    return ToolError("validation", False, message, attempted=attempted, alternatives=alternatives).to_dict()


def resolve_grafana_endpoint() -> PrometheusEndpoint | dict[str, Any]:
    """Build the Grafana datasource-proxy endpoint, or return a structured validation error."""
    grafana_url = os.environ.get("GRAFANA_URL", "").strip().rstrip("/")
    uid = os.environ.get("GRAFANA_DATASOURCE_UID", "").strip()
    token = os.environ.get("GRAFANA_API_TOKEN", "").strip()
    cookie = os.environ.get("GRAFANA_SESSION_COOKIE", "").strip()
    attempted = {"mode": "grafana", "grafana_url": grafana_url, "datasource_uid": uid or None}

    parsed = urlparse(grafana_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return _config_error(
            f"GRAFANA_URL {grafana_url!r} is not a valid http(s) URL.",
            attempted,
            ["Set GRAFANA_URL to the Grafana base URL, e.g. https://grafana.example.com"],
        )
    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
        return _config_error(
            "GRAFANA_URL must use https for non-local hosts, so Grafana credentials are never sent in cleartext.",
            attempted,
            ["Use the https:// Grafana URL"],
        )

    if not _DATASOURCE_UID_RE.match(uid):
        return _config_error(
            "GRAFANA_DATASOURCE_UID is missing or malformed.",
            attempted,
            [
                "Set GRAFANA_DATASOURCE_UID to the Prometheus datasource uid "
                "(Grafana → Connections → Data sources → the datasource's URL ends in /edit/<uid>)",
                "Or unset GRAFANA_URL to query PROMETHEUS_URL directly",
            ],
        )

    if token:
        headers = {"Authorization": f"Bearer {token}"}
        secrets = [token]
    elif cookie:
        headers = {"Cookie": f"grafana_session={cookie}"}
        secrets = [cookie]
    else:
        return _config_error(
            "Grafana mode needs GRAFANA_API_TOKEN or GRAFANA_SESSION_COOKIE.",
            attempted,
            [
                "Set GRAFANA_API_TOKEN to a Viewer-role service account token (preferred), or "
                "GRAFANA_SESSION_COOKIE to your browser's grafana_session cookie value, in .env",
                "Record this as an unknowns/gap — do not report zero/normal metrics because they could not be retrieved",
            ],
        )

    return PrometheusEndpoint(
        mode="grafana",
        base_url=f"{grafana_url}/api/datasources/proxy/uid/{uid}",
        headers=headers,
        secrets=secrets,
    )


def resolve_prometheus_endpoint() -> PrometheusEndpoint | dict[str, Any] | None:
    """Return the configured endpoint, a structured error for bad Grafana config,
    or None when nothing is configured (callers keep their own missing-config error)."""
    if grafana_mode_enabled():
        return resolve_grafana_endpoint()

    url = os.environ.get("PROMETHEUS_URL", "").strip().rstrip("/")
    return PrometheusEndpoint(mode="direct", base_url=url) if url else None
