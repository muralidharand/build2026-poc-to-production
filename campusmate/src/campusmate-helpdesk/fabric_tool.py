"""Fabric Data Agent integration for the field-ops-agent.

═══════════════════════════════════════════════════════════════════════════════
DEMO POINT — "Fabric IQ as a knowledge source"

Talk track:
    "Beyond Work IQ, we also wire in Fabric IQ. The Fabric data agent gives us
     a natural-language interface over our live site reliability telemetry —
     metrics, incidents, SLOs — all sitting in OneLake. The field-ops-agent
     just calls it like any other tool."

What this module exposes:
  - ``query_site_reliability`` — an @tool function the worker agent can call.
    Forwards a natural-language question to the published Fabric data agent
    via its Assistants-API-compatible endpoint and returns the answer.

The connection details (workspace_id, artifact_id) are sourced from a Foundry
project connection of type ``MicrosoftFabric`` so the same wiring works in
hosted (Foundry) and local runs.

Environment variables:
  FABRIC_DATA_AGENT_CONNECTION_NAME
      Name of the MicrosoftFabric connection in the Foundry project (e.g.,
      "fabric-site-reliability"). When unset, this module falls back to
      FABRIC_WORKSPACE_ID + FABRIC_ARTIFACT_ID.
  FABRIC_WORKSPACE_ID, FABRIC_ARTIFACT_ID
      Direct overrides for local development.
  FABRIC_DATA_AGENT_TIMEOUT_SEC
      Run polling timeout (default 90s).
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

import httpx
from agent_framework import tool
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

logger = logging.getLogger(__name__)

_FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"
_FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
_API_VERSION = "2024-05-01-preview"
_DEFAULT_TIMEOUT = float(os.getenv("FABRIC_DATA_AGENT_TIMEOUT_SEC", "90"))


def _resolve_ids() -> tuple[str | None, str | None]:
    """Pull workspace_id + artifact_id from env, optionally via a Foundry
    project connection lookup if FOUNDRY_PROJECT_ENDPOINT is also set.

    Local dev / fallback path is just env vars."""
    ws = os.getenv("FABRIC_WORKSPACE_ID", "").strip()
    art = os.getenv("FABRIC_ARTIFACT_ID", "").strip()
    if ws and art:
        return ws, art

    conn_name = os.getenv("FABRIC_DATA_AGENT_CONNECTION_NAME", "").strip()
    project_endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    if not (conn_name and project_endpoint):
        return None, None

    try:
        from azure.ai.projects import AIProjectClient

        cred = DefaultAzureCredential()
        client = AIProjectClient(endpoint=project_endpoint, credential=cred)
        conn = client.connections.get(conn_name, include_credentials=False)
        meta = getattr(conn, "metadata", None) or {}
        ws = meta.get("workspace-id") or meta.get("workspaceId") or ""
        art = meta.get("artifact-id") or meta.get("artifactId") or ""
        if ws and art:
            return ws, art
    except Exception as e:
        logger.warning("Fabric connection lookup via project_client failed: %s", e)
    return None, None


def _bearer() -> str:
    """Single-shot Fabric scope token. Caller is responsible for refresh."""
    cred = DefaultAzureCredential()
    provider = get_bearer_token_provider(cred, _FABRIC_SCOPE)
    return provider()


def _call_fabric_data_agent(question: str) -> dict[str, Any]:
    """Synchronously call the published Fabric data agent.

    Uses the data agent's Assistants-API-compatible endpoint:
      POST {base}/v1/workspaces/{ws}/dataagents/{art}/aiassistant/openai/assistants
      POST {base}/v1/workspaces/{ws}/dataagents/{art}/aiassistant/openai/threads
      POST .../threads/{tid}/messages
      POST .../threads/{tid}/runs
      GET  .../threads/{tid}/runs/{rid}   (poll)
      GET  .../threads/{tid}/messages     (read assistant response)
    """
    ws, art = _resolve_ids()
    if not (ws and art):
        return {
            "status": "error",
            "error": (
                "Fabric data agent connection not configured. Set "
                "FABRIC_DATA_AGENT_CONNECTION_NAME (and FOUNDRY_PROJECT_ENDPOINT) "
                "or FABRIC_WORKSPACE_ID + FABRIC_ARTIFACT_ID."
            ),
        }

    token = _bearer()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "ActivityId": str(uuid.uuid4()),
    }
    base = f"{_FABRIC_API_BASE}/workspaces/{ws}/dataagents/{art}/aiassistant/openai"
    api_q = f"?api-version={_API_VERSION}"

    deadline = time.monotonic() + _DEFAULT_TIMEOUT
    with httpx.Client(timeout=30.0) as client:
        # 1. Create an assistant inside the data-agent context.
        # The "model" field is unused — the Fabric runtime picks the model.
        r = client.post(f"{base}/assistants{api_q}", headers=headers,
                        json={"model": "not used"})
        if r.status_code >= 400:
            return {"status": "error", "stage": "create_assistant",
                    "code": r.status_code, "error": r.text[:500]}
        assistant_id = r.json().get("id")

        # 2. Create thread
        r = client.post(f"{base}/threads{api_q}", headers=headers, json={})
        if r.status_code >= 400:
            return {"status": "error", "stage": "create_thread",
                    "code": r.status_code, "error": r.text[:500]}
        thread_id = r.json().get("id")

        # 3. Post user message
        r = client.post(
            f"{base}/threads/{thread_id}/messages{api_q}",
            headers=headers,
            json={"role": "user", "content": question},
        )
        if r.status_code >= 400:
            return {"status": "error", "stage": "create_message",
                    "code": r.status_code, "error": r.text[:500]}

        # 4. Create run
        r = client.post(
            f"{base}/threads/{thread_id}/runs{api_q}",
            headers=headers,
            json={"assistant_id": assistant_id},
        )
        if r.status_code >= 400:
            return {"status": "error", "stage": "create_run",
                    "code": r.status_code, "error": r.text[:500]}
        run_id = r.json().get("id")

        # 5. Poll until terminal
        while True:
            if time.monotonic() > deadline:
                return {"status": "error", "stage": "poll",
                        "error": "Fabric data agent run timed out"}
            time.sleep(2.0)
            r = client.get(f"{base}/threads/{thread_id}/runs/{run_id}{api_q}", headers=headers)
            if r.status_code >= 400:
                return {"status": "error", "stage": "poll",
                        "code": r.status_code, "error": r.text[:500]}
            state = r.json().get("status")
            if state == "completed":
                break
            if state in ("failed", "cancelled", "expired", "requires_action"):
                return {"status": "error", "stage": "run_terminal",
                        "run_status": state, "error": str(r.json().get("last_error"))}

        # 6. Read messages, find the latest assistant message
        r = client.get(
            f"{base}/threads/{thread_id}/messages{api_q}&order=desc&limit=10",
            headers=headers,
        )
        if r.status_code >= 400:
            return {"status": "error", "stage": "read_messages",
                    "code": r.status_code, "error": r.text[:500]}
        msgs = r.json().get("data") or []
        answer = None
        for m in msgs:
            if m.get("role") != "assistant":
                continue
            content = m.get("content") or []
            chunks = []
            for c in content:
                if c.get("type") == "text":
                    chunks.append(c.get("text", {}).get("value") or "")
            if chunks:
                answer = "\n\n".join(chunks).strip()
                break

        # Best-effort cleanup so we don't leak threads/assistants per query.
        try:
            client.delete(f"{base}/threads/{thread_id}{api_q}", headers=headers)
            client.delete(f"{base}/assistants/{assistant_id}{api_q}", headers=headers)
        except Exception:  # noqa: BLE001
            pass

        return {
            "status": "success",
            "answer": answer or "(Fabric data agent returned no text response)",
            "workspace_id": ws,
            "artifact_id": art,
            "thread_id": thread_id,
            "run_id": run_id,
        }


@tool
def query_site_reliability(question: str) -> str:
    """Query the Fabric data agent for site reliability information.

    Use this tool to answer questions about:
      - Current health, telemetry, and anomalies at a specific data center site
        (e.g., "How is Quincy North performing?", "What's wrong at Singapore South?")
      - Open or recent incidents at a site (P1/P2/P3 with root cause)
      - Daily availability / SLO performance over time
      - Which subcontractor was dispatched for a given incident
      - Network latency p50/p95/p99 trends

    The data agent has access to per-minute optical / power / thermal /
    latency telemetry plus an incident log and daily SLO summary for every
    data center fleet site (Quincy North, East US DC-4/7/9,
    Singapore South/West, Sweden Central DC-1/2, Spain Central DC-1, etc.).

    Args:
        question: The natural-language question to ask. Be specific about
            site name or site_id when known.
    """
    try:
        result = _call_fabric_data_agent(question)
        return json.dumps(result, indent=2)
    except Exception as e:  # noqa: BLE001
        logger.exception("query_site_reliability failed")
        return json.dumps({"status": "error", "error": f"{type(e).__name__}: {e}"})
