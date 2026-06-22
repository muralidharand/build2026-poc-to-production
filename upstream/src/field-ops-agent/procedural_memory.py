"""Procedural memory integration for the field-ops-agent.

═══════════════════════════════════════════════════════════════════════════════
DEMO POINT — "Procedural Memory"

What this module does (the demo headline):
  1. At agent startup, fetch the procedural memory items the agent has learned
     from past conversations.
  2. Render them as a "Learned procedures" block that gets PREPENDED to the
     agent's system prompt. The model treats them as native skills it has
     acquired, not "tool output."
  3. As the agent operates and learns new patterns (e.g., a user corrects an
     SOP), the patterns are surfaced through the @tool ``recall_learned_procedures``
     for explicit visibility, and could be appended to the store via
     ``update_memories`` on the running session (see "How procedures get
     written" below).

Where the procedures come from (two-tier loader):
  • PRIMARY — Live Foundry Memory Store API:
      POST {project}/memory_stores/{store}/items:list?kind=procedural
    Token scopes: ai.azure.com (for memstore) + cognitiveservices.azure.com
    (for the extraction backend, forwarded via x-ms-oai-authorization-header-value).
    Returns a list of procedural items with .content.applicable_to and
    .content.instruction.
  • FALLBACK — Local seed file ``procedural_memory_seed.json`` shipped with
    the agent. Used when the live API is unreachable OR when the agent runs
    in a project that isn't configured for the memory-store preview.
  Set ``PROCEDURAL_MEMORY_MODE=live|seed|hybrid`` (default: hybrid) to
  control which path is taken.

How procedures get WRITTEN (upgrade path):
  After each completed worker run, ``run_worker`` could POST the
  conversation back to ``{store}:update_memories`` with scope=user-procedural.
  The memory-store backend mines that conversation for new procedural
  patterns (clarifying questions, SOP overrides, ...) and appends them to
  the store. The next agent startup picks them up automatically. We've
  scaffolded that call as ``capture_learned_procedure(conversation)`` but
  gated it behind the same allowlist check until the preview opens up.

═══════════════════════════════════════════════════════════════════════════════

Environment variables (all optional):
  FOUNDRY_PROJECT_ENDPOINT
      Project endpoint (auto-injected in hosted containers). Required for
      the live path.
  PROCEDURAL_MEMORY_STORE_NAME
      Memory store name (default: ``field-ops-procedural``).
  PROCEDURAL_MEMORY_SCOPE
      Scope filter (default: ``user-procedural``).
  PROCEDURAL_MEMORY_MODE
      ``live`` | ``seed`` | ``hybrid`` (default: ``hybrid``). Hybrid tries live
      first, falls back to seed on any error.
  PROCEDURAL_MEMORY_TIMEOUT_SEC
      HTTP timeout for the list call (default: 8 seconds — startup-blocking).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from agent_framework import tool
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

logger = logging.getLogger(__name__)

DEFAULT_STORE_NAME = "field-ops-procedural"
DEFAULT_SCOPE = "user-procedural"
_DEFAULT_TIMEOUT_SEC = 3.0   # tight startup budget — fall through to seed fast
_SEED_PATH = Path(__file__).parent / "procedural_memory_seed.json"
_MODE = os.getenv("PROCEDURAL_MEMORY_MODE", "hybrid").strip().lower()
_STORE = os.getenv("PROCEDURAL_MEMORY_STORE_NAME", DEFAULT_STORE_NAME)
_SCOPE = os.getenv("PROCEDURAL_MEMORY_SCOPE", DEFAULT_SCOPE)
_TIMEOUT = float(os.getenv("PROCEDURAL_MEMORY_TIMEOUT_SEC", str(_DEFAULT_TIMEOUT_SEC)))

# Module-level cache: procedural items are loaded once at startup and reused
# across the lifetime of the container. Memory churn during a demo is
# negligible; if live updates ever matter, replace with an async ttl cache.
_CACHE: list["ProceduralItem"] | None = None


@dataclass
class ProceduralItem:
    """A single procedural memory entry the agent has learned.

    Mirrors the shape returned by the Memory Store API:
        item.content.applicable_to   — when the procedure applies
        item.content.instruction     — what the agent should do
    """
    applicable_to: str
    instruction: str
    source: str = "unknown"  # "live" | "seed"
    item_id: str | None = None

    def render(self) -> str:
        return (
            f"- applicable_to: {self.applicable_to}\n"
            f"  instruction: {self.instruction}"
        )


# ── LIVE PATH — fetch from Foundry Memory Store ───────────────────────────────


def _bearer(scope: str) -> str:
    cred = DefaultAzureCredential()
    return get_bearer_token_provider(cred, scope)()


def _fetch_live() -> list[ProceduralItem]:
    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").rstrip("/")
    if not endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT not set; cannot use live path")

    url = f"{endpoint}/memory_stores/{_STORE}/items:list?kind=procedural&api-version=v1"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_bearer('https://ai.azure.com/.default')}",
        "x-ms-oai-authorization-header-value":
            f"Bearer {_bearer('https://cognitiveservices.azure.com/.default')}",
        "Foundry-Features": "MemoryStores=V1Preview",
    }
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.post(url, headers=headers, json={"scope": _SCOPE})
    if r.status_code >= 400:
        raise RuntimeError(f"memory_stores items:list returned {r.status_code}: {r.text[:300]}")
    data = r.json().get("data") or []
    items: list[ProceduralItem] = []
    for d in data:
        content = d.get("content") or {}
        # Memory store returns content as either a string or a dict — handle both
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except Exception:
                content = {"applicable_to": "(unstructured)", "instruction": content}
        items.append(ProceduralItem(
            applicable_to=str(content.get("applicable_to") or "(unspecified)"),
            instruction=str(content.get("instruction") or content.get("text") or ""),
            source="live",
            item_id=d.get("id"),
        ))
    return items


# ── SEED PATH — load curated items from local JSON ────────────────────────────


def _fetch_seed() -> list[ProceduralItem]:
    if not _SEED_PATH.exists():
        return []
    raw = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    items = []
    for entry in raw.get("items", []):
        content = entry.get("content") or {}
        items.append(ProceduralItem(
            applicable_to=str(content.get("applicable_to") or "(unspecified)"),
            instruction=str(content.get("instruction") or ""),
            source="seed",
            item_id=entry.get("id"),
        ))
    return items


def load_procedural_items(force_refresh: bool = False) -> list[ProceduralItem]:
    """Load procedural items per the configured mode.

    Hybrid (default): try live, fall back to seed.
    Live: live only; empty list on failure.
    Seed: seed only.
    """
    global _CACHE
    if _CACHE is not None and not force_refresh:
        return _CACHE

    items: list[ProceduralItem] = []
    if _MODE in ("live", "hybrid"):
        try:
            items = _fetch_live()
            logger.info("Procedural memory: loaded %d item(s) from live store %r",
                        len(items), _STORE)
        except Exception as e:
            logger.warning("Procedural memory: live load failed (%s); "
                           "%s", e, "falling back to seed" if _MODE == "hybrid" else "no fallback configured")
            if _MODE == "live":
                items = []
    if not items and _MODE in ("seed", "hybrid"):
        items = _fetch_seed()
        logger.info("Procedural memory: loaded %d item(s) from seed file", len(items))

    _CACHE = items
    return items


# ── INSTRUCTION RENDERING — what gets prepended to the system prompt ──────────


_INSTRUCTION_HEADER = """## Learned procedures (from procedural memory)

The following are procedures you have learned from past field-tech conversations. Apply them when the `applicable_to` situation arises. These take precedence over generic SOP guidance because they reflect site- or user-specific corrections you have already received.
"""


def render_procedures_block() -> str:
    items = load_procedural_items()
    if not items:
        return ""
    body = "\n\n".join(item.render() for item in items)
    return f"{_INSTRUCTION_HEADER}\n{body}\n"


# ── RUNTIME TOOL — explicit recall for the model ──────────────────────────────


@tool
def recall_learned_procedures(situation: str = "") -> str:
    """Recall procedural memories you have learned from past field-tech conversations.

    Call this when a user asks how to do something and you want to make sure you're
    applying any site-specific overrides or clarifying-question patterns you've
    learned. Returns each procedure with `applicable_to` (when it fires) and
    `instruction` (what to do).

    Args:
        situation: Optional free-text description of the situation, e.g.
            "user asked for last service date" or "user asked for a procedure".
            Returns all stored procedures regardless — the agent should filter.
    """
    items = load_procedural_items()
    out = {
        "count": len(items),
        "procedures": [
            {
                "id": it.item_id,
                "applicable_to": it.applicable_to,
                "instruction": it.instruction,
                "source": it.source,
            }
            for it in items
        ],
    }
    return json.dumps(out, indent=2)
