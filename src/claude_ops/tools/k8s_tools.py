from __future__ import annotations

import json
import subprocess
from typing import Any

from claude_ops.errors import ToolError, ok
from claude_ops.hooks import validate_kubectl_verb


def _run_kubectl(args: list[str], timeout_seconds: int = 20) -> dict[str, Any]:
    if not args:
        return ToolError("validation", False, "kubectl args cannot be empty").to_dict()

    verb = args[0]
    decision = validate_kubectl_verb(verb)
    if not decision.allowed:
        return ToolError(
            "permission",
            False,
            decision.reason,
            attempted={"kubectl_args": args},
            alternatives=["Use read-only get/describe/logs/top tools", "Request explicit human approval for remediation"],
        ).to_dict()

    try:
        completed = subprocess.run(
            ["kubectl", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return ToolError(
            "transient",
            True,
            "kubectl command timed out",
            attempted={"kubectl_args": args, "timeout_seconds": timeout_seconds},
            alternatives=["Retry with smaller scope", "Increase time window only if safe"],
        ).to_dict()

    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        lower = stderr.lower()

        if "forbidden" in lower or "unauthorized" in lower:
            return ToolError(
                "permission",
                False,
                stderr or "kubectl command failed: forbidden",
                attempted={"kubectl_args": args},
                partialResults=stdout or None,
                alternatives=[
                    "Verify this identity has the required read-only RBAC (get/list/watch — see k8s/readonly-rbac.yaml)",
                    "Ask a human to grant the missing read-only permission — do not attempt to work around this with elevated, destructive, or exec-based commands",
                ],
            ).to_dict()

        if verb == "top" and (
            "metrics not available" in lower
            or "metrics api not available" in lower
            or "metrics.k8s.io" in lower
            or "could not find the requested resource" in lower
        ):
            return ToolError(
                "business",
                False,
                stderr or "kubectl top failed: metrics-server unavailable",
                attempted={"kubectl_args": args},
                partialResults=stdout or None,
                alternatives=[
                    "metrics-server may not be installed/available in this cluster — this is a coverage gap, not zero CPU/memory usage",
                    "Use prom_get_pod_cpu_usage / prom_get_pod_memory_usage instead if Prometheus is configured for this cluster",
                ],
            ).to_dict()

        return ToolError(
            "unknown",
            False,
            stderr or "kubectl command failed",
            attempted={"kubectl_args": args},
            partialResults=stdout or None,
        ).to_dict()

    return ok(completed.stdout)


def list_pods(namespace: str, label_selector: str | None = None) -> dict[str, Any]:
    args = ["get", "pods", "-n", namespace, "-o", "json"]
    if label_selector:
        args.extend(["-l", label_selector])
    result = _run_kubectl(args)
    if result.get("isError"):
        return result
    try:
        data = json.loads(result["data"])
        pods = [
            {
                "name": item["metadata"]["name"],
                "namespace": item["metadata"]["namespace"],
                "phase": item.get("status", {}).get("phase"),
                "restartCount": sum(c.get("restartCount", 0) for c in item.get("status", {}).get("containerStatuses", [])),
                "containers": [c.get("name") for c in item.get("spec", {}).get("containers", [])],
            }
            for item in data.get("items", [])
        ]
        return ok(pods)
    except Exception as exc:
        return ToolError("validation", False, f"Failed to parse pod list: {exc}").to_dict()


def describe_pod(namespace: str, pod_name: str) -> dict[str, Any]:
    return _run_kubectl(["describe", "pod", pod_name, "-n", namespace])


def get_pod_json(namespace: str, pod_name: str) -> dict[str, Any]:
    result = _run_kubectl(["get", "pod", pod_name, "-n", namespace, "-o", "json"])
    if result.get("isError"):
        return result
    try:
        return ok(json.loads(result["data"]))
    except Exception as exc:
        return ToolError("validation", False, f"Failed to parse pod json: {exc}").to_dict()


def get_recent_namespace_events(namespace: str) -> dict[str, Any]:
    return _run_kubectl(["get", "events", "-n", namespace, "--sort-by=.lastTimestamp"])


def get_pod_logs(namespace: str, pod_name: str, container: str | None = None, since_minutes: int = 60, tail: int = 200) -> dict[str, Any]:
    args = ["logs", pod_name, "-n", namespace, f"--since={since_minutes}m", f"--tail={tail}"]
    if container:
        args.extend(["-c", container])
    return _run_kubectl(args, timeout_seconds=30)


def top_pods(namespace: str) -> dict[str, Any]:
    return _run_kubectl(["top", "pods", "-n", namespace], timeout_seconds=20)


def get_deployment_json(namespace: str, deployment: str) -> dict[str, Any]:
    result = _run_kubectl(["get", "deployment", deployment, "-n", namespace, "-o", "json"])
    if result.get("isError"):
        return result
    try:
        return ok(json.loads(result["data"]))
    except Exception as exc:
        return ToolError("validation", False, f"Failed to parse deployment json: {exc}").to_dict()
