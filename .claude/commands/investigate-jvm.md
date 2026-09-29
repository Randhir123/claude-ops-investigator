# Investigate JVM

Use this command for a quick, standalone JVM health check against one OpenJ9/IBM
Semeru service — GC behavior, heap, memory pools, threads — using only this
project's own MCP tools. No incident-investigation framework, subagents, or
evidence store required; this is the "just point it at a service and see what's
going on" entry point.

If you're running inside a fuller incident-investigation setup (e.g.
claude-ops-investigator) and want this JVM data folded into a shared,
evidence_ref-tracked incident report alongside Kubernetes/log/metrics evidence,
use that project's `/investigate-incident` command instead — it routes
GC/heap/OOM-flavored symptoms to a `jvm-analyst` subagent that calls these same
tools plus `evidence_store_external` to archive findings into that
investigation's evidence trail. This command is the lightweight, single-server
alternative to that.

Arguments:

- `namespace` (required) — the Kubernetes namespace the JVM pods run in.
- `service` (required) — the service name (matched against `JVM_LABEL_KEY`,
  e.g. `job=~"(event-data)"`).
- `lookback_minutes` (optional, default 60) — time window for pause stats,
  throughput, and trend queries.

Examples:

```
/investigate-jvm namespace=si-dev-001a service=event-data
/investigate-jvm namespace=si-dev-001a service=time-series-query lookback_minutes=120
```

## Workflow

1. **Confirm `PROMETHEUS_URL` and `JVM_LABEL_KEY` are configured** (see
   `.env.example`). If a tool call returns a structured `isError: true` /
   `errorCategory: "validation"` result naming `PROMETHEUS_URL`, stop and
   report that as a setup gap rather than treating it as "no JVM activity."
2. **Start broad:** call `get_jvm_incident_snapshot(namespace, service,
   lookback_minutes)` for a first-look read across heap, GC pause/throughput,
   native memory, and threads in one call. Note anything in its
   `failed_signals` list as a gap, not a clean result.
3. **Follow up on whatever the snapshot flags**, using the focused tool for
   that signal — and render the matching chart alongside it. Every one of
   these questions is inherently about a window of time, not a single
   number, so pair the data call with its chart call rather than reporting
   avg/max/min in isolation:
   - Elevated GC pause/throughput concerns → `get_gc_pause_stats` /
     `get_gc_throughput` for the numbers, **plus `render_gc_behavior_chart`**
     to see whether frequency/overhead is trending, spiking, or steady across
     the window rather than just its current value.
   - High or rising heap usage → `get_heap_status` for the current number,
     **plus `render_heap_trend_chart`** to see the actual shape (sawtooth
     returning to baseline vs. a rising floor), and **`render_gc_memory_correlation_chart`**
     to check whether it's a real leak (rising heap floor + climbing GC
     frequency, visible as the two lines trending up together) or a load
     spike (heap keeps returning to baseline each cycle even with elevated
     GC activity) — read the chart's shape, don't infer it from
     `get_gc_memory_correlation`'s raw numbers alone.
   - Native/non-heap memory concerns → `get_memory_pool_breakdown` and
     `get_native_memory_summary` (point-in-time only — there is no chart tool
     for these yet; if the trend matters, re-run at intervals and compare).
   - Committed memory looks high relative to what's actually used → `get_heap_fragmentation`
     for the per-pool committed-vs-used gap. A large gap is space the JVM is holding from the
     OS, not internal fragmentation JMX can see — read its `caveat` before calling it a problem.
   - Heap/GC pressure with an unclear cause → `get_memory_allocation_rate` to see whether
     young-gen churn (not necessarily unhealthy on its own) is unusually high, and
     `get_memory_leak_indicator` for a regression-fitted tenured/old-gen trend (slope, R²,
     naive days-to-full) — the closest single-number leak signal this project offers. Neither
     substitutes for the other: allocation rate is churn, the leak indicator is the old-gen
     trend; report both caveats verbatim if either number is surfaced.
   - Symptom onset correlates with a recent deploy/restart/config change → `get_before_after_deploy_comparison`
     with the deploy's Unix-epoch timestamp, to compare heap/GC/thread averages just before vs.
     just after it. This shows correlation in time only — corroborate against actual
     deployment/restart history before calling the deploy the cause.
   - Elevated or climbing thread count → `get_thread_status`. If a hang or
     deadlock is suspected rather than just a rising count, say so explicitly
     and recommend a real thread dump analyzed with IBM TMDA — this project's
     tools cannot see thread state, only thread count.
4. **A chart tool returns an image directly, not JSON** — pass `namespace`,
   `service`, and `lookback_minutes` (and `step` if you need finer/coarser
   granularity than the default `60s`) exactly as you would to the
   corresponding data tool. If the underlying query returned no data points
   for the window, the chart tool returns a structured error instead of a
   blank image (`errorCategory: "business"`, message ending in "nothing to
   chart") — treat that as a real gap (wrong `JVM_LABEL_KEY`/service match,
   or a window shorter than the scrape interval), not as "chart tools don't
   work here."
5. **Report caveats verbatim, don't paraphrase them away.** Several tools
   return a `caveat` field (GC pause max/min are bucket-averaged
   approximations, not true single-event extremes; heap "max" isn't the
   pod's memory limit; native memory visibility stops at JVM-managed pools;
   thread count isn't thread state). Include the relevant caveat any time you
   report the corresponding number or chart, so it isn't read as more precise
   or complete than it is.
6. **State unknowns explicitly.** Treat any `isError: true` result or entry in
   `failed_signals` as a gap — never report a metric that failed to return
   data as if it were a clean/normal reading.

ARGUMENTS: namespace, service, lookback_minutes
