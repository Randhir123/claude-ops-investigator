"""Thread and class-loading tools.

Metric source: `java_lang_Threading_ThreadCount` (`java.lang:type=Threading`)
and `java_lang_ClassLoading_LoadedClassCount`
(`java.lang:type=ClassLoading`).

CAVEAT: this is a live thread *count*, not thread *state*. It cannot tell you
how many threads are RUNNABLE vs BLOCKED vs WAITING, and it cannot show you
what a thread is stuck on. A rising thread count is a useful early symptom
(thread leak, connection-pool exhaustion, a stuck downstream call spawning
retries), but diagnosing an actual hang, deadlock, or lock-contention issue
requires a real thread dump / javacore analyzed with a tool like IBM's TMDA
(Thread and Monitor Dump Analyzer for Java) -- this tool cannot substitute
for that and should not be used to rule out a hang just because the count
looks normal.
"""

from __future__ import annotations

from typing import Any

from jvm_troubleshooter.errors import ok
from jvm_troubleshooter.tools.prometheus_client import instant_query, namespace_service_selector

_THREAD_DUMP_NOTE = (
    "This is a live thread COUNT only, not thread STATE -- it cannot distinguish RUNNABLE from "
    "BLOCKED/WAITING and has no visibility into what a thread is stuck on. A steady or normal "
    "count does not rule out a hang or deadlock among a subset of threads. If a slowdown or hang "
    "is suspected, take a real thread dump (javacore) and analyze it with IBM TMDA -- that is a "
    "capability boundary this metric-only tool cannot cross."
)


def get_thread_status(namespace: str, service: str) -> dict[str, Any]:
    """Current live thread count and loaded class count, per pod."""
    selector = namespace_service_selector(namespace, service)

    threads = instant_query(f"java_lang_Threading_ThreadCount{{{selector}}}")
    if threads.get("isError"):
        return threads
    classes = instant_query(f"java_lang_ClassLoading_LoadedClassCount{{{selector}}}")
    if classes.get("isError"):
        return classes

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "thread_count": threads["data"],
            "loaded_class_count": classes["data"],
            "caveat": _THREAD_DUMP_NOTE,
        }
    )
