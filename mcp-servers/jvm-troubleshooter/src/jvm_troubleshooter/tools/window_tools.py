"""Time-window tools: an explicit incident window, a baseline comparison, and
CPU-vs-GC correlation.

Every tool here reads the same six per-pod signals as range queries and
summarizes them client-side:

  heap_used_percent           100 * heap used / heap max
  gc_overhead_percent         share of wall time spent in GC (all collectors)
  gc_per_minute               collections per minute (all collectors)
  thread_count                live threads
  threads_started_per_second  thread creation rate (churn)
  cpu_cores                   process CPU, in cores

Times may be ISO 8601 (`2026-10-01T21:17:00Z`; no offset = UTC) or Unix
epoch seconds. Windows are capped at 7 days. The query step is chosen
automatically (60 s or more, about 400 points per series) unless given.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

from jvm_troubleshooter.errors import ToolError, ok
from jvm_troubleshooter.tools.prometheus_client import clamp_lookback_minutes, namespace_service_selector, range_query

_MAX_WINDOW_SECONDS = 7 * 86400
_TARGET_POINTS = 400

_SIGNALS = {
    "heap_used_percent": (
        "100 * sum by (pod, instance) (java_lang_Memory_HeapMemoryUsage_used{{{sel}}}) / "
        "sum by (pod, instance) (java_lang_Memory_HeapMemoryUsage_max{{{sel}}})"
    ),
    "gc_overhead_percent": "sum by (pod, instance) (rate(java_lang_GarbageCollector_CollectionTime{{{sel}}}[5m])) / 10",
    "gc_per_minute": "sum by (pod, instance) (rate(java_lang_GarbageCollector_CollectionCount{{{sel}}}[5m])) * 60",
    "thread_count": "sum by (pod, instance) (java_lang_Threading_ThreadCount{{{sel}}})",
    "threads_started_per_second": "sum by (pod, instance) (rate(java_lang_Threading_TotalStartedThreadCount{{{sel}}}[5m]))",
    "cpu_cores": "sum by (pod, instance) (rate(process_cpu_seconds_total{{{sel}}}[5m]))",
}

_POOLED_NOTE = (
    "Statistics pool every sample from every pod in the window, so they still compare cleanly "
    "across a rollout that replaced the pods. Percentiles are nearest-rank over the samples at "
    "the query step, not over individual events; the *_per_* and gc_overhead signals are 5-minute "
    "rates, so a spike shorter than that is smoothed."
)
_BASELINE_NOTE = (
    "A change against the baseline shows that the JVM behaves differently, not why. Compare like "
    "with like: the same hour of day and day of week, and similar traffic, before reading a change "
    "as a regression. Pod counts can differ between the windows (scaling, rollouts), and these are "
    "per-pod values, pooled. " + _POOLED_NOTE
)
_CORRELATION_NOTE = (
    "Pearson r between process CPU and GC overhead, per pod, over the same timestamps. GC uses CPU, "
    "so some positive correlation is normal. A high r together with a high gc_overhead_percent "
    "means GC is driving the CPU. A high r with a LOW gc_overhead_percent (well under a few %) "
    "means load drives both: more requests, more allocation, more GC and more CPU, with GC a small "
    "share. A low r with high CPU means the CPU goes to application work. "
    "Correlation is not causation, and r from fewer than ~20 points is unreliable."
)


# --- time and window helpers ---------------------------------------------------------


def _parse_time(value: str | int | float) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(start: str | int | float, end: str | int | float, label: str) -> tuple[float, float] | dict[str, Any]:
    s, e = _parse_time(start), _parse_time(end)
    attempted = {f"{label}_start": start, f"{label}_end": end}
    if s is None or e is None:
        return ToolError(
            "validation", False,
            f"Could not parse the {label} window times.", attempted=attempted,
            alternatives=["Use ISO 8601 such as 2026-10-01T21:17:00Z, or Unix epoch seconds"],
        ).to_dict()
    now = time.time()
    if s >= now:
        return ToolError("validation", False, f"The {label} window starts in the future.", attempted=attempted).to_dict()
    e = min(e, now)
    if e <= s:
        return ToolError("validation", False, f"The {label} window must end after it starts.", attempted=attempted).to_dict()
    if e - s > _MAX_WINDOW_SECONDS:
        return ToolError(
            "validation", False, f"The {label} window is longer than 7 days.", attempted=attempted,
            alternatives=["Narrow the window to 7 days or less"],
        ).to_dict()
    return s, e


def _auto_step(span_seconds: float, step: str) -> str:
    if step and step != "auto":
        return step
    return f"{max(60, math.ceil(span_seconds / _TARGET_POINTS / 60) * 60)}s"


def _pod_of(metric: dict[str, Any]) -> str:
    return metric.get("pod") or metric.get("instance") or "unknown-pod"


def _fetch(sel: str, start: float, end: float, step: str) -> tuple[dict[str, dict[str, list[tuple[float, float]]]], dict[str, Any] | None]:
    """All signals as {signal: {pod: [(ts, value), ...]}}."""
    out: dict[str, dict[str, list[tuple[float, float]]]] = {}
    for name, template in _SIGNALS.items():
        response = range_query(template.format(sel=sel), start=str(int(start)), end=str(int(end)), step=step)
        if response.get("isError"):
            return {}, response
        by_pod: dict[str, list[tuple[float, float]]] = {}
        for entry in ((response.get("data") or {}).get("data") or {}).get("result") or []:
            points = []
            for ts, val in entry.get("values", []):
                try:
                    v = float(val)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(v):
                    points.append((float(ts), v))
            by_pod[_pod_of(entry.get("metric", {}))] = points
        out[name] = by_pod
    return out, None


def _percentile(sorted_values: list[float], pct: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, max(0, math.ceil(pct / 100 * len(sorted_values)) - 1))]


def _stats(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    ordered = sorted(values)
    r = lambda v: round(v, 3)  # noqa: E731
    return {
        "samples": len(ordered),
        "avg": r(sum(ordered) / len(ordered)),
        "p50": r(_percentile(ordered, 50)),
        "p95": r(_percentile(ordered, 95)),
        "p99": r(_percentile(ordered, 99)),
        "max": r(ordered[-1]),
    }


def _pooled(data: dict[str, dict[str, list[tuple[float, float]]]]) -> dict[str, Any]:
    return {name: _stats([v for points in by_pod.values() for _, v in points]) for name, by_pod in data.items()}


def _pods_seen(data: dict[str, dict[str, list[tuple[float, float]]]]) -> dict[str, dict[str, str]]:
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    for by_pod in data.values():
        for pod, points in by_pod.items():
            if points:
                first[pod] = min(first.get(pod, points[0][0]), points[0][0])
                last[pod] = max(last.get(pod, points[-1][0]), points[-1][0])
    return {pod: {"first_sample": _iso(first[pod]), "last_sample": _iso(last[pod])} for pod in sorted(first)}


# --- tools -------------------------------------------------------------------------


def get_incident_window(namespace: str, service: str, start: str, end: str, step: str = "auto") -> dict[str, Any]:
    """Heap, GC, threads, churn and CPU for an explicit start/end window: per pod avg/max and when
    the max happened, plus service-wide pooled avg/p50/p95/p99/max."""
    window = _window(start, end, "incident")
    if isinstance(window, dict):
        return window
    s, e = window
    step = _auto_step(e - s, step)

    data, err = _fetch(namespace_service_selector(namespace, service), s, e, step)
    if err:
        return err

    pods: dict[str, dict[str, Any]] = {}
    for name, by_pod in data.items():
        for pod, points in by_pod.items():
            if not points:
                continue
            peak_ts, peak = max(points, key=lambda p: p[1])
            pods.setdefault(pod, {})[name] = {
                "avg": round(sum(v for _, v in points) / len(points), 3),
                "max": round(peak, 3),
                "max_at": _iso(peak_ts),
            }

    return ok({
        "namespace": namespace,
        "service": service,
        "window": {"start": _iso(s), "end": _iso(e), "step": step},
        "service_wide": _pooled(data),
        "pods": dict(sorted(pods.items())),
        "pods_seen": _pods_seen(data),
        "note": (
            "pods_seen shows when each pod has samples: a pod that starts or stops mid-window "
            "restarted or was replaced. " + _POOLED_NOTE
        ),
    })


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
    """Service-wide avg/p50/p95/p99/max of heap, GC, threads, churn and CPU in a baseline window
    vs a current window (explicit, or the last `current_minutes`), with the % change of avg and
    p95."""
    baseline = _window(baseline_start, baseline_end, "baseline")
    if isinstance(baseline, dict):
        return baseline
    if current_start is None:
        now = time.time()
        current = (now - clamp_lookback_minutes(current_minutes) * 60, now)
    else:
        current = _window(current_start, current_end if current_end is not None else time.time(), "current")
        if isinstance(current, dict):
            return current
    if baseline[1] > current[0]:
        return ToolError(
            "validation", False, "The baseline window must end before the current window starts.",
            attempted={"baseline": [_iso(baseline[0]), _iso(baseline[1])], "current": [_iso(current[0]), _iso(current[1])]},
        ).to_dict()

    sel = namespace_service_selector(namespace, service)
    step = _auto_step(max(baseline[1] - baseline[0], current[1] - current[0]), step)
    base_data, err = _fetch(sel, baseline[0], baseline[1], step)
    if err:
        return err
    cur_data, err = _fetch(sel, current[0], current[1], step)
    if err:
        return err

    base_stats, cur_stats = _pooled(base_data), _pooled(cur_data)

    def change(key: str, stat: str) -> float | None:
        b, c = base_stats.get(key), cur_stats.get(key)
        if not b or not c or b[stat] == 0:
            return None
        return round(100 * (c[stat] - b[stat]) / abs(b[stat]), 1)

    comparison = {
        name: {
            "baseline": base_stats[name],
            "current": cur_stats[name],
            "avg_change_percent": change(name, "avg"),
            "p95_change_percent": change(name, "p95"),
        }
        for name in _SIGNALS
    }
    return ok({
        "namespace": namespace,
        "service": service,
        "baseline_window": {"start": _iso(baseline[0]), "end": _iso(baseline[1]), "pods": len(_pods_seen(base_data))},
        "current_window": {"start": _iso(current[0]), "end": _iso(current[1]), "pods": len(_pods_seen(cur_data))},
        "step": step,
        "signals": comparison,
        "caveat": _BASELINE_NOTE,
    })


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def get_cpu_gc_correlation(namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s") -> dict[str, Any]:
    """Per pod: Pearson correlation between process CPU (cores) and GC overhead (%) over the
    window, with average CPU and GC overhead."""
    window_m = clamp_lookback_minutes(lookback_minutes)
    end = time.time()
    data, err = _fetch(namespace_service_selector(namespace, service), end - window_m * 60, end, step)
    if err:
        return err

    pods: dict[str, Any] = {}
    for pod in sorted(set(data["cpu_cores"]) & set(data["gc_overhead_percent"])):
        gc = dict(data["gc_overhead_percent"][pod])
        pairs = [(cpu, gc[ts]) for ts, cpu in data["cpu_cores"][pod] if ts in gc]
        r = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
        pods[pod] = {
            "points": len(pairs),
            "pearson_r": round(r, 3) if r is not None else None,
            "cpu_cores_avg": round(sum(p[0] for p in pairs) / len(pairs), 3) if pairs else None,
            "gc_overhead_percent_avg": round(sum(p[1] for p in pairs) / len(pairs), 3) if pairs else None,
        }

    return ok({
        "namespace": namespace,
        "service": service,
        "lookback_minutes": window_m,
        "step": step,
        "pods": pods,
        "caveat": _CORRELATION_NOTE,
    })
