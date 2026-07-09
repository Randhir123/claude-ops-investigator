#!/usr/bin/env python3
"""PostToolUse hook (matcher: mcp__claude-ops-investigator__.*).

Appends one JSON line per completed MCP tool call to
runs/mcp-tool-audit.jsonl: tool name, timestamp, status, and evidence_ref if
the tool result carries one. This gives a flat, greppable audit trail of
every claude-ops-investigator MCP tool invocation in a session, independent
of (and complementary to) the evidence artifacts already written under
artifacts/ by the app's own evidence store (src/claude_ops/evidence/).

Read-only except for appending to runs/mcp-tool-audit.jsonl (an existing
generated/output directory -- see CLAUDE.md). Never calls Kubernetes,
Prometheus, IBM Cloud Logs, or the Claude API; only inspects the PostToolUse
payload already provided on stdin.

Disable locally: set CLAUDE_OPS_HOOKS_DISABLED=1 in the environment, or set
"disableAllHooks": true in .claude/settings.local.json. See README.md.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any


def _hooks_disabled() -> bool:
    return os.environ.get("CLAUDE_OPS_HOOKS_DISABLED", "").strip().lower() in ("1", "true", "yes")


def _project_root() -> Path:
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(root) if root else Path.cwd()


def _coerce_json(value: Any) -> Any:
    """Best-effort: turn a JSON-string payload into a parsed object."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _normalize_tool_result(tool_response: Any) -> dict[str, Any] | None:
    """Normalize the several shapes a PostToolUse tool_response can take for
    an MCP tool into the {isError, data} dict our tools actually return.

    MCP tool results may arrive as: the raw dict our tool function returned,
    a JSON string of that dict (our MCP tools return `_json(...)` strings),
    or an MCP content envelope like {"content": [{"type": "text", "text":
    "<json>"}]}. Try each in order; fall back to None (unknown shape) rather
    than guessing.
    """
    candidate = _coerce_json(tool_response)

    if isinstance(candidate, dict) and "isError" in candidate:
        return candidate

    if isinstance(candidate, dict) and isinstance(candidate.get("content"), list):
        for block in candidate["content"]:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parsed = _coerce_json(block["text"])
                if isinstance(parsed, dict) and "isError" in parsed:
                    return parsed

    return None


def _extract_evidence_ref(result: dict[str, Any] | None) -> str | None:
    if not result:
        return None
    data = result.get("data")
    if isinstance(data, dict):
        ref = data.get("evidence_ref")
        if isinstance(ref, str):
            return ref
    ref = result.get("evidence_ref")
    return ref if isinstance(ref, str) else None


def main() -> int:
    if _hooks_disabled():
        return 0

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.startswith("mcp__claude-ops-investigator__"):
        return 0

    result = _normalize_tool_result(payload.get("tool_response"))
    if result is not None:
        status = "error" if result.get("isError") else "ok"
    else:
        status = "unknown"

    entry = {
        "tool_name": tool_name,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": status,
        "evidence_ref": _extract_evidence_ref(result),
        "session_id": payload.get("session_id"),
    }

    runs_dir = _project_root() / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    audit_path = runs_dir / "mcp-tool-audit.jsonl"
    with audit_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
