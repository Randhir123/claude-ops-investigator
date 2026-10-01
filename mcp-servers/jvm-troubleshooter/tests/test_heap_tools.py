from __future__ import annotations

from jvm_troubleshooter.tools import heap_tools
from tests.conftest import FakeResponse


def test_get_heap_status_queries_used_max_committed_percent(prom_env, capture_queries):
    result = heap_tools.get_heap_status("si-dev-001a", "time-series-query")

    assert result["isError"] is False
    assert len(capture_queries) == 4
    queries = [c["params"]["query"] for c in capture_queries]
    assert "java_lang_Memory_HeapMemoryUsage_used" in queries[0]
    assert "java_lang_Memory_HeapMemoryUsage_max" in queries[1]
    assert "java_lang_Memory_HeapMemoryUsage_committed" in queries[2]
    assert queries[3].startswith("100 *")
    assert "> 0)" in queries[3]
    assert "caveat" in result["data"]
    assert "resources.limits.memory" in result["data"]["caveat"]


def test_get_heap_status_short_circuits_on_error(prom_env, queue_responses):
    queue_responses(
        FakeResponse(200, json_data={"status": "success", "data": {"result": []}}),
        FakeResponse(500, text="boom"),
    )

    result = heap_tools.get_heap_status("si-dev-001a", "time-series-query")

    assert result["isError"] is True
    assert len(queue_responses.calls) == 2  # stopped after the second (failing) call


def test_get_heap_trend_over_time_is_a_range_query(prom_env, capture_queries):
    result = heap_tools.get_heap_trend_over_time(
        "si-dev-001a", "time-series-query", lookback_minutes=45, step="15s"
    )

    assert result["isError"] is False
    assert len(capture_queries) == 2
    for call in capture_queries:
        assert call["url"].endswith("/api/v1/query_range")
        assert call["params"]["step"] == "15s"
    assert result["data"]["lookback_minutes"] == 45
