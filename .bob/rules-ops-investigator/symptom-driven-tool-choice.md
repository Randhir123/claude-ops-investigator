# Symptom-Driven Tool Choice

## Core Principle

Choose tools based on the reported symptom. Do not run every tool by default.

## Symptom-to-Tool Mapping

### OOM / Restarts / Crash-Loop

**Primary tools:**
- `k8s_list_pods` - discover affected pods
- `k8s_describe_pod` - get termination reason (OOMKilled, Error)
- `k8s_get_recent_namespace_events` - cluster-level restart events
- `prom_get_pod_restart_increase(namespace, service, since_minutes)` - size restarts over incident window
- `prom_get_pod_memory_usage` - current memory pressure

**Supporting tools:**
- `prom_get_pod_restart_counts` - cumulative restart count (context only)
- `k8s_top_pods` - current resource usage snapshot
- `k8s_get_pod_logs` - current pod logs
- `ibm_logs_search_errors` - historical errors across restarts

### Readiness / Liveness Probe Failures

**Primary tools:**
- `k8s_get_recent_namespace_events` - Unhealthy events
- `k8s_describe_pod` - probe configuration and failures
- `runbook_search` - known probe failure patterns
- `ibm_logs_search_probe_failures` - historical probe errors

**Supporting tools:**
- `prom_get_pod_restart_increase` - size restarts over incident window
- `prom_get_http_error_rate` - availability impact
- `prom_get_latency_p95` - latency impact
- `k8s_get_pod_logs` - current pod logs

### Kafka Commit Rate Low / Consumer Lag

**Primary tools:**
- `runbook_search` - known Kafka patterns
- `ibm_logs_search_text` - specific error strings
- `k8s_list_pods` - current pod status
- `k8s_get_recent_namespace_events` - recent pod events

**Supporting tools:**
- `prom_query_instant` - Kafka-specific metrics (if available)
- `ibm_logs_search_errors` - ERROR-level logs

### Latency / Elevated Errors

**Primary tools:**
- `prom_get_latency_p95` - latency measurement
- `prom_get_http_error_rate` - error rate measurement
- `ibm_logs_search_errors` - errors behind the numbers

**Supporting tools:**
- `ibm_logs_search_text` - specific error patterns
- `k8s_get_pod_logs` - current pod logs (if needed)
- `k8s_list_pods` - pod status
- `k8s_describe_pod` - pod details (if logs point to specific pod)

## Tool Preference Order

1. **Typed tools over generic**
   - Prefer `prom_get_pod_restart_increase` over `prom_query_instant`
   - Prefer `ibm_logs_search_errors` over `ibm_logs_search`
   - Prefer `ibm_logs_search_probe_failures` over `ibm_logs_search_text`

2. **Historical over current-only**
   - Prefer `ibm_logs_search*` over `k8s_get_pod_logs` for cross-restart analysis
   - Use `k8s_get_pod_logs` only for "what is this pod doing right now"

3. **Incident-window metrics over cumulative**
   - Prefer `prom_get_pod_restart_increase(since_minutes)` for incident sizing
   - Use `prom_get_pod_restart_counts` only as supporting context

## Unknown Symptom

If symptom doesn't match a known pattern:
1. Start with `runbook_search` on symptom text
2. Let runbook result guide tool selection
3. Fall back to broad discovery if no runbook match:
   - `k8s_list_pods`
   - `k8s_get_recent_namespace_events`
   - `k8s_describe_pod` (affected pods)

## Anti-Patterns

❌ **Don't do this:**
- Run every tool for every incident
- Use generic tools when typed tools exist
- Fetch current pod logs for historical analysis

✅ **Do this:**
- Choose tools based on symptom
- Use narrowest tool that answers the question
- Use historical tools for cross-restart analysis
