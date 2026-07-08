"""MCP server for Claude Ops Support Agent.

Run locally over STDIO:

    python -m claude_ops.mcp.server

Then connect Claude Code using `.mcp.json`.

This exposes:
- resources: runbook catalog and service catalog
- tools: narrow read-only Kubernetes investigation tools
- prompt: incident investigation workflow

Safety:
- underlying k8s tools allow only read-only kubectl verbs
- destructive commands are blocked in hooks.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from claude_ops.tools.k8s_tools import (
    list_pods,
    describe_pod,
    get_pod_logs,
    get_recent_namespace_events,
    top_pods,
)
from claude_ops.tools.runbook_tools import get_runbook_catalog, search_runbooks


PROJECT_ROOT = Path(__file__).resolve().parents[3]
mcp = FastMCP("claude-ops-support-agent")


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


@mcp.resource("ops://runbook-catalog")
def runbook_catalog() -> str:
    """Catalog of available incident runbooks.

    Use this resource before calling search_runbooks when Claude needs to know
    what runbooks exist.
    """
    return _json(get_runbook_catalog())


@mcp.resource("ops://service-catalog")
def service_catalog() -> str:
    """Catalog of known services, namespaces, labels, and production risk."""
    path = PROJECT_ROOT / "data" / "service_catalog.json"
    return path.read_text()


@mcp.tool()
def k8s_list_pods(namespace: str, label_selector: str | None = None) -> str:
    """List pods in a namespace using an optional Kubernetes label selector.

    Use this for read-only pod discovery. Example:
    namespace="si", label_selector="app=event-data"

    Returns pod name, phase, restart count, namespace, and container names.
    """
    return _json(list_pods(namespace=namespace, label_selector=label_selector))


@mcp.tool()
def k8s_describe_pod(namespace: str, pod_name: str) -> str:
    """Describe a single Kubernetes pod using read-only kubectl describe.

    Use this to inspect restart reasons, last state, probe configuration,
    container statuses, events, and scheduling details.
    """
    return _json(describe_pod(namespace=namespace, pod_name=pod_name))


@mcp.tool()
def k8s_get_pod_logs(
    namespace: str,
    pod_name: str,
    container: str | None = None,
    since_minutes: int = 60,
    tail: int = 200,
) -> str:
    """Fetch recent logs for a pod.

    Read-only. Use this to inspect recent application errors around an incident.
    Keep since_minutes and tail bounded to avoid excessive context.
    """
    return _json(
        get_pod_logs(
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            since_minutes=since_minutes,
            tail=tail,
        )
    )


@mcp.tool()
def k8s_get_recent_namespace_events(namespace: str) -> str:
    """Get recent namespace events sorted by last timestamp.

    Use this to inspect liveness failures, scheduling issues, image pull errors,
    OOMKilled events, and Kubernetes warnings.
    """
    return _json(get_recent_namespace_events(namespace=namespace))


@mcp.tool()
def k8s_top_pods(namespace: str) -> str:
    """Get current pod CPU/memory usage via kubectl top pods.

    This requires metrics-server availability. If metrics-server is unavailable,
    the tool returns a structured error.
    """
    return _json(top_pods(namespace=namespace))


@mcp.tool()
def runbook_search(query: str) -> str:
    """Search local incident runbooks by keyword.

    Use after reading the runbook catalog resource or when symptoms match a
    known incident pattern such as OOMKilled, Kafka commit rate low, or liveness
    probe failure.
    """
    return _json(search_runbooks(query=query))


@mcp.prompt()
def investigate_incident(namespace: str, service: str, since_minutes: int = 60) -> str:
    """Reusable prompt for read-only Kubernetes incident investigation."""
    return f"""Investigate a Kubernetes incident using only read-only tools.

Namespace: {namespace}
Service: {service}
Time window: last {since_minutes} minutes

Workflow:
1. Read ops://service-catalog and ops://runbook-catalog resources.
2. Use k8s_list_pods with label_selector="app={service}".
3. Inspect recent namespace events.
4. For relevant pods, use describe/log/top tools.
5. Search runbooks for matching symptoms.
6. Produce an evidence-grounded incident report.

Rules:
- Do not run or suggest destructive commands without human approval.
- Preserve evidence source and detail.
- State unknowns explicitly.
- If remediation is production-impacting, set requires_human=true.
"""


if __name__ == "__main__":
    mcp.run()
