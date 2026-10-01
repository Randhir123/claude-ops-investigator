from __future__ import annotations

from jvm_troubleshooter.tools import correlation_tools


def test_get_gc_memory_correlation_composes_heap_and_gc_trends_end_to_end(prom_env, capture_queries):
    """End-to-end: real heap_tools + gc_tools composition against a mocked Prometheus."""
    result = correlation_tools.get_gc_memory_correlation(
        "si-dev-001a", "event-data", lookback_minutes=20, step="30s"
    )

    assert result["isError"] is False
    # get_heap_trend_over_time makes 2 range calls, get_gc_behavior_over_time makes 2 more
    assert len(capture_queries) == 4
    data = result["data"]
    assert set(data) >= {
        "heap_used_bytes_over_time",
        "heap_max_bytes_over_time",
        "gc_frequency_per_min_over_time",
        "gc_overhead_percent_over_time",
        "how_to_read",
    }


def test_get_gc_memory_correlation_propagates_heap_error(prom_env, monkeypatch):
    monkeypatch.setattr(
        correlation_tools,
        "get_heap_trend_over_time",
        lambda *a, **k: {"isError": True, "errorCategory": "transient", "message": "heap query failed"},
    )

    result = correlation_tools.get_gc_memory_correlation("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert result["message"] == "heap query failed"


def test_get_gc_memory_correlation_propagates_gc_error(prom_env, monkeypatch):
    monkeypatch.setattr(
        correlation_tools,
        "get_heap_trend_over_time",
        lambda *a, **k: {
            "isError": False,
            "data": {"heap_used_bytes_over_time": [], "heap_max_bytes_over_time": []},
        },
    )
    monkeypatch.setattr(
        correlation_tools,
        "get_gc_behavior_over_time",
        lambda *a, **k: {"isError": True, "errorCategory": "transient", "message": "gc query failed"},
    )

    result = correlation_tools.get_gc_memory_correlation("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert result["message"] == "gc query failed"
