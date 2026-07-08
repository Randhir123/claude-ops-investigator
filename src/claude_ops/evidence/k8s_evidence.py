from __future__ import annotations

from typing import Any, Callable

from claude_ops.evidence.raw_store import store_raw_evidence
from claude_ops.evidence.summarizers import summarize_kubectl_result


def store_k8s_tool_result(
    *,
    content_type: str,
    result: dict[str, Any],
    label: str,
    metadata: dict[str, Any],
    summarize: Callable[[Any], str] | None = None,
) -> dict[str, Any]:
    """Store successful K8s tool output as raw evidence and return compact metadata.

    Errors are returned unchanged because the agent needs to see the error details
    directly to decide whether to retry, narrow scope, or report a permission issue.

    `summarize` lets callers plug in a content-type-specific summarizer (e.g.
    `summarize_k8s_events`); it receives the raw data and must return the summary
    string. Defaults to the generic `summarize_kubectl_result`.
    """
    if result.get("isError"):
        return result

    raw = result.get("data")
    summary = summarize(raw) if summarize is not None else summarize_kubectl_result(raw, label=label)

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
