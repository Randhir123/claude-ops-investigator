---
description: Quick, standalone JVM health check (GC, heap, memory pools, threads) against one OpenJ9/IBM Semeru service via the jvm-troubleshooter MCP server -- no orchestrator delegation or evidence store required
argument-hint: namespace=<namespace> service=<service> lookback_minutes=<minutes>
---

Use this command for a quick, standalone JVM health check against one
OpenJ9/IBM Semeru service — GC behavior, heap, memory pools, threads,
allocation rate, leak trend — using only the separate `jvm-troubleshooter`
MCP server's own tools (configured in `.bob/mcp.json`). No orchestrator
delegation, specialist modes, or evidence store required; this is the "just
point it at a service and see what's going on" entry point.

If you want this JVM data folded into a shared, evidence_ref-tracked
incident report alongside Kubernetes/log/metrics evidence instead, use
`/investigate-incident` — its orchestrator routes GC/heap/OOM-flavored
symptoms to the `jvm-analyst` mode, which calls these same tools plus
`evidence_store_external` to archive findings into that investigation's
evidence trail. This command is the lightweight, single-server alternative
to that, and can be run from whatever mode you're currently in — it needs no
mode switch.

Arguments (namespace and service required): namespace, service,
lookback_minutes (optional, default 60 — time window for pause stats,
throughput, and trend queries).

$ARGUMENTS

## Workflow

1. **Confirm `PROMETHEUS_URL` and `JVM_LABEL_KEY` are configured** for the
   `jvm-troubleshooter` server (see `mcp-servers/jvm-troubleshooter/.env.example`
   and this project's own `.bob/mcp.json`). If a tool call returns a
   structured `isError: true` / `errorCategory: "validation"` result naming
   `PROMETHEUS_URL`, stop and report that as a setup gap rather than "no JVM
   activity."
2. **Start broad:** call `get_jvm_incident_snapshot(namespace, service,
   lookback_minutes)` for a first-look read across heap, GC pause/throughput,
   native memory, and threads in one call. Note anything in its
   `failed_signals` list as a gap, not a clean result.
3. **Follow up on whatever the snapshot flags**, using the focused tool for
   that signal — and render the matching chart alongside it when the
   question is about a window of time, not a single number:
   - Elevated GC pause/throughput concerns → `get_gc_pause_stats` /
     `get_gc_throughput`, plus `render_gc_behavior_chart` for the trend.
   - High or rising heap usage → `get_heap_status`, plus
     `render_heap_trend_chart` (sawtooth vs. rising floor) and
     `render_gc_memory_correlation_chart` (real leak vs. load spike) — read
     the chart's shape, don't infer it from raw numbers alone.
   - Native/non-heap memory concerns → `get_memory_pool_breakdown` and
     `get_native_memory_summary` (point-in-time only).
   - Committed memory looks high relative to what's used → `get_heap_fragmentation`
     for the per-pool committed-vs-used gap — space held from the OS, not
     internal fragmentation JMX can see; read its `caveat` before calling it
     a problem.
   - Heap/GC pressure with an unclear cause → `get_memory_allocation_rate`
     (young-gen churn) and `get_memory_leak_indicator` (regression-fitted
     tenured/old-gen trend: slope, R², naive days-to-full). Neither
     substitutes for the other — report both caveats verbatim if surfaced.
   - Symptom onset correlates with a recent deploy/restart/config change →
     `get_before_after_deploy_comparison` with the deploy's Unix-epoch
     timestamp. Correlation in time only — corroborate against actual
     deployment/restart history before calling the deploy the cause.
   - Elevated or climbing thread count → `get_thread_status`. If a hang or
     deadlock is suspected, say so explicitly and recommend a real thread
     dump analyzed with IBM TMDA — this server cannot see thread state.
4. **A chart tool returns an image directly, not JSON.** If the underlying
   query returned no data points, the chart tool returns a structured error
   (`errorCategory: "business"`, "nothing to chart") instead of a blank
   image — treat that as a real gap (wrong `JVM_LABEL_KEY`/service match, or
   a window shorter than the scrape interval), not a rendering failure.
5. **Report caveats verbatim, don't paraphrase them away.** Every tool that
   returns a `caveat` field is flagging a real limit (bucket-averaged GC
   pause approximations, heap "max" vs. pod memory limit, native-memory
   blind spots, thread count vs. thread state, allocation-rate/leak-indicator/
   fragmentation/before-after caveats). Carry the relevant one into your
   report whenever you cite the corresponding number or chart.
6. **State unknowns explicitly.** Treat any `isError: true` result or entry
   in `failed_signals` as a gap — never report a metric that failed to
   return data as if it were a clean/normal reading.
