"""One-call JVM triage snapshot for active incidents.

Every other tool in this project answers one focused question. This one
exists for the moment an investigator (human or subagent) has a symptom like
"time-series-query is slow" and needs a fast first read across heap, GC,
memory pools, and threads before deciding which of the focused tools to dig
into next -- one call instead of five round-trips.

This tool is intentionally resilient to partial failure: if one underlying
signal errors (e.g. a bad label config affecting only one metric family), the
snapshot still returns everything that succeeded plus a list of what didn't,
rather than failing the whole triage because of one broken sub-query.
"""

from __future__ import annotations

from typing import Any

from jvm_troubleshooter.errors import ok
from jvm_troubleshooter.tools.gc_tools import get_gc_pause_stats, get_gc_throughput
from jvm_troubleshooter.tools.heap_tools import get_heap_status
from jvm_troubleshooter.tools.memory_pool_tools import get_native_memory_summary
from jvm_troubleshooter.tools.prometheus_client import clamp_lookback_minutes
from jvm_troubleshooter.tools.runtime_tools import get_memory_vs_limit, get_thread_trend
from jvm_troubleshooter.tools.thread_tools import get_thread_status


def get_jvm_incident_snapshot(namespace: str, service: str, lookback_minutes: int = 60) -> dict[str, Any]:
    """Combined heap/GC/native-memory/thread snapshot, per pod, for fast incident triage.

    Returns partial results and a per-signal error list rather than failing
    outright if one sub-query errors -- an incomplete snapshot is still more
    useful mid-incident than no snapshot at all.
    """
    window_m = clamp_lookback_minutes(lookback_minutes)

    checks = {
        "heap_status": lambda: get_heap_status(namespace, service),
        "gc_pause_stats": lambda: get_gc_pause_stats(namespace, service, lookback_minutes=window_m),
        "gc_throughput": lambda: get_gc_throughput(namespace, service, lookback_minutes=window_m),
        "native_memory_summary": lambda: get_native_memory_summary(namespace, service),
        "thread_status": lambda: get_thread_status(namespace, service),
        "thread_trend": lambda: get_thread_trend(namespace, service, lookback_minutes=window_m),
        "memory_vs_limit": lambda: get_memory_vs_limit(namespace, service),
    }

    results: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []
    for key, fn in checks.items():
        outcome = fn()
        if outcome.get("isError"):
            failures.append({"signal": key, **outcome})
        else:
            results[key] = outcome["data"]

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "lookback_minutes": window_m,
            "signals": results,
            "failed_signals": failures,
            "note": (
                "This is a breadth-first snapshot, not a diagnosis. Follow up with "
                "get_gc_memory_correlation for a leak-vs-load-spike read over time, or "
                "get_memory_pool_breakdown for the full per-pool picture. GC pause max/min carry "
                "the bucket-averaging caveat -- see gc_pause_stats.caveat when present. Thread "
                "count carries its own caveat -- see thread_status.caveat when present. "
                "thread_trend adds threads started per second (churn) and deadlocked threads; "
                "memory_vs_limit.least_headroom names the pods closest to their container memory "
                "limit (OOMKilled risk). "
                "failed_signals lists any sub-query that errored; treat those as gaps, not as "
                "confirmation that the corresponding metric is normal."
            ),
        }
    )
