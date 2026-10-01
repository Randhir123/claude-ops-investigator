"""Old-gen (tenured) memory-leak trend indicator.

Metric source: `java_lang_MemoryPool_Usage_used` and `_max`, summed across
OpenJ9's tenured-SOA and tenured-LOA pools (see `memory_pool_tools`'s module
docstring for the full OpenJ9 pool-name list). Tenured space is only
reclaimed by a global (full) GC, which fires far less often than a
young/scavenge GC -- so, unlike the young-gen pool `allocation_tools.py`
works with, the raw sampled tenured-usage series over a wide-enough window
already behaves like a slow-moving trend line without needing to isolate
post-GC low-water-mark dips explicitly.

METHOD: fetches the tenured-pool used-bytes time series over the lookback
window (one `query_range` call) and fits an ordinary-least-squares line to
each pod's series in plain Python -- the same textbook linear-regression
approach used by reference JVM root-cause-analysis tooling for this exact
question. Reports the fitted slope (bytes/sec), a naive linear projection of
days-until-full against the pool's current max, and the fit's R^2 so the
caller can judge whether the slope is a clean trend or noise.

CAVEATS:
- This is a statistical trend over the requested window, not a certainty. A
  short window, an unusually quiet or busy traffic period, or a recent
  config change (e.g. a just-raised -Xmx) can all produce a slope that
  doesn't hold up over a longer window. Cross-check a flagged trend against
  `correlation_tools.get_gc_memory_correlation` and a longer
  `lookback_minutes` before treating it as confirmation of a leak.
- If a global GC hasn't fired at all during the window, this tool cannot
  distinguish "tenured space filling because of a leak" from "tenured space
  filling because of a load spike that will drain on the next global GC" --
  both look identical in the trend alone.
- `r_squared` near 0 means the "slope" is noise, not a trend -- treat a
  low-R^2 slope as inconclusive, not as evidence either way.
- `days_to_full` is a *naive linear* extrapolation of the current slope
  against the pool's current max. A real leak's growth curve is rarely
  perfectly linear (it depends on what's leaking and how), so treat this
  number as an order-of-magnitude urgency signal, not a forecast to plan an
  incident timeline around.
"""

from __future__ import annotations

import time as _time
from typing import Any

from jvm_troubleshooter.errors import ok
from jvm_troubleshooter.tools.prometheus_client import (
    clamp_lookback_minutes,
    instant_query,
    namespace_service_selector,
    range_query,
)

_TENURED_POOLS_ALT = "tenured-SOA|tenured-LOA"

_LEAK_TREND_NOTE = (
    "Ordinary-least-squares slope fitted to the raw sampled tenured-pool time series -- not "
    "isolated post-GC low-water marks, though tenured space's own slow (global-GC-only) reclaim "
    "cadence makes the raw series a reasonable proxy for one. A short window, an unusual traffic "
    "period, or a recent -Xmx/config change can all produce a misleading slope -- cross-check "
    "against get_gc_memory_correlation and a longer lookback_minutes before treating a positive "
    "slope as confirmation of a leak. r_squared near 0 means the slope is noise, not a trend, and "
    "days_to_full is a naive linear projection, not a forecast."
)


def _linear_regression(points: list[tuple[float, float]]) -> dict[str, float] | None:
    """Ordinary least-squares slope/intercept/R^2 for (timestamp_seconds, value) points.

    Returns None if there are fewer than 2 points, or all points share the same x (nothing to
    fit a slope to).
    """
    n = len(points)
    if n < 2:
        return None

    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    ss_xx = sum((x - mean_x) ** 2 for x, _ in points)
    if ss_xx == 0:
        return None

    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    ss_tot = sum((y - mean_y) ** 2 for _, y in points)
    if ss_tot == 0:
        r_squared = 1.0  # perfectly flat series -- the (flat) fit explains all the variance
    else:
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
        r_squared = 1 - ss_res / ss_tot

    return {"slope_bytes_per_second": slope, "intercept": intercept, "r_squared": r_squared}


def _extract_series_by_instance(prom_response: dict[str, Any]) -> dict[str, list[tuple[float, float]]]:
    result = (prom_response or {}).get("data", {}).get("result", [])
    series: dict[str, list[tuple[float, float]]] = {}
    for entry in result:
        instance = entry.get("metric", {}).get("instance", "unknown-pod")
        points: list[tuple[float, float]] = []
        for ts, val in entry.get("values", []):
            try:
                points.append((float(ts), float(val)))
            except (TypeError, ValueError):
                continue
        series[instance] = points
    return series


def _extract_vector_by_instance(prom_response: dict[str, Any]) -> dict[str, float]:
    result = (prom_response or {}).get("data", {}).get("result", [])
    out: dict[str, float] = {}
    for entry in result:
        instance = entry.get("metric", {}).get("instance", "unknown-pod")
        try:
            out[instance] = float(entry.get("value", [None, None])[1])
        except (TypeError, ValueError):
            continue
    return out


def get_memory_leak_indicator(
    namespace: str, service: str, lookback_minutes: int = 180, step: str = "60s"
) -> dict[str, Any]:
    """Tenured/old-gen usage trend per pod, fitted with linear regression, over a lookback
    window -- a rising, high-R^2 slope is the closest single-number leak signal this project
    can offer; see the module docstring for what it can't rule out."""
    window_m = clamp_lookback_minutes(lookback_minutes)
    selector = namespace_service_selector(namespace, service)
    end = int(_time.time())
    start = end - window_m * 60

    trend_result = range_query(
        f'sum by(instance) (java_lang_MemoryPool_Usage_used{{{selector}, name=~"({_TENURED_POOLS_ALT})"}})',
        start=str(start),
        end=str(end),
        step=step,
    )
    if trend_result.get("isError"):
        return trend_result
    max_result = instant_query(
        f'sum by(instance) (java_lang_MemoryPool_Usage_max{{{selector}, name=~"({_TENURED_POOLS_ALT})"}})'
    )
    if max_result.get("isError"):
        return max_result

    series_by_pod = _extract_series_by_instance(trend_result["data"])
    max_by_pod = _extract_vector_by_instance(max_result["data"])

    pods: dict[str, Any] = {}
    for instance, points in series_by_pod.items():
        fit = _linear_regression(points)
        if fit is None:
            pods[instance] = {
                "samples": len(points),
                "trend": None,
                "note": "Not enough distinct samples in this window to fit a trend.",
            }
            continue

        slope = fit["slope_bytes_per_second"]
        current_bytes = points[-1][1] if points else None
        pool_max = max_by_pod.get(instance)

        days_to_full = None
        if slope > 0 and current_bytes is not None and pool_max:
            remaining = pool_max - current_bytes
            days_to_full = (remaining / slope / 86400) if remaining > 0 else 0.0

        pods[instance] = {
            "samples": len(points),
            "slope_bytes_per_second": slope,
            "slope_mb_per_hour": slope * 3600 / 1048576,
            "r_squared": fit["r_squared"],
            "current_bytes": current_bytes,
            "pool_max_bytes": pool_max,
            "days_to_full_at_current_slope": days_to_full,
        }

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "lookback_minutes": window_m,
            "step": step,
            "pods": pods,
            "caveat": _LEAK_TREND_NOTE,
        }
    )
