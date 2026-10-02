from __future__ import annotations

import httpx
import pytest

from jvm_troubleshooter.tools import runtime_tools
from tests.conftest import FakeResponse

POD_A = "tsq-6fb9-aaaaa"
POD_B = "tsq-6fb9-bbbbb"


def _labels(pod: str, **extra) -> dict:
    return {"pod": pod, "container": "tsq", "instance": f"10.0.0.{1 if pod == POD_A else 2}:8080", **extra}


def vector(*items: tuple[str, float], **extra) -> dict:
    return {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": _labels(pod, **extra), "value": [1790000000, str(value)]} for pod, value in items
    ]}}


def matrix(*items: tuple[str, list[float]], step_s: int = 60) -> dict:
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": _labels(pod), "values": [[1790000000 + i * step_s, str(v)] for i, v in enumerate(values)]}
        for pod, values in items
    ]}}


EMPTY = {"status": "success", "data": {"resultType": "vector", "result": []}}


@pytest.fixture
def prom(monkeypatch, prom_env):
    """Answer each PromQL query from `routes`: the first (substring, payload) whose substring is in
    the query wins; unmatched queries get an empty result. Records every query."""
    routes: list[tuple[str, dict, str | None]] = []
    seen: list[str] = []

    def fake_request(method, url, **kwargs):
        query = kwargs["params"]["query"]
        seen.append(query)
        endpoint = "range" if url.endswith("/query_range") else "instant"
        for needle, payload, kind in routes:
            if needle in query and kind in (None, endpoint):
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(200, json_data=payload)
        return FakeResponse(200, json_data=EMPTY)

    monkeypatch.setattr(httpx, "request", fake_request)

    class Prom:
        def route(self, needle, payload, kind=None):
            routes.append((needle, payload, kind))

        queries = seen

    return Prom()


# --- threads -----------------------------------------------------------------------------


def test_thread_trend_summarizes_count_churn_and_deadlocks(prom):
    prom.route("java_lang_Threading_ThreadCount", matrix((POD_A, [400, 380, 1024, 446])))
    prom.route("rate(java_lang_Threading_TotalStartedThreadCount", vector((POD_A, 135.94)))
    prom.route("increase(java_lang_Threading_TotalStartedThreadCount", vector((POD_A, 129.51)))
    prom.route("java_lang_Threading_PeakThreadCount", vector((POD_A, 1512)))
    prom.route("java_lang_Threading_DaemonThreadCount", vector((POD_A, 159)))
    prom.route("jvm_threads_deadlocked", vector((POD_A, 0)))

    result = runtime_tools.get_thread_trend("si", "tsq", lookback_minutes=60)

    assert result["isError"] is False
    assert result["data"]["pods"][POD_A] == {
        "thread_count_now": 446,
        "thread_count_min": 380,
        "thread_count_max": 1024,
        "thread_count_change": 46,
        "peak_thread_count": 1512,
        "daemon_thread_count": 159,
        "threads_started_per_second_now": 135.9,
        "threads_started_per_second_window_avg": 129.5,
        "deadlocked_threads": 0,
    }
    assert result["data"]["deadlock_metric_available"] is True
    # the window average divides the counter increase by the window in seconds
    assert any(q.endswith("[60m]) / 3600") for q in prom.queries)
    assert "not thread STATE" in result["data"]["caveat"]


def test_thread_trend_without_deadlock_metric(prom):
    prom.route("java_lang_Threading_ThreadCount", matrix((POD_A, [10, 12])))

    data = runtime_tools.get_thread_trend("si", "tsq")["data"]

    assert data["pods"][POD_A]["deadlocked_threads"] is None
    assert data["deadlock_metric_available"] is False


def test_thread_trend_propagates_query_errors(prom):
    prom.route("java_lang_Threading_ThreadCount", httpx.ConnectError("boom"))

    result = runtime_tools.get_thread_trend("si", "tsq")

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"


# --- process resources --------------------------------------------------------------------


def test_process_resources_cpu_against_limit_and_fds(prom):
    prom.route("java_lang_Memory_HeapMemoryUsage_committed", vector((POD_A, 1)))
    prom.route('resource="cpu"', vector((POD_A, 4)))
    prom.route("rate(process_cpu_seconds_total", vector((POD_A, 2.3364)), kind="instant")
    prom.route("rate(process_cpu_seconds_total", matrix((POD_A, [2.0, 4.0, 3.0])), kind="range")
    prom.route("process_open_fds", vector((POD_A, 1484)))
    prom.route("process_max_fds", vector((POD_A, 1048576)))
    prom.route("process_resident_memory_bytes", vector((POD_A, 15e9)))

    pod = runtime_tools.get_process_resources("si", "tsq")["data"]["pods"][POD_A]

    assert pod["cpu_cores_now"] == 2.336
    assert pod["cpu_cores_window_avg"] == 3.0
    assert pod["cpu_cores_window_max"] == 4.0
    assert pod["cpu_limit_cores"] == 4.0
    assert pod["cpu_percent_of_limit_window_max"] == 100.0
    assert pod["fd_open"] == 1484 and pod["fd_max"] == 1048576
    assert pod["fd_percent_used"] == 0.14
    assert pod["resident_memory_bytes"] == 15_000_000_000


def test_process_resources_without_cpu_limit(prom):
    prom.route("java_lang_Memory_HeapMemoryUsage_committed", vector((POD_A, 1)))
    prom.route("rate(process_cpu_seconds_total", vector((POD_A, 2.0)), kind="instant")
    prom.route("rate(process_cpu_seconds_total", matrix((POD_A, [2.0])), kind="range")

    data = runtime_tools.get_process_resources("si", "tsq")["data"]

    assert data["pods"][POD_A]["cpu_limit_cores"] is None
    assert data["pods"][POD_A]["cpu_percent_of_limit_window_max"] is None
    assert "null = no CPU limit" in data["caveat"]


# --- memory against the container limit --------------------------------------------------


def test_memory_vs_limit_headroom_breakdown_and_ranking(prom):
    gb = 1_000_000_000
    prom.route("java_lang_Memory_HeapMemoryUsage_committed", vector((POD_A, 11.7 * gb), (POD_B, 12 * gb)))
    prom.route("container_memory_working_set_bytes", vector((POD_A, 15.0 * gb), (POD_B, 19.6 * gb)))
    prom.route("container_memory_rss", vector((POD_A, 14.9 * gb), (POD_B, 19.5 * gb)))
    prom.route('resource="memory"', vector((POD_A, 21.5 * gb), (POD_B, 21.5 * gb)))
    prom.route("java_lang_Memory_HeapMemoryUsage_used", vector((POD_A, 8 * gb), (POD_B, 9 * gb)))
    prom.route("java_lang_Memory_NonHeapMemoryUsage_committed", vector((POD_A, 0.5 * gb), (POD_B, 0.5 * gb)))
    prom.route('pool="direct"', vector((POD_A, 1.3 * gb), (POD_B, 1.3 * gb)))

    data = runtime_tools.get_memory_vs_limit("si", "tsq")["data"]

    a = data["pods"][POD_A]
    assert a["working_set_percent_of_limit"] == 69.8
    assert a["headroom_percent"] == 30.2
    assert a["other_bytes"] == 1_500_000_000  # 15.0 - 11.7 - 0.5 - 1.3 GB
    assert data["least_headroom"][0] == {"pod": POD_B, "headroom_percent": 8.8}
    assert data["limit_available"] is True
    # container metrics are selected by the JVM series' own pod and container names
    container_query = next(q for q in prom.queries if "container_memory_working_set_bytes" in q)
    assert f'pod=~"{POD_A}|{POD_B}"' in container_query and 'container=~"tsq"' in container_query


def test_memory_vs_limit_ignores_unsafe_pod_names(prom):
    prom.route("java_lang_Memory_HeapMemoryUsage_committed", vector((POD_A, 1), ('evil".*', 1)))

    runtime_tools.get_memory_vs_limit("si", "tsq")

    container_query = next(q for q in prom.queries if "container_memory_working_set_bytes" in q)
    assert 'evil' not in container_query


def test_memory_vs_limit_without_jvm_series_is_business_gap(prom):
    result = runtime_tools.get_memory_vs_limit("si", "tsq")

    assert result["errorCategory"] == "business"
    assert any("JVM_LABEL_KEY" in alt for alt in result["alternatives"])


# --- class loading ------------------------------------------------------------------------


def test_class_loading_growth_per_hour(prom):
    # +100 classes per sample, one sample every 2 minutes = 3000 classes/hour, perfectly linear
    prom.route("java_lang_ClassLoading_LoadedClassCount", matrix((POD_A, [19000, 19100, 19200, 19300]), step_s=120))
    prom.route("TotalLoadedClassCount", vector((POD_A, 300)))
    prom.route("UnloadedClassCount", vector((POD_A, 2)))

    pod = runtime_tools.get_class_loading_trend("si", "tsq", lookback_minutes=180)["data"]["pods"][POD_A]

    assert pod["loaded_classes_now"] == 19300
    assert pod["growth_classes_per_hour"] == 3000.0
    assert pod["r_squared"] == 1.0
    assert pod["classes_loaded_in_window"] == 300 and pod["classes_unloaded_in_window"] == 2


# --- JVM identity -------------------------------------------------------------------------


def test_runtime_info_versions_uptime_and_restart_spotting(prom):
    prom.route("jvm_info", {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": _labels(POD_A, version="17.0.20.1+1", vendor="Eclipse OpenJ9", runtime="IBM Semeru"),
         "value": [1790000000, "1"]},
        {"metric": _labels(POD_B, version="17.0.19+7", vendor="Eclipse OpenJ9", runtime="IBM Semeru"),
         "value": [1790000000, "1"]},
    ]}})
    prom.route("java_lang_Runtime_Uptime", vector((POD_A, 12.38 * 3_600_000), (POD_B, 0.5 * 3_600_000)))
    prom.route("process_start_time_seconds", vector((POD_A, 1790000000), (POD_B, 1790042000)))

    data = runtime_tools.get_jvm_runtime_info("si", "tsq")["data"]

    assert data["pods"][POD_A]["uptime_hours"] == 12.38
    assert data["pods"][POD_A]["started_at"] == "2026-09-21T14:13:20Z"
    assert data["mixed_versions"] is True
    assert data["distinct_versions"] == ["17.0.19+7", "17.0.20.1+1"]
    assert data["newest_pod"] == POD_B and data["oldest_pod"] == POD_A


def test_new_tools_are_registered_on_the_mcp_server():
    import asyncio

    from jvm_troubleshooter.mcp import server

    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {
        "get_thread_trend", "get_process_resources", "get_memory_vs_limit",
        "get_class_loading_trend", "get_jvm_runtime_info",
    } <= names
