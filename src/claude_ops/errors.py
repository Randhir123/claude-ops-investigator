from dataclasses import dataclass, asdict
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
