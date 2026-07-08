from __future__ import annotations


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
