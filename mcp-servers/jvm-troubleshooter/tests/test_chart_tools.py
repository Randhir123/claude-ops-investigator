from __future__ import annotations

from jvm_troubleshooter.tools import chart_tools
from tests.conftest import FakeResponse, empty_matrix_result, matrix_result

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# --- parsing helpers ---------------------------------------------------------


def test_iter_matrix_series_extracts_points_and_skips_non_numeric():
    response = matrix_result(
        [
            {
                "metric": {"instance": "pod-1"},
                "values": [(1700000000, "1.5"), (1700000060, "NaN"), (1700000120, "3")],
            }
        ]
    )

    series = chart_tools._iter_matrix_series(response)

    assert len(series) == 1
    metric, points = series[0]
    assert metric == {"instance": "pod-1"}
    assert points == [(1700000000.0, 1.5), (1700000120.0, 3.0)]  # the "NaN" point is dropped


def test_iter_matrix_series_tolerates_empty_result():
    assert chart_tools._iter_matrix_series(empty_matrix_result()) == []
    assert chart_tools._iter_matrix_series({}) == []


def test_series_label_prefers_instance_then_pod_then_falls_back():
    assert chart_tools._series_label({"instance": "pod-1"}) == "pod-1"
    assert chart_tools._series_label({"pod": "pod-2"}) == "pod-2"
    assert chart_tools._series_label({}) == "unknown-pod"


def test_series_label_appends_extra_keys():
    label = chart_tools._series_label({"instance": "pod-1", "name": "scavenge"}, extra_keys=("name",))
    assert label == "pod-1 / scavenge"


# --- render_heap_trend_chart -------------------------------------------------


def test_render_heap_trend_chart_returns_png_on_success(prom_env, queue_responses):
    queue_responses(
        FakeResponse(200, json_data=matrix_result()),  # used
        FakeResponse(200, json_data=matrix_result()),  # max
    )

    result = chart_tools.render_heap_trend_chart("si-dev-001a", "event-data", lookback_minutes=60)

    assert result["isError"] is False
    assert result["png_bytes"].startswith(PNG_MAGIC)
    assert "60m" in result["caption"]


def test_render_heap_trend_chart_returns_business_error_when_no_data_points(prom_env, queue_responses):
    queue_responses(
        FakeResponse(200, json_data=empty_matrix_result()),
        FakeResponse(200, json_data=empty_matrix_result()),
    )

    result = chart_tools.render_heap_trend_chart("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert result["errorCategory"] == "business"
    assert "nothing to chart" in result["message"]


def test_render_heap_trend_chart_propagates_underlying_error(prom_env, queue_responses):
    queue_responses(FakeResponse(500, text="boom"))

    result = chart_tools.render_heap_trend_chart("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"


# --- render_gc_behavior_chart -------------------------------------------------


def test_render_gc_behavior_chart_returns_png_on_success(prom_env, queue_responses):
    queue_responses(
        FakeResponse(200, json_data=matrix_result([{"metric": {"instance": "pod-1", "name": "scavenge"}, "values": [(1700000000, "1"), (1700000060, "2")]}])),
        FakeResponse(200, json_data=matrix_result([{"metric": {"instance": "pod-1", "name": "scavenge"}, "values": [(1700000000, "0.1"), (1700000060, "0.2")]}])),
    )

    result = chart_tools.render_gc_behavior_chart("si-dev-001a", "event-data", lookback_minutes=30)

    assert result["isError"] is False
    assert result["png_bytes"].startswith(PNG_MAGIC)
    assert "frequency" in result["caption"].lower()


def test_render_gc_behavior_chart_no_data(prom_env, queue_responses):
    queue_responses(
        FakeResponse(200, json_data=empty_matrix_result()),
        FakeResponse(200, json_data=empty_matrix_result()),
    )

    result = chart_tools.render_gc_behavior_chart("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert result["errorCategory"] == "business"


# --- render_gc_memory_correlation_chart ---------------------------------------


def test_render_gc_memory_correlation_chart_returns_png_on_success(prom_env, queue_responses):
    queue_responses(
        FakeResponse(200, json_data=matrix_result()),  # heap used
        FakeResponse(200, json_data=matrix_result()),  # heap max
        FakeResponse(200, json_data=matrix_result([{"metric": {"instance": "pod-1", "name": "scavenge"}, "values": [(1700000000, "1")]}])),  # gc freq
        FakeResponse(200, json_data=matrix_result([{"metric": {"instance": "pod-1", "name": "scavenge"}, "values": [(1700000000, "0.1")]}])),  # gc overhead
    )

    result = chart_tools.render_gc_memory_correlation_chart("si-dev-001a", "event-data")

    assert result["isError"] is False
    assert result["png_bytes"].startswith(PNG_MAGIC)
    assert "leak" in result["caption"].lower() or "load" in result["caption"].lower()


def test_render_gc_memory_correlation_chart_propagates_error(prom_env, monkeypatch):
    monkeypatch.setattr(
        chart_tools,
        "get_gc_memory_correlation",
        lambda *a, **k: {"isError": True, "errorCategory": "transient", "message": "boom"},
    )

    result = chart_tools.render_gc_memory_correlation_chart("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert result["message"] == "boom"
