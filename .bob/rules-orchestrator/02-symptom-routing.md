# Symptom-to-Mode Routing

Choose the narrowest relevant set of specialist modes for the reported
symptom — do not run every mode by default. This routing applies after the
mandatory wave 0 Prometheus preflight (see `00-workflow.md`) has passed.

**The steps mapped below are each specialist's baseline sweep.** They run
in full, every time a mode is delegated to, regardless of whether a Code
Context Brief is present in the Structured Finding Brief — see
`00-workflow.md`'s ordering rule. A brief may prompt additional,
brief-informed queries on top of these steps; it must never cause a
specialist to skip, narrow, or substitute any of them.

- **OOM / restarts / crash-loop** → k8s-evidence-collector (pod list,
  describe affected pods, namespace events, top pods), then
  prometheus-analyst (`prom_get_pod_memory_usage`,
  `prom_get_pod_restart_increase` over the incident window;
  `prom_get_pod_restart_counts` only as supporting context for the current
  cumulative total), then log-analyst if the incident spans earlier pod
  incarnations. If the affected service is a JVM (OpenJ9/IBM Semeru) workload
  and pod-level evidence doesn't explain *why* the OOM happened, also run
  jvm-analyst (`get_heap_status`, `get_native_memory_summary`) to check
  whether it was heap-driven or native-memory-driven — don't guess from
  restart counts alone.
- **GC pause / throughput / heap pressure alert (JVM services only)** →
  jvm-analyst first (`get_jvm_incident_snapshot` for a first-look read, then
  `get_gc_pause_stats`/`get_gc_throughput`/`get_gc_behavior_over_time` and
  `get_heap_status`/`get_heap_trend_over_time`/`get_gc_memory_correlation` as
  the symptom narrows), then prometheus-analyst only if pod-level CPU/memory
  corroboration is also needed, then k8s-evidence-collector if a specific pod
  needs to be identified or its resource limits checked.
- **Readiness / liveness probe failures** → k8s-evidence-collector (namespace
  events, describe affected pods), runbook-analyst, log-analyst
  (`ibm_logs_search_probe_failures`), prometheus-analyst
  (`prom_get_pod_restart_increase`; `prom_get_http_error_rate` /
  `prom_get_latency_p95` if available for the service).
- **Kafka commit rate low / consumer lag** → runbook-analyst first, then
  log-analyst (`ibm_logs_search` or `ibm_logs_search_text`), then
  k8s-evidence-collector for current pod status.
- **Latency / elevated errors** → prometheus-analyst
  (`prom_get_latency_p95`, `prom_get_http_error_rate`) as the primary
  signal, then log-analyst for the errors behind the numbers, then
  k8s-evidence-collector only if logs point to a specific running pod.
- If the symptom doesn't clearly match one of the above, start with
  runbook-analyst on the symptom text and let the result steer which modes
  are needed next.

jvm-analyst calls the separate `jvm-troubleshooter` MCP server and archives
its own findings via `evidence_store_external` — it does not automatically
run for non-JVM services; only route to it when the affected service is a
known JVM (OpenJ9/IBM Semeru) workload.

Always finish with **incident-reporter** once the relevant specialists have
run.
