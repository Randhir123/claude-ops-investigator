#!/usr/bin/env python3
"""Stop hook: validate a final incident report contains the required fields.

Complements src/claude_ops/schemas/incident_report_schema.py, which validates
a report object programmatically inside the app. This hook is a harness-level,
best-effort text check over the last assistant message in the session
transcript, so an incident report can't silently ship without the fields
CLAUDE.md and .claude/rules/incident-output.md require.

Only enforced when the last assistant message looks like an attempted
incident report (contains a report-shaped marker such as "Subagent usage
audit" or "incident report"); ordinary conversational turns are left alone.

Read-only: it only reads the local transcript JSONL named in the Stop hook
payload. It never calls Kubernetes, Prometheus, IBM Cloud Logs, or the
Claude API, and never writes anything.

Disable locally: set CLAUDE_OPS_HOOKS_DISABLED=1 in the environment, or set
"disableAllHooks": true in .claude/settings.local.json. See README.md.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_REPORT_MARKERS = (
    "subagent usage audit",
    "incident report",
    "requires_human",
)

_REQUIRED: dict[str, re.Pattern[str]] = {
    "evidence_ref": re.compile(r"evidence_ref", re.IGNORECASE),
    "Subagent usage audit": re.compile(r"subagent usage audit", re.IGNORECASE),
    "ruled_out": re.compile(r"ruled[ _]?out", re.IGNORECASE),
    "unknowns": re.compile(r"unknowns?\b", re.IGNORECASE),
    "confirmed/not-confirmed statement": re.compile(
        r"\b(not\s+confirmed|confirmed|verdict)\b", re.IGNORECASE
    ),
}


def _hooks_disabled() -> bool:
    return os.environ.get("CLAUDE_OPS_HOOKS_DISABLED", "").strip().lower() in ("1", "true", "yes")


def _extract_text_blocks(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            out.append(node["text"])
        for value in node.values():
            _extract_text_blocks(value, out)
    elif isinstance(node, list):
        for item in node:
            _extract_text_blocks(item, out)


def _last_assistant_text(transcript_path: str) -> str | None:
    path = Path(transcript_path)
    if not path.exists():
        return None

    last_text: str | None = None
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            role = record.get("type") or (record.get("message") or {}).get("role")
            if role != "assistant":
                continue

            blocks: list[str] = []
            _extract_text_blocks(record, blocks)
            if blocks:
                last_text = "\n".join(blocks)

    return last_text


def main() -> int:
    if _hooks_disabled():
        return 0

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    if payload.get("stop_hook_active"):
        # We already blocked once on our own feedback -- don't loop forever.
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        return 0

    text = _last_assistant_text(transcript_path)
    if not text:
        return 0

    lowered = text.lower()
    if not any(marker in lowered for marker in _REPORT_MARKERS):
        return 0  # not an incident-report-shaped turn -- nothing to validate

    missing = [label for label, pattern in _REQUIRED.items() if not pattern.search(text)]
    if not missing:
        return 0

    reason = (
        "Final incident report is missing required elements: "
        + ", ".join(missing)
        + ". Per CLAUDE.md output rules and .claude/rules/incident-output.md, "
        "the report must include evidence_refs, a 'Subagent usage audit' table, "
        "ruled_out, unknowns, and an explicit confirmed/not-confirmed verdict "
        "before stopping."
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
