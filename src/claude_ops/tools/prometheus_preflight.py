"""Prometheus reachability preflight and optional local port-forward.

This is the only module in this project allowed to start a `kubectl
port-forward` process. It is intentionally separate from `prometheus_tools.py`:
the `prom_get_*` / `prom_query_instant` MCP tools only ever *query*
Prometheus and, if it's unreachable, point back here — they never start a
port-forward themselves.

Environment variables:
  PROMETHEUS_URL                 default "http://localhost:9090"
  PROMETHEUS_PF_SERVICE          default "kube-prometheus-stack-prometheus"
  PROMETHEUS_PF_NAMESPACE        default "monitoring"
  PROMETHEUS_PF_LOCAL_PORT       default "9090"
  PROMETHEUS_PF_REMOTE_PORT      default same as PROMETHEUS_PF_LOCAL_PORT
  PROMETHEUS_AUTO_PORT_FORWARD   default "false" — must be exactly "true"
                                  (case-insensitive) before ensure_prometheus
                                  will start a port-forward process.

Safety:
  - The only subprocess ever started here is `kubectl port-forward`; no
    destructive kubectl verb is reachable from this module.
  - ensure_prometheus() never starts a port-forward unless
    PROMETHEUS_AUTO_PORT_FORWARD=true — otherwise it only checks reachability
    and returns instructions.
  - ensure_prometheus() is not called automatically by prometheus_tools.py or
    by the incident investigation workflow. It is only reached via the
    explicit prom_ensure_connection MCP tool or direct human/CLI use.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import time
from typing import Any

import httpx

from claude_ops.errors import ToolError, ok

_STATUS_CONFIG_PATH = "/api/v1/status/config"
_REACHABILITY_TIMEOUT_SECONDS = 3.0
_PORT_FORWARD_WAIT_SECONDS = 20
_POLL_INTERVAL_SECONDS = 1.0

_DEFAULT_PROMETHEUS_URL = "http://localhost:9090"
_DEFAULT_PF_SERVICE = "kube-prometheus-stack-prometheus"
_DEFAULT_PF_NAMESPACE = "monitoring"
_DEFAULT_PF_LOCAL_PORT = "9090"


def _resolve_config(config: dict[str, Any] | None) -> dict[str, Any]:
    config = config or {}

    local_port = str(
        config.get("prometheus_pf_local_port")
        or os.environ.get("PROMETHEUS_PF_LOCAL_PORT", _DEFAULT_PF_LOCAL_PORT)
    )
    remote_port = str(
        config.get("prometheus_pf_remote_port")
        or os.environ.get("PROMETHEUS_PF_REMOTE_PORT", local_port)
    )
    auto_pf_raw = str(
        config.get("prometheus_auto_port_forward")
        if config.get("prometheus_auto_port_forward") is not None
        else os.environ.get("PROMETHEUS_AUTO_PORT_FORWARD", "false")
    )

    return {
        "prometheus_url": (
            config.get("prometheus_url") or os.environ.get("PROMETHEUS_URL", _DEFAULT_PROMETHEUS_URL)
        ).rstrip("/"),
        "prometheus_pf_service": config.get("prometheus_pf_service")
        or os.environ.get("PROMETHEUS_PF_SERVICE", _DEFAULT_PF_SERVICE),
        "prometheus_pf_namespace": config.get("prometheus_pf_namespace")
        or os.environ.get("PROMETHEUS_PF_NAMESPACE", _DEFAULT_PF_NAMESPACE),
        "prometheus_pf_local_port": local_port,
        "prometheus_pf_remote_port": remote_port,
        "prometheus_auto_port_forward": auto_pf_raw.strip().lower() == "true",
    }


def prom_reachable(url: str) -> bool:
    """Return True if Prometheus at `url` answers /api/v1/status/config."""
    try:
        resp = httpx.get(f"{url.rstrip('/')}{_STATUS_CONFIG_PATH}", timeout=_REACHABILITY_TIMEOUT_SECONDS)
        return resp.status_code == 200
    except httpx.RequestError:
        return False


def _start_port_forward(cfg: dict[str, Any]) -> subprocess.Popen | None:
    target = f"{cfg['prometheus_pf_local_port']}:{cfg['prometheus_pf_remote_port']}"
    args = [
        "kubectl",
        "port-forward",
        f"svc/{cfg['prometheus_pf_service']}",
        target,
        "-n",
        cfg["prometheus_pf_namespace"],
    ]
    try:
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        return None


def ensure_prometheus(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check Prometheus reachability and, only if explicitly enabled, start a
    local `kubectl port-forward` to reach it.

    Returns `ok({"reachable": True, "started_port_forward": bool, ...})` on
    success, or a structured ToolError dict.
    """
    cfg = _resolve_config(config)
    prom_url = cfg["prometheus_url"]

    if prom_reachable(prom_url):
        return ok({
            "reachable": True,
            "started_port_forward": False,
            "prometheus_url": prom_url,
        })

    if not cfg["prometheus_auto_port_forward"]:
        return ToolError(
            "transient",
            True,
            f"Prometheus at {prom_url} is not reachable and PROMETHEUS_AUTO_PORT_FORWARD is not enabled.",
            attempted={"prometheus_url": prom_url},
            alternatives=[
                "Verify PROMETHEUS_URL points at a reachable Prometheus instance",
                "Set PROMETHEUS_AUTO_PORT_FORWARD=true and call prom_ensure_connection to start a local kubectl port-forward",
            ],
        ).to_dict()

    proc = _start_port_forward(cfg)
    if proc is None:
        return ToolError(
            "validation",
            False,
            "kubectl not found — cannot start port-forward",
            attempted={
                "prometheus_url": prom_url,
                "service": cfg["prometheus_pf_service"],
                "namespace": cfg["prometheus_pf_namespace"],
            },
            alternatives=["Install/configure kubectl", "Set PROMETHEUS_URL to a reachable endpoint instead"],
        ).to_dict()

    atexit.register(proc.terminate)

    elapsed = 0.0
    while elapsed < _PORT_FORWARD_WAIT_SECONDS:
        time.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS
        if prom_reachable(prom_url):
            return ok({
                "reachable": True,
                "started_port_forward": True,
                "prometheus_url": prom_url,
                "port_forward_pid": proc.pid,
                "seconds_to_ready": elapsed,
            })

    return ToolError(
        "transient",
        True,
        f"Started kubectl port-forward but Prometheus at {prom_url} was not reachable after {_PORT_FORWARD_WAIT_SECONDS}s",
        attempted={
            "prometheus_url": prom_url,
            "service": cfg["prometheus_pf_service"],
            "namespace": cfg["prometheus_pf_namespace"],
        },
        partialResults={"port_forward_pid": proc.pid},
        alternatives=[
            "Check kubectl connectivity/RBAC for the target namespace",
            "Verify PROMETHEUS_PF_SERVICE/PROMETHEUS_PF_NAMESPACE/PROMETHEUS_PF_LOCAL_PORT are correct",
        ],
    ).to_dict()
