from __future__ import annotations

import re
from collections import Counter
from typing import Any

_EVENT_HEADER_PREFIX = "LAST SEEN"
_MAX_TOP_REASONS = 5
_MAX_WARNING_LINES = 5
_MAX_WARNING_OBJECTS = 10
_MAX_PROMETHEUS_SERIES = 25
_MAX_LOG_SAMPLE_LINES = 10


def summarize_text_block(text: str, *, label: str, max_lines: int = 8) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    sample = lines[:max_lines]
    return (
        f"{label}: {len(lines)} non-empty lines captured. "
        f"First lines: " + " | ".join(sample)
        if sample
        else f"{label}: no non-empty lines captured."
    )


def summarize_kubectl_result(data: object, *, label: str) -> str:
    if isinstance(data, str):
        return summarize_text_block(data, label=label)
    if isinstance(data, list):
        return f"{label}: {len(data)} records captured."
    if isinstance(data, dict):
        return f"{label}: object captured with keys: {', '.join(sorted(data.keys())[:10])}."
    return f"{label}: captured value of type {type(data).__name__}."


def _event_column(line: str, index: int) -> str:
    """Return the value at `index` from a `kubectl get events` table row.

    Columns (LAST SEEN, TYPE, REASON, OBJECT, MESSAGE) are separated by
    variable-width whitespace, so we split on runs of 2+ spaces rather than
    any single space, which would otherwise break on messages like
    "Liveness probe failed".
    """
    columns = re.split(r"\s{2,}", line.strip())
    return columns[index].strip() if index < len(columns) else ""


def summarize_k8s_events(text: str, target_service: str | None = None) -> str:
    """Summarize `kubectl get events` table output for incident triage.

    Surfaces Warning/Unhealthy counts, top reasons, and the objects and raw
    lines behind any warnings so the agent can often reason from the summary
    alone instead of fetching the full evidence detail.
    """
    all_lines = [line for line in text.splitlines() if line.strip()]
    event_lines = [line for line in all_lines if not line.strip().upper().startswith(_EVENT_HEADER_PREFIX)]

    if not event_lines:
        return "Kubernetes events: no non-empty event lines captured."

    warning_lines = [line for line in event_lines if _event_column(line, 1) == "Warning"]
    unhealthy_lines = [line for line in event_lines if _event_column(line, 2) == "Unhealthy"]

    reason_counts = Counter(reason for line in event_lines if (reason := _event_column(line, 2)))
    top_reasons = reason_counts.most_common(_MAX_TOP_REASONS)

    warning_objects = sorted({obj for line in warning_lines if (obj := _event_column(line, 3))})

    parts = [
        f"Kubernetes events: {len(event_lines)} event lines "
        f"({len(warning_lines)} Warning, {len(unhealthy_lines)} Unhealthy)."
    ]

    if top_reasons:
        parts.append("Top reasons: " + ", ".join(f"{reason} x{count}" for reason, count in top_reasons) + ".")

    if warning_objects:
        parts.append("Warning objects: " + ", ".join(warning_objects[:_MAX_WARNING_OBJECTS]) + ".")

    if warning_lines:
        parts.append("First Warning lines: " + " | ".join(line.strip() for line in warning_lines[:_MAX_WARNING_LINES]))

    if target_service:
        matching = [line for line in event_lines if target_service in line]
        if matching:
            parts.append(f"Lines mentioning '{target_service}': " + " | ".join(line.strip() for line in matching[:_MAX_WARNING_LINES]))

    return " ".join(parts)


def summarize_prometheus_result(raw: Any, *, label: str, max_series: int = _MAX_PROMETHEUS_SERIES) -> str:
    """Summarize a raw Prometheus `/api/v1/query(_range)` JSON response.

    Turns a potentially large series list into a compact text table so the
    raw JSON never has to enter model context directly.
    """
    if not isinstance(raw, dict) or raw.get("status") != "success":
        error = raw.get("error", "unknown error") if isinstance(raw, dict) else "malformed response"
        return f"{label}: query error - {error}."

    result = raw.get("data", {}).get("result", [])
    if not result:
        return f"{label}: query returned zero series."

    lines = []
    for series in result[:max_series]:
        metric = series.get("metric", {})
        label_str = ", ".join(
            f"{k}={v}" for k, v in metric.items() if k not in ("__name__", "job", "instance")
        )
        value = series.get("value", series.get("values", ["", "?"]))
        val = value[1] if isinstance(value, list) else value
        try:
            val = f"{float(val):.2f}"
        except (ValueError, TypeError):
            pass
        lines.append(f"{label_str or '(no labels)'} => {val}")

    header = f"{label}: {len(result)} series (showing up to {max_series})."
    return header + " " + " | ".join(lines)


def summarize_log_matches(entries: list[dict[str, str]], *, label: str, max_lines: int = _MAX_LOG_SAMPLE_LINES) -> str:
    """Summarize a list of {"timestamp", "text"} log entries into a compact string.

    Entries are expected to already be sorted chronologically by the caller.
    """
    if not entries:
        return f"{label}: no matching log lines."

    timestamps = [entry["timestamp"] for entry in entries if entry.get("timestamp")]
    time_range = f"{timestamps[0]} to {timestamps[-1]}" if timestamps else "unknown time range"

    sample = entries[:max_lines]
    sample_lines = " | ".join(f"{entry.get('timestamp') or '?'} {entry.get('text', '')}" for entry in sample)

    return (
        f"{label}: {len(entries)} matching log lines ({time_range}). "
        f"First lines: {sample_lines}"
    )
