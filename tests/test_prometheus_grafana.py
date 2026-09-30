from __future__ import annotations

import subprocess

import httpx
import pytest

from claude_ops.evidence.raw_store import store_raw_evidence as real_store_raw_evidence
from claude_ops.tools import prometheus_preflight, prometheus_tools
from claude_ops.tools.prometheus_endpoint import PrometheusEndpoint, resolve_prometheus_endpoint

GRAFANA = "https://grafana.example.com"
UID = "prom-uid_1"
PROXY_BASE = f"{GRAFANA}/api/datasources/proxy/uid/{UID}"
TOKEN = "glsa_super_secret_token"
COOKIE = "s3ss10n-cookie-value"


class FakeResponse:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


@pytest.fixture
def grafana_env(monkeypatch):
    monkeypatch.setenv("GRAFANA_URL", GRAFANA + "/")
    monkeypatch.setenv("GRAFANA_DATASOURCE_UID", UID)
    monkeypatch.setenv("GRAFANA_API_TOKEN", TOKEN)
    monkeypatch.setenv("PROMETHEUS_URL", "http://localhost:9090")


def _no_network(*args, **kwargs):
    raise AssertionError("should not make a network call with invalid Grafana config")


def test_direct_mode_when_grafana_url_unset(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://localhost:9090/")
    assert resolve_prometheus_endpoint() == PrometheusEndpoint(mode="direct", base_url="http://localhost:9090")


def test_grafana_mode_takes_precedence_and_uses_bearer_token(grafana_env):
    endpoint = resolve_prometheus_endpoint()
    assert endpoint.mode == "grafana"
    assert endpoint.base_url == PROXY_BASE
    assert endpoint.headers == {"Authorization": f"Bearer {TOKEN}"}
    assert endpoint.secrets == [TOKEN]


def test_grafana_session_cookie_used_when_no_token(grafana_env, monkeypatch):
    monkeypatch.delenv("GRAFANA_API_TOKEN")
    monkeypatch.setenv("GRAFANA_SESSION_COOKIE", COOKIE)
    endpoint = resolve_prometheus_endpoint()
    assert endpoint.headers == {"Cookie": f"grafana_session={COOKIE}"}
    assert endpoint.secrets == [COOKIE]


def test_grafana_token_preferred_over_cookie(grafana_env, monkeypatch):
    monkeypatch.setenv("GRAFANA_SESSION_COOKIE", COOKIE)
    assert resolve_prometheus_endpoint().headers == {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"GRAFANA_DATASOURCE_UID": ""}, "GRAFANA_DATASOURCE_UID"),
        ({"GRAFANA_DATASOURCE_UID": "../../api/admin"}, "GRAFANA_DATASOURCE_UID"),
        ({"GRAFANA_API_TOKEN": ""}, "GRAFANA_API_TOKEN or GRAFANA_SESSION_COOKIE"),
        ({"GRAFANA_URL": "http://grafana.example.com"}, "https"),
        ({"GRAFANA_URL": "grafana.example.com"}, "not a valid http(s) URL"),
    ],
)
def test_invalid_grafana_config_is_validation_error_without_network(grafana_env, monkeypatch, overrides, expected):
    for var, value in overrides.items():
        monkeypatch.setenv(var, value)
    monkeypatch.setattr(httpx, "request", _no_network)

    result = prometheus_tools.prom_query_instant("up")

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert result["isRetryable"] is False
    assert expected in result["message"]
    assert result["attempted"]["promql"] == "up"


def test_http_allowed_for_local_grafana(grafana_env, monkeypatch):
    monkeypatch.setenv("GRAFANA_URL", "http://localhost:3000")
    assert resolve_prometheus_endpoint().base_url == f"http://localhost:3000/api/datasources/proxy/uid/{UID}"


def test_query_goes_through_grafana_proxy_with_auth_header(grafana_env, monkeypatch, tmp_path):
    monkeypatch.setattr(
        prometheus_tools, "store_raw_evidence", lambda **kwargs: real_store_raw_evidence(**kwargs, artifact_dir=tmp_path)
    )
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, params=kwargs.get("params"), headers=kwargs.get("headers"))
        return FakeResponse(200, json_data={"status": "success", "data": {"resultType": "vector", "result": []}})

    monkeypatch.setattr(httpx, "request", fake_request)

    result = prometheus_tools.prom_query_instant("up")

    assert result["isError"] is False
    assert captured["method"] == "GET"
    assert captured["url"] == f"{PROXY_BASE}/api/v1/query"
    assert captured["params"] == {"query": "up"}
    assert captured["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    # the stored evidence must not carry the credential
    stored = next(tmp_path.glob("ev_*.json")).read_text()
    assert TOKEN not in stored


def test_grafana_auth_failure_is_permission_error_with_secret_redacted(grafana_env, monkeypatch):
    monkeypatch.setattr(
        httpx, "request", lambda method, url, **kwargs: FakeResponse(401, text=f'{{"message":"invalid token {TOKEN}"}}')
    )

    result = prometheus_tools.prom_query_instant("up")

    assert result["errorCategory"] == "permission"
    assert TOKEN not in str(result)
    assert "***REDACTED***" in result["partialResults"]
    assert any("GRAFANA_SESSION_COOKIE" in alt for alt in result["alternatives"])


def test_grafana_login_page_html_gets_cookie_hint(grafana_env, monkeypatch):
    monkeypatch.setattr(httpx, "request", lambda method, url, **kwargs: FakeResponse(200, text="<html>login</html>"))

    result = prometheus_tools.prom_query_instant("up")

    assert result["errorCategory"] == "unknown"
    assert any("login page" in alt for alt in result["alternatives"])


def test_preflight_grafana_mode_checks_proxy_and_never_port_forwards(grafana_env, monkeypatch):
    monkeypatch.setenv("PROMETHEUS_AUTO_PORT_FORWARD", "true")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no port-forward")))
    captured = {}

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return FakeResponse(200)

    monkeypatch.setattr(httpx, "get", fake_get)

    result = prometheus_preflight.ensure_prometheus()

    assert result["isError"] is False
    assert result["data"] == {
        "reachable": True,
        "started_port_forward": False,
        "mode": "grafana",
        "prometheus_url": PROXY_BASE,
    }
    assert captured["url"] == f"{PROXY_BASE}/api/v1/query"
    assert captured["params"] == {"query": "1"}
    assert captured["headers"] == {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize("status, category", [(401, "permission"), (404, "validation"), (502, "transient")])
def test_preflight_grafana_mode_http_failures(grafana_env, monkeypatch, status, category):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no port-forward")))
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: FakeResponse(status))

    result = prometheus_preflight.ensure_prometheus()

    assert result["isError"] is True
    assert result["errorCategory"] == category
    assert TOKEN not in str(result)


def test_preflight_grafana_network_error_redacts_secret(grafana_env, monkeypatch):
    def fake_get(url, **kwargs):
        raise httpx.ConnectError(f"boom {TOKEN}")

    monkeypatch.setattr(httpx, "get", fake_get)

    result = prometheus_preflight.ensure_prometheus()

    assert result["errorCategory"] == "transient"
    assert TOKEN not in str(result)


def test_preflight_explicit_prometheus_url_config_stays_direct(grafana_env, monkeypatch):
    monkeypatch.setattr(prometheus_preflight, "prom_reachable", lambda url: url == "http://localhost:9090")

    result = prometheus_preflight.ensure_prometheus({"prometheus_url": "http://localhost:9090"})

    assert result["data"]["prometheus_url"] == "http://localhost:9090"
    assert "mode" not in result["data"]
