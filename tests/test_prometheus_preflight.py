from __future__ import annotations

import subprocess
import time

import httpx

from claude_ops.tools import prometheus_preflight


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeProcess:
    def __init__(self, pid: int = 12345):
        self.pid = pid
        self.terminated = False

    def terminate(self):
        self.terminated = True


def test_prom_reachable_true_on_200(monkeypatch):
    def fake_get(url, timeout):
        assert url == "http://localhost:9090/api/v1/status/config"
        return FakeResponse(200)

    monkeypatch.setattr(httpx, "get", fake_get)

    assert prometheus_preflight.prom_reachable("http://localhost:9090") is True


def test_prom_reachable_false_on_non_200(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, timeout: FakeResponse(500))

    assert prometheus_preflight.prom_reachable("http://localhost:9090") is False


def test_prom_reachable_false_on_network_error(monkeypatch):
    def fake_get(url, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", fake_get)

    assert prometheus_preflight.prom_reachable("http://localhost:9090") is False


def test_ensure_prometheus_already_reachable_does_not_start_port_forward(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, timeout: FakeResponse(200))

    def unexpected_popen(*args, **kwargs):
        raise AssertionError("should not start kubectl port-forward when already reachable")

    monkeypatch.setattr(subprocess, "Popen", unexpected_popen)

    result = prometheus_preflight.ensure_prometheus({"prometheus_url": "http://localhost:9090"})

    assert result["isError"] is False
    assert result["data"]["reachable"] is True
    assert result["data"]["started_port_forward"] is False


def test_ensure_prometheus_unreachable_auto_disabled_returns_structured_error(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, timeout: FakeResponse(500))

    def unexpected_popen(*args, **kwargs):
        raise AssertionError("should not start kubectl port-forward when auto-port-forward is disabled")

    monkeypatch.setattr(subprocess, "Popen", unexpected_popen)

    result = prometheus_preflight.ensure_prometheus({
        "prometheus_url": "http://localhost:9090",
        "prometheus_auto_port_forward": "false",
    })

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True
    assert any("PROMETHEUS_AUTO_PORT_FORWARD" in alt for alt in result["alternatives"])


def test_ensure_prometheus_unreachable_auto_enabled_starts_port_forward_and_becomes_reachable(monkeypatch):
    call_count = {"n": 0}

    def fake_get(url, timeout):
        call_count["n"] += 1
        # First call (initial reachability check) fails; second call (after
        # the port-forward is started and the first poll happens) succeeds.
        return FakeResponse(200 if call_count["n"] >= 2 else 500)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    captured_popen = {}

    def fake_popen(args, **kwargs):
        captured_popen["args"] = args
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    registered = {}
    monkeypatch.setattr(prometheus_preflight.atexit, "register", lambda fn: registered.setdefault("fn", fn))

    result = prometheus_preflight.ensure_prometheus({
        "prometheus_url": "http://localhost:9090",
        "prometheus_auto_port_forward": "true",
        "prometheus_pf_service": "kube-prometheus-stack-prometheus",
        "prometheus_pf_namespace": "monitoring",
        "prometheus_pf_local_port": "9090",
    })

    assert captured_popen["args"] == [
        "kubectl", "port-forward", "svc/kube-prometheus-stack-prometheus", "9090:9090", "-n", "monitoring",
    ]
    assert "fn" in registered

    assert result["isError"] is False
    assert result["data"]["reachable"] is True
    assert result["data"]["started_port_forward"] is True
    assert result["data"]["port_forward_pid"] == 12345


def test_ensure_prometheus_port_forward_never_becomes_reachable(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, timeout: FakeResponse(500))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: FakeProcess())
    monkeypatch.setattr(prometheus_preflight.atexit, "register", lambda fn: None)

    result = prometheus_preflight.ensure_prometheus({
        "prometheus_url": "http://localhost:9090",
        "prometheus_auto_port_forward": "true",
    })

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"
    assert result["partialResults"]["port_forward_pid"] == 12345


def test_ensure_prometheus_kubectl_not_found_returns_validation_error(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, timeout: FakeResponse(500))

    def fake_popen(args, **kwargs):
        raise FileNotFoundError("kubectl not found")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = prometheus_preflight.ensure_prometheus({
        "prometheus_url": "http://localhost:9090",
        "prometheus_auto_port_forward": "true",
    })

    assert result["isError"] is True
    assert result["errorCategory"] == "validation"
    assert "kubectl" in result["message"]


def test_resolve_config_env_var_fallback(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom-from-env:9090")
    monkeypatch.setenv("PROMETHEUS_PF_SERVICE", "svc-from-env")
    monkeypatch.setenv("PROMETHEUS_PF_NAMESPACE", "ns-from-env")
    monkeypatch.setenv("PROMETHEUS_PF_LOCAL_PORT", "9999")
    monkeypatch.delenv("PROMETHEUS_PF_REMOTE_PORT", raising=False)
    monkeypatch.setenv("PROMETHEUS_AUTO_PORT_FORWARD", "true")

    cfg = prometheus_preflight._resolve_config(None)

    assert cfg["prometheus_url"] == "http://prom-from-env:9090"
    assert cfg["prometheus_pf_service"] == "svc-from-env"
    assert cfg["prometheus_pf_namespace"] == "ns-from-env"
    assert cfg["prometheus_pf_local_port"] == "9999"
    assert cfg["prometheus_pf_remote_port"] == "9999"
    assert cfg["prometheus_auto_port_forward"] is True


def test_resolve_config_defaults_when_nothing_set(monkeypatch):
    for var in [
        "PROMETHEUS_URL",
        "PROMETHEUS_PF_SERVICE",
        "PROMETHEUS_PF_NAMESPACE",
        "PROMETHEUS_PF_LOCAL_PORT",
        "PROMETHEUS_PF_REMOTE_PORT",
        "PROMETHEUS_AUTO_PORT_FORWARD",
    ]:
        monkeypatch.delenv(var, raising=False)

    cfg = prometheus_preflight._resolve_config(None)

    assert cfg["prometheus_url"] == "http://localhost:9090"
    assert cfg["prometheus_pf_service"] == "kube-prometheus-stack-prometheus"
    assert cfg["prometheus_pf_namespace"] == "monitoring"
    assert cfg["prometheus_pf_local_port"] == "9090"
    assert cfg["prometheus_pf_remote_port"] == "9090"
    assert cfg["prometheus_auto_port_forward"] is False
