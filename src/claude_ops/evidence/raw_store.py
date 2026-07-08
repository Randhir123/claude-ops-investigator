from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_ops.evidence.models import EvidenceRecord


DEFAULT_ARTIFACT_DIR = Path("artifacts")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _stable_hash(*, content_type: str, metadata: dict[str, Any], raw: Any) -> str:
    """Hash content_type + metadata + raw together.

    Hashing `raw` alone lets two distinct tool calls (e.g. different metric
    names) collide on the same evidence_ref whenever they happen to return the
    same empty/identical payload in the same second, silently overwriting one
    artifact with another. Including content_type and metadata (which carry
    the tool/query identity) makes the ref unique per call even when the raw
    body is identical.
    """
    payload = {"content_type": content_type, "metadata": metadata, "raw": raw}
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def store_raw_evidence(
    *,
    content_type: str,
    raw: Any,
    summary: str,
    metadata: dict[str, Any] | None = None,
    artifact_dir: Path | str = DEFAULT_ARTIFACT_DIR,
    max_summary_chars: int = 1000,
) -> EvidenceRecord:
    artifact_root = Path(artifact_dir)
    artifact_root.mkdir(parents=True, exist_ok=True)

    metadata = metadata or {}
    evidence_ref = f"ev_{_utc_now_compact()}_{_stable_hash(content_type=content_type, metadata=metadata, raw=raw)}"
    artifact_path = artifact_root / f"{evidence_ref}.json"

    payload = {
        "evidence_ref": evidence_ref,
        "content_type": content_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "raw": raw,
    }

    artifact_path.write_text(json.dumps(payload, indent=2, default=str))

    summary = summary.strip()
    truncated = False
    if len(summary) > max_summary_chars:
        summary = summary[:max_summary_chars].rstrip() + "..."
        truncated = True

    return EvidenceRecord(
        evidence_ref=evidence_ref,
        content_type=content_type,
        summary=summary,
        artifact_path=str(artifact_path),
        size_bytes=artifact_path.stat().st_size,
        metadata=metadata,
        truncated=truncated,
    )


def load_raw_evidence(
    evidence_ref: str,
    artifact_dir: Path | str = DEFAULT_ARTIFACT_DIR,
) -> dict[str, Any]:
    artifact_path = Path(artifact_dir) / f"{evidence_ref}.json"
    if not artifact_path.exists():
        raise FileNotFoundError(f"Evidence artifact not found: {evidence_ref}")
    return json.loads(artifact_path.read_text())
