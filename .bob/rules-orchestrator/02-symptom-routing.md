# Symptom-to-Mode Routing

Ported from `.claude/commands/investigate-incident.md`. Choose the narrowest
relevant set of specialist modes for the reported symptom — do not run every
mode by default.

- **OOM / restarts / crash-loop** → k8s-evidence-collector (pod list,
  describe affected pods, namespace events, top pods), then
  prometheus-analyst (`prom_get_pod_memory_usage`,
  `prom_get_pod_restart_increase` over the incident window;
  `prom_get_pod_restart_counts` only as supporting context for the current
  cumulative total), then log-analyst if the incident spans earlier pod
  incarnations.
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

Always finish with **incident-reporter** once the relevant specialists have
run.
