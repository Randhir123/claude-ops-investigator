from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "block_unsafe_shell.py"


def _run_hook(command: str) -> dict | None:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_OPS_HOOKS_DISABLED"}
    completed = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return json.loads(completed.stdout) if completed.stdout.strip() else None


@pytest.mark.parametrize(
    "command",
    [
        "scripts/capture-javacore.sh si time-series-query-abc",
        "./scripts/capture-javacore.sh -n 3 -i 10 si pod-1",
        "bash ./scripts/capture-javacore.sh si pod-1",
        "bash -x scripts/capture-javacore.sh si pod-1",
        "cd /repo && sh scripts/capture-javacore.sh si pod-1 app",
        "env KUBECONFIG=/tmp/k nohup /repo/scripts/capture-javacore.sh si pod-1",
        "timeout 60 zsh scripts/capture-javacore.sh si pod-1",
        "source scripts/capture-javacore.sh si pod-1",
        "echo hi; capture-javacore.sh si pod-1",
        "scripts/capture-gclog.sh si pod-1",
        "bash scripts/capture-gclog.sh -m 5 si pod-1 app",
        "scripts/cleanup-javacores.sh si pod-1",
        "bash scripts/cleanup-javacores.sh -a -y si pod-1",
        "sh -c 'scripts/cleanup-javacores.sh -y si pod-1'",
        # wrapped in sh -c / bash -lc, or backgrounded
        "sh -c 'scripts/capture-javacore.sh si pod-1'",
        'bash -lc "cd /repo && scripts/capture-gclog.sh si pod-1"',
        "true & scripts/capture-gclog.sh si pod-1",
        # quoting doesn't hide a destructive verb
        'echo "x; kubectl delete pod pod-1"',
        "kubectl exec -n si pod-1 -- kill -3 1",
        "kubectl -n si delete pod pod-1",
    ],
)
def test_human_only_and_destructive_commands_are_denied(command):
    output = _run_hook(command)

    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "kubectl -n si logs time-series-query-abc --since=60m",
        "kubectl -n si get pods",
        "pytest tests/test_jvm_diagnostics_tools.py",
        # reading/inspecting the human-only script is fine; only running it is denied
        'grep -n "capture-javacore.sh <namespace>" README.md',
        "sed -n 1,20p scripts/capture-javacore.sh",
        "chmod +x scripts/capture-javacore.sh",
        "git add scripts/capture-javacore.sh",
        "sed -n 1,20p scripts/capture-gclog.sh",
        "chmod +x scripts/cleanup-javacores.sh",
        # a quoted | inside a search pattern is not a pipe into the script
        'grep -c -E "capture-javacore|capture-gclog|jvm_analyze" README.md',
        "grep -e 'a;scripts/capture-gclog.sh' notes.txt",
        "kubectl get pods -n si | grep delete",
        "kubectl logs pod-1 2>&1 | tail -5",
    ],
)
def test_read_only_commands_are_allowed(command):
    assert _run_hook(command) is None
