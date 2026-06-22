"""Teams Activity Protocol passthrough handler for fibey-coordinator.

Serves ``POST /api/messages`` (mounted on the same agentserver port) to receive
raw Bot Framework activities forwarded by the Foundry activity-protocol endpoint.

Reactive path: inbound message → MAF agent (streamed) → rich Teams reply
(informative updates → token streaming → AI label + feedback + citations).

Approval path: the proactive approval card's Approve/Reject submit →
``durable_manager.approve``/``reject`` → decision card back in Teams.

Proactive path: ``send_approval_card`` (called by the ``request_approval`` tool
during the heartbeat) looks Jeff up in the shared identity store, (re)creates the
1:1 conversation if needed, and posts the approval adaptive card.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import teams_cards
from teams_connector import StreamingTurn, build_citations, connector
from teams_state import ConversationRef, store

logger = logging.getLogger("fibey-coordinator.teams")

# Friendly informative-update text per tool, for the Teams "blue bar".
_TOOL_STATUS = {
    "check_network_telemetry": "Checking network telemetry…",
    "get_active_incidents": "Pulling incident history…",
    "save_investigation": "Saving investigation dossier…",
    "request_approval": "Requesting approval…",
    "dispatch_work_order": "Dispatching work order…",
    "escalate_incident": "Escalating incident…",
}

# Lazily injected by main.py to avoid an import cycle.
_agent_getter = None
_durable_manager = None


def configure(*, agent_getter, durable_manager) -> None:
    global _agent_getter, _durable_manager
    _agent_getter = agent_getter
    _durable_manager = durable_manager


# ── inbound activity helpers ─────────────────────────────────────────────────


def _ref_from_activity(activity: dict[str, Any]) -> Optional[ConversationRef]:
    """Build a durable ConversationRef from an inbound Teams activity."""
    from_obj = activity.get("from") or {}
    conv = activity.get("conversation") or {}
    recipient = activity.get("recipient") or {}
    service_url = activity.get("serviceUrl") or ""
    channel_data = activity.get("channelData") or {}
    tenant_id = (
        (channel_data.get("tenant") or {}).get("id")
        or (conv.get("tenantId"))
        or os.getenv("AZURE_TENANT_ID", "")
    )
    aad_id = from_obj.get("aadObjectId") or from_obj.get("id") or ""
    upn = (
        from_obj.get("userPrincipalName")
        or _aad_upn_from_activity(activity)
        or os.getenv("APPROVER_UPN", "")
    )
    if not service_url or not aad_id:
        return None
    return ConversationRef(
        aad_object_id=aad_id,
        upn=upn,
        tenant_id=tenant_id,
        service_url=service_url,
        bot_id=recipient.get("id") or "",
        channel_id=activity.get("channelId") or "msteams",
        conversation_id=conv.get("id"),
        user_name=from_obj.get("name"),
    )


def _aad_upn_from_activity(activity: dict[str, Any]) -> Optional[str]:
    # Teams sometimes surfaces UPN/email under channelData or from.properties.
    cd = activity.get("channelData") or {}
    return cd.get("userPrincipalName") or cd.get("email")


# ── reactive chat ─────────────────────────────────────────────────────────────


async def _run_agent_streamed(text: str, turn: StreamingTurn) -> str:
    agent = await _agent_getter()
    accumulated = ""
    announced: set[str] = set()
    citations_seen: list[tuple[str, str]] = []

    await turn.informative("Fibey is on it…")

    try:
        stream = agent.run(messages=text, stream=True)
        async for update in stream:
            for content in getattr(update, "contents", []) or []:
                ctype = getattr(content, "type", None)
                if ctype == "function_call":
                    name = getattr(content, "name", None)
                    if name and name not in announced:
                        announced.add(name)
                        await turn.informative(_TOOL_STATUS.get(name, f"Running {name}…"))
                elif ctype == "function_result":
                    name = getattr(content, "name", None) or "result"
                    res = getattr(content, "result", None)
                    if res is not None and name in ("get_active_incidents", "save_investigation"):
                        citations_seen.append((name, str(res)))
            delta = getattr(update, "text", "") or ""
            if delta:
                accumulated += delta
                await turn.chunk(accumulated)
    except Exception as e:
        logger.error("agent streaming failed: %s", e, exc_info=True)
        if not accumulated:
            accumulated = f"I hit an error processing that: {e}"

    if not accumulated.strip():
        accumulated = "(Fibey completed without a text response.)"

    citations = build_citations(citations_seen[:5]) if citations_seen else None
    await turn.final(accumulated, citations=citations)
    return accumulated


async def _run_agent_collect(text: str) -> tuple[str, Optional[list[dict[str, Any]]]]:
    """Run the agent non-streamed and return (reply_text, citations).

    Used for the ``expectReplies`` inline-response path, where we cannot stream
    typing indicators to the Bot Connector and instead return the final reply in
    the HTTP response body.
    """
    agent = await _agent_getter()
    accumulated = ""
    citations_seen: list[tuple[str, str]] = []
    try:
        result = await agent.run(messages=text, stream=False)
        accumulated = str(result.message) if hasattr(result, "message") else str(result)
        try:
            for msg in getattr(result, "messages", []) or []:
                for content in getattr(msg, "contents", []) or []:
                    if getattr(content, "type", None) == "function_result":
                        name = getattr(content, "name", None) or ""
                        res = getattr(content, "result", None)
                        if res is not None and name in ("get_active_incidents", "save_investigation"):
                            citations_seen.append((name, str(res)))
        except Exception:  # pragma: no cover - citation extraction is best-effort
            pass
    except Exception as e:
        logger.error("agent run failed: %s", e, exc_info=True)
        if not accumulated:
            accumulated = f"I hit an error processing that: {e}"

    if not accumulated.strip():
        accumulated = "(Fibey completed without a text response.)"

    citations = build_citations(citations_seen[:5]) if citations_seen else None
    return accumulated, citations


async def _handle_message_activity(
    activity: dict[str, Any], expect_replies: bool = False
) -> list[dict[str, Any]]:
    """Handle an inbound message; return reply activities for ``expectReplies``.

    For ``expectReplies`` (the activity-protocol passthrough contract), the reply
    activities are returned to the caller (Vienna → channel) in the HTTP response
    body — no outbound Bot Connector token is required. Otherwise we fall back to
    the out-of-band streaming path (which needs an agentic Bot Framework token).
    """
    ref = _ref_from_activity(activity)
    if ref:
        store.upsert(ref)

    value = activity.get("value")
    if isinstance(value, dict) and value.get("kind") == "approval_decision":
        return await _handle_approval_decision(activity, ref, value, expect_replies)

    text = (activity.get("text") or "").strip()
    if not text:
        return []

    if expect_replies:
        reply_text, citations = await _run_agent_collect(text)
        msg = connector.message_activity(
            reply_text, ai_generated=True, feedback=True, citations=citations
        )
        return [msg, {"type": "endOfConversation"}]

    service_url = activity.get("serviceUrl") or (ref.service_url if ref else "")
    conv_id = (activity.get("conversation") or {}).get("id") or (ref.conversation_id if ref else "")
    if not service_url or not conv_id:
        logger.warning("message activity missing serviceUrl/conversation; cannot reply")
        return []

    turn = StreamingTurn(connector, service_url, conv_id)
    await _run_agent_streamed(text, turn)
    return []


async def _handle_approval_decision(
    activity: dict[str, Any],
    ref: Optional[ConversationRef],
    value: dict[str, Any],
    expect_replies: bool = False,
) -> list[dict[str, Any]]:
    approval_id = value.get("approval_id") or ""
    decision = value.get("decision") or "approve"
    approver = (ref.upn if ref and ref.upn else None) or (
        (activity.get("from") or {}).get("name")
    ) or os.getenv("APPROVER_UPN", "operator")
    approved = decision == "approve"

    if _durable_manager and approval_id:
        try:
            if approved:
                _durable_manager.approve(approval_id, approver=approver, feedback="Approved via Teams")
            else:
                _durable_manager.reject(approval_id, approver=approver, feedback="Rejected via Teams")
        except Exception as e:
            logger.error("durable approve/reject failed for %s: %s", approval_id, e)

    card = teams_cards.build_decision_result_card(
        action=value.get("action") or "Field work order dispatch",
        approval_id=approval_id,
        approved=approved,
        approver=approver,
    )
    msg = connector.message_activity(
        "Approved — dispatching now. ✅" if approved else "Rejected — standing down. 🛑",
        ai_generated=False,
        feedback=False,
        attachments=[teams_cards.as_attachment(card)],
    )

    if expect_replies:
        return [msg, {"type": "endOfConversation"}]

    service_url = activity.get("serviceUrl") or (ref.service_url if ref else "")
    conv_id = (activity.get("conversation") or {}).get("id") or (ref.conversation_id if ref else "")
    if service_url and conv_id:
        try:
            await connector.send_activity(service_url, conv_id, msg)
        except Exception as e:
            logger.error("failed to send decision card: %s", e)
    return []


# ── Starlette route ───────────────────────────────────────────────────────────


async def handle_messages(request: Request) -> Response:
    """POST /api/messages — receive a forwarded Bot Framework activity.

    Single-user demo: auth is relaxed (the platform validates the caller before
    forwarding). For ``deliveryMode == expectReplies`` (the activity-protocol
    passthrough contract) we return the reply activities inline in the HTTP
    response body — no outbound Bot Connector token is needed. Otherwise we
    acknowledge fast and reply out-of-band.
    """
    try:
        activity = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid activity json"}, status_code=400)

    # Capture the raw inbound activity for diagnostics (deliveryMode/serviceUrl/…).
    store.debug_capture(activity)

    atype = activity.get("type")
    expect_replies = activity.get("deliveryMode") == "expectReplies"

    try:
        if atype == "message":
            replies = await _handle_message_activity(activity, expect_replies)
            if expect_replies:
                return JSONResponse({"activities": replies})
        elif atype in ("conversationUpdate", "installationUpdate"):
            ref = _ref_from_activity(activity)
            if ref:
                store.upsert(ref)
        # Other activity types (typing, etc.) are simply acknowledged.
    except Exception as e:
        logger.error("error handling activity (%s): %s", atype, e, exc_info=True)
        if expect_replies:
            return JSONResponse(
                {
                    "activities": [
                        {"type": "message", "text": f"I hit an error: {e}"},
                        {"type": "endOfConversation"},
                    ]
                }
            )

    return Response(status_code=200)


# ── proactive approval card (used by the request_approval tool) ───────────────


async def send_approval_card(
    *, action: str, description: str, severity: str, approval_id: str
) -> bool:
    """Proactively post the approval card to Jeff's 1:1 Teams chat.

    Looks Jeff up in the shared identity store, (re)creates the conversation via
    the Bot Connector if needed, then sends the adaptive card. Returns success.
    """
    ref = store.get_approver()
    if ref is None:
        logger.warning(
            "send_approval_card: no stored conversation ref — Jeff must DM the agent "
            "once (pre-show 'hi') to seed the store."
        )
        return False

    conv_id = ref.conversation_id
    try:
        if not conv_id:
            conv_id = await connector.create_conversation(ref)
            ref.conversation_id = conv_id
            store.upsert(ref)
    except Exception as e:
        logger.error("send_approval_card: createConversation failed: %s", e)
        return False

    card = teams_cards.build_approval_card(
        action=action, description=description, severity=severity, approval_id=approval_id
    )
    msg = connector.message_activity(
        f"⚠️ Approval required: {action}",
        ai_generated=False,
        feedback=False,
        attachments=[teams_cards.as_attachment(card)],
    )
    try:
        await connector.send_activity(ref.service_url, conv_id, msg)
        logger.info("send_approval_card: posted approval %s to %s", approval_id, ref.key())
        return True
    except Exception as e:
        logger.error("send_approval_card: send failed: %s", e)
        return False


def send_approval_card_sync(
    *, action: str, description: str, severity: str, approval_id: str
) -> bool:
    """Blocking wrapper so the synchronous ``request_approval`` tool can fire the card.

    Runs the coroutine on a dedicated thread/loop to avoid interfering with any
    event loop already running in the calling (tool-invocation) context.
    """
    result: dict[str, bool] = {"ok": False}

    def _runner() -> None:
        try:
            result["ok"] = asyncio.run(
                send_approval_card(
                    action=action,
                    description=description,
                    severity=severity,
                    approval_id=approval_id,
                )
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.error("send_approval_card_sync runner failed: %s", e)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=20)
    return result["ok"]
