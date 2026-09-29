"""Heap usage tools.

Metric source: the standard JMX Exporter names for `java.lang:type=Memory`
-- `java_lang_Memory_HeapMemoryUsage_used` / `_max` / `_committed`. These are
single gauges per pod (no pool-name label); for a per-pool breakdown
(nursery, tenured, JIT caches, etc.) use `memory_pool_tools` instead.

CAVEAT: `_max` reflects the JVM's own view of its heap ceiling (effectively
-Xmx, or the OpenJ9 default derived from it). It is NOT the same thing as the
container's `resources.limits.memory` in Kubernetes -- that limit also has to
cover non-heap JVM memory (JIT caches, class storage, thread stacks, direct
buffers) and any off-heap/native allocations. A pod can be OOM-killed by the
container runtime while `_used/_max` here still looks comfortably low. Always
cross-check `_max` against the pod's actual `-Xmx` and against
`resources.limits.memory`, and pair this tool with `memory_pool_tools` /
`get_native_memory_summary` before concluding heap pressure is or isn't the
cause of an OOM.
"""

from __future__ import annotations

from typing import Any

from jvm_troubleshooter.errors import ok
from jvm_troubleshooter.tools.prometheus_client import (
    clamp_lookback_minutes,
    instant_query,
    namespace_service_selector,
    range_query,
)

_HEAP_CEILING_NOTE = (
    "'max' here is the JVM's own heap ceiling (-Xmx), not the pod's resources.limits.memory -- "
    "non-heap JVM memory (JIT caches, class storage, thread stacks, direct buffers) and native "
    "allocations also draw from the container's memory limit. A pod can be OOM-killed with heap "
    "usage still comfortably below this 'max'. Cross-check against the pod's actual -Xmx and "
    "resources.limits.memory, and see get_native_memory_summary for the non-heap picture."
)


def get_heap_status(namespace: str, service: str) -> dict[str, Any]:
    """Current heap used/max/committed and %used, per pod."""
    selector = namespace_service_selector(namespace, service)

    used = instant_query(f"java_lang_Memory_HeapMemoryUsage_used{{{selector}}}")
    if used.get("isError"):
        return used
    max_ = instant_query(f"java_lang_Memory_HeapMemoryUsage_max{{{selector}}}")
    if max_.get("isError"):
        return max_
    committed = instant_query(f"java_lang_Memory_HeapMemoryUsage_committed{{{selector}}}")
    if committed.get("isError"):
        return committed
    percent_expr = (
        f"100 * java_lang_Memory_HeapMemoryUsage_used{{{selector}}} / "
        f"(java_lang_Memory_HeapMemoryUsage_max{{{selector}}} > 0)"
    )
    percent = instant_query(percent_expr)
    if percent.get("isError"):
        return percent

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "heap_used_bytes": used["data"],
            "heap_max_bytes": max_["data"],
            "heap_committed_bytes": committed["data"],
            "heap_used_percent": percent["data"],
            "caveat": _HEAP_CEILING_NOTE,
        }
    )


def get_heap_trend_over_time(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
) -> dict[str, Any]:
    """Heap used/max as a time series -- use to see growth trends, sawtooth shape, or a leak
    (used trending up across GC cycles instead of returning to baseline)."""
    window_m = clamp_lookback_minutes(lookback_minutes)
    selector = namespace_service_selector(namespace, service)
    import time as _time

    end = int(_time.time())
    start = end - window_m * 60

    used_result = range_query(
        f"java_lang_Memory_HeapMemoryUsage_used{{{selector}}}", start=str(start), end=str(end), step=step
    )
    if used_result.get("isError"):
        return used_result
    max_result = range_query(
        f"java_lang_Memory_HeapMemoryUsage_max{{{selector}}}", start=str(start), end=str(end), step=step
    )
    if max_result.get("isError"):
        return max_result

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "lookback_minutes": window_m,
            "step": step,
            "heap_used_bytes_over_time": used_result["data"],
            "heap_max_bytes_over_time": max_result["data"],
            "note": (
                "Look for 'used' failing to drop back to baseline after each GC (a rising floor) "
                "-- that shape is more indicative of a leak than any single point-in-time reading."
            ),
        }
    )
