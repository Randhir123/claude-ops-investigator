"""MCP server for Claude Ops Investigator.

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

from dotenv import load_dotenv

# Load .env before importing tool modules that read Prometheus/IBM Cloud Logs
# env vars at call time (PROMETHEUS_URL, IBM_LOGS_ENDPOINT, IBM_CLOUD_API_KEY,
# etc.). Safe to call when no .env file exists — it's a no-op in that case,
# and it never overrides variables already set in the real environment.
load_dotenv()

from mcp.server.fastmcp import FastMCP

from claude_ops.tools.k8s_tools import (
    list_pods,
    describe_pod,
    get_pod_logs,
    get_recent_namespace_events,
    top_pods,
)
from claude_ops.tools.runbook_tools import get_runbook_catalog, search_runbooks
from claude_ops.tools import prometheus_tools, prometheus_preflight, ibm_logs_tools
from claude_ops.evidence.k8s_evidence import store_k8s_tool_result
from claude_ops.evidence.raw_store import load_raw_evidence
from claude_ops.evidence.summarizers import summarize_k8s_events


PROJECT_ROOT = Path(__file__).resolve().parents[3]
mcp = FastMCP("claude-ops-investigator")


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
    result = describe_pod(namespace=namespace, pod_name=pod_name)
    return _json(
        store_k8s_tool_result(
            content_type="k8s.pod_describe",
            result=result,
            label=f"describe pod {namespace}/{pod_name}",
            metadata={"namespace": namespace, "pod_name": pod_name},
        )
    )


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
    result = get_pod_logs(
        namespace=namespace,
        pod_name=pod_name,
        container=container,
        since_minutes=since_minutes,
        tail=tail,
    )
    return _json(
        store_k8s_tool_result(
            content_type="k8s.pod_logs",
            result=result,
            label=f"logs for pod {namespace}/{pod_name}",
            metadata={
                "namespace": namespace,
                "pod_name": pod_name,
                "container": container,
                "since_minutes": since_minutes,
                "tail": tail,
            },
        )
    )


@mcp.tool()
def k8s_get_recent_namespace_events(namespace: str) -> str:
    """Get recent namespace events sorted by last timestamp.

    Use this to inspect liveness failures, scheduling issues, image pull errors,
    OOMKilled events, and Kubernetes warnings.
    """
    result = get_recent_namespace_events(namespace=namespace)
    return _json(
        store_k8s_tool_result(
            content_type="k8s.namespace_events",
            result=result,
            label=f"recent events in namespace {namespace}",
            metadata={"namespace": namespace},
            summarize=summarize_k8s_events,
        )
    )


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


@mcp.tool()
def prom_query_instant(promql: str) -> str:
    """Run a read-only, bounded instant PromQL query against Prometheus.

    Prefer the typed prom_get_* tools for common questions; use this only when
    they don't cover what you need. Requires PROMETHEUS_URL. Rejects
    excessively long queries or unbounded range durations. Raw Prometheus JSON
    is stored as evidence, not returned directly — use the compact summary and
    evidence_ref.
    """
    return _json(prometheus_tools.prom_query_instant(promql))


@mcp.tool()
def prom_get_pod_restart_counts(namespace: str, service: str) -> str:
    """Get current pod container restart counts for a service via Prometheus.

    Use to corroborate OOMKilled/CrashLoopBackOff patterns seen in Kubernetes
    events with cluster-wide restart metrics.
    """
    return _json(prometheus_tools.prom_get_pod_restart_counts(namespace=namespace, service=service))


@mcp.tool()
def prom_get_pod_cpu_usage(namespace: str, service: str) -> str:
    """Get current per-pod CPU usage (5m rate) for a service via Prometheus."""
    return _json(prometheus_tools.prom_get_pod_cpu_usage(namespace=namespace, service=service))


@mcp.tool()
def prom_get_pod_memory_usage(namespace: str, service: str) -> str:
    """Get current per-pod working-set memory usage for a service via Prometheus.

    Use to confirm memory pressure behind OOMKilled restarts.
    """
    return _json(prometheus_tools.prom_get_pod_memory_usage(namespace=namespace, service=service))


@mcp.tool()
def prom_get_http_error_rate(namespace: str, service: str, since_minutes: int = 60) -> str:
    """Get the HTTP 5xx error rate for a service over a bounded time window via Prometheus."""
    return _json(prometheus_tools.prom_get_http_error_rate(namespace=namespace, service=service, since_minutes=since_minutes))


@mcp.tool()
def prom_get_latency_p95(namespace: str, service: str, since_minutes: int = 60) -> str:
    """Get p95 HTTP request latency for a service over a bounded time window via Prometheus."""
    return _json(prometheus_tools.prom_get_latency_p95(namespace=namespace, service=service, since_minutes=since_minutes))


@mcp.tool()
def prom_ensure_connection() -> str:
    """Check Prometheus reachability, and only start a local kubectl port-forward if PROMETHEUS_AUTO_PORT_FORWARD=true.

    Call this explicitly when a prom_get_*/prom_query_instant tool reports
    Prometheus is unreachable — it is never called automatically by those
    tools, and the investigation workflow does not call it automatically
    either. This is the only tool in this project that may start a `kubectl
    port-forward` process, and it only does so when
    PROMETHEUS_AUTO_PORT_FORWARD=true; otherwise it only reports reachability
    and returns instructions for enabling port-forward.
    """
    return _json(prometheus_preflight.ensure_prometheus())


@mcp.tool()
def ibm_logs_search(namespace: str, query: str, app: str | None = None, since_minutes: int = 60, limit: int = 200) -> str:
    """Search IBM Cloud Logs (persistent, cross-restart/cross-deployment) for a plain-text keyword.

    Prefer this over k8s_get_pod_logs for historical analysis: these logs
    survive pod restarts, deployments, and scale-downs, and span all pod
    incarnations of a service. Requires IBM_CLOUD_API_KEY and
    IBM_LOGS_ENDPOINT. Returns a compact summary plus evidence_ref.
    """
    return _json(
        ibm_logs_tools.ibm_logs_search(namespace=namespace, query=query, app=app, since_minutes=since_minutes, limit=limit)
    )


@mcp.tool()
def ibm_logs_search_errors(namespace: str, app: str, since_minutes: int = 60, limit: int = 200) -> str:
    """Search IBM Cloud Logs for ERROR-level log lines for a specific app."""
    return _json(
        ibm_logs_tools.ibm_logs_search_errors(namespace=namespace, app=app, since_minutes=since_minutes, limit=limit)
    )


@mcp.tool()
def ibm_logs_search_probe_failures(namespace: str, app: str, since_minutes: int = 60, limit: int = 200) -> str:
    """Search IBM Cloud Logs for liveness/readiness probe failure log lines for a specific app."""
    return _json(
        ibm_logs_tools.ibm_logs_search_probe_failures(namespace=namespace, app=app, since_minutes=since_minutes, limit=limit)
    )


@mcp.tool()
def ibm_logs_search_text(namespace: str, app: str, text: str, since_minutes: int = 60, limit: int = 200) -> str:
    """Search IBM Cloud Logs for an arbitrary plain-text pattern scoped to a specific app."""
    return _json(
        ibm_logs_tools.ibm_logs_search_text(namespace=namespace, app=app, text=text, since_minutes=since_minutes, limit=limit)
    )


@mcp.tool()
def evidence_get_detail(evidence_ref: str) -> str:
    """Fetch raw evidence detail by evidence_ref.

    Use this only when the compact summary is insufficient. Prefer reasoning over
    summaries first to avoid unnecessary context growth.
    """
    try:
        return _json({
            "isError": False,
            "data": load_raw_evidence(evidence_ref),
        })
    except FileNotFoundError as exc:
        return _json({
            "isError": True,
            "errorCategory": "validation",
            "isRetryable": False,
            "message": str(exc),
            "attempted": {"evidence_ref": evidence_ref},
            "partialResults": None,
            "alternatives": ["Use a valid evidence_ref returned by a previous tool call"],
        })


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
5. If deeper evidence is needed, use typed prom_get_* tools for metrics
   (restarts, CPU, memory, error rate, latency) and ibm_logs_search_* tools
   for historical logs spanning pod restarts and deployments.
6. Search runbooks for matching symptoms.
7. Produce an evidence-grounded incident report.

Rules:
- Do not run or suggest destructive commands without human approval.
- Preserve evidence source and detail.
- State unknowns explicitly.
- If remediation is production-impacting, set requires_human=true.
"""


if __name__ == "__main__":
    mcp.run()
