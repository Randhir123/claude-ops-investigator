"""Young-generation allocation-rate estimation.

Metric source: `java_lang_MemoryPool_Usage_used{name="nursery-allocate"}` --
the OpenJ9 young-gen active allocation pool (see `memory_pool_tools`'s module
docstring for the full OpenJ9 pool-name list).

TECHNIQUE, AND WHY IT'S LEGITIMATE DESPITE THE METRIC BEING A GAUGE:
Prometheus's `increase()`/`rate()` are documented for monotonically
increasing counters, and nursery-allocate usage is a gauge that drops on
every young/scavenge GC -- not a counter. But `increase()`'s counter-reset
compensation (it adds back the apparent drop whenever a sample is lower than
the previous one, on the assumption it's a counter reset) happens to
reconstruct a reasonable estimate of *total bytes allocated* into young gen
over the window when pointed at a sawtoothing gauge like this one: every
GC-triggered drop gets treated as a "reset," and the amount that was "lost"
across the drop is added back into the accumulated total instead of being
subtracted out. This is a known, deliberate (mis)use of `increase()` against
a sawtoothing gauge -- not a mistake -- and it's how this project estimates
an allocation rate without any per-allocation counter existing to query.

CAVEATS:
- This estimates young-gen *churn*, not a true object-allocation byte
  counter: it overcounts if the pool's own committed/max size changes during
  the window (a resize looks identical to a GC-triggered drop to this
  formula), and it says nothing about object counts or sizes -- a high rate
  from a few huge arrays and the same rate from millions of tiny objects
  look identical here.
- If no young GC fires at all during the window, `increase()` degrades to a
  plain first-to-last delta, which under-counts allocation if a GC-triggered
  drop happened just outside the window edge.
- A high, stable allocation rate is often perfectly healthy -- it's exactly
  what young-gen GC exists to absorb. This tool is churn, not a leak signal.
  For a leak indicator, see `leak_tools.get_memory_leak_indicator`, which
  tracks the tenured/old-gen trend instead.
"""

from __future__ import annotations

from typing import Any

from jvm_troubleshooter.errors import ok
from jvm_troubleshooter.tools.prometheus_client import (
    clamp_lookback_minutes,
    instant_query,
    namespace_service_selector,
)

_NURSERY_ALLOCATE_POOL = "nursery-allocate"

# Allocation rate is a "right now" churn reading -- a long lookback makes the increase()
# estimate stale (it answers "what was the rate over the last N hours," not "now") and more
# exposed to a mid-window pool resize being misread as GC activity. Capped tighter than the
# project-wide 1440m default.
_MAX_LOOKBACK_MINUTES = 180

_ALLOCATION_RATE_NOTE = (
    "Estimated by applying increase() to the nursery-allocate pool's used-bytes gauge, which "
    "resets on every young GC -- Prometheus's counter-reset compensation happens to reconstruct "
    "total bytes allocated this way, but it is young-gen churn, not a leak signal, and it "
    "overcounts if the pool's own size changed during the window rather than just being "
    "collected. It also cannot distinguish few-large-object allocation from many-small-object "
    "allocation. For leak detection, use get_memory_leak_indicator instead, which tracks the "
    "tenured/old-gen trend."
)


def get_memory_allocation_rate(namespace: str, service: str, lookback_minutes: int = 15) -> dict[str, Any]:
    """Estimated young-gen allocation rate (MB/s and GB/hr) per pod, over a lookback window.

    See module docstring: this is a churn estimate derived from increase() against a
    sawtoothing gauge, not a direct allocation-byte counter -- OpenJ9/JMX exposes no such
    counter directly.
    """
    window_m = clamp_lookback_minutes(lookback_minutes, max_minutes=_MAX_LOOKBACK_MINUTES)
    selector = namespace_service_selector(namespace, service)
    window_s = window_m * 60

    bytes_increase_expr = (
        f'increase(java_lang_MemoryPool_Usage_used{{{selector}, name="{_NURSERY_ALLOCATE_POOL}"}}[{window_m}m])'
    )
    mb_per_s_expr = f"({bytes_increase_expr}) / {window_s} / 1048576"
    gb_per_hr_expr = f"({bytes_increase_expr}) / {window_s} * 3600 / 1073741824"

    mb_per_s_result = instant_query(mb_per_s_expr)
    if mb_per_s_result.get("isError"):
        return mb_per_s_result
    gb_per_hr_result = instant_query(gb_per_hr_expr)
    if gb_per_hr_result.get("isError"):
        return gb_per_hr_result

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "lookback_minutes": window_m,
            "allocation_rate_mb_per_sec": mb_per_s_result["data"],
            "allocation_rate_gb_per_hour": gb_per_hr_result["data"],
            "caveat": _ALLOCATION_RATE_NOTE,
        }
    )
