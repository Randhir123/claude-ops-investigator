from __future__ import annotations

from jvm_troubleshooter.tools import leak_tools
from tests.conftest import FakeResponse, matrix_result, vector_result


def test_get_memory_leak_indicator_fits_regression_per_pod(prom_env, queue_responses):
    trend = matrix_result(
        series=[
            {
                "metric": {"instance": "pod-1"},
                "values": [
                    (1700000000, "100"),
                    (1700000060, "200"),
                    (1700000120, "300"),
                    (1700000180, "400"),
                ],
            }
        ]
    )
    max_vec = vector_result(value="1000", metric={"instance": "pod-1"})
    queue_responses(FakeResponse(200, json_data=trend), FakeResponse(200, json_data=max_vec))

    result = leak_tools.get_memory_leak_indicator("si-dev-001a", "event-data", lookback_minutes=10)

    assert result["isError"] is False
    assert len(queue_responses.calls) == 2
    trend_q = queue_responses.calls[0]["params"]["query"]
    assert "sum by(instance)" in trend_q
    assert "tenured-SOA|tenured-LOA" in trend_q
    assert queue_responses.calls[0]["url"].endswith("/api/v1/query_range")
    assert queue_responses.calls[1]["url"].endswith("/api/v1/query")

    pod = result["data"]["pods"]["pod-1"]
    assert pod["slope_bytes_per_second"] > 0
    assert pod["r_squared"] > 0.99  # perfectly linear series in the fixture
    assert pod["pool_max_bytes"] == 1000.0
    assert pod["days_to_full_at_current_slope"] is not None
    assert "caveat" in result["data"]
    assert "r_squared" in result["data"]["caveat"]


def test_get_memory_leak_indicator_handles_too_few_samples(prom_env, queue_responses):
    trend = matrix_result(series=[{"metric": {"instance": "pod-1"}, "values": [(1700000000, "100")]}])
    max_vec = vector_result(value="1000", metric={"instance": "pod-1"})
    queue_responses(FakeResponse(200, json_data=trend), FakeResponse(200, json_data=max_vec))

    result = leak_tools.get_memory_leak_indicator("si-dev-001a", "event-data")

    assert result["isError"] is False
    pod = result["data"]["pods"]["pod-1"]
    assert pod["trend"] is None
    assert pod["samples"] == 1


def test_get_memory_leak_indicator_flat_series_has_zero_slope_and_full_r_squared(prom_env, queue_responses):
    trend = matrix_result(
        series=[
            {
                "metric": {"instance": "pod-1"},
                "values": [(1700000000, "500"), (1700000060, "500"), (1700000120, "500")],
            }
        ]
    )
    max_vec = vector_result(value="1000", metric={"instance": "pod-1"})
    queue_responses(FakeResponse(200, json_data=trend), FakeResponse(200, json_data=max_vec))

    result = leak_tools.get_memory_leak_indicator("si-dev-001a", "event-data")

    pod = result["data"]["pods"]["pod-1"]
    assert pod["slope_bytes_per_second"] == 0
    assert pod["days_to_full_at_current_slope"] is None  # no growth -- no projection to make


def test_get_memory_leak_indicator_short_circuits_on_trend_query_error(prom_env, queue_responses):
    queue_responses(FakeResponse(500, text="boom"))

    result = leak_tools.get_memory_leak_indicator("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert len(queue_responses.calls) == 1


def test_get_memory_leak_indicator_short_circuits_on_max_query_error(prom_env, queue_responses):
    trend = matrix_result(series=[{"metric": {"instance": "pod-1"}, "values": [(1700000000, "100"), (1700000060, "200")]}])
    queue_responses(FakeResponse(200, json_data=trend), FakeResponse(500, text="boom"))

    result = leak_tools.get_memory_leak_indicator("si-dev-001a", "event-data")

    assert result["isError"] is True
    assert len(queue_responses.calls) == 2
