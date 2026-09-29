from __future__ import annotations

from jvm_troubleshooter.tools import comparative_tools
from tests.conftest import FakeResponse, vector_result


def _queue_all_ok(queue_responses, *, before_value="100", after_value="150"):
    # 4 signals x 2 (before/after) = 8 calls
    for _ in range(4):
        queue_responses(
            FakeResponse(200, json_data=vector_result(before_value, metric={"instance": "pod-1"})),
            FakeResponse(200, json_data=vector_result(after_value, metric={"instance": "pod-1"})),
        )


def test_get_before_after_deploy_comparison_builds_at_modifier_queries(prom_env, queue_responses):
    _queue_all_ok(queue_responses)

    result = comparative_tools.get_before_after_deploy_comparison(
        "si-dev-001a", "event-data", deploy_timestamp=1700000000, window_minutes=15
    )

    assert result["isError"] is False
    assert len(queue_responses.calls) == 8

    before_q = queue_responses.calls[0]["params"]["query"]
    after_q = queue_responses.calls[1]["params"]["query"]
    assert "avg_over_time(" in before_q
    assert "[15m] @ 1700000000)" in before_q
    assert "[15m] @ 1700000900)" in after_q  # deploy_ts + 15*60

    data = result["data"]
    assert set(data["signals"]) == {
        "heap_used_bytes",
        "gc_frequency_per_min",
        "gc_overhead_percent",
        "thread_count",
    }
    pod = data["signals"]["heap_used_bytes"]["pod-1"]
    assert pod["before"] == 100.0
    assert pod["after"] == 150.0
    assert pod["percent_change"] == 50.0
    assert "caveat" in data


def test_get_before_after_deploy_comparison_rejects_non_numeric_timestamp(prom_env, capture_queries):
    result = comparative_tools.get_before_after_deploy_comparison(
        "si-dev-001a", "event-data", deploy_timestamp="not-a-timestamp"
    )

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert len(capture_queries) == 0  # never made a single Prometheus call


def test_get_before_after_deploy_comparison_accepts_string_epoch(prom_env, queue_responses):
    _queue_all_ok(queue_responses)

    result = comparative_tools.get_before_after_deploy_comparison(
        "si-dev-001a", "event-data", deploy_timestamp="1700000000"
    )

    assert result["isError"] is False
    assert result["data"]["deploy_timestamp"] == 1700000000


def test_get_before_after_deploy_comparison_short_circuits_on_first_error(prom_env, queue_responses):
    queue_responses(FakeResponse(500, text="boom"))

    result = comparative_tools.get_before_after_deploy_comparison(
        "si-dev-001a", "event-data", deploy_timestamp=1700000000
    )

    assert result["isError"] is True
    assert len(queue_responses.calls) == 1


def test_get_before_after_deploy_comparison_clamps_window_minutes(prom_env, queue_responses):
    _queue_all_ok(queue_responses)

    result = comparative_tools.get_before_after_deploy_comparison(
        "si-dev-001a", "event-data", deploy_timestamp=1700000000, window_minutes=99_999
    )

    assert result["data"]["window_minutes"] == 180
