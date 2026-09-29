from __future__ import annotations

from jvm_troubleshooter.tools import memory_pool_tools
from tests.conftest import FakeResponse


def test_get_memory_pool_breakdown_queries_used_and_max(prom_env, capture_queries):
    result = memory_pool_tools.get_memory_pool_breakdown("si-dev-001a", "event-data")

    assert result["isError"] is False
    assert len(capture_queries) == 2
    assert "java_lang_MemoryPool_Usage_used" in capture_queries[0]["params"]["query"]
    assert "java_lang_MemoryPool_Usage_max" in capture_queries[1]["params"]["query"]


def test_get_native_memory_summary_scopes_to_non_heap_pools_and_direct_buffers(prom_env, capture_queries):
    result = memory_pool_tools.get_native_memory_summary("si-dev-001a", "event-data")

    assert result["isError"] is False
    assert len(capture_queries) == 3
    pool_query = capture_queries[0]["params"]["query"]
    for pool_name in ("JIT code cache", "JIT data cache", "class storage", "miscellaneous non-heap storage"):
        assert pool_name in pool_query
    assert 'java_nio_BufferPool_MemoryUsed{' in capture_queries[2]["params"]["query"]
    assert 'name="direct"' in capture_queries[2]["params"]["query"]
    assert "caveat" in result["data"]
    assert "malloc" in result["data"]["caveat"]


def test_get_heap_fragmentation_queries_used_committed_and_percent(prom_env, capture_queries):
    result = memory_pool_tools.get_heap_fragmentation("si-dev-001a", "event-data")

    assert result["isError"] is False
    assert len(capture_queries) == 3
    queries = [c["params"]["query"] for c in capture_queries]
    assert "java_lang_MemoryPool_Usage_used" in queries[0]
    assert "java_lang_MemoryPool_Usage_committed" in queries[1]
    assert queries[2].startswith("100 * (")
    assert "> 0)" in queries[2]
    for pool_name in ("nursery-allocate", "nursery-survivor", "tenured-SOA", "tenured-LOA"):
        assert pool_name in queries[0]
    assert "caveat" in result["data"]
    assert "committed-but-unused" in result["data"]["caveat"]


def test_get_heap_fragmentation_short_circuits_on_first_error(prom_env, queue_responses):
    queue_responses(FakeResponse(500, text="boom"))

    result = memory_pool_tools.get_heap_fragmentation("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert len(queue_responses.calls) == 1
