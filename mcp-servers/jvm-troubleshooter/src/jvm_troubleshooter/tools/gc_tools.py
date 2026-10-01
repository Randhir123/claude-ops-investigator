"""GC activity, pause-latency, and throughput tools.

Metric source: the standard Prometheus JMX Exporter names for
`java.lang:type=GarbageCollector,name=*` -- `java_lang_GarbageCollector_
CollectionCount` / `_CollectionTime`, split by the `name` label (OpenJ9
gencon: "scavenge" = young/nursery GC, "global" = old/full GC).

IMPORTANT ACCURACY CAVEAT, carried over from hands-on validation against a
real IBM Semeru/OpenJ9 workload: this MXBean only exposes CUMULATIVE
counters (total count, total time), never individual per-collection
durations. That means:

- `get_gc_pause_stats`'s "avg pause" is exact (total time / total count over
  the window -- a true weighted average).
- Its "max pause" and "min pause" are NOT true single-event extremes. They
  are the highest/lowest *bucket-averaged* pause-per-collection observed
  across fixed 5-minute windows. In a real comparison against a GCeasy
  report parsed from an actual verbose GC log for the same JVM over the same
  time range, the true max single-event pause was 945ms while this
  bucket-averaged formula only surfaced 109ms for the identical window -- an
  8-9x understatement. Always report these two fields as approximations /
  lower-bounds, never as a real worst-case pause, and say so in any summary
  that surfaces them.
- A genuine per-event max/min, a pause-duration histogram, or GC *causes*
  all require parsing the actual verbose GC log -- none of it is available
  from JMX alone. Treat that as a hard capability boundary, not something to
  work around with cleverer PromQL.
"""

from __future__ import annotations

from typing import Any

from jvm_troubleshooter.errors import ok
from jvm_troubleshooter.tools.prometheus_client import (
    clamp_lookback_minutes,
    instant_query,
    namespace_service_selector,
)

_GC_APPROXIMATION_NOTE = (
    "max/min pause are bucket-averaged approximations (5m windows), not true single-event "
    "extremes -- validated to understate a real worst-case pause by 8-9x against an actual GC "
    "log for the same workload/window. Do not report them as the worst pause observed; a true "
    "per-event max requires parsing the verbose GC log, which this tool cannot do."
)


def get_gc_activity(namespace: str, service: str) -> dict[str, Any]:
    """Current cumulative GC collection counts and total time, per pod and generation."""
    selector = namespace_service_selector(namespace, service)
    counts = instant_query(f"java_lang_GarbageCollector_CollectionCount{{{selector}}}")
    if counts.get("isError"):
        return counts
    times = instant_query(f"java_lang_GarbageCollector_CollectionTime{{{selector}}}")
    if times.get("isError"):
        return times

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "collection_counts": counts["data"],
            "collection_time_ms": times["data"],
            "note": "Cumulative since JVM start (or last counter reset on pod restart), per pod+generation.",
        }
    )


def get_gc_pause_stats(namespace: str, service: str, lookback_minutes: int = 60) -> dict[str, Any]:
    """Avg/max/min GC pause (ms) per pod over a lookback window, young+old combined.

    See module docstring: avg is an exact weighted average; max/min are
    bucket-averaged approximations, not true single-event extremes.
    """
    window_m = clamp_lookback_minutes(lookback_minutes)
    selector = namespace_service_selector(namespace, service)

    rate_ratio = (
        f"sum by(instance) (rate(java_lang_GarbageCollector_CollectionTime{{{selector}}}[5m])) / "
        f"(sum by(instance) (rate(java_lang_GarbageCollector_CollectionCount{{{selector}}}[5m])) > 0)"
    )
    avg_expr = (
        f"sum by(instance) (increase(java_lang_GarbageCollector_CollectionTime{{{selector}}}[{window_m}m])) / "
        f"(sum by(instance) (increase(java_lang_GarbageCollector_CollectionCount{{{selector}}}[{window_m}m])) > 0)"
    )
    max_expr = f"max_over_time(({rate_ratio})[{window_m}m:5m])"
    min_expr = f"min_over_time(({rate_ratio})[{window_m}m:5m])"

    avg_result = instant_query(avg_expr)
    if avg_result.get("isError"):
        return avg_result
    max_result = instant_query(max_expr)
    if max_result.get("isError"):
        return max_result
    min_result = instant_query(min_expr)
    if min_result.get("isError"):
        return min_result

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "lookback_minutes": window_m,
            "avg_pause_ms": avg_result["data"],
            "max_pause_ms_approx": max_result["data"],
            "min_pause_ms_approx": min_result["data"],
            "caveat": _GC_APPROXIMATION_NOTE,
        }
    )


def get_gc_throughput(namespace: str, service: str, lookback_minutes: int = 60) -> dict[str, Any]:
    """GC throughput % (time NOT paused for GC) and avg interval between collections, per pod.

    Both figures here are exact -- derived from cumulative counters over the
    whole window, no bucket-averaging involved.
    """
    window_m = clamp_lookback_minutes(lookback_minutes)
    selector = namespace_service_selector(namespace, service)
    window_s = window_m * 60

    throughput_expr = (
        f"100 - 100 * (sum by(instance) (increase(java_lang_GarbageCollector_CollectionTime"
        f"{{{selector}}}[{window_m}m])) / 1000) / {window_s}"
    )
    interval_expr = (
        f"{window_s} / (sum by(instance) (increase(java_lang_GarbageCollector_CollectionCount"
        f"{{{selector}}}[{window_m}m])) > 0)"
    )

    throughput_result = instant_query(throughput_expr)
    if throughput_result.get("isError"):
        return throughput_result
    interval_result = instant_query(interval_expr)
    if interval_result.get("isError"):
        return interval_result

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "lookback_minutes": window_m,
            "throughput_percent": throughput_result["data"],
            "gc_interval_avg_seconds": interval_result["data"],
        }
    )


def get_gc_behavior_over_time(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
) -> dict[str, Any]:
    """GC frequency (collections/min) and overhead (%) as a time series, per pod and generation.

    Use this (rather than the point-in-time tools above) when the question
    is "did GC behavior change over the incident window" or "is GC pressure
    trending up," not just "what is it right now."
    """
    from jvm_troubleshooter.tools.prometheus_client import range_query
    import time as _time

    window_m = clamp_lookback_minutes(lookback_minutes)
    selector = namespace_service_selector(namespace, service)
    end = int(_time.time())
    start = end - window_m * 60

    frequency_expr = f"rate(java_lang_GarbageCollector_CollectionCount{{{selector}}}[5m]) * 60"
    overhead_expr = f"rate(java_lang_GarbageCollector_CollectionTime{{{selector}}}[5m]) / 10"

    freq_result = range_query(frequency_expr, start=str(start), end=str(end), step=step)
    if freq_result.get("isError"):
        return freq_result
    overhead_result = range_query(overhead_expr, start=str(start), end=str(end), step=step)
    if overhead_result.get("isError"):
        return overhead_result

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "lookback_minutes": window_m,
            "step": step,
            "gc_frequency_per_min": freq_result["data"],
            "gc_overhead_percent": overhead_result["data"],
        }
    )
