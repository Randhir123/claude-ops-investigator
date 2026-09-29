from __future__ import annotations

import httpx

from jvm_troubleshooter.tools.http_client import request_json
from tests.conftest import FakeResponse


def test_timeout_maps_to_transient_retryable(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "request", raise_timeout)

    result = request_json("GET", "http://prometheus.local:9090/api/v1/query")

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True
    assert "timed out" in result["message"]


def test_network_error_maps_to_transient_retryable(monkeypatch):
    def raise_network_error(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "request", raise_network_error)

    result = request_json("GET", "http://prometheus.local:9090/api/v1/query")

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True


def test_401_maps_to_permission_not_retryable(monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda *a, **k: FakeResponse(401, text="unauthorized"))

    result = request_json("GET", "http://prometheus.local:9090/api/v1/query")

    assert result["isError"] is True
    assert result["errorCategory"] == "permission"
    assert result["isRetryable"] is False
    assert result["partialResults"] == "unauthorized"


def test_403_maps_to_permission(monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda *a, **k: FakeResponse(403, text="forbidden"))

    result = request_json("GET", "http://prometheus.local:9090/api/v1/query")

    assert result["errorCategory"] == "permission"


def test_429_maps_to_transient_retryable_with_backoff_hint(monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda *a, **k: FakeResponse(429, text="slow down"))

    result = request_json("GET", "http://prometheus.local:9090/api/v1/query")

    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True
    assert any("backoff" in a.lower() for a in result["alternatives"])


def test_400_maps_to_validation_not_retryable(monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda *a, **k: FakeResponse(400, text="bad promql"))

    result = request_json("GET", "http://prometheus.local:9090/api/v1/query")

    assert result["errorCategory"] == "validation"
    assert result["isRetryable"] is False
    assert result["partialResults"] == "bad promql"


def test_500_maps_to_transient_retryable(monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda *a, **k: FakeResponse(500, text="oops"))

    result = request_json("GET", "http://prometheus.local:9090/api/v1/query")

    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True


def test_success_returns_ok_with_parsed_json(monkeypatch):
    payload = {"status": "success", "data": {"result": []}}
    monkeypatch.setattr(httpx, "request", lambda *a, **k: FakeResponse(200, json_data=payload))

    result = request_json("GET", "http://prometheus.local:9090/api/v1/query")

    assert result == {"isError": False, "data": payload}


def test_unparsable_json_maps_to_unknown_error(monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda *a, **k: FakeResponse(200, json_data=None, text="<html>"))

    result = request_json("GET", "http://prometheus.local:9090/api/v1/query")

    assert result["isError"] is True
    assert result["errorCategory"] == "unknown"
    assert result["partialResults"] == "<html>"
