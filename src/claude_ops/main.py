from __future__ import annotations

import argparse
import json

from claude_ops.tools.k8s_tools import (
    list_pods,
    get_pod_logs,
    describe_pod,
    get_recent_namespace_events,
    top_pods,
)
from claude_ops.tools.runbook_tools import get_runbook_catalog, search_runbooks


def investigate(namespace: str, service: str, since_minutes: int) -> dict:
    label_selector = f"app={service}"

    pods = list_pods(namespace, label_selector)
    events = get_recent_namespace_events(namespace)
    usage = top_pods(namespace)
    runbook_catalog = get_runbook_catalog()
    runbook_matches = search_runbooks(service)

    pod_details = []
    if not pods.get("isError"):
        for pod in pods["data"][:3]:
            pod_name = pod["name"]
            pod_details.append({
                "pod": pod_name,
                "describe": describe_pod(namespace, pod_name),
                "logs": get_pod_logs(namespace, pod_name, since_minutes=since_minutes, tail=200),
            })

    return {
        "request": {
            "namespace": namespace,
            "service": service,
            "since_minutes": since_minutes,
        },
        "resources": {
            "runbook_catalog": runbook_catalog,
        },
        "tool_results": {
            "pods": pods,
            "namespace_events": events,
            "resource_usage": usage,
            "runbook_matches": runbook_matches,
            "pod_details": pod_details,
        },
        "next_instruction": "Use this snapshot to produce a structured incident report using INCIDENT_REPORT_SCHEMA. Preserve evidence and unknowns.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Kubernetes incident investigator")
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser("investigate")
    inv.add_argument("--namespace", required=True)
    inv.add_argument("--service", required=True)
    inv.add_argument("--since-minutes", type=int, default=60)

    args = parser.parse_args()

    if args.command == "investigate":
        print(json.dumps(investigate(args.namespace, args.service, args.since_minutes), indent=2))


if __name__ == "__main__":
    main()
