#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash): deny unsafe Kubernetes/Helm commands.

This is a harness-level companion to src/claude_ops/hooks.py::validate_kubectl_verb.
That function only gates kubectl calls made through the typed MCP tool layer
(tools/k8s_tools.py::_run_kubectl) -- it never sees a raw Bash invocation. This
hook is the second gate, for the case where a raw `kubectl`/`helm` command
reaches the Bash tool directly, bypassing the typed tools altogether.

Read-only: it only inspects tool_input.command from the PreToolUse payload on
stdin. It never executes anything, and never calls Kubernetes, Helm,
Prometheus, IBM Cloud Logs, or the Claude API.

Disable locally: set CLAUDE_OPS_HOOKS_DISABLED=1 in the environment (or in a
local .env sourced before launching Claude Code), or set
"disableAllHooks": true in .claude/settings.local.json. See README.md.
"""
from __future__ import annotations

import json
import os
import re
import sys

_SHELL_SEPARATORS = re.compile(r"&&|\|\||;|\|")

# Mirrors the *category* of src/claude_ops/hooks.py::DESTRUCTIVE_KUBECTL_VERBS
# for the specific verbs called out for this harness-level gate. Intentionally
# hardcoded rather than imported: this script must run standalone (no
# PYTHONPATH/venv dependency) with the stdlib only.
_DENY_PATTERNS = [
    (re.compile(r"\bkubectl\b.*\bdelete\b"), "kubectl delete"),
    (re.compile(r"\bkubectl\b.*\bapply\b"), "kubectl apply"),
    (re.compile(r"\bkubectl\b.*\bpatch\b"), "kubectl patch"),
    (re.compile(r"\bkubectl\b.*\bscale\b"), "kubectl scale"),
    (re.compile(r"\bkubectl\b.*\brollout\b.*\brestart\b"), "kubectl rollout restart"),
    (re.compile(r"\bkubectl\b.*\bexec\b"), "kubectl exec"),
    (re.compile(r"\bhelm\b.*\bupgrade\b"), "helm upgrade"),
]


def _hooks_disabled() -> bool:
    return os.environ.get("CLAUDE_OPS_HOOKS_DISABLED", "").strip().lower() in ("1", "true", "yes")


def _find_violation(command: str) -> str | None:
    # Check each shell-separated segment independently so a denied verb in
    # one clause doesn't false-positive off an unrelated kubectl/helm call
    # elsewhere in the same line (e.g. "kubectl get pods | grep delete").
    for segment in _SHELL_SEPARATORS.split(command):
        for pattern, label in _DENY_PATTERNS:
            if pattern.search(segment):
                return label
    return None


def main() -> int:
    if _hooks_disabled():
        return 0

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # fail open on malformed input -- nothing we can check

    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command", "")
    if not isinstance(command, str) or not command.strip():
        return 0

    violation = _find_violation(command)
    if violation is None:
        return 0

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Blocked unsafe shell command matching '{violation}'. This project "
                "is read-only by default (see CLAUDE.md non-negotiable safety "
                "rules) -- destructive kubectl/helm actions require explicit human "
                "approval through an approved gate, not raw Bash."
            ),
        },
        "systemMessage": f"Blocked unsafe shell command ({violation}).",
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
