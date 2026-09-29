"""Structured tool result/error conventions.

Deliberately mirrors claude-ops-investigator's `claude_ops.errors` module
(same shape: `ok()` / `ToolError`) so that a subagent already fluent in one
project's tool outputs doesn't have to learn a second error convention when
it starts calling tools from this server too. This file has no dependency on
claude-ops-investigator -- it's a small, intentionally-duplicated contract,
not a shared import, so this server stays installable and usable completely
on its own.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

ErrorCategory = Literal["transient", "validation", "permission", "business", "unknown"]


@dataclass
class ToolError:
    errorCategory: ErrorCategory
    isRetryable: bool
    message: str
    attempted: dict[str, Any] | None = None
    partialResults: Any | None = None
    alternatives: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "isError": True,
            **asdict(self),
        }


def ok(data: Any) -> dict[str, Any]:
    return {"isError": False, "data": data}
