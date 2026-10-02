"""Runtime and resource tools: thread trend and churn, process CPU and file
descriptors, memory against the container limit, class loading, JVM identity.

Metric sources (all verified against JMX Exporter-scraped OpenJ9 JVMs; the
JMX Exporter java agent also emits the Prometheus Java client's default
`jvm_*` / `process_*` metrics):
  - `java_lang_Threading_*`, `jvm_threads_deadlocked`
  - `process_cpu_seconds_total`, `process_open_fds`, `process_max_fds`,
    `process_resident_memory_bytes`, `process_start_time_seconds`
  - `java_lang_ClassLoading_*`, `java_lang_Runtime_Uptime`, `jvm_info`
  - `java_lang_Memory_*`, `jvm_buffer_pool_used_bytes`
  - cAdvisor `container_memory_*` and kube-state-metrics
    `kube_pod_container_resource_limits` for the container side

Unlike the older tools in this package, these return compact per-pod
summaries keyed by pod name (falling back to the scrape `instance`), not raw
Prometheus vectors.
"""

from __future__ import annotations

import re
import time
from typing import Any

from jvm_troubleshooter.errors import ToolError, ok
from jvm_troubleshooter.tools.leak_tools import _linear_regression
from jvm_troubleshooter.tools.prometheus_client import (
    clamp_lookback_minutes,
    escape_label_value,
    instant_query,
    namespace_service_selector,
    range_query,
)

_POD_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

_THREAD_TREND_NOTE = (
    "Thread COUNT and thread CREATION rate, not thread STATE. A steady count with a high "
    "threads_started_per_second means churn: pool threads expiring and being recreated (short "
    "keep-alive, core threads allowed to time out, or a pool created per task). To see which pool "
    "churns, or whether threads are stuck, a human captures javacores (scripts/capture-javacore.sh "
    "-n 3) and they are compared with jvm_compare_javacores. deadlocked_threads comes from the JVM's "
    "own deadlock detection at scrape time; 0 does not rule out livelock or threads stuck on I/O."
)
_PROCESS_NOTE = (
    "cpu_cores is CPU time used per second (1.0 = one full core), averaged over 5 minutes for "
    "'now'. Compare it with cpu_limit_cores when a limit is set (null = no CPU limit on the "
    "container, so no throttling from a limit); sustained use near the limit means CPU "
    "throttling. File descriptors near fd_max cause 'Too many open files' errors; a "
    "steadily rising open count suggests leaked sockets or files."
)
_MEMORY_LIMIT_NOTE = (
    "working_set_bytes is what the kubelet compares against the memory limit for eviction and "
    "what the kernel's OOM killer acts on, so headroom_percent near 0 means OOMKilled risk "
    "regardless of how full the heap looks. Heap committed counts in full, whether or not it is "
    "used. other_bytes = working set - heap committed - non-heap committed - direct buffers: JIT, "
    "thread stacks, malloc'd native memory and page cache the JVM cannot see; a large or growing "
    "other_bytes points at native memory, not the heap."
)
_CLASS_LOADING_NOTE = (
    "Classes normally climb during warm-up and then flatten. A steady climb hours after start, "
    "with few or no unloads, suggests a classloader leak (redeploys, dynamic proxies, script "
    "engines, per-request class generation). r_squared near 0 means the slope is noise; "
    "growth_classes_per_hour is a linear fit over the window, not a forecast."
)


def _pod_of(metric: dict[str, Any]) -> str:
    return metric.get("pod") or metric.get("instance") or "unknown-pod"


def _result(response: dict[str, Any]) -> list[dict[str, Any]]:
    return ((response.get("data") or {}).get("data") or {}).get("result") or []


def _vector(expr: str) -> tuple[dict[str, float], dict[str, Any] | None]:
    """Instant query → {pod: value}. Returns (values, error_dict_or_None)."""
    response = instant_query(expr)
    if response.get("isError"):
        return {}, response
    out: dict[str, float] = {}
    for entry in _result(response):
        try:
            out[_pod_of(entry.get("metric", {}))] = float(entry["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out, None


def _series(expr: str, start: int, end: int, step: str) -> tuple[dict[str, list[tuple[float, float]]], dict[str, Any] | None]:
    """Range query → {pod: [(ts, value), ...]}."""
    response = range_query(expr, start=str(start), end=str(end), step=step)
    if response.get("isError"):
        return {}, response
    out: dict[str, list[tuple[float, float]]] = {}
    for entry in _result(response):
        points = []
        for ts, val in entry.get("values", []):
            try:
                points.append((float(ts), float(val)))
            except (TypeError, ValueError):
                continue
        out[_pod_of(entry.get("metric", {}))] = points
    return out, None


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _window(lookback_minutes: int) -> tuple[int, int, int]:
    window_m = clamp_lookback_minutes(lookback_minutes)
    end = int(time.time())
    return window_m, end - window_m * 60, end


# --- threads ----------------------------------------------------------------------


def get_thread_trend(namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s") -> dict[str, Any]:
    """Thread count over a window, peak and daemon counts, threads started per second (churn),
    and deadlocked threads, per pod."""
    sel = namespace_service_selector(namespace, service)
    window_m, start, end = _window(lookback_minutes)

    counts, err = _series(f"java_lang_Threading_ThreadCount{{{sel}}}", start, end, step)
    if err:
        return err
    started_now, err = _vector(f"rate(java_lang_Threading_TotalStartedThreadCount{{{sel}}}[5m])")
    if err:
        return err
    started_window, err = _vector(
        f"increase(java_lang_Threading_TotalStartedThreadCount{{{sel}}}[{window_m}m]) / {window_m * 60}"
    )
    if err:
        return err
    peak, err = _vector(f"java_lang_Threading_PeakThreadCount{{{sel}}}")
    if err:
        return err
    daemon, err = _vector(f"java_lang_Threading_DaemonThreadCount{{{sel}}}")
    if err:
        return err
    deadlocked, err = _vector(f"jvm_threads_deadlocked{{{sel}}}")
    if err:
        return err

    pods: dict[str, Any] = {}
    for pod in sorted(set(counts) | set(started_now) | set(peak)):
        values = [v for _, v in counts.get(pod, [])]
        pods[pod] = {
            "thread_count_now": int(values[-1]) if values else None,
            "thread_count_min": int(min(values)) if values else None,
            "thread_count_max": int(max(values)) if values else None,
            "thread_count_change": int(values[-1] - values[0]) if len(values) > 1 else None,
            "peak_thread_count": int(peak[pod]) if pod in peak else None,
            "daemon_thread_count": int(daemon[pod]) if pod in daemon else None,
            "threads_started_per_second_now": _rounded(started_now.get(pod), 1),
            "threads_started_per_second_window_avg": _rounded(started_window.get(pod), 1),
            "deadlocked_threads": int(deadlocked[pod]) if pod in deadlocked else None,
        }

    return ok({
        "namespace": namespace,
        "service": service,
        "lookback_minutes": window_m,
        "pods": pods,
        "deadlock_metric_available": bool(deadlocked),
        "caveat": _THREAD_TREND_NOTE,
    })


# --- process: CPU and file descriptors --------------------------------------------


def get_process_resources(namespace: str, service: str, lookback_minutes: int = 60, step: str = "60s") -> dict[str, Any]:
    """Process CPU (cores, now and over a window, vs the container CPU limit), open vs max file
    descriptors, and resident memory, per pod."""
    sel = namespace_service_selector(namespace, service)
    window_m, start, end = _window(lookback_minutes)

    cpu_now, err = _vector(f"rate(process_cpu_seconds_total{{{sel}}}[5m])")
    if err:
        return err
    cpu_series, err = _series(f"rate(process_cpu_seconds_total{{{sel}}}[5m])", start, end, step)
    if err:
        return err
    fds_open, err = _vector(f"process_open_fds{{{sel}}}")
    if err:
        return err
    fds_max, err = _vector(f"process_max_fds{{{sel}}}")
    if err:
        return err
    rss, err = _vector(f"process_resident_memory_bytes{{{sel}}}")
    if err:
        return err

    cpu_limits = _container_limits(namespace, sel, "cpu")

    pods: dict[str, Any] = {}
    for pod in sorted(set(cpu_now) | set(fds_open) | set(rss)):
        values = [v for _, v in cpu_series.get(pod, [])]
        limit = cpu_limits.get(pod)
        opened, maximum = fds_open.get(pod), fds_max.get(pod)
        pods[pod] = {
            "cpu_cores_now": _rounded(cpu_now.get(pod), 3),
            "cpu_cores_window_avg": _rounded(sum(values) / len(values), 3) if values else None,
            "cpu_cores_window_max": _rounded(max(values), 3) if values else None,
            "cpu_limit_cores": _rounded(limit, 3),
            "cpu_percent_of_limit_window_max": _rounded(100 * max(values) / limit, 1) if values and limit else None,
            "fd_open": int(opened) if opened is not None else None,
            "fd_max": int(maximum) if maximum is not None else None,
            "fd_percent_used": _rounded(100 * opened / maximum, 2) if opened is not None and maximum else None,
            "resident_memory_bytes": int(rss[pod]) if pod in rss else None,
        }

    return ok({
        "namespace": namespace,
        "service": service,
        "lookback_minutes": window_m,
        "pods": pods,
        "caveat": _PROCESS_NOTE,
    })


# --- memory against the container limit ------------------------------------------


def _pods_and_containers(sel: str) -> tuple[list[str], list[str], dict[str, Any] | None]:
    """Pod and container names of the JVM series, for querying container-level metrics."""
    response = instant_query(f"java_lang_Memory_HeapMemoryUsage_committed{{{sel}}}")
    if response.get("isError"):
        return [], [], response
    pods, containers = set(), set()
    for entry in _result(response):
        metric = entry.get("metric", {})
        if _POD_NAME_RE.match(metric.get("pod", "")):
            pods.add(metric["pod"])
        if _POD_NAME_RE.match(metric.get("container", "")):
            containers.add(metric["container"])
    return sorted(pods), sorted(containers), None


def _container_selector(namespace: str, pods: list[str], containers: list[str]) -> str:
    sel = f'namespace="{escape_label_value(namespace)}", pod=~"{"|".join(pods)}"'
    if containers:
        sel += f', container=~"{"|".join(containers)}"'
    return sel


def _container_limits(namespace: str, jvm_sel: str, resource: str) -> dict[str, float]:
    """Container resource limits per pod (empty when unavailable; limits are optional context)."""
    pods, containers, err = _pods_and_containers(jvm_sel)
    if err or not pods:
        return {}
    values, err = _vector(
        f'kube_pod_container_resource_limits{{{_container_selector(namespace, pods, containers)}, resource="{resource}"}}'
    )
    return {} if err else values


def get_memory_vs_limit(namespace: str, service: str) -> dict[str, Any]:
    """Container memory working set and RSS against the memory limit (headroom), next to the JVM's
    heap committed, non-heap committed and direct buffers, per pod."""
    sel = namespace_service_selector(namespace, service)
    pods, containers, err = _pods_and_containers(sel)
    if err:
        return err
    if not pods:
        return ToolError(
            "business",
            False,
            f"No JVM pods found for service '{service}' in namespace '{namespace}'.",
            attempted={"namespace": namespace, "service": service},
            alternatives=["Check JVM_LABEL_KEY and the service name -- a wrong label key returns no series"],
        ).to_dict()

    csel = _container_selector(namespace, pods, containers)
    working_set, err = _vector(f"container_memory_working_set_bytes{{{csel}}}")
    if err:
        return err
    container_rss, err = _vector(f"container_memory_rss{{{csel}}}")
    if err:
        return err
    limit, err = _vector(f'kube_pod_container_resource_limits{{{csel}, resource="memory"}}')
    if err:
        return err
    heap_committed, err = _vector(f"java_lang_Memory_HeapMemoryUsage_committed{{{sel}}}")
    if err:
        return err
    heap_used, err = _vector(f"java_lang_Memory_HeapMemoryUsage_used{{{sel}}}")
    if err:
        return err
    nonheap_committed, err = _vector(f"java_lang_Memory_NonHeapMemoryUsage_committed{{{sel}}}")
    if err:
        return err
    direct, err = _vector(f'jvm_buffer_pool_used_bytes{{{sel}, pool="direct"}}')
    if err:
        return err

    out: dict[str, Any] = {}
    for pod in pods:
        ws, lim = working_set.get(pod), limit.get(pod)
        jvm_parts = [heap_committed.get(pod), nonheap_committed.get(pod), direct.get(pod)]
        other = ws - sum(p for p in jvm_parts if p is not None) if ws is not None else None
        out[pod] = {
            "working_set_bytes": int(ws) if ws is not None else None,
            "container_rss_bytes": int(container_rss[pod]) if pod in container_rss else None,
            "memory_limit_bytes": int(lim) if lim is not None else None,
            "working_set_percent_of_limit": _rounded(100 * ws / lim, 1) if ws is not None and lim else None,
            "headroom_percent": _rounded(100 * (lim - ws) / lim, 1) if ws is not None and lim else None,
            "heap_committed_bytes": int(heap_committed[pod]) if pod in heap_committed else None,
            "heap_used_bytes": int(heap_used[pod]) if pod in heap_used else None,
            "nonheap_committed_bytes": int(nonheap_committed[pod]) if pod in nonheap_committed else None,
            "direct_buffer_bytes": int(direct[pod]) if pod in direct else None,
            "other_bytes": int(other) if other is not None else None,
        }

    ranked = sorted(
        (p for p in out if out[p]["headroom_percent"] is not None), key=lambda p: out[p]["headroom_percent"]
    )
    return ok({
        "namespace": namespace,
        "service": service,
        "pods": out,
        "least_headroom": [{"pod": p, "headroom_percent": out[p]["headroom_percent"]} for p in ranked[:3]],
        "limit_available": bool(limit),
        "caveat": _MEMORY_LIMIT_NOTE,
    })


# --- class loading ----------------------------------------------------------------


def get_class_loading_trend(namespace: str, service: str, lookback_minutes: int = 180, step: str = "120s") -> dict[str, Any]:
    """Loaded classes over a window with a linear growth rate (classes/hour), plus classes loaded
    and unloaded during the window, per pod."""
    sel = namespace_service_selector(namespace, service)
    window_m, start, end = _window(lookback_minutes)

    loaded, err = _series(f"java_lang_ClassLoading_LoadedClassCount{{{sel}}}", start, end, step)
    if err:
        return err
    loaded_in_window, err = _vector(f"increase(java_lang_ClassLoading_TotalLoadedClassCount{{{sel}}}[{window_m}m])")
    if err:
        return err
    unloaded_in_window, err = _vector(f"increase(java_lang_ClassLoading_UnloadedClassCount{{{sel}}}[{window_m}m])")
    if err:
        return err

    pods: dict[str, Any] = {}
    for pod, points in sorted(loaded.items()):
        fit = _linear_regression(points)
        pods[pod] = {
            "loaded_classes_now": int(points[-1][1]) if points else None,
            "samples": len(points),
            "growth_classes_per_hour": _rounded(fit["slope_bytes_per_second"] * 3600, 1) if fit else None,
            "r_squared": _rounded(fit["r_squared"], 3) if fit else None,
            "classes_loaded_in_window": _rounded(loaded_in_window.get(pod), 0),
            "classes_unloaded_in_window": _rounded(unloaded_in_window.get(pod), 0),
        }

    return ok({
        "namespace": namespace,
        "service": service,
        "lookback_minutes": window_m,
        "pods": pods,
        "caveat": _CLASS_LOADING_NOTE,
    })


# --- JVM identity and uptime ------------------------------------------------------


def get_jvm_runtime_info(namespace: str, service: str) -> dict[str, Any]:
    """JVM version, vendor and runtime name, uptime and start time, per pod -- spots mixed JVM
    versions, the oldest pod, and recent restarts."""
    sel = namespace_service_selector(namespace, service)

    info_response = instant_query(f"jvm_info{{{sel}}}")
    if info_response.get("isError"):
        return info_response
    uptime_ms, err = _vector(f"java_lang_Runtime_Uptime{{{sel}}}")
    if err:
        return err
    start_s, err = _vector(f"process_start_time_seconds{{{sel}}}")
    if err:
        return err

    info = {_pod_of(e.get("metric", {})): e.get("metric", {}) for e in _result(info_response)}
    pods: dict[str, Any] = {}
    for pod in sorted(set(info) | set(uptime_ms)):
        labels = info.get(pod, {})
        started = start_s.get(pod)
        pods[pod] = {
            "version": labels.get("version"),
            "vendor": labels.get("vendor"),
            "runtime": labels.get("runtime"),
            "uptime_hours": _rounded(uptime_ms[pod] / 3_600_000, 2) if pod in uptime_ms else None,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)) if started else None,
        }

    by_uptime = sorted((p for p in pods if pods[p]["uptime_hours"] is not None), key=lambda p: pods[p]["uptime_hours"])
    versions = sorted({v["version"] for v in pods.values() if v["version"]})
    return ok({
        "namespace": namespace,
        "service": service,
        "pods": pods,
        "distinct_versions": versions,
        "mixed_versions": len(versions) > 1,
        "newest_pod": by_uptime[0] if by_uptime else None,
        "oldest_pod": by_uptime[-1] if by_uptime else None,
        "note": "uptime resets on container restart; a pod much younger than its siblings restarted or was rescheduled.",
    })
