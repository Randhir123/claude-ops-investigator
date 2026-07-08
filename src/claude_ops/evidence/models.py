from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_ref: str
    content_type: str
    summary: str
    artifact_path: str
    size_bytes: int
    metadata: dict[str, Any]
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
