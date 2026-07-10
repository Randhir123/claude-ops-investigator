"""MCP server for Claude Ops Investigator.

Run locally over STDIO:

    python -m claude_ops.mcp.server

Then connect an MCP client using that client's MCP server configuration
(e.g. `.mcp.json`).

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
    """Index of every local incident runbook (id, title, trigger symptoms).

    No parameters. Returns the full runbook index as JSON — this is the
    catalog metadata (what runbooks exist and what they cover), not the
    runbook bodies themselves.

    Read this once at the start of an investigation to learn what known
    incident patterns exist before deciding whether `runbook_search` is
    likely to find a match. Prefer `runbook_search` once you have the actual
    symptom text — this resource is for discovery, not per-incident lookup.
    Read-only; no side effects.
    """
    return _json(get_runbook_catalog())


@mcp.resource("ops://service-catalog")
def service_catalog() -> str:
    """Known services, their namespaces/labels, and production-risk tier.

    No parameters. Returns `data/service_catalog.json` verbatim — for each
    known service: namespace, the `app` label to use with
    `k8s_list_pods(label_selector="app=<service>")`, and a production-risk
    flag support agents should use to judge how cautious to be.

    Read this before investigating an unfamiliar service to confirm the
    correct namespace/label and to know whether a symptom is in a
    production-risk-flagged service (raise the bar for evidence before
    concluding a root cause, and lean toward `requires_human: true` for any
    remediation suggestion). Read-only; no side effects.
    """
    path = PROJECT_ROOT / "data" / "service_catalog.json"
    return path.read_text()


@mcp.tool()
def k8s_list_pods(namespace: str, label_selector: str | None = None) -> str:
    """Discover pods for a service — the usual first step of an investigation.

    Parameters:
    - `namespace`: the Kubernetes namespace to list pods in (e.g. "si").
    - `label_selector`: optional Kubernetes label selector to scope the
      listing. For service-scoped investigation, always pass
      `label_selector="app=<service>"` (e.g. "app=event-data") rather than
      listing the whole namespace — this keeps results scoped to the
      reported service and avoids pulling in unrelated pods. Omit it only
      when you deliberately need every pod in the namespace.

    Returns, per pod: name, phase (Running/Pending/Failed/...), restart
    count, namespace, and container names. This is the source of exact
    `pod_name` values required by `k8s_describe_pod` and `k8s_get_pod_logs`
    — don't guess pod names.

    Use this instead of a generic search whenever you need current pod
    identity/state for a service; it is not for historical data (pods that
    no longer exist won't show up — use `ibm_logs_search*` for anything that
    predates the current pod incarnation).

    Response shape: the result is compact enough that it is usually returned
    inline as plain JSON, not archived via `evidence_get_detail` — do not
    expect an `evidence_ref` on this tool's output. Read-only; no side
    effects.
    """
    return _json(list_pods(namespace=namespace, label_selector=label_selector))


@mcp.tool()
def k8s_describe_pod(namespace: str, pod_name: str) -> str:
    """Deep-inspect one pod: probes, restart reasons, container states, events.

    Parameters:
    - `namespace`: the pod's Kubernetes namespace.
    - `pod_name`: the *exact* pod name — must come from a prior
      `k8s_list_pods` call (or another tool that returned a real pod name);
      never guess or construct one, pod name suffixes are non-deterministic.

    Equivalent to read-only `kubectl describe pod`. Returns readiness/
    liveness probe configuration and failures, container statuses and last
    termination reason (e.g. OOMKilled, Error), restart count, scheduling
    details, and the pod's own recent events.

    Use this for probe-failure and restart/crash-loop investigations once
    you've identified the affected pod(s) via `k8s_list_pods` — it gives far
    more resolution on *why* a specific pod is unhealthy than namespace-wide
    events do. Not for logs (use `k8s_get_pod_logs`) or cluster-wide events
    across pods (use `k8s_get_recent_namespace_events`).

    Returns a compact summary plus an `evidence_ref` — the full raw
    `kubectl describe` output is archived and retrievable via
    `evidence_get_detail` if the summary isn't enough. Read-only; no side
    effects.
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
    """Fetch recent logs for the currently-running instance of one pod.

    Parameters:
    - `namespace`, `pod_name`: exact pod identity, `pod_name` from
      `k8s_list_pods` (never guessed).
    - `container`: optional container name, for multi-container pods; omit
      to use the pod's default/only container.
    - `since_minutes`: how far back to look (default 60) — keep this
      bounded to the incident window.
    - `tail`: max lines to return (default 200) — keep bounded to avoid
      excessive context.

    Use this only for "what is this specific running pod doing right now"
    checks. It only sees the current pod incarnation — if the pod has
    restarted or been rescheduled since the incident started, earlier logs
    are gone. For anything that spans a restart, deployment, or scale event,
    or that needs to correlate logs across multiple pod incarnations, prefer
    `ibm_logs_search`/`ibm_logs_search_errors`/`ibm_logs_search_probe_failures`/
    `ibm_logs_search_text` instead — those are backed by persistent,
    cross-restart log storage.

    Returns a compact summary plus an `evidence_ref`; the full log body is
    archived and retrievable via `evidence_get_detail`. Read-only; no side
    effects.
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
    """Get recent Kubernetes events for a whole namespace, newest first.

    Parameters:
    - `namespace`: the Kubernetes namespace to pull events from. There is no
      service/label filter — this call is namespace-wide.

    Use this to spot `Unhealthy` (probe failures), `OOMKilled`,
    `FailedScheduling`, image-pull errors (`ErrImagePull`/`ImagePullBackOff`),
    and other Kubernetes warnings around the incident window. Good as an
    early, broad signal before narrowing to `k8s_describe_pod` on a specific
    pod.

    Because this is namespace-scoped, not service-scoped, results may
    include events for unrelated services sharing the namespace — you must
    filter findings down to the target service/pods yourself before treating
    anything here as evidence for the reported symptom, and should not
    report unrelated-service events as part of this incident (note them as
    background/unrelated signals instead, if relevant to the brief).

    Returns a compact summary (event counts by reason/type, matching
    objects) plus an `evidence_ref` — the full raw event table is archived
    and retrievable via `evidence_get_detail`. Read-only; no side effects.
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
    """Get current CPU/memory usage per pod via `kubectl top pods`.

    Parameters:
    - `namespace`: the Kubernetes namespace to report usage for.

    Returns a live, point-in-time snapshot only — current CPU (cores) and
    memory (bytes) per pod, sourced from metrics-server. This is not a
    historical trend or time series; it cannot tell you what usage looked
    like earlier in the incident window. Use `prom_get_pod_cpu_usage` /
    `prom_get_pod_memory_usage` when you need metrics over time or
    Prometheus isn't corroborated yet.

    Requires metrics-server to be running in the cluster. If metrics-server
    is unavailable, this returns a structured error (`isError: true`,
    `errorCategory`) rather than fabricated or zeroed numbers — treat that
    as a gap, not "no usage." Read-only; no side effects.
    """
    return _json(top_pods(namespace=namespace))


@mcp.tool()
def runbook_search(query: str) -> str:
    """Match a reported symptom against known local incident runbooks.

    Parameters:
    - `query`: search text. Use the reported symptom's own words first (e.g.
      "readiness probe failing intermittently", "KafkaConsumerCommitRateLow
      alert fired", "pods restarting with OOMKilled") — only broaden to more
      generic terms if that returns no matches.

    Returns, for each match: runbook id, title, and the relevant excerpt
    (diagnosis steps, likely causes, safe read-only checks, and any risky
    actions that require human approval). Returns an explicit no-match
    result rather than a fabricated runbook if nothing matches.

    Use this to check whether the current symptom is a known/documented
    pattern and to get a checklist of what to look at next — it is a
    starting hypothesis and safety-warning source, not evidence. A runbook
    match never proves root cause by itself; every claim in the final report
    still needs corroborating evidence from the k8s/Prometheus/log tools.
    Read-only; no side effects.
    """
    return _json(search_runbooks(query=query))


@mcp.tool()
def prom_query_instant(promql: str) -> str:
    """Free-form escape hatch: run one read-only, bounded instant PromQL query.

    Parameters:
    - `promql`: the raw PromQL expression to evaluate as an instant query
      (a single point in time, not a range). Keep it narrow — overly long
      or unbounded-range queries are rejected.

    This is a fallback only — reach for it only when none of the typed
    `prom_get_pod_restart_counts` / `prom_get_pod_restart_increase` /
    `prom_get_pod_cpu_usage` / `prom_get_pod_memory_usage` /
    `prom_get_http_error_rate` / `prom_get_latency_p95` tools answer the
    question, since those already build correct, bounded PromQL for the
    common cases. Requires `PROMETHEUS_URL` to be configured; if it's
    missing or Prometheus is unreachable, this returns a structured error,
    not a zeroed/empty result.

    Raw Prometheus JSON is archived as evidence, not returned directly — the
    response is a compact summary plus `evidence_ref`; use
    `evidence_get_detail` only if the summary doesn't resolve the question.
    Read-only; cannot mutate Prometheus or cluster state.
    """
    return _json(prometheus_tools.prom_query_instant(promql))


@mcp.tool()
def prom_get_pod_restart_counts(namespace: str, service: str) -> str:
    """Get each pod's *cumulative* container restart count for a service.

    Parameters:
    - `namespace`, `service`: scope the query to the service's pods (via its
      `app` label) in that namespace.

    This is the all-time cumulative restart count since each container's
    current lifetime began — it is **not** windowed to the incident, and a
    high number here doesn't tell you whether the restarts happened during
    the reported time window or weeks ago. Use it only as supporting
    context for the current total. For "did this service restart during the
    incident window" questions, prefer `prom_get_pod_restart_increase`
    instead — it's the incident-window-scoped version of this same signal.

    Requires `PROMETHEUS_URL`; if Prometheus is unreachable or unconfigured,
    this returns a structured error/gap — treat that as "unknown," not
    "zero restarts." Response is a compact summary plus `evidence_ref`.
    Read-only; cannot mutate Prometheus or cluster state.
    """
    return _json(prometheus_tools.prom_get_pod_restart_counts(namespace=namespace, service=service))


@mcp.tool()
def prom_get_pod_restart_increase(namespace: str, service: str, since_minutes: int = 60) -> str:
    """Get each pod's restart *increase* over the incident window.

    Preferred signal for restart/OOM/crash-loop/liveness investigations.

    Parameters:
    - `namespace`, `service`: scope the query to the service's pods.
    - `since_minutes`: the incident window to measure the increase over
      (default 60).

    Unlike `prom_get_pod_restart_counts` (all-time cumulative), this
    measures how many restarts happened specifically within the last
    `since_minutes` — the number you actually want when sizing "did this
    service crash-loop/OOM/fail liveness checks during the reported
    window." Prefer this tool first for OOM/restart/crash-loop/liveness
    symptoms; fall back to `prom_get_pod_restart_counts` only when you also
    need the current cumulative total as supporting context.

    Requires `PROMETHEUS_URL`; if Prometheus is unreachable or unconfigured,
    this returns a structured error/gap, not a zero. Response is a compact
    summary plus `evidence_ref`. Read-only; cannot mutate Prometheus or
    cluster state.
    """
    return _json(
        prometheus_tools.prom_get_pod_restart_increase(
            namespace=namespace, service=service, since_minutes=since_minutes
        )
    )


@mcp.tool()
def prom_get_pod_cpu_usage(namespace: str, service: str) -> str:
    """Get current per-pod CPU usage (5-minute rate) for a service.

    Parameters:
    - `namespace`, `service`: scope the query to the service's pods.

    Returns each pod's CPU usage as a 5-minute rate (cores) — a current
    signal, not a historical series or trend. On its own this cannot prove
    root cause; use it to corroborate a CPU-pressure hypothesis (e.g.
    alongside latency/error-rate metrics or logs showing timeouts), not as
    standalone proof of a problem.

    Requires `PROMETHEUS_URL`; if Prometheus is unreachable or unconfigured,
    this returns a structured error/gap. Response is a compact summary plus
    `evidence_ref`. Read-only; cannot mutate Prometheus or cluster state.
    """
    return _json(prometheus_tools.prom_get_pod_cpu_usage(namespace=namespace, service=service))


@mcp.tool()
def prom_get_pod_memory_usage(namespace: str, service: str) -> str:
    """Get current per-pod working-set memory usage for a service.

    Parameters:
    - `namespace`, `service`: scope the query to the service's pods.

    Returns each pod's current working-set memory (bytes) — a live signal,
    not a historical trend. Use this to confirm memory pressure behind
    suspected OOMKilled restarts (cross-reference with
    `prom_get_pod_restart_increase` and `k8s_describe_pod`'s last-termination
    reason) — high/rising memory alone doesn't confirm an OOM already
    happened, only that pressure exists now.

    Requires `PROMETHEUS_URL`; if Prometheus is unreachable or unconfigured,
    this returns a structured error/gap. Response is a compact summary plus
    `evidence_ref`. Read-only; cannot mutate Prometheus or cluster state.
    """
    return _json(prometheus_tools.prom_get_pod_memory_usage(namespace=namespace, service=service))


@mcp.tool()
def prom_get_http_error_rate(namespace: str, service: str, since_minutes: int = 60) -> str:
    """Get the HTTP 5xx error rate for a service over a bounded time window.

    Parameters:
    - `namespace`, `service`: scope the query to the service.
    - `since_minutes`: the time window to compute the error rate over
      (default 60).

    Use this to measure availability impact for "elevated errors" or
    caller-reported failure symptoms. If the service has no HTTP error-rate
    metric coverage, that is a **gap** (record it under `unknowns`), not
    evidence of a 0% error rate — do not treat missing coverage as "no
    errors."

    Requires `PROMETHEUS_URL`; if Prometheus is unreachable or unconfigured,
    this returns a structured error/gap. Response is a compact summary plus
    `evidence_ref`. Read-only; cannot mutate Prometheus or cluster state.
    """
    return _json(prometheus_tools.prom_get_http_error_rate(namespace=namespace, service=service, since_minutes=since_minutes))


@mcp.tool()
def prom_get_latency_p95(namespace: str, service: str, since_minutes: int = 60) -> str:
    """Get p95 HTTP request latency for a service over a bounded time window.

    Parameters:
    - `namespace`, `service`: scope the query to the service.
    - `since_minutes`: the time window to compute p95 latency over
      (default 60).

    Use this to measure latency impact for "slow"/"latency spike" symptoms.
    If the service has no latency metric coverage, that is a **gap** (record
    it under `unknowns`), not evidence of normal/acceptable latency — do not
    treat missing coverage as "latency is fine."

    Requires `PROMETHEUS_URL`; if Prometheus is unreachable or unconfigured,
    this returns a structured error/gap. Response is a compact summary plus
    `evidence_ref`. Read-only; cannot mutate Prometheus or cluster state.
    """
    return _json(prometheus_tools.prom_get_latency_p95(namespace=namespace, service=service, since_minutes=since_minutes))


@mcp.tool()
def prom_ensure_connection() -> str:
    """Prometheus connectivity preflight/setup — reserved for your harness's
    designated entry point for this, not for general use.

    No parameters. Checks Prometheus reachability at `PROMETHEUS_URL` first.

    Each MCP client harness decides who is allowed to call this tool and
    when — e.g. only a top-level coordinating role, and only when the user
    explicitly asked for a Prometheus-backed investigation or metrics are
    necessary to answer the symptom. Never call this automatically, and it
    is never called by the `prom_get_*`/`prom_query_instant` tools
    themselves, which report unreachability as a structured gap instead of
    calling this. After a successful call, retry the metric query via the
    relevant `prom_get_*` tool.

    Side effects: this is the **only** tool in this project that may start
    a `kubectl port-forward` subprocess, and only if
    `PROMETHEUS_AUTO_PORT_FORWARD=true` in the environment — otherwise it
    only reports reachability and returns instructions for enabling
    port-forward manually. The port-forward process (if started) is
    registered for cleanup on process exit. Never starts any other
    kubectl/Prometheus mutation.
    """
    return _json(prometheus_preflight.ensure_prometheus())


@mcp.tool()
def ibm_logs_search(namespace: str, query: str, app: str | None = None, since_minutes: int = 60, limit: int = 200) -> str:
    """Generic plain-text search over persistent, cross-restart IBM Cloud Logs.

    Parameters:
    - `namespace`: Kubernetes namespace to scope the search to.
    - `query`: free-text search string (DataPrime `source logs` search).
    - `app`: optional application label/name to further scope results —
      this is the service's `app` label (e.g. "event-data"), *not* a
      `pod_name`. Use `k8s_get_pod_logs` if you specifically need one
      currently-running pod's logs.
    - `since_minutes` (default 60), `limit` (default 200): bound the time
      window and result count — keep both no wider than the incident needs.

    This is the generic fallback search: prefer the narrower typed tools
    (`ibm_logs_search_errors`, `ibm_logs_search_probe_failures`,
    `ibm_logs_search_text`) when your query fits one of those shapes; use
    this one when the query doesn't fit a typed search.

    Prefer any `ibm_logs_search*` tool over `k8s_get_pod_logs` for
    historical analysis: these logs survive pod restarts, deployments, and
    scale-downs, and span every pod incarnation of a service, where
    `k8s_get_pod_logs` only sees the currently-running pod. Requires
    `IBM_CLOUD_API_KEY` and `IBM_LOGS_ENDPOINT`; missing config is reported
    as a structured error/gap, not "no matching logs." Returns a compact
    summary plus `evidence_ref`; never returns or logs the API key/token.
    Read-only; no side effects.
    """
    return _json(
        ibm_logs_tools.ibm_logs_search(namespace=namespace, query=query, app=app, since_minutes=since_minutes, limit=limit)
    )


@mcp.tool()
def ibm_logs_search_errors(namespace: str, app: str, since_minutes: int = 60, limit: int = 200) -> str:
    """Search IBM Cloud Logs for ERROR-level log lines for one app.

    Parameters:
    - `namespace`: Kubernetes namespace.
    - `app`: the application label/name (e.g. "event-data") — the service's
      `app` label, *not* a `pod_name`. For a specific currently-running
      pod's logs use `k8s_get_pod_logs` instead.
    - `since_minutes` (default 60), `limit` (default 200): bound the search.

    Use this — rather than the generic `ibm_logs_search` — whenever the
    question is "what errors did this service log," since it narrows to
    ERROR-level lines automatically. Results are historical and span pod
    restarts/deployments (unlike `k8s_get_pod_logs`, which only sees the
    current pod).

    Requires `IBM_CLOUD_API_KEY` and `IBM_LOGS_ENDPOINT`; missing config is a
    structured error/gap, not "no errors." Returns a compact summary plus
    `evidence_ref`; never returns secrets/credentials found in log text.
    Read-only; no side effects.
    """
    return _json(
        ibm_logs_tools.ibm_logs_search_errors(namespace=namespace, app=app, since_minutes=since_minutes, limit=limit)
    )


@mcp.tool()
def ibm_logs_search_probe_failures(namespace: str, app: str, since_minutes: int = 60, limit: int = 200) -> str:
    """Search IBM Cloud Logs for readiness/liveness/startup probe-failure log lines for one app.

    Parameters:
    - `namespace`: Kubernetes namespace.
    - `app`: the application label/name (e.g. "event-data") — the service's
      `app` label, *not* a `pod_name`. For a specific currently-running
      pod's logs use `k8s_get_pod_logs` instead.
    - `since_minutes` (default 60), `limit` (default 200): bound the search.

    Use this for readiness/liveness/startup probe-related symptoms — it
    narrows to probe-failure-shaped log lines automatically, complementing
    `k8s_describe_pod` (current probe config/failures) and
    `k8s_get_recent_namespace_events` (cluster-level `Unhealthy` events) with
    the application's own historical view, which survives pod
    restarts/deployments.

    Requires `IBM_CLOUD_API_KEY` and `IBM_LOGS_ENDPOINT`; missing config is a
    structured error/gap, not "no probe failures." Returns a compact summary
    plus `evidence_ref`. Read-only; no side effects.
    """
    return _json(
        ibm_logs_tools.ibm_logs_search_probe_failures(namespace=namespace, app=app, since_minutes=since_minutes, limit=limit)
    )


@mcp.tool()
def ibm_logs_search_text(namespace: str, app: str, text: str, since_minutes: int = 60, limit: int = 200) -> str:
    """Search IBM Cloud Logs for one precise, known text pattern for one app.

    Parameters:
    - `namespace`: Kubernetes namespace.
    - `app`: the application label/name (e.g. "event-data") — the service's
      `app` label, *not* a `pod_name`. For a specific currently-running
      pod's logs use `k8s_get_pod_logs` instead.
    - `text`: the exact text to search for, e.g. a specific exception class
      (`NullPointerException`), an endpoint path (`/v1/ingest`), or an
      alert/error string named in the reported symptom.
    - `since_minutes` (default 60), `limit` (default 200): bound the search.

    Use this instead of the generic `ibm_logs_search` when you already have
    a precise, known string to match — it's the most targeted of the typed
    log tools. Results are historical and span pod restarts/deployments.

    Requires `IBM_CLOUD_API_KEY` and `IBM_LOGS_ENDPOINT`; missing config is a
    structured error/gap, not "no matches." Returns a compact summary plus
    `evidence_ref`; never returns secrets/credentials found in log text.
    Read-only; no side effects.
    """
    return _json(
        ibm_logs_tools.ibm_logs_search_text(namespace=namespace, app=app, text=text, since_minutes=since_minutes, limit=limit)
    )


@mcp.tool()
def evidence_get_detail(evidence_ref: str) -> str:
    """Fetch the full raw payload behind a compact evidence summary.

    Parameters:
    - `evidence_ref`: the `evidence_ref` string returned alongside a compact
      summary by another tool (e.g. `k8s_describe_pod`, `k8s_get_pod_logs`,
      `k8s_get_recent_namespace_events`, any `prom_get_*`/`prom_query_instant`
      tool, or any `ibm_logs_search*` tool). This must be a real ref
      returned by a prior call in this investigation — it is not a
      guessable ID or free-text query.

    Returns the complete raw tool output (`data`) that was archived under
    `artifacts/` when the original tool call ran — e.g. the full `kubectl
    describe` text, the full log body, or the full Prometheus JSON vector,
    with no truncation.

    Only call this when the compact summary you already received is
    genuinely insufficient — e.g. it's truncated at a point that matters, or
    you need a full stack trace/log body to confirm a hypothesis. Reasoning
    from summaries first keeps context bounded; do not pull raw detail
    speculatively or "just in case." If `evidence_ref` doesn't exist, this
    returns a structured validation error rather than raising. Read-only; no
    side effects — it only reads from the local evidence store, it never
    re-queries Kubernetes/Prometheus/IBM Cloud Logs.
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
def investigate_incident(namespace: str, service: str, symptom: str, since_minutes: int = 60) -> str:
    """Reusable, symptom-driven prompt for a read-only Kubernetes incident investigation.

    Parameters:
    - `namespace`, `service`: the target service and its Kubernetes namespace.
    - `symptom`: the specific reported symptom or alert (e.g. "readiness
      probe failing intermittently", "KafkaConsumerCommitRateLow alert
      fired", "pods restarting with OOMKilled", "elevated 5xx errors and
      latency"). Required — this workflow investigates a concrete, reported
      symptom, not a generic service health check.
    - `since_minutes`: the time window to investigate (default 60).

    If your MCP client provides its own dedicated, project-level command for
    this investigation (for example a slash command that delegates to
    specialized sub-agents or modes), prefer that instead of calling this
    prompt directly — check your client's command list first. Otherwise,
    the returned prompt text below describes the same evidence-grounded,
    `evidence_ref`-preserving, read-only workflow and safety rules to follow
    directly.
    """
    return f"""Investigate a specific reported Kubernetes incident symptom using only
read-only tools.

Namespace: {namespace}
Service: {service}
Symptom: {symptom}
Time window: last {since_minutes} minutes

If your MCP client provides its own dedicated, project-level command for
this investigation (for example a slash command that delegates to
specialized sub-agents or modes), prefer that instead of following the
steps below directly — check your client's command list first. Otherwise,
follow this workflow directly:

Workflow:
1. Read ops://service-catalog and ops://runbook-catalog resources.
2. Use k8s_list_pods with label_selector="app={service}" to discover pods
   for this service only — do not enumerate unrelated services.
3. Inspect recent namespace events (k8s_get_recent_namespace_events),
   filtering out anything unrelated to {service}.
4. For relevant pods, use k8s_describe_pod / k8s_get_pod_logs / k8s_top_pods,
   matched to the symptom (e.g. probe config and restart reasons for probe
   failures, memory for suspected OOM).
5. If deeper evidence is needed, use the typed prom_get_* tools for metrics
   (restart increase/counts, CPU, memory, HTTP error rate, latency p95) and
   ibm_logs_search_* tools for historical logs spanning pod restarts and
   deployments — prefer these over k8s_get_pod_logs for anything that
   predates the current pod incarnation.
6. Search runbooks (runbook_search) for the symptom text to check for a
   known incident pattern and its safety warnings.
7. Reason from each tool's compact summary first; call evidence_get_detail
   only when a summary is insufficient to confirm or rule out a cause.
8. Produce an evidence-grounded incident report: every claim must cite the
   evidence_ref it came from. Do not claim a root cause without evidence.

Rules:
- Do not run or suggest destructive commands (kubectl delete/apply/patch/
  scale/rollout restart/exec, helm upgrade) without human approval.
- Preserve evidence source, evidence_ref, and detail — never summarize away
  the evidence_ref.
- State unknowns explicitly, including missing config (PROMETHEUS_URL,
  IBM_CLOUD_API_KEY, IBM_LOGS_ENDPOINT) or unreachable Prometheus — treat
  these as gaps, not zero/normal values.
- Explicitly list what evidence ruled out.
- If remediation is production-impacting, policy is unclear, or evidence is
  insufficient, set requires_human=true.
"""


if __name__ == "__main__":
    mcp.run()
