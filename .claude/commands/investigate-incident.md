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

1. **Restate the symptom.** Echo back namespace, service, symptom, and time window
   before doing anything else, so the investigation stays scoped to what was
   actually reported.
2. **Read context first.** Read the `ops://service-catalog` and
   `ops://runbook-catalog` resources to learn the service's known behavior,
   production risk, and any runbook that already matches this symptom pattern.
3. **Discover pods for the target service only.** Use `k8s_list_pods` with
   `label_selector="app={service}"` in `{namespace}`. Do not enumerate or
   investigate unrelated services in the namespace.
4. **Choose tools based on the symptom** — do not run every tool by default.
   Map the symptom to the narrowest relevant set:
   - **OOM / restarts** → `k8s_list_pods`, `k8s_describe_pod` (affected pods),
     `k8s_get_pod_logs`, `k8s_get_recent_namespace_events`, `k8s_top_pods`
   - **Readiness / liveness probe failures** → `k8s_get_recent_namespace_events`,
     `k8s_describe_pod` (affected pods), `k8s_get_pod_logs`, `runbook_search`
   - **Kafka commit rate low / consumer lag** → `runbook_search`,
     `k8s_get_pod_logs`, pod status via `k8s_list_pods`,
     `k8s_get_recent_namespace_events`
   - **Latency / elevated errors** → `k8s_get_pod_logs` and the relevant pod
     status (`k8s_list_pods` / `k8s_describe_pod` only if logs point to a
     specific pod)
   - If the symptom doesn't clearly match one of the above, start with
     `runbook_search` on the symptom text and let the result steer which
     tools are needed next.
5. **Use evidence summaries first.** Tool responses return a compact summary
   plus an `evidence_ref`. Reason from the summary.
6. **Do not call `evidence_get_detail` unless the summary is insufficient** to
   confirm or rule out a cause — e.g. the summary is truncated at a point that
   matters, or you need the full stack trace/log body to verify a hypothesis.
7. **Produce a structured incident report grounded in evidence refs.** Every
   claim must cite the `evidence_ref` it came from. Do not claim a root cause
   without evidence.
8. **Explicitly list what was ruled out and what remains unknown.** State which
   candidate causes the evidence excludes, and use `unknowns` for anything the
   gathered evidence can't confirm.
9. **Read-only only.** Do not use shell commands or raw `kubectl` — only the
   narrow, typed MCP tools. Never run or suggest `kubectl delete`, `apply`,
   `patch`, `scale`, `rollout restart`, `helm upgrade`, or `kubectl exec`. Mark
   any production-impacting remediation as `requires_human: true`.

ARGUMENTS: namespace, service, symptom, since_minutes
