"""Cross-signal correlation tools.

These don't introduce new PromQL primitives -- they compose the time-series
outputs of `heap_tools` and `gc_tools` into one call, because the question an
investigator actually asks ("is GC pressure being driven by a real memory
leak, or is this just normal sawtooth behavior under load?") needs both
signals lined up on the same time axis, not two separate tool calls the
caller has to mentally overlay.
"""

from __future__ import annotations

from typing import Any

from jvm_troubleshooter.errors import ok
from jvm_troubleshooter.tools.gc_tools import get_gc_behavior_over_time
from jvm_troubleshooter.tools.heap_tools import get_heap_trend_over_time
from jvm_troubleshooter.tools.prometheus_client import clamp_lookback_minutes


def get_gc_memory_correlation(
    namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s"
) -> dict[str, Any]:
    """Heap-used trend and GC frequency/overhead trend over the same window, per pod.

    Use this to distinguish two very differently-actionable situations that
    look similar from a single "GC is busy" alert:
      - Heap used keeps a rising floor (doesn't return to baseline after GC)
        AND GC frequency is climbing -- consistent with a real leak or
        undersized heap; escalate toward a heap dump, not just a restart.
      - Heap used oscillates back to a stable baseline every cycle even though
        GC frequency/overhead is elevated -- more consistent with a load
        spike or an undersized young generation than a leak.

    This tool only lines up the two time series; it does not itself classify
    which situation applies -- reading the shapes is on the caller.
    """
    window_m = clamp_lookback_minutes(lookback_minutes)

    heap_result = get_heap_trend_over_time(namespace, service, lookback_minutes=window_m, step=step)
    if heap_result.get("isError"):
        return heap_result
    gc_result = get_gc_behavior_over_time(namespace, service, lookback_minutes=window_m, step=step)
    if gc_result.get("isError"):
        return gc_result

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "lookback_minutes": window_m,
            "step": step,
            "heap_used_bytes_over_time": heap_result["data"]["heap_used_bytes_over_time"],
            "heap_max_bytes_over_time": heap_result["data"]["heap_max_bytes_over_time"],
            "gc_frequency_per_min_over_time": gc_result["data"]["gc_frequency_per_min"],
            "gc_overhead_percent_over_time": gc_result["data"]["gc_overhead_percent"],
            "how_to_read": (
                "Rising heap floor + climbing GC frequency together points toward a leak/undersized "
                "heap. Heap returning to a stable baseline each cycle, even with elevated GC "
                "frequency/overhead, points more toward a load spike or undersized young generation."
            ),
        }
    )
