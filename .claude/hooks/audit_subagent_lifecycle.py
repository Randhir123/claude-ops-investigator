#!/usr/bin/env python3
"""SubagentStart/SubagentStop hook: append lifecycle events to runs/subagent-audit.jsonl.

Wired twice in settings.json (once per event) with the event name passed as
argv[1] -- the hook payload on stdin is not guaranteed to say which lifecycle
event fired, so each settings.json entry states it explicitly rather than
guessing from payload shape.

Read-only except for appending to runs/subagent-audit.jsonl (an existing
generated/output directory -- see CLAUDE.md). Never calls Kubernetes,
Prometheus, IBM Cloud Logs, or the Claude API; only inspects the hook
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


def _hooks_disabled() -> bool:
    return os.environ.get("CLAUDE_OPS_HOOKS_DISABLED", "").strip().lower() in ("1", "true", "yes")


def _project_root() -> Path:
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(root) if root else Path.cwd()


def main() -> int:
    if _hooks_disabled():
        return 0

    event = sys.argv[1] if len(sys.argv) > 1 else "unknown"

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}

    entry = {
        "event": event,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": payload.get("session_id"),
        "subagent_type": (
            payload.get("subagent_type")
            or payload.get("agent_type")
            or payload.get("name")
        ),
        "description": payload.get("description") or payload.get("task"),
    }

    runs_dir = _project_root() / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    audit_path = runs_dir / "subagent-audit.jsonl"
    with audit_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
