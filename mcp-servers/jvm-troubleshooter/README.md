# jvm-troubleshooter-mcp

A standalone MCP server exposing read-only OpenJ9/IBM Semeru JVM
observability tools -- GC, heap, memory pools, threads -- backed by
Prometheus (or a Thanos Query front-end). Built to work either on its own,
or as a second tool source alongside an incident-investigation MCP server
such as [claude-ops-investigator](https://github.com/Randhir123/claude-ops-investigator).

## Why a separate server instead of folding this into an existing investigator MCP

- **Independent lifecycle.** JVM/OpenJ9 metric-naming knowledge (JMX Exporter
  conventions, OpenJ9 pool names, the GC-pause-approximation caveat) changes
  on its own schedule, separate from an investigator's Kubernetes/log/runbook
  tooling. Coupling them means every JVM-specific fix requires touching and
  re-testing the whole investigator server.
- **Reusable on its own merits.** Any Java/OpenJ9 shop can install and use
  this server without adopting an entire incident-investigation framework,
  its subagents, or its evidence-store conventions.
- **Composable, not load-bearing.** An investigator integrates this by
  pointing an MCP client config at a second server (see
  ["How the two servers work together"](../../README.md#how-the-two-servers-work-together)
  in the claude-ops-investigator README),
  not by importing this package's internals. Either project can evolve, or be
  replaced, without breaking the other.
- **Matches how the ecosystem is already shaped.** Prometheus itself,
  Alertmanager, and most exporters are single-purpose servers composed
  together rather than one monolith -- this follows the same shape.

## What this is not

This server only ever issues `GET /api/v1/query` and `GET /api/v1/query_range`
against Prometheus. It cannot mutate Prometheus, the JVMs it scrapes, or the
cluster. It also cannot do anything JMX itself cannot do -- see "Hard
capability boundaries" below before treating any tool's output as a complete
diagnosis.

## Requirements

- A Prometheus (or Thanos Query) endpoint that scrapes OpenJ9 JVMs via the
  standard [Prometheus JMX Exporter](https://github.com/prometheus/jmx_exporter),
  using its default metric names (`java_lang_GarbageCollector_*`,
  `java_lang_Memory_*`, `java_lang_MemoryPool_*`, `java_lang_Threading_*`,
  `java_lang_ClassLoading_*`, `java_nio_BufferPool_*`).
- Python >= 3.10.

> **If your cluster uses Micrometer-style metric names instead**
> (`jvm_gc_collection_seconds_count`, `jvm_memory_pool_used_bytes`, etc. --
> the convention Spring Boot Actuator and Micrometer registries use), this
> server's PromQL will silently return no data. It was built and validated
> against real JMX-Exporter-scraped OpenJ9 metrics, not Micrometer's naming.
> Porting it would mean rewriting every PromQL expression's metric names, not
> just changing a config value.

## Install

```bash
cd jvm-troubleshooter-mcp
pip install -e .
cp .env.example .env   # then edit PROMETHEUS_URL and JVM_LABEL_KEY
```

## Run standalone

```bash
python -m jvm_troubleshooter.mcp.server
```

Or point any MCP-compatible client at it via `.mcp.json`:

```json
{
  "mcpServers": {
    "jvm-troubleshooter": {
      "command": "python",
      "args": ["-m", "jvm_troubleshooter.mcp.server"],
      "env": {
        "PROMETHEUS_URL": "http://prometheus:9090",
        "JVM_LABEL_KEY": "job"
      }
    }
  }
}
```

`JVM_LABEL_KEY` must match whatever Prometheus label your scrape config uses
to identify a service/job (commonly `job`, sometimes `service` or `app`). A
wrong value returns empty results, not an error -- confirm it against your
own scrape config before trusting a "no data" response.

### Via Grafana instead of direct Prometheus

To query through Grafana's datasource proxy (no port-forward, just a Grafana
login), set `GRAFANA_URL`, `GRAFANA_DATASOURCE_UID`, and either
`GRAFANA_API_TOKEN` (service account token, preferred) or
`GRAFANA_SESSION_COOKIE` (browser `grafana_session` cookie) in `.env` --
not in `.mcp.json`. `GRAFANA_URL` takes precedence over `PROMETHEUS_URL`
when set. See `.env.example`.

## Tools

| Tool | Purpose |
|---|---|
| `get_gc_activity` | Cumulative GC count/time per pod and generation |
| `get_gc_pause_stats` | Avg (exact) / max / min (approx) GC pause ms per pod |
| `get_gc_throughput` | GC throughput % and avg collection interval per pod |
| `get_gc_behavior_over_time` | GC frequency/overhead as a time series |
| `get_heap_status` | Current heap used/max/committed/%used per pod |
| `get_heap_trend_over_time` | Heap used/max as a time series (leak signature) |
| `get_memory_pool_breakdown` | Per-OpenJ9-pool used/max (nursery, tenured, JIT caches, class storage) |
| `get_native_memory_summary` | Non-heap JVM memory + direct/NIO buffers per pod |
| `get_heap_fragmentation` | Committed-vs-used per heap pool — space held from the OS but unused |
| `get_memory_allocation_rate` | Estimated young-gen allocation rate (MB/s, GB/hr) — churn, not a leak signal |
| `get_thread_status` | Live thread count + loaded class count per pod |
| `get_gc_memory_correlation` | Heap trend + GC frequency trend lined up, for leak-vs-load-spike triage |
| `get_memory_leak_indicator` | Tenured/old-gen trend fitted with linear regression — slope, R², days-to-full |
| `get_before_after_deploy_comparison` | Heap/GC/thread averages just before vs. just after a deploy timestamp |
| `get_jvm_incident_snapshot` | One-call combined snapshot across all of the above, partial-failure tolerant |
| `render_heap_trend_chart` | PNG line chart of heap used/max per pod over the lookback window |
| `render_gc_behavior_chart` | PNG chart of GC frequency and overhead per pod+generation over the lookback window |
| `render_gc_memory_correlation_chart` | PNG dual-axis chart overlaying heap used against GC frequency — the leak-vs-load-spike visual |

### Charts

The `render_*_chart` tools return an actual PNG (via MCP's image content type),
not JSON — an MCP client that supports image content (Claude Desktop, Claude
Code, and most others) renders it inline in the conversation. Reach for these
whenever the question is about a *window* of time rather than a single
current value — `get_heap_status` tells you heap is at 78% right now;
`render_heap_trend_chart` shows you whether that's a stable plateau, a
sawtooth returning to baseline each GC cycle, or a rising floor. If a query
returns no data points for the requested window, the chart tool returns a
structured `errorCategory: "business"` error instead of a blank image —
that's a real signal (wrong `JVM_LABEL_KEY`/service match, or too short a
window) worth surfacing, not a rendering failure to retry past. Rendering is
headless (matplotlib's `Agg` backend) — no display server needed even when
this server runs on a headless host.

Every tool returns the shared result contract:
`{"isError": false, "data": {...}}` on success, or
`{"isError": true, "errorCategory": ..., "isRetryable": ..., "message": ..., "attempted": ..., "alternatives": [...]}`
on failure. This intentionally mirrors `claude-ops-investigator`'s
`claude_ops.errors` shape (see `src/jvm_troubleshooter/errors.py`'s docstring)
so a caller already fluent in one project's tool outputs doesn't have to
learn a second convention -- but it's a small, duplicated contract, not a
shared import, so this server stays independently installable.

## Hard capability boundaries (read before trusting a "clean" result)

- **GC pause max/min are approximations, not true extremes.** JMX's
  `GarbageCollectorMXBean` only exposes cumulative counters, never
  per-collection durations. `get_gc_pause_stats`'s max/min are bucket-averaged
  over 5-minute windows -- validated against a real GCeasy report parsed from
  an actual verbose GC log to understate the true worst-case pause by 8-9x
  (945ms actual vs. 109ms from this approximation, same workload, same
  window). A genuine per-event max, a pause histogram, or GC *causes* all
  require parsing the verbose GC log; no PromQL formula against these metrics
  can recover them.
- **Heap "max" is not the pod's memory limit.** It's the JVM's own -Xmx-derived
  ceiling. Non-heap JVM memory and native allocations also draw from
  `resources.limits.memory`; a pod can be OOM-killed with heap usage still
  low. See `get_heap_status`'s and `get_native_memory_summary`'s caveats.
- **Native memory visibility stops at JVM-managed pools.** There's no
  visibility into malloc'd native memory outside the pools JMX reports. A gap
  between container RSS and heap+non-heap-pools is real and needs a
  container-level memory profile or an OpenJ9 native memory report, not a
  different PromQL query.
- **Thread count is not thread state.** It cannot show RUNNABLE vs.
  BLOCKED/WAITING, or what a thread is stuck on. Diagnosing a hang or
  deadlock needs a real thread dump (javacore), analyzed with a tool like IBM
  TMDA (Thread and Monitor Dump Analyzer for Java) -- a separate tool from
  GCMV, which only covers GC/memory logs.
- **Allocation rate is churn, derived from a workaround, not a direct
  counter.** `get_memory_allocation_rate` applies `increase()` to a gauge
  (young-gen pool usage) that resets on every GC, relying on Prometheus's
  counter-reset compensation to reconstruct a total -- deliberate, but still
  an estimate, and it overcounts if the pool itself is resized mid-window.
- **The leak indicator is a regression fit, not a certainty.** A short
  window, an unusual traffic period, or a recent config change can produce a
  misleading slope; `r_squared` near 0 means noise, and `days_to_full` is a
  naive linear projection, not a forecast. See `get_memory_leak_indicator`'s
  `caveat`.
- **The before/after deploy comparison shows correlation, not causation,**
  and needs the Prometheus `@` modifier (default-enabled since Prometheus
  2.33) -- on an older or explicitly-disabled Prometheus, every field in
  `get_before_after_deploy_comparison` comes back as a query error.

## Development

```bash
pip install -e ".[dev]"
pytest
```
