from __future__ import annotations

from typing import Any

from claude_ops.evidence.raw_store import store_raw_evidence
from claude_ops.evidence.summarizers import summarize_kubectl_result


def store_k8s_tool_result(
    *,
    content_type: str,
    result: dict[str, Any],
    label: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Store successful K8s tool output as raw evidence and return compact metadata.

    Errors are returned unchanged because the agent needs to see the error details
    directly to decide whether to retry, narrow scope, or report a permission issue.
    """
    if result.get("isError"):
        return result

    raw = result.get("data")
    summary = summarize_kubectl_result(raw, label=label)

    record = store_raw_evidence(
        content_type=content_type,
        raw=raw,
        summary=summary,
        metadata=metadata,
    )

    return {
        "isError": False,
        "data": record.to_dict(),
    }
