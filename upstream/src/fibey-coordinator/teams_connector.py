"""Bot Framework Connector client for the Teams Activity Protocol passthrough.

Responsibilities:
  * Mint a Bot Framework token for the **agent's own identity** (Entra Agent ID)
    — audience ``https://api.botframework.com`` — so the agent can call the Bot
    Connector. No separate bot app secret is used.
  * Send / reply activities, and create a 1:1 conversation on demand
    (``POST /v3/conversations``) from stable identity facts so proactive sends
    work from a fresh heartbeat session.
  * Teams **streaming UX** helpers (informative updates → token chunks → final),
    plus the AI-generated label, feedback buttons, and inline citations.

References: Teams "Stream bot messages" + "Bot messages with AI-generated content".
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Optional

import aiohttp

from teams_state import ConversationRef

logger = logging.getLogger(__name__)

BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"

_SCHEMA_ORG_MESSAGE = {
    "type": "https://schema.org/Message",
    "@type": "Message",
    "@context": "https://schema.org",
}


def _ai_label_entity(citations: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """schema.org Message entity that drives the Teams 'AI generated' label + citations."""
    entity = dict(_SCHEMA_ORG_MESSAGE)
    entity["additionalType"] = ["AIGeneratedContent"]
    if citations:
        entity["citation"] = citations
    return entity


def build_citations(sources: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Build Teams inline citation entries from (name, text/url) tuples."""
    citations = []
    for i, (name, content) in enumerate(sources, start=1):
        citations.append(
            {
                "@type": "Claim",
                "position": i,
                "appearance": {
                    "@type": "DigitalDocument",
                    "name": name,
                    "abstract": content[:480],
                },
            }
        )
    return citations


class _TokenCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._expires_on: float = 0.0
        self._credential = None

    def _cred(self):
        if self._credential is None:
            from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

            client_id = os.getenv("AZURE_CLIENT_ID")
            self._credential = (
                ManagedIdentityCredential(client_id=client_id)
                if client_id
                else DefaultAzureCredential()
            )
        return self._credential

    def get(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_on - 120:
                return self._token
            tok = self._cred().get_token(BOTFRAMEWORK_SCOPE)
            self._token = tok.token
            self._expires_on = float(tok.expires_on)
            return self._token


_token_cache = _TokenCache()


class TeamsConnector:
    """Thin async client over the Bot Connector REST API."""

    async def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {_token_cache.get()}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, headers=headers) as resp:
                text = await resp.text()
                if resp.status >= 300:
                    logger.error("Bot Connector POST %s failed %s: %s", url, resp.status, text)
                    resp.raise_for_status()
                try:
                    return await resp.json() if text else {}
                except Exception:
                    return {}

    async def send_activity(
        self,
        service_url: str,
        conversation_id: str,
        activity: dict[str, Any],
        reply_to_activity_id: Optional[str] = None,
    ) -> dict[str, Any]:
        base = service_url.rstrip("/")
        if reply_to_activity_id:
            url = f"{base}/v3/conversations/{conversation_id}/activities/{reply_to_activity_id}"
        else:
            url = f"{base}/v3/conversations/{conversation_id}/activities"
        return await self._post(url, activity)

    async def create_conversation(self, ref: ConversationRef) -> str:
        """Create (or get) a 1:1 conversation with the user; returns conversationId."""
        url = f"{ref.service_url.rstrip('/')}/v3/conversations"
        body = {
            "isGroup": False,
            "bot": {"id": ref.bot_id},
            "members": [{"id": ref.aad_object_id}],
            "tenantId": ref.tenant_id,
            "channelData": {"tenant": {"id": ref.tenant_id}},
        }
        result = await self._post(url, body)
        conv_id = result.get("id") or ref.conversation_id
        if not conv_id:
            raise RuntimeError("createConversation did not return a conversation id")
        return conv_id

    # ---- message builders -------------------------------------------------

    @staticmethod
    def message_activity(
        text: str,
        *,
        ai_generated: bool = True,
        feedback: bool = True,
        citations: Optional[list[dict[str, Any]]] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        activity: dict[str, Any] = {"type": "message", "text": text}
        entities = []
        if ai_generated:
            entities.append(_ai_label_entity(citations))
        if entities:
            activity["entities"] = entities
        if feedback:
            activity["channelData"] = {"feedbackLoop": {"type": "default"}}
        if attachments:
            activity["attachments"] = attachments
        return activity


class StreamingTurn:
    """Drives a single Teams streaming response: informative → chunks → final.

    Teams requires increasing ``streamSequence`` and that the same ``streamId``
    (returned by the first streamed activity) is echoed on subsequent ones.
    Falls back to a single final message if the streaming channel errors out.
    """

    def __init__(self, connector: TeamsConnector, service_url: str, conversation_id: str):
        self._c = connector
        self._service_url = service_url
        self._conversation_id = conversation_id
        self._stream_id: Optional[str] = None
        self._seq = 0
        self._broken = False
        self._last_sent = 0.0

    async def _send_stream(self, stream_type: str, text: Optional[str]) -> None:
        if self._broken:
            return
        self._seq += 1
        entity: dict[str, Any] = {"type": "streaminfo", "streamType": stream_type, "streamSequence": self._seq}
        if self._stream_id:
            entity["streamId"] = self._stream_id
        activity: dict[str, Any] = {"type": "typing", "entities": [entity]}
        if text:
            activity["text"] = text
        if self._stream_id:
            activity["channelData"] = {"streamId": self._stream_id}
        try:
            result = await self._c.send_activity(self._service_url, self._conversation_id, activity)
            if not self._stream_id:
                self._stream_id = result.get("id")
            self._last_sent = time.time()
        except Exception as e:
            logger.warning("streaming send failed (%s); will fall back to final message", e)
            self._broken = True

    async def informative(self, text: str) -> None:
        """Blue progress bar update (must stay under ~1KB)."""
        await self._send_stream("informative", text[:1000])

    async def chunk(self, text: str) -> None:
        """Append a streamed token chunk (text is the full content so far)."""
        # Light client-side throttle: Teams recommends ~1 update/sec.
        if not self._broken and (time.time() - self._last_sent) < 0.8:
            return
        await self._send_stream("streaming", text)

    async def final(
        self,
        text: str,
        *,
        citations: Optional[list[dict[str, Any]]] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Send the final message (AI label + feedback + optional citations)."""
        activity = TeamsConnector.message_activity(
            text, ai_generated=True, feedback=True, citations=citations, attachments=attachments
        )
        if self._stream_id and not self._broken:
            activity.setdefault("entities", []).append(
                {"type": "streaminfo", "streamType": "final", "streamId": self._stream_id}
            )
            activity.setdefault("channelData", {})["streamId"] = self._stream_id
        return await self._c.send_activity(self._service_url, self._conversation_id, activity)


connector = TeamsConnector()
