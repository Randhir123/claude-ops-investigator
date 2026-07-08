from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_ops.errors import ToolError, ok


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def get_runbook_catalog() -> dict[str, Any]:
    path = PROJECT_ROOT / "data" / "runbook_index.json"
    try:
        return ok(json.loads(path.read_text()))
    except Exception as exc:
        return ToolError("validation", False, f"Failed to load runbook catalog: {exc}").to_dict()


def search_runbooks(query: str) -> dict[str, Any]:
    catalog = get_runbook_catalog()
    if catalog.get("isError"):
        return catalog

    query_lower = query.lower()
    matches: list[dict[str, Any]] = []

    for rb in catalog["data"].get("runbooks", []):
        path = PROJECT_ROOT / rb["path"]
        try:
            text = path.read_text()
        except Exception:
            continue
        haystack = f"{rb['title']}\n{text}".lower()
        if query_lower in haystack or any(token in haystack for token in query_lower.split()):
            matches.append({
                "id": rb["id"],
                "title": rb["title"],
                "path": rb["path"],
                "excerpt": text[:1200],
            })

    return ok(matches)
