# Investigate Incident

Use this command when a developer wants a read-only Kubernetes investigation of a
**specific reported symptom or alert** — not a general service health check. Every
invocation must be anchored to a concrete symptom; if no symptom is given, ask for
one before doing any investigation.

Arguments (all required):

- namespace
- service
- symptom — the reported symptom or alert name/text (e.g. "KafkaConsumerCommitRateLow alert fired", "readiness probe failing", "p99 latency spike")
- since_minutes — time window to investigate

Examples:

```
/investigate-incident namespace=si service=event-data symptom="KafkaConsumerCommitRateLow alert fired" since_minutes=60
/investigate-incident namespace=si service=event-data symptom="pods restarting with OOMKilled" since_minutes=30
/investigate-incident namespace=si service=tenant-configuration symptom="readiness probe failing intermittently" since_minutes=45
/investigate-incident namespace=si service=rest-gw symptom="elevated 5xx errors and latency reported by callers" since_minutes=60
```

## Workflow

1. **Use the subagent workflow.** Use `incident-coordinator` as the top-level
   subagent when available — the main Claude Code conversation must not
   gather all evidence directly. The coordinator delegates to specialist
   subagents based on the symptom: `k8s-evidence-collector`,
   `prometheus-analyst`, `log-analyst`, `runbook-analyst`, and
   `incident-reporter` last. If subagents are unavailable, explicitly state:
   "Subagents unavailable; using single-agent fallback." Final output must
   include a "Subagent usage audit" table with columns: Subagent | Task |
   Tools used | Evidence refs | Result.
2. **Restate the symptom.** Echo back namespace, service, symptom, and time window
   before doing anything else, so the investigation stays scoped to what was
   actually reported.
3. **Read context first.** Read the `ops://service-catalog` and
   `ops://runbook-catalog` resources to learn the service's known behavior,
   production risk, and any runbook that already matches this symptom pattern.
4. **Discover pods for the target service only.** Use `k8s_list_pods` with
   `label_selector="app={service}"` in `{namespace}`. Do not enumerate or
   investigate unrelated services in the namespace.
5. **Choose tools based on the symptom** — do not run every tool by default.
   Map the symptom to the narrowest relevant set:
   - **OOM / restarts / crash-loop** → `k8s_list_pods`, `k8s_describe_pod`
     (affected pods), `k8s_get_recent_namespace_events`, `k8s_top_pods`;
     `prom_get_pod_memory_usage` and `prom_get_pod_restart_increase(namespace,
     service, since_minutes)` to size and corroborate the pattern with
     metrics over the incident window — use `prom_get_pod_restart_counts` only
     as supporting context for the current cumulative count; `k8s_get_pod_logs`
     for the current pod, and `ibm_logs_search_errors` if the incident spans
     earlier pod incarnations.
   - **Readiness / liveness probe failures** → `k8s_get_recent_namespace_events`,
     `k8s_describe_pod` (affected pods), `runbook_search`;
     `ibm_logs_search_probe_failures` for historical probe/app errors across
     restarts; `prom_get_pod_restart_increase(namespace, service,
     since_minutes)` to size restarts over the incident window (cumulative
     restart counts only as supporting context); `prom_get_http_error_rate`
     and `prom_get_latency_p95` if availability/latency data is available for
     the service.
   - **Kafka commit rate low / consumer lag** → `runbook_search` first, then
     `ibm_logs_search` (or `ibm_logs_search_text` with the specific error
     string) for historical evidence, plus `k8s_list_pods` and
     `k8s_get_recent_namespace_events` for current pod status; typed
     `prom_get_*` metrics if consumer-related metrics are available.
   - **Latency / elevated errors** → `prom_get_latency_p95` and
     `prom_get_http_error_rate` as the primary signal; `ibm_logs_search_errors`
     or `ibm_logs_search_text` for the errors behind the numbers; fall back to
     `k8s_get_pod_logs` and pod status (`k8s_list_pods` / `k8s_describe_pod`)
     only if logs point to a specific currently-running pod.
   - If the symptom doesn't clearly match one of the above, start with
     `runbook_search` on the symptom text and let the result steer which
     tools are needed next.
6. **Prefer IBM Cloud Logs over live pod logs for historical analysis.**
   `ibm_logs_search*` results survive pod restarts, deployments, and
   scale-downs and span all pod incarnations of the service. Reserve
   `k8s_get_pod_logs` for "what is this specific running pod doing right now"
   checks.
7. **Prefer typed Prometheus tools over free-form `prom_query_instant`.** Use
   `prom_get_pod_restart_increase` / `prom_get_pod_restart_counts` /
   `prom_get_pod_cpu_usage` / `prom_get_pod_memory_usage` /
   `prom_get_http_error_rate` / `prom_get_latency_p95` for anything they
   cover. Only reach for `prom_query_instant` when none of them answer the
   question, and keep the query narrow. For OOM/restart/crash-loop/liveness
   symptoms, prefer `prom_get_pod_restart_increase(namespace, service,
   since_minutes)` over the incident window; use `prom_get_pod_restart_counts`
   only as supporting context for the current cumulative count.
8. **Prometheus connectivity is coordinator-owned.** If `prometheus-analyst`
   reports Prometheus as unreachable, do not treat metrics as zero.
   `incident-coordinator` may call `prom_ensure_connection` only when the user
   explicitly asked for a Prometheus-backed investigation or when metrics are
   necessary to answer the reported symptom. `prom_ensure_connection` may
   start a local `kubectl port-forward` only if
   `PROMETHEUS_AUTO_PORT_FORWARD=true`. If it succeeds, delegate back to
   `prometheus-analyst` to retry. If it fails, or auto-port-forward is
   disabled, record Prometheus as an `unknowns` gap.
9. **Use evidence summaries first.** Tool responses return a compact summary
   plus an `evidence_ref`. Reason from the summary.
10. **Do not call `evidence_get_detail` unless the summary is insufficient** to
    confirm or rule out a cause — e.g. the summary is truncated at a point that
    matters, or you need the full stack trace/log body to verify a hypothesis.
11. **Produce a structured incident report grounded in evidence refs.** Every
    claim must cite the `evidence_ref` it came from. Do not claim a root cause
    without evidence.
12. **Explicitly list what was ruled out and what remains unknown.** State
    which candidate causes the evidence excludes, and use `unknowns` for
    anything the gathered evidence can't confirm — including missing
    `PROMETHEUS_URL`, `IBM_CLOUD_API_KEY`, or `IBM_LOGS_ENDPOINT` config, or
    an unreachable Prometheus, reported by a tool.
13. **Read-only only.** Do not use shell commands or raw `kubectl` — only the
    narrow, typed MCP tools. Never run or suggest `kubectl delete`, `apply`,
    `patch`, `scale`, `rollout restart`, `helm upgrade`, or `kubectl exec`.
    Mark any production-impacting remediation as `requires_human: true`.

ARGUMENTS: namespace, service, symptom, since_minutes
