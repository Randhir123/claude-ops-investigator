from __future__ import annotations

import httpx
import pytest

from jvm_troubleshooter.tools.prometheus_client import instant_query, prometheus_base_url, range_query
from tests.conftest import FakeResponse, vector_result

GRAFANA = "https://grafana.example.com"
UID = "prom-uid_1"
PROXY_BASE = f"{GRAFANA}/api/datasources/proxy/uid/{UID}"
TOKEN = "glsa_super_secret_token"
COOKIE = "s3ss10n-cookie-value"


@pytest.fixture
def grafana_env(monkeypatch):
    monkeypatch.setenv("GRAFANA_URL", GRAFANA)
    monkeypatch.setenv("GRAFANA_DATASOURCE_UID", UID)
    monkeypatch.setenv("GRAFANA_API_TOKEN", TOKEN)
    monkeypatch.setenv("PROMETHEUS_URL", "http://localhost:9090")


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"url": url, "params": kwargs.get("params"), "headers": kwargs.get("headers")})
        return FakeResponse(200, json_data=vector_result())

    monkeypatch.setattr(httpx, "request", fake_request)
    return calls


def test_grafana_url_takes_precedence_over_prometheus_url(grafana_env):
    assert prometheus_base_url() == PROXY_BASE


def test_instant_query_through_grafana_sends_bearer_token(grafana_env, captured):
    result = instant_query("up")

    assert result["isError"] is False
    assert captured == [
        {"url": f"{PROXY_BASE}/api/v1/query", "params": {"query": "up"}, "headers": {"Authorization": f"Bearer {TOKEN}"}}
    ]


def test_range_query_through_grafana_with_session_cookie(grafana_env, captured, monkeypatch):
    monkeypatch.delenv("GRAFANA_API_TOKEN")
    monkeypatch.setenv("GRAFANA_SESSION_COOKIE", COOKIE)

    range_query("up", start="1", end="2")

    assert captured[0]["url"] == f"{PROXY_BASE}/api/v1/query_range"
    assert captured[0]["headers"] == {"Cookie": f"grafana_session={COOKIE}"}


def test_direct_mode_sends_no_auth_headers(prom_env, captured):
    instant_query("up")
    assert captured[0]["url"] == "http://prometheus.local:9090/api/v1/query"
    assert captured[0]["headers"] is None


@pytest.mark.parametrize(
    "var, value, expected",
    [
        ("GRAFANA_DATASOURCE_UID", "", "GRAFANA_DATASOURCE_UID"),
        ("GRAFANA_API_TOKEN", "", "GRAFANA_API_TOKEN or GRAFANA_SESSION_COOKIE"),
        ("GRAFANA_URL", "http://grafana.example.com", "https"),
    ],
)
def test_invalid_grafana_config_is_validation_error_without_network(grafana_env, monkeypatch, var, value, expected):
    monkeypatch.setenv(var, value)

    def unexpected_request(*args, **kwargs):
        raise AssertionError("should not make a network call with invalid Grafana config")

    monkeypatch.setattr(httpx, "request", unexpected_request)

    result = instant_query("up")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert expected in result["message"]


def test_grafana_auth_failure_redacts_secret(grafana_env, monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda method, url, **kwargs: FakeResponse(403, text=f"bad token {TOKEN}"))

    result = instant_query("up")

    assert result["errorCategory"] == "permission"
    assert TOKEN not in str(result)
    assert "***REDACTED***" in result["partialResults"]


def test_grafana_network_error_redacts_secret(grafana_env, monkeypatch):
    def fake_request(method, url, **kwargs):
        raise httpx.ConnectError(f"boom {TOKEN}")

    monkeypatch.setattr(httpx, "request", fake_request)

    result = instant_query("up")

    assert result["errorCategory"] == "transient"
    assert TOKEN not in str(result)
