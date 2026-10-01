from __future__ import annotations

from jvm_troubleshooter.tools import gc_tools
from tests.conftest import FakeResponse, vector_result


def test_get_gc_activity_queries_count_and_time(prom_env, capture_queries):
    result = gc_tools.get_gc_activity("si-dev-001a", "event-data")

    assert result["isError"] is False
    assert len(capture_queries) == 2
    assert "java_lang_GarbageCollector_CollectionCount" in capture_queries[0]["params"]["query"]
    assert "java_lang_GarbageCollector_CollectionTime" in capture_queries[1]["params"]["query"]
    assert 'namespace="si-dev-001a"' in capture_queries[0]["params"]["query"]
    assert 'job=~"(event-data)"' in capture_queries[0]["params"]["query"]


def test_get_gc_activity_short_circuits_on_first_error(prom_env, queue_responses):
    queue_responses(FakeResponse(500, text="boom"))

    result = gc_tools.get_gc_activity("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert len(queue_responses.calls) == 1  # never made the second call


def test_get_gc_pause_stats_uses_fixed_5m_windows_not_interval_macros(prom_env, capture_queries):
    result = gc_tools.get_gc_pause_stats("si-dev-001a", "event-data", lookback_minutes=30)

    assert result["isError"] is False
    assert len(capture_queries) == 3
    avg_q, max_q, min_q = (c["params"]["query"] for c in capture_queries)

    # avg: exact increase-ratio over the requested window
    assert "increase(java_lang_GarbageCollector_CollectionTime" in avg_q
    assert "[30m]" in avg_q
    assert "> 0)" in avg_q  # divide-by-zero guard present

    # max/min: bucket-averaged over literal 5m subquery windows, never $__interval/$__rate_interval
    assert "max_over_time" in max_q
    assert "[30m:5m]" in max_q
    assert "$__interval" not in max_q and "$__rate_interval" not in max_q
    assert "min_over_time" in min_q
    assert "[30m:5m]" in min_q

    assert "caveat" in result["data"]
    assert "8-9x" in result["data"]["caveat"]


def test_get_gc_pause_stats_clamps_lookback(prom_env, capture_queries):
    gc_tools.get_gc_pause_stats("si-dev-001a", "event-data", lookback_minutes=999_999)
    assert "[1440m]" in capture_queries[0]["params"]["query"]


def test_get_gc_throughput_uses_exact_formula(prom_env, capture_queries):
    result = gc_tools.get_gc_throughput("si-dev-001a", "event-data", lookback_minutes=60)

    assert result["isError"] is False
    throughput_q, interval_q = (c["params"]["query"] for c in capture_queries)
    assert throughput_q.startswith("100 - 100 *")
    assert "/ 3600" in throughput_q  # 60 minutes -> 3600 seconds
    assert "3600 / (" in interval_q
    assert "> 0)" in interval_q


def test_get_gc_behavior_over_time_uses_range_query(prom_env, capture_queries):
    result = gc_tools.get_gc_behavior_over_time("si-dev-001a", "event-data", lookback_minutes=10, step="30s")

    assert result["isError"] is False
    assert len(capture_queries) == 2
    for call in capture_queries:
        assert call["url"].endswith("/api/v1/query_range")
        assert call["params"]["step"] == "30s"
    assert "rate(java_lang_GarbageCollector_CollectionCount" in capture_queries[0]["params"]["query"]
    assert "rate(java_lang_GarbageCollector_CollectionTime" in capture_queries[1]["params"]["query"]
