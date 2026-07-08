from __future__ import annotations

from dataclasses import dataclass


DESTRUCTIVE_KUBECTL_VERBS = {
    "delete",
    "apply",
    "patch",
    "scale",
    "replace",
    "cordon",
    "uncordon",
    "drain",
    "taint",
    "annotate",
    "label",
    "create",
    "edit",
    "rollout",
    "exec",
}

ALLOWED_KUBECTL_VERBS = {
    "get",
    "describe",
    "logs",
    "top",
    "auth",
    "config",
}


@dataclass
class GateDecision:
    allowed: bool
    reason: str


def validate_kubectl_verb(verb: str) -> GateDecision:
    if verb in DESTRUCTIVE_KUBECTL_VERBS:
        return GateDecision(False, f"Blocked destructive kubectl verb: {verb}")
    if verb not in ALLOWED_KUBECTL_VERBS:
        return GateDecision(False, f"Blocked unrecognized kubectl verb: {verb}")
    return GateDecision(True, "Allowed read-only kubectl verb")


def require_human_approval(action: str, approved: bool) -> GateDecision:
    if approved:
        return GateDecision(True, f"Human approved action: {action}")
    return GateDecision(False, f"Human approval required for action: {action}")
