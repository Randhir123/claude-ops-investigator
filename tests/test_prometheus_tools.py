from __future__ import annotations

import httpx
import pytest

from claude_ops.tools import prometheus_tools
from claude_ops.evidence.raw_store import store_raw_evidence as real_store_raw_evidence


class FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


def _redirect_store_to_tmp(monkeypatch, tmp_path):
    def fake_store_raw_evidence(*, content_type, raw, summary, metadata):
        return real_store_raw_evidence(
            content_type=content_type,
            raw=raw,
            summary=summary,
            metadata=metadata,
            artifact_dir=tmp_path,
        )

    monkeypatch.setattr(prometheus_tools, "store_raw_evidence", fake_store_raw_evidence)


def test_prom_query_instant_missing_config_returns_structured_error(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("should not make a network call without PROMETHEUS_URL")

    monkeypatch.setattr(httpx, "request", unexpected_request)

    result = prometheus_tools.prom_query_instant("up")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert result["isRetryable"] is False
    assert "PROMETHEUS_URL" in result["message"]
    assert result["alternatives"]


def test_prom_query_instant_success_is_summarized_and_stored(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")
    _redirect_store_to_tmp(monkeypatch, tmp_path)

    fake_json = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"__name__": "up", "pod": "event-data-abc"}, "value": [1700000000, "1"]},
            ],
        },
    }

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return FakeResponse(200, json_data=fake_json)

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools.prom_query_instant("up")

    assert captured["method"] == "GET"
    assert captured["url"] == "http://prometheus.local:9090/api/v1/query"
    assert captured["params"] == {"query": "up"}

    assert result["isError"] is False
    assert result["data"]["evidence_ref"].startswith("ev_")
    assert result["data"]["content_type"] == "prometheus.query_result"
    assert "1 series" in result["data"]["summary"]
    assert "pod=event-data-abc" in result["data"]["summary"]


def test_prom_get_pod_restart_counts_builds_bounded_promql_and_escapes_labels(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")
    _redirect_store_to_tmp(monkeypatch, tmp_path)

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["params"] = kwargs.get("params")
        return FakeResponse(200, json_data={"status": "success", "data": {"result": []}})

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools.prom_get_pod_restart_counts('si"', "event-data")

    promql = captured["params"]["query"]
    assert 'namespace="si\\""' in promql
    assert 'pod=~"event-data.*"' in promql
    assert result["isError"] is False
    assert result["data"]["metadata"]["metric"] == "pod_restart_counts"


def test_prom_get_pod_restart_increase_builds_bounded_increase_query(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")
    _redirect_store_to_tmp(monkeypatch, tmp_path)

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["params"] = kwargs.get("params")
        return FakeResponse(200, json_data={"status": "success", "data": {"result": []}})

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools.prom_get_pod_restart_increase("si", "event-data", since_minutes=60)

    promql = captured["params"]["query"]
    assert "increase(kube_pod_container_status_restarts_total" in promql
    assert 'namespace="si"' in promql
    assert 'pod=~"event-data.*"' in promql
    assert "[60m]" in promql
    assert result["isError"] is False
    assert result["data"]["metadata"]["metric"] == "pod_restart_increase"
    assert result["data"]["metadata"]["since_minutes"] == 60


def test_prom_get_pod_restart_increase_clamps_since_minutes(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")
    _redirect_store_to_tmp(monkeypatch, tmp_path)

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["params"] = kwargs.get("params")
        return FakeResponse(200, json_data={"status": "success", "data": {"result": []}})

    monkeypatch.setattr(httpx, "request", fake_request)

    prometheus_tools.prom_get_pod_restart_increase("si", "event-data", since_minutes=999999)

    assert f"[{prometheus_tools._MAX_SINCE_MINUTES}m]" in captured["params"]["query"]


def test_prom_get_pod_restart_increase_missing_config_returns_structured_error(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("should not make a network call without PROMETHEUS_URL")

    monkeypatch.setattr(httpx, "request", unexpected_request)

    result = prometheus_tools.prom_get_pod_restart_increase("si", "event-data")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert result["isRetryable"] is False
    assert "PROMETHEUS_URL" in result["message"]


def test_prom_get_http_error_rate_clamps_since_minutes(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")
    _redirect_store_to_tmp(monkeypatch, tmp_path)

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["params"] = kwargs.get("params")
        return FakeResponse(200, json_data={"status": "success", "data": {"result": []}})

    monkeypatch.setattr(httpx, "request", fake_request)

    prometheus_tools.prom_get_http_error_rate("si", "event-data", since_minutes=999999)

    assert f"[{prometheus_tools._MAX_SINCE_MINUTES}m]" in captured["params"]["query"]


def test_prom_query_instant_rejects_oversized_query(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")

    def unexpected_request(*args, **kwargs):
        raise AssertionError("should not make a network call for an invalid query")

    monkeypatch.setattr(httpx, "request", unexpected_request)

    huge_query = "up" + "a" * prometheus_tools._MAX_PROMQL_LENGTH
    result = prometheus_tools.prom_query_instant(huge_query)

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"


def test_prom_query_instant_rejects_huge_range_duration(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")

    def unexpected_request(*args, **kwargs):
        raise AssertionError("should not make a network call for an invalid query")

    monkeypatch.setattr(httpx, "request", unexpected_request)

    result = prometheus_tools.prom_query_instant("rate(http_requests_total[999d])")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert "range duration" in result["message"]


def test_prom_query_instant_maps_server_error_to_transient(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")

    def fake_request(method, url, **kwargs):
        return FakeResponse(503, text="service unavailable")

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools.prom_query_instant("up")

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True


def test_prom_query_instant_missing_config_does_not_treat_as_zero(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("should not make a network call without PROMETHEUS_URL")

    monkeypatch.setattr(httpx, "request", unexpected_request)

    result = prometheus_tools.prom_query_instant("up")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert result["isRetryable"] is False
    assert any("PROMETHEUS_URL" in alt for alt in result["alternatives"])
    assert any("gap" in alt.lower() for alt in result["alternatives"])


def test_prom_query_instant_connection_error_includes_preflight_hint(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")

    def fake_request(method, url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools.prom_query_instant("up")

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True
    assert any("prom_ensure_connection" in alt for alt in result["alternatives"])


def test_prom_query_instant_timeout_includes_preflight_hint(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")

    def fake_request(method, url, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools.prom_query_instant("up")

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True
    assert any("prom_ensure_connection" in alt for alt in result["alternatives"])


def test_prom_query_instant_401_maps_to_permission_not_retryable(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")

    def fake_request(method, url, **kwargs):
        return FakeResponse(401, text="unauthorized")

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools.prom_query_instant("up")

    assert result["isError"] is True
    assert result["errorCategory"] == "permission"
    assert result["isRetryable"] is False
    assert any("authenticat" in alt.lower() or "auth" in alt.lower() for alt in result["alternatives"])
    # never suggests bypassing/destructive workarounds
    joined = " ".join(result["alternatives"]).lower()
    assert "delete" not in joined
    assert "bypass" not in joined or "do not" in joined


def test_prom_query_instant_403_maps_to_permission_not_retryable(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")

    def fake_request(method, url, **kwargs):
        return FakeResponse(403, text="forbidden")

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools.prom_query_instant("up")

    assert result["isError"] is True
    assert result["errorCategory"] == "permission"
    assert result["isRetryable"] is False


def test_prom_query_instant_400_includes_promql_guidance(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")

    def fake_request(method, url, **kwargs):
        return FakeResponse(400, text='{"status":"error","errorType":"bad_data","error":"parse error"}')

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools.prom_query_instant("up(")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert result["isRetryable"] is False
    assert any("promql" in alt.lower() for alt in result["alternatives"])


def test_range_query_hits_query_range_endpoint(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090")

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return FakeResponse(200, json_data={"status": "success", "data": {"result": []}})

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools._range_query("up", start="0", end="60", step="15s")

    assert captured["url"] == "http://prometheus.local:9090/api/v1/query_range"
    assert captured["params"] == {"query": "up", "start": "0", "end": "60", "step": "15s"}
    assert result["isError"] is False
