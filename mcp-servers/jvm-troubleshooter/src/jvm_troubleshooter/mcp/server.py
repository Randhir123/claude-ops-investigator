"""jvm-troubleshooter MCP server entrypoint.

Registers every tool in `jvm_troubleshooter.tools.*` with FastMCP over stdio
transport. Run standalone with:

    python -m jvm_troubleshooter.mcp.server

or wire it into a client's `.mcp.json` as its own server entry, e.g.:

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

This server is stateless and read-only: every tool ends in a GET against
Prometheus's `/api/v1/query` or `/api/v1/query_range`. There is no code path
here that can mutate Prometheus, the JVMs it scrapes, or the cluster.
"""

from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Image

from jvm_troubleshooter.tools import (
    allocation_tools,
    chart_tools,
    comparative_tools,
    correlation_tools,
    gc_tools,
    heap_tools,
    incident_tools,
    leak_tools,
    memory_pool_tools,
    runtime_tools,
    thread_tools,
    window_tools,
)

load_dotenv()

mcp = FastMCP("jvm-troubleshooter")


# --- GC ----------------------------------------------------------------


@mcp.tool()
def get_gc_activity(namespace: str, service: str) -> dict[str, Any]:
    """Current cumulative GC collection counts and total time, per pod and generation
    (scavenge=young, global=old/full). Cumulative since JVM start or last pod restart."""
    return gc_tools.get_gc_activity(namespace, service)


@mcp.tool()
def get_gc_pause_stats(namespace: str, service: str, lookback_minutes: int = 60) -> dict[str, Any]:
    """Avg/max/min GC pause (ms) per pod over a lookback window. Avg is a true weighted average;
    max/min are bucket-averaged approximations, NOT true single-event extremes -- validated to
    understate a real worst-case pause by 8-9x. See the returned 'caveat' field."""
    return gc_tools.get_gc_pause_stats(namespace, service, lookback_minutes)


@mcp.tool()
def get_gc_throughput(namespace: str, service: str, lookback_minutes: int = 60) -> dict[str, Any]:
    """GC throughput % (time NOT paused for GC) and avg interval between collections, per pod.
    Both figures are exact (cumulative-counter derived), no bucket-averaging involved."""
    return gc_tools.get_gc_throughput(namespace, service, lookback_minutes)


@mcp.tool()
def get_gc_behavior_over_time(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
) -> dict[str, Any]:
    """GC frequency (collections/min) and overhead (%) as a time series, per pod and generation.
    Use when the question is whether GC behavior is trending/changing, not just its current value."""
    return gc_tools.get_gc_behavior_over_time(namespace, service, lookback_minutes, step)


# --- Heap ----------------------------------------------------------------


@mcp.tool()
def get_heap_status(namespace: str, service: str) -> dict[str, Any]:
    """Current heap used/max/committed and %used, per pod. Note: heap 'max' is the JVM's own
    -Xmx-derived ceiling, not the pod's resources.limits.memory -- see the returned 'caveat'."""
    return heap_tools.get_heap_status(namespace, service)


@mcp.tool()
def get_heap_trend_over_time(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
) -> dict[str, Any]:
    """Heap used/max as a time series. Use to spot growth trends or a leak signature (used
    failing to return to baseline after each GC cycle)."""
    return heap_tools.get_heap_trend_over_time(namespace, service, lookback_minutes, step)


# --- Memory pools / native memory -----------------------------------------


@mcp.tool()
def get_memory_pool_breakdown(namespace: str, service: str) -> dict[str, Any]:
    """Current used/max per OpenJ9 memory pool (nursery-allocate, nursery-survivor, tenured-SOA,
    tenured-LOA, JIT code cache, JIT data cache, class storage, misc non-heap), per pod."""
    return memory_pool_tools.get_memory_pool_breakdown(namespace, service)


@mcp.tool()
def get_native_memory_summary(namespace: str, service: str) -> dict[str, Any]:
    """Non-heap JVM memory (JIT caches, class storage, misc) plus direct/NIO buffer memory, per
    pod. Has no visibility into malloc'd native memory outside JVM-managed pools -- see 'caveat'."""
    return memory_pool_tools.get_native_memory_summary(namespace, service)


@mcp.tool()
def get_heap_fragmentation(namespace: str, service: str) -> dict[str, Any]:
    """Committed-vs-used per heap pool (nursery-allocate, nursery-survivor, tenured-SOA,
    tenured-LOA), per pod. A large committed/used gap is space the JVM holds from the OS without
    currently using -- not internal free-list fragmentation, which JMX cannot see. See 'caveat'."""
    return memory_pool_tools.get_heap_fragmentation(namespace, service)


@mcp.tool()
def get_memory_allocation_rate(namespace: str, service: str, lookback_minutes: int = 15) -> dict[str, Any]:
    """Estimated young-gen allocation rate (MB/s and GB/hr) per pod, over a lookback window --
    churn, not a leak signal (see get_memory_leak_indicator for that). Derived from increase()
    against a sawtoothing gauge; see the returned 'caveat' for why that's a deliberate technique
    and what it can't distinguish."""
    return allocation_tools.get_memory_allocation_rate(namespace, service, lookback_minutes)


# --- Threads ---------------------------------------------------------------


@mcp.tool()
def get_thread_status(namespace: str, service: str) -> dict[str, Any]:
    """Current live thread count and loaded class count, per pod. This is a COUNT only, not
    thread STATE -- cannot detect hangs/deadlocks. See the returned 'caveat'."""
    return thread_tools.get_thread_status(namespace, service)


@mcp.tool()
def get_thread_trend(namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s") -> dict[str, Any]:
    """Per pod: thread count now/min/max/change over the window, peak and daemon counts, threads
    STARTED per second (now and window average) and deadlocked threads. A steady count with a high
    start rate = thread churn (pool threads expiring and recreated), measured without a thread
    dump. Count/creation rate only, not thread state -- see the returned 'caveat'."""
    return runtime_tools.get_thread_trend(namespace, service, lookback_minutes, step)


# --- Process, container and runtime -------------------------------------------


@mcp.tool()
def get_process_resources(namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s") -> dict[str, Any]:
    """Per pod: process CPU in cores (now, window average and max) against the container CPU limit
    (null = none set), open vs max file descriptors, and resident memory."""
    return runtime_tools.get_process_resources(namespace, service, lookback_minutes, step)


@mcp.tool()
def get_memory_vs_limit(namespace: str, service: str) -> dict[str, Any]:
    """Per pod: container memory working set and RSS against the memory limit (headroom %, the
    OOMKilled risk), next to JVM heap committed/used, non-heap committed, direct buffers and the
    rest ('other_bytes': JIT, thread stacks, native). 'least_headroom' ranks the riskiest pods.
    Use this for any OOM-flavored symptom: heap 'max' is not the container limit."""
    return runtime_tools.get_memory_vs_limit(namespace, service)


@mcp.tool()
def get_class_loading_trend(
    namespace: str, service: str, lookback_minutes: int = 180, step: str = "120s"
) -> dict[str, Any]:
    """Per pod: loaded classes now, linear growth in classes/hour with r_squared, and classes
    loaded/unloaded during the window. Steady growth hours after start with few unloads suggests
    a classloader leak -- see the returned 'caveat'."""
    return runtime_tools.get_class_loading_trend(namespace, service, lookback_minutes, step)


@mcp.tool()
def get_jvm_runtime_info(namespace: str, service: str) -> dict[str, Any]:
    """Per pod: JVM version, vendor and runtime name, uptime and start time; plus distinct
    versions, mixed_versions, and the newest/oldest pod -- spots recent restarts and pods running
    a different JVM build."""
    return runtime_tools.get_jvm_runtime_info(namespace, service)


# --- Time windows -------------------------------------------------------------


@mcp.tool()
def get_incident_window(namespace: str, service: str, start: str, end: str, step: str = "auto") -> dict[str, Any]:
    """Heap used %, GC overhead %, GCs/min, thread count, threads started/s and CPU cores for an
    EXPLICIT window (start/end as ISO 8601 such as 2026-10-01T21:17:00Z, or Unix epoch seconds;
    max 7 days). Per pod: avg, max and when the max happened. Service-wide: pooled
    avg/p50/p95/p99/max. pods_seen shows pods that started or stopped mid-window (restarts)."""
    return window_tools.get_incident_window(namespace, service, start, end, step)


@mcp.tool()
def get_baseline_comparison(
    namespace: str,
    service: str,
    baseline_start: str,
    baseline_end: str,
    current_start: str | None = None,
    current_end: str | None = None,
    current_minutes: int = 60,
    step: str = "auto",
) -> dict[str, Any]:
    """Compare a baseline window with a current window (explicit, or the last current_minutes):
    service-wide avg/p50/p95/p99/max of heap, GC, threads, churn and CPU, with the % change of avg
    and p95. Pick a baseline at the same hour/weekday with similar traffic -- see 'caveat'."""
    return window_tools.get_baseline_comparison(
        namespace, service, baseline_start, baseline_end, current_start, current_end, current_minutes, step
    )


@mcp.tool()
def get_cpu_gc_correlation(namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s") -> dict[str, Any]:
    """Per pod: Pearson r between process CPU and GC overhead over the window, with their averages.
    High r + high GC overhead = GC drives CPU; high r + low overhead = load drives both; low r =
    CPU is application work. See 'caveat'."""
    return window_tools.get_cpu_gc_correlation(namespace, service, lookback_minutes, step)


# --- Cross-signal / triage ---------------------------------------------------


@mcp.tool()
def get_gc_memory_correlation(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
) -> dict[str, Any]:
    """Heap-used trend and GC frequency/overhead trend over the same window, per pod -- use to
    distinguish a real leak/undersized heap from a normal load spike."""
    return correlation_tools.get_gc_memory_correlation(namespace, service, lookback_minutes, step)


@mcp.tool()
def get_jvm_incident_snapshot(namespace: str, service: str, lookback_minutes: int = 60) -> dict[str, Any]:
    """One-call combined heap/GC/native-memory/thread snapshot per pod, for fast first-look
    incident triage. Resilient to partial failure -- see 'failed_signals' in the result."""
    return incident_tools.get_jvm_incident_snapshot(namespace, service, lookback_minutes)


@mcp.tool()
def get_memory_leak_indicator(
    namespace: str, service: str, lookback_minutes: int = 180, step: str = "60s"
) -> dict[str, Any]:
    """Tenured/old-gen usage trend per pod, fitted with linear regression over a lookback
    window -- a rising, high-R^2 slope is the closest single-number leak signal this project can
    offer. Returns slope, a naive linear days-to-full projection, and R^2 per pod; see the
    returned 'caveat' for what this can't rule out (a load spike can look identical without a
    global GC in the window)."""
    return leak_tools.get_memory_leak_indicator(namespace, service, lookback_minutes, step)


@mcp.tool()
def get_before_after_deploy_comparison(
    namespace: str, service: str, deploy_timestamp: str, window_minutes: int = 15
) -> dict[str, Any]:
    """Heap/GC-frequency/GC-overhead/thread-count averages in the window immediately before vs.
    immediately after deploy_timestamp (a Unix epoch in seconds), per pod -- use to check whether
    a deploy, restart, or config change changed JVM behavior. Correlation in time, not a causal
    proof -- see the returned 'caveat'. Requires the Prometheus @ modifier (default-enabled since
    Prometheus 2.33)."""
    return comparative_tools.get_before_after_deploy_comparison(namespace, service, deploy_timestamp, window_minutes)


# --- Charts -----------------------------------------------------------------
#
# These render an actual PNG of a lookback-window time series, instead of leaving the
# caller to reconstruct "what happened over time" from avg/max numbers alone. Each
# returns an `Image` (rendered inline by an MCP client that supports image content) on
# success, or the same structured JSON error every other tool in this project uses on
# failure -- including "no data points in this window," which is a real, distinct
# outcome from a connectivity/config error and is reported as its own error rather than
# a blank chart.


@mcp.tool()
def render_heap_trend_chart(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
):
    """Render a PNG line chart of heap used/max per pod over the lookback window -- use this,
    not get_heap_status, whenever the question is about a window of time rather than right now."""
    result = chart_tools.render_heap_trend_chart(namespace, service, lookback_minutes, step)
    if result.get("isError"):
        return _json(result)
    return Image(data=result["png_bytes"], format="png")


@mcp.tool()
def render_gc_behavior_chart(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
):
    """Render a PNG chart of GC frequency (collections/min) and overhead (%) per pod+generation
    over the lookback window -- use this, not a single get_gc_throughput call, to see whether
    GC pressure is trending, spiking, or steady across the window."""
    result = chart_tools.render_gc_behavior_chart(namespace, service, lookback_minutes, step)
    if result.get("isError"):
        return _json(result)
    return Image(data=result["png_bytes"], format="png")


@mcp.tool()
def render_gc_memory_correlation_chart(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
):
    """Render a PNG dual-axis chart overlaying heap used against GC frequency over the lookback
    window -- the leak-vs-load-spike visual: a rising heap floor alongside climbing GC frequency
    points toward a leak/undersized heap; heap returning to baseline each cycle points toward a
    load spike. Prefer this over get_gc_memory_correlation's raw numbers when the shape over time,
    not just the values, is what answers the question."""
    result = chart_tools.render_gc_memory_correlation_chart(namespace, service, lookback_minutes, step)
    if result.get("isError"):
        return _json(result)
    return Image(data=result["png_bytes"], format="png")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
