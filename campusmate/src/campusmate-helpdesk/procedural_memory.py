"""Two-tier procedural memory loader for CampusMate helpdesk.

Priority:
  1. Live Foundry Memory Store (when AZURE_AI_PROJECT_ENDPOINT is set and
     the store is reachable).
  2. Local seed file  procedural_memory_seed.json  (always available offline).

Public API
----------
recall_learned_procedures() -> str   — @tool callable by the worker agent
render_procedures_block()  -> str   — prepended to the system prompt
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Try to import agent_framework; degrade gracefully when not installed.
try:
    from agent_framework import tool  # type: ignore[import]
except ImportError:  # pragma: no cover
    def tool(fn):  # type: ignore[misc]
        return fn


_SEED_PATH = Path(__file__).parent / "procedural_memory_seed.json"
_DEFAULT_STORE_NAME = "campusmate-procedural"
_PROCEDURAL_MEMORY_MODE = os.getenv("PROCEDURAL_MEMORY_MODE", "hybrid")


def _load_seed() -> list[dict[str, Any]]:
    """Load procedures from the local seed JSON file."""
    if not _SEED_PATH.exists():
        return []
    try:
        data = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception as exc:  # pragma: no cover
        print(f"[procedural_memory] Seed load error: {exc}")
        return []


def _load_live() -> list[dict[str, Any]]:
    """Attempt to fetch procedures from the Foundry Memory Store."""
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "").strip()
    if not endpoint:
        return []

    store_name = os.getenv("PROCEDURAL_MEMORY_STORE_NAME", _DEFAULT_STORE_NAME)
    try:
        # Import here so missing package doesn't break the module at import time.
        from azure.ai.projects import AIProjectClient  # type: ignore[import]
        from azure.identity import DefaultAzureCredential  # type: ignore[import]

        client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
        store = client.agents.get_vector_store_by_name(store_name)  # type: ignore[attr-defined]
        if store is None:
            return []

        # Search with an empty query to retrieve all items (best-effort).
        files = client.agents.list_vector_store_files(store.id)  # type: ignore[attr-defined]
        items: list[dict[str, Any]] = []
        for f in (files.data if hasattr(files, "data") else []):
            items.append({
                "id": f.id,
                "kind": "procedural",
                "scope": "user-procedural",
                "content": {"instruction": getattr(f, "filename", str(f))},
            })
        return items
    except Exception as exc:  # pragma: no cover
        print(f"[procedural_memory] Live load failed ({exc}), falling back to seed.")
        return []


def _get_procedures() -> list[dict[str, Any]]:
    mode = _PROCEDURAL_MEMORY_MODE.lower()
    if mode == "seed":
        return _load_seed()
    elif mode == "live":
        live = _load_live()
        return live if live else _load_seed()
    else:  # hybrid (default)
        seed = _load_seed()
        live = _load_live()
        # Merge: live items take precedence over seed items with the same id.
        merged = {item["id"]: item for item in seed}
        for item in live:
            merged[item["id"]] = item
        return list(merged.values())


@tool
def recall_learned_procedures() -> str:
    """Return all learned procedural guidelines that have been stored in memory.

    Call this whenever the student asks what you have learned, or when you want
    to check whether there is a standing guideline before responding.
    """
    procedures = _get_procedures()
    if not procedures:
        return "No learned procedures found."

    lines: list[str] = []
    for p in procedures:
        content = p.get("content", {})
        applicable = content.get("applicable_to", "")
        instruction = content.get("instruction", "")
        lines.append(f"- [{p.get('id', '?')}] {applicable}\n  → {instruction}")
    return "\n".join(lines)


def render_procedures_block() -> str:
    """Render a markdown block of procedures to prepend to the system prompt."""
    procedures = _get_procedures()
    if not procedures:
        return ""

    parts = ["## Learned Procedural Guidelines\n"]
    for p in procedures:
        content = p.get("content", {})
        applicable = content.get("applicable_to", "")
        instruction = content.get("instruction", "")
        parts.append(f"**When**: {applicable}\n**Do**: {instruction}\n")
    return "\n".join(parts)
