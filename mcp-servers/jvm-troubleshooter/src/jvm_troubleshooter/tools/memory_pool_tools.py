"""OpenJ9 memory-pool and native/non-heap memory tools.

Metric source: `java_lang_MemoryPool_Usage_used` / `_max`, split by the
`name` label. OpenJ9's JMX Exporter reports these pool names (this is an
OpenJ9-specific set, different from HotSpot's Eden/Survivor/Metaspace
naming):

  - "nursery-allocate"              young-gen active allocation area
  - "nursery-survivor"              young-gen survivor area
  - "tenured-SOA"                   old-gen Small Object Area
  - "tenured-LOA"                   old-gen Large Object Area
  - "JIT code cache"                compiled-code cache (non-heap)
  - "JIT data cache"                JIT compiler working data (non-heap)
  - "class storage"                 loaded-class metadata (non-heap)
  - "miscellaneous non-heap storage" catch-all non-heap (non-heap)

`java_nio_BufferPool_MemoryUsed{name="direct"}` (direct ByteBuffer / NIO
memory) is reported separately from the MemoryPool MBeans but is included in
`get_native_memory_summary` below since it's a common contributor to
container memory pressure that heap-only monitoring misses entirely.

HARD BOUNDARY: none of this is a substitute for a real native-memory
tracking tool (e.g. an NMT-equivalent, or `-Xtrace`/OpenJ9 native memory
reports). JMX has no visibility into malloc'd native memory outside these
JVM-managed pools (native libraries, JNI allocations, etc.) -- if RSS/cgroup
memory usage is significantly higher than heap + these non-heap pools
combined, that gap is invisible here and needs a different diagnostic (a
container-level memory profile or an OpenJ9 native memory report), not
cleverer PromQL against these metrics.
"""

from __future__ import annotations

from typing import Any

from jvm_troubleshooter.errors import ok
from jvm_troubleshooter.tools.prometheus_client import instant_query, namespace_service_selector

_NATIVE_MEMORY_POOLS = ["JIT code cache", "JIT data cache", "class storage", "miscellaneous non-heap storage"]

_NATIVE_VISIBILITY_NOTE = (
    "This covers only JVM-managed non-heap pools plus direct NIO buffers -- it has no visibility "
    "into malloc'd native memory (native libraries, JNI allocations, etc.). If container RSS is "
    "meaningfully higher than heap + these pools combined, that gap is a real blind spot here, not "
    "something a different PromQL expression can recover -- it needs a container-level memory "
    "profile or an OpenJ9 native memory report instead."
)

_HEAP_POOLS = ["nursery-allocate", "nursery-survivor", "tenured-SOA", "tenured-LOA"]

_FRAGMENTATION_NOTE = (
    "'Fragmentation' here means committed-but-unused space per heap pool (space the JVM has "
    "claimed from the OS but isn't currently holding live/allocatable data in), not internal "
    "free-list/object-layout fragmentation inside a pool -- JMX exposes no metric for the latter "
    "at all. A high percentage can also just mean the pool recently shrank its live set after a "
    "GC and hasn't been asked to grow again yet, which is normal, not a problem on its own; treat "
    "this as a lead worth investigating over time (does it keep climbing?), not a standalone "
    "verdict. This also relies on java_lang_MemoryPool_Usage_committed being exported per pool -- "
    "confirmed present for java_lang_Memory_HeapMemoryUsage (see heap_tools), and expected by the "
    "same JMX Exporter convention for MemoryPool MXBeans' own Usage composite, but not "
    "independently verified against a live OpenJ9 JMX Exporter dump for MemoryPool specifically -- "
    "check your own /metrics output if this tool returns unexpectedly empty results."
)


def get_memory_pool_breakdown(namespace: str, service: str) -> dict[str, Any]:
    """Current used/max per OpenJ9 memory pool (nursery, tenured, JIT caches, class storage), per pod."""
    selector = namespace_service_selector(namespace, service)

    used = instant_query(f"java_lang_MemoryPool_Usage_used{{{selector}}}")
    if used.get("isError"):
        return used
    max_ = instant_query(f"java_lang_MemoryPool_Usage_max{{{selector}}}")
    if max_.get("isError"):
        return max_

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "pool_used_bytes": used["data"],
            "pool_max_bytes": max_["data"],
            "note": (
                "Grouped by the 'name' label: OpenJ9 pools are nursery-allocate, nursery-survivor, "
                "tenured-SOA, tenured-LOA (heap) and 'JIT code cache', 'JIT data cache', "
                "'class storage', 'miscellaneous non-heap storage' (non-heap)."
            ),
        }
    )


def get_native_memory_summary(namespace: str, service: str) -> dict[str, Any]:
    """Non-heap JVM memory (JIT caches, class storage, misc) plus direct/NIO buffer memory, per pod."""
    selector = namespace_service_selector(namespace, service)
    pool_alt = "|".join(_NATIVE_MEMORY_POOLS)

    non_heap_used = instant_query(
        f'java_lang_MemoryPool_Usage_used{{{selector}, name=~"({pool_alt})"}}'
    )
    if non_heap_used.get("isError"):
        return non_heap_used
    non_heap_max = instant_query(
        f'java_lang_MemoryPool_Usage_max{{{selector}, name=~"({pool_alt})"}}'
    )
    if non_heap_max.get("isError"):
        return non_heap_max
    direct_buffers = instant_query(f'java_nio_BufferPool_MemoryUsed{{{selector}, name="direct"}}')
    if direct_buffers.get("isError"):
        return direct_buffers

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "non_heap_pool_used_bytes": non_heap_used["data"],
            "non_heap_pool_max_bytes": non_heap_max["data"],
            "direct_buffer_memory_bytes": direct_buffers["data"],
            "caveat": _NATIVE_VISIBILITY_NOTE,
        }
    )


def get_heap_fragmentation(namespace: str, service: str) -> dict[str, Any]:
    """Committed-vs-used per heap pool (nursery-allocate, nursery-survivor, tenured-SOA,
    tenured-LOA), per pod -- a large committed/used gap is space the JVM is holding from the
    OS without currently using it, distinct from a true heap-ceiling (-Xmx) pressure question."""
    selector = namespace_service_selector(namespace, service)
    pool_alt = "|".join(_HEAP_POOLS)

    used = instant_query(f'java_lang_MemoryPool_Usage_used{{{selector}, name=~"({pool_alt})"}}')
    if used.get("isError"):
        return used
    committed = instant_query(f'java_lang_MemoryPool_Usage_committed{{{selector}, name=~"({pool_alt})"}}')
    if committed.get("isError"):
        return committed
    fragmentation_percent = instant_query(
        f'100 * (java_lang_MemoryPool_Usage_committed{{{selector}, name=~"({pool_alt})"}} - '
        f'java_lang_MemoryPool_Usage_used{{{selector}, name=~"({pool_alt})"}}) / '
        f'(java_lang_MemoryPool_Usage_committed{{{selector}, name=~"({pool_alt})"}} > 0)'
    )
    if fragmentation_percent.get("isError"):
        return fragmentation_percent

    return ok(
        {
            "namespace": namespace,
            "service": service,
            "pool_used_bytes": used["data"],
            "pool_committed_bytes": committed["data"],
            "fragmentation_percent": fragmentation_percent["data"],
            "caveat": _FRAGMENTATION_NOTE,
        }
    )
