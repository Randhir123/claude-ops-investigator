from __future__ import annotations

import httpx
import pytest

from jvm_troubleshooter.tools import window_tools
from tests.conftest import FakeResponse

NOW = 1_790_000_000  # 2026-09-21T14:13:20Z
T0 = NOW - 3600
POD_A = "tsq-6fb9-aaaaa"
POD_B = "tsq-6fb9-bbbbb"

# substring of each signal's PromQL -> which signal it is (order matters: most specific first)
NEEDLES = {
    "heap": "HeapMemoryUsage_used",
    "gc_overhead": "CollectionTime",
    "gc_per_minute": "CollectionCount",
    "started": "TotalStartedThreadCount",
    "threads": "Threading_ThreadCount",
    "cpu": "process_cpu_seconds_total",
}


def matrix(series: dict[str, list[float]], start: int = T0, step_s: int = 60) -> dict:
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"pod": pod, "instance": f"10.0.0.{i}:8080"},
         "values": [[start + j * step_s, str(v)] for j, v in enumerate(values)]}
        for i, (pod, values) in enumerate(series.items())
    ]}}


@pytest.fixture
def prom(monkeypatch, prom_env):
    """Per-window answers: answers[(window_start, signal)] or answers[signal] = matrix payload."""
    answers: dict = {}
    calls: list[dict] = []

    def fake_request(method, url, **kwargs):
        params = kwargs["params"]
        calls.append(params)
        signal = next(name for name, needle in NEEDLES.items() if needle in params["query"])
        payload = answers.get((int(params["start"]), signal), answers.get(signal))
        return FakeResponse(200, json_data=payload or matrix({}))

    monkeypatch.setattr(httpx, "request", fake_request)
    monkeypatch.setattr(window_tools.time, "time", lambda: NOW)
    answers["calls"] = calls
    return answers


# --- parsing and validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-09-21T14:13:20Z", NOW),
        ("2026-09-21T14:13:20", NOW),  # no offset = UTC
        ("2026-09-21T16:13:20+02:00", NOW),
        (NOW, NOW),
        (str(NOW), NOW),
        ("yesterday", None),
    ],
)
def test_parse_time(value, expected):
    assert window_tools._parse_time(value) == expected


@pytest.mark.parametrize(
    "start, end, message",
    [
        ("nope", NOW, "Could not parse"),
        (NOW - 60, NOW - 120, "must end after it starts"),
        (NOW - 8 * 86400, NOW, "longer than 7 days"),
        (NOW + 60, NOW + 120, "starts in the future"),
    ],
)
def test_incident_window_validation(prom, start, end, message):
    result = window_tools.get_incident_window("si", "tsq", start, end)

    assert result["errorCategory"] == "validation"
    assert message in result["message"]
    assert prom["calls"] == []  # nothing queried


def test_auto_step():
    assert window_tools._auto_step(3600, "auto") == "60s"
    assert window_tools._auto_step(7 * 86400, "auto") == "1560s"
    assert window_tools._auto_step(7 * 86400, "30s") == "30s"


# --- incident window -----------------------------------------------------------------


def test_incident_window_per_pod_and_pooled(prom):
    prom["cpu"] = matrix({POD_A: [1.0, 3.0, 2.0]})
    prom["gc_overhead"] = matrix({POD_A: [0.1, 0.1, 4.0]})
    # pod B only appears 2 minutes into the window: a restart or replacement
    prom["threads"] = {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"pod": POD_A}, "values": [[T0, "400"], [T0 + 60, "500"], [T0 + 120, "600"]]},
        {"metric": {"pod": POD_B}, "values": [[T0 + 120, "300"]]},
    ]}}

    data = window_tools.get_incident_window("si", "tsq", T0, NOW)["data"]

    assert data["window"] == {"start": "2026-09-21T13:13:20Z", "end": "2026-09-21T14:13:20Z", "step": "60s"}
    assert data["pods"][POD_A]["cpu_cores"] == {"avg": 2.0, "max": 3.0, "max_at": "2026-09-21T13:14:20Z"}
    assert data["pods"][POD_A]["gc_overhead_percent"]["max_at"] == "2026-09-21T13:15:20Z"
    assert data["service_wide"]["cpu_cores"] == {"samples": 3, "avg": 2.0, "p50": 2.0, "p95": 3.0, "p99": 3.0, "max": 3.0}
    assert data["service_wide"]["heap_used_percent"] is None  # no samples, not zero
    assert data["pods_seen"][POD_B] == {"first_sample": "2026-09-21T13:15:20Z", "last_sample": "2026-09-21T13:15:20Z"}
    assert len(prom["calls"]) == 6  # one range query per signal


# --- baseline comparison -------------------------------------------------------------


def test_baseline_comparison_change_percent(prom):
    base_start = NOW - 86400 - 3600
    prom[(base_start, "started")] = matrix({POD_A: [80, 90]}, start=base_start)
    prom[(NOW - 3600, "started")] = matrix({POD_A: [130, 140]}, start=NOW - 3600)
    prom[(base_start, "cpu")] = matrix({POD_A: [2.0, 2.0]}, start=base_start)
    prom[(NOW - 3600, "cpu")] = matrix({POD_A: [1.0, 1.0]}, start=NOW - 3600)

    data = window_tools.get_baseline_comparison("si", "tsq", base_start, NOW - 86400, current_minutes=60)["data"]

    started = data["signals"]["threads_started_per_second"]
    assert started["baseline"]["avg"] == 85.0 and started["current"]["avg"] == 135.0
    assert started["avg_change_percent"] == 58.8
    assert data["signals"]["cpu_cores"]["avg_change_percent"] == -50.0
    assert data["signals"]["heap_used_percent"]["avg_change_percent"] is None  # no data either side
    assert data["current_window"]["start"] == "2026-09-21T13:13:20Z"
    assert "same hour of day" in data["caveat"]


def test_baseline_must_end_before_current(prom):
    result = window_tools.get_baseline_comparison("si", "tsq", NOW - 7200, NOW - 600, current_minutes=60)

    assert result["errorCategory"] == "validation"
    assert "must end before the current window" in result["message"]


# --- CPU vs GC correlation ------------------------------------------------------------


def test_cpu_gc_correlation(prom):
    prom["cpu"] = matrix({POD_A: [1, 2, 3, 4, 5], POD_B: [1, 2, 3, 4, 5]})
    prom["gc_overhead"] = matrix({POD_A: [0.1, 0.2, 0.3, 0.4, 0.5], POD_B: [5, 4, 3, 2, 1]})

    pods = window_tools.get_cpu_gc_correlation("si", "tsq", lookback_minutes=60)["data"]["pods"]

    assert pods[POD_A] == {"points": 5, "pearson_r": 1.0, "cpu_cores_avg": 3.0, "gc_overhead_percent_avg": 0.3}
    assert pods[POD_B]["pearson_r"] == -1.0


def test_cpu_gc_correlation_aligns_timestamps_and_handles_flat_series(prom):
    prom["cpu"] = matrix({POD_A: [2, 2, 2, 2]})
    prom["gc_overhead"] = matrix({POD_A: [0.1, 0.2]}, start=T0 + 120)  # only the last two timestamps overlap

    pod = window_tools.get_cpu_gc_correlation("si", "tsq")["data"]["pods"][POD_A]

    assert pod["points"] == 2
    assert pod["pearson_r"] is None  # too few points, and CPU is flat


def test_window_tools_are_registered_on_the_mcp_server():
    import asyncio

    from jvm_troubleshooter.mcp import server

    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"get_incident_window", "get_baseline_comparison", "get_cpu_gc_correlation"} <= names
