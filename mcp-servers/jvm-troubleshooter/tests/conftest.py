from __future__ import annotations

import itertools

import httpx
import pytest


class CallLog(list):
    """A list subclass so `set_responses`/`calls` can be attached to it (plain lists can't
    carry attributes)."""


class FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


def vector_result(value: str = "1", metric: dict | None = None) -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": metric or {"instance": "pod-1"}, "value": [1700000000, value]}],
        },
    }


def matrix_result(series: list[dict] | None = None) -> dict:
    """A Prometheus /api/v1/query_range-shaped result. `series` is a list of
    {"metric": {...}, "values": [(ts, value), ...]} dicts; defaults to one series with
    three increasing points."""
    if series is None:
        series = [
            {
                "metric": {"instance": "pod-1"},
                "values": [(1700000000, "1"), (1700000060, "2"), (1700000120, "3")],
            }
        ]
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [{"metric": s["metric"], "values": [list(p) for p in s["values"]]} for s in series],
        },
    }


def empty_matrix_result() -> dict:
    return {"status": "success", "data": {"resultType": "matrix", "result": []}}


@pytest.fixture(autouse=True)
def _no_grafana_env(monkeypatch):
    # server.py's load_dotenv() may pull GRAFANA_* from a local .env; GRAFANA_URL switches
    # every query to Grafana mode, so clear them and let Grafana tests opt in explicitly.
    for var in ("GRAFANA_URL", "GRAFANA_DATASOURCE_UID", "GRAFANA_API_TOKEN", "GRAFANA_SESSION_COOKIE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def prom_env(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")
    monkeypatch.setenv("JVM_LABEL_KEY", "job")


@pytest.fixture
def capture_queries(monkeypatch):
    """Patches httpx.request to return one canned vector result per call (cycling if there
    are more calls than responses) and records every call's params for inspection."""

    calls = CallLog()
    state = {"responses": [vector_result()]}

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, "params": kwargs.get("params")})
        responses = state["responses"]
        idx = min(len(calls) - 1, len(responses) - 1)
        payload = responses[idx]
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(200, json_data=payload)

    monkeypatch.setattr(httpx, "request", fake_request)

    def set_responses(responses):
        state["responses"] = responses

    calls.set_responses = set_responses
    return calls


@pytest.fixture
def queue_responses(monkeypatch):
    """Patches httpx.request to return exactly one queued FakeResponse per call, in order --
    for tests where different calls in a sequence need different (e.g. success-then-error)
    outcomes. Also records each call's params."""

    calls: list[dict] = []
    queue: list = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, "params": kwargs.get("params")})
        return queue.pop(0)

    monkeypatch.setattr(httpx, "request", fake_request)

    def push(*responses):
        queue.extend(responses)

    push.calls = calls  # type: ignore[attr-defined]
    return push
