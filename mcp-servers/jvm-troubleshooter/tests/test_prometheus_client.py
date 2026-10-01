from __future__ import annotations

import httpx

from jvm_troubleshooter.tools.prometheus_client import (
    clamp_lookback_minutes,
    escape_label_value,
    instant_query,
    label_key,
    namespace_service_selector,
    prometheus_base_url,
    range_query,
)
from tests.conftest import FakeResponse


def test_prometheus_base_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local:9090/")
    assert prometheus_base_url() == "http://prometheus.local:9090"


def test_prometheus_base_url_none_when_unset(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    assert prometheus_base_url() is None


def test_label_key_defaults_to_job(monkeypatch):
    monkeypatch.delenv("JVM_LABEL_KEY", raising=False)
    assert label_key() == "job"


def test_label_key_reads_env(monkeypatch):
    monkeypatch.setenv("JVM_LABEL_KEY", "service")
    assert label_key() == "service"


def test_escape_label_value_escapes_quotes_and_backslashes():
    assert escape_label_value('si"') == 'si\\"'
    assert escape_label_value("a\\b") == "a\\\\b"


def test_namespace_service_selector_builds_expected_fragment(monkeypatch):
    monkeypatch.setenv("JVM_LABEL_KEY", "job")
    selector = namespace_service_selector("si-dev-001a", "event-data")
    assert selector == 'namespace="si-dev-001a", job=~"(event-data)"'


def test_instant_query_missing_config_returns_validation_error(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("should not make a network call without PROMETHEUS_URL")

    monkeypatch.setattr(httpx, "request", unexpected_request)

    result = instant_query("up")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert "PROMETHEUS_URL" in result["message"]


def test_instant_query_rejects_empty_promql(prom_env):
    result = instant_query("   ")
    assert result["isError"] is True
    assert result["errorCategory"] == "validation"


def test_instant_query_rejects_oversized_promql(prom_env):
    result = instant_query("up{" + "x" * 2000 + "}")
    assert result["isError"] is True
    assert "length" in result["message"]


def test_instant_query_rejects_range_over_seven_days(prom_env):
    result = instant_query("increase(foo[8d])")
    assert result["isError"] is True
    assert "7-day" in result["message"] or "exceeds" in result["message"]


def test_instant_query_allows_range_within_seven_days(prom_env, capture_queries):
    result = instant_query("increase(foo[6d])")
    assert result["isError"] is False


def test_instant_query_sends_get_with_query_param(prom_env, capture_queries):
    instant_query("up")
    assert capture_queries[0]["method"] == "GET"
    assert capture_queries[0]["url"] == "http://prometheus.local:9090/api/v1/query"
    assert capture_queries[0]["params"] == {"query": "up"}


def test_range_query_sends_start_end_step(prom_env, capture_queries):
    range_query("up", start="100", end="200", step="30s")
    params = capture_queries[0]["params"]
    assert params == {"query": "up", "start": "100", "end": "200", "step": "30s"}
    assert capture_queries[0]["url"] == "http://prometheus.local:9090/api/v1/query_range"


def test_clamp_lookback_minutes_clamps_to_bounds():
    assert clamp_lookback_minutes(0) == 1
    assert clamp_lookback_minutes(-5) == 1
    assert clamp_lookback_minutes(60) == 60
    assert clamp_lookback_minutes(10_000) == 1440
    assert clamp_lookback_minutes(10_000, max_minutes=100) == 100
