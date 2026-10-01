from __future__ import annotations

from jvm_troubleshooter.tools import allocation_tools
from tests.conftest import FakeResponse


def test_get_memory_allocation_rate_queries_nursery_allocate_pool(prom_env, capture_queries):
    result = allocation_tools.get_memory_allocation_rate("si-dev-001a", "event-data", lookback_minutes=15)

    assert result["isError"] is False
    assert len(capture_queries) == 2
    mb_q, gb_q = (c["params"]["query"] for c in capture_queries)

    assert "increase(java_lang_MemoryPool_Usage_used" in mb_q
    assert 'name="nursery-allocate"' in mb_q
    assert "[15m]" in mb_q
    assert "/ 900 / 1048576" in mb_q  # 15 minutes -> 900 seconds
    assert "increase(java_lang_MemoryPool_Usage_used" in gb_q
    assert "* 3600 / 1073741824" in gb_q

    assert "caveat" in result["data"]
    assert "increase()" in result["data"]["caveat"]


def test_get_memory_allocation_rate_short_circuits_on_first_error(prom_env, queue_responses):
    queue_responses(FakeResponse(500, text="boom"))

    result = allocation_tools.get_memory_allocation_rate("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert len(queue_responses.calls) == 1  # never made the second query


def test_get_memory_allocation_rate_clamps_lookback_to_180m(prom_env, capture_queries):
    allocation_tools.get_memory_allocation_rate("si-dev-001a", "event-data", lookback_minutes=999_999)
    assert "[180m]" in capture_queries[0]["params"]["query"]
    assert "[1440m]" not in capture_queries[0]["params"]["query"]
