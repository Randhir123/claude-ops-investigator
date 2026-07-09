from __future__ import annotations

import subprocess

from claude_ops.tools import k8s_tools


class FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_describe_pod_forbidden_maps_to_permission_with_rbac_guidance(monkeypatch):
    def fake_run(args, **kwargs):
        return FakeCompleted(
            1,
            stderr='Error from server (Forbidden): pods "event-data-abc" is forbidden: '
            'User "system:serviceaccount:si:claude-ops" cannot get resource "pods" in API group ""',
        )

    monkeypatch.setattr(k8s_tools.subprocess, "run", fake_run)

    result = k8s_tools.describe_pod(namespace="si", pod_name="event-data-abc")

    assert result["isError"] is True
    assert result["errorCategory"] == "permission"
    assert result["isRetryable"] is False
    assert any("RBAC" in alt or "rbac" in alt for alt in result["alternatives"])
    joined = " ".join(result["alternatives"]).lower()
    # guidance may name destructive verbs only to say not to use them
    assert "do not attempt" in joined
    assert "run kubectl delete" not in joined and "run kubectl patch" not in joined


def test_top_pods_metrics_server_unavailable_maps_to_business_gap(monkeypatch):
    def fake_run(args, **kwargs):
        return FakeCompleted(
            1,
            stderr="error: Metrics API not available",
        )

    monkeypatch.setattr(k8s_tools.subprocess, "run", fake_run)

    result = k8s_tools.top_pods(namespace="si")

    assert result["isError"] is True
    assert result["errorCategory"] == "business"
    assert result["isRetryable"] is False
    joined = " ".join(result["alternatives"]).lower()
    assert "gap" in joined
    assert "zero" in joined
    assert "prom_get_pod_cpu_usage" in joined or "prom_get_pod_memory_usage" in joined


def test_top_pods_metrics_server_not_found_variant_maps_to_business_gap(monkeypatch):
    def fake_run(args, **kwargs):
        return FakeCompleted(
            1,
            stderr='Error from server (NotFound): the server could not find the requested resource '
            "(get pods.metrics.k8s.io)",
        )

    monkeypatch.setattr(k8s_tools.subprocess, "run", fake_run)

    result = k8s_tools.top_pods(namespace="si")

    assert result["isError"] is True
    assert result["errorCategory"] == "business"


def test_generic_kubectl_failure_still_maps_to_unknown(monkeypatch):
    def fake_run(args, **kwargs):
        return FakeCompleted(1, stderr="Error: some unrelated kubectl failure")

    monkeypatch.setattr(k8s_tools.subprocess, "run", fake_run)

    result = k8s_tools.describe_pod(namespace="si", pod_name="event-data-abc")

    assert result["isError"] is True
    assert result["errorCategory"] == "unknown"


def test_kubectl_timeout_is_transient_and_retryable(monkeypatch):
    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="kubectl", timeout=kwargs.get("timeout", 20))

    monkeypatch.setattr(k8s_tools.subprocess, "run", fake_run)

    result = k8s_tools.describe_pod(namespace="si", pod_name="event-data-abc")

    assert result["isError"] is True
    assert result["errorCategory"] == "transient"
    assert result["isRetryable"] is True


def test_destructive_verb_is_blocked_before_any_subprocess_call(monkeypatch):
    def unexpected_run(*args, **kwargs):
        raise AssertionError("should never invoke subprocess for a blocked verb")

    monkeypatch.setattr(k8s_tools.subprocess, "run", unexpected_run)

    result = k8s_tools._run_kubectl(["delete", "pod", "event-data-abc", "-n", "si"])

    assert result["isError"] is True
    assert result["errorCategory"] == "permission"
    assert result["isRetryable"] is False


def test_successful_kubectl_call_is_unaffected(monkeypatch):
    def fake_run(args, **kwargs):
        return FakeCompleted(0, stdout="pod/event-data-abc\n")

    monkeypatch.setattr(k8s_tools.subprocess, "run", fake_run)

    result = k8s_tools.describe_pod(namespace="si", pod_name="event-data-abc")

    assert result["isError"] is False
    assert result["data"] == "pod/event-data-abc\n"
