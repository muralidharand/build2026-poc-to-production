"""Durable, identity-keyed conversation store for proactive Teams messaging.

The heartbeat routine runs in a *different* session than the operator's reactive
Teams chat, so the mapping from the operator's stable identity (UPN / AAD object id)
to the facts needed to reach them on Teams (serviceUrl, tenantId, bot id, conversationId)
must live in a **shared, durable** store — never session-local ``$HOME``.

Backends (selected by environment):
  * Azure Blob (durable, cross-session) — set ``TEAMS_STATE_BLOB_URL`` to a blob
    container URL; auth via ``DefaultAzureCredential`` / the agent identity.
  * Local JSON file (dev/single-session only) — fallback under ``$HOME``.

Records are keyed by lower-cased UPN. ``get_approver`` returns the record for
``APPROVER_UPN`` or the most recently updated.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

APPROVER_UPN = os.getenv("APPROVER_UPN", "operator@example.com").lower()
_BLOB_URL = os.getenv("TEAMS_STATE_BLOB_URL", "").rstrip("/")
_BLOB_NAME = os.getenv("TEAMS_STATE_BLOB_NAME", "teams-conversations.json")
_FILE_PATH = pathlib.Path(
    os.getenv("TEAMS_STATE_FILE", str(pathlib.Path(os.environ.get("HOME", "/tmp")) / "teams_state" / "conversations.json"))
)


@dataclass
class ConversationRef:
    """The minimal stable facts needed to address a user proactively on Teams."""

    aad_object_id: str
    upn: str
    tenant_id: str
    service_url: str
    bot_id: str
    channel_id: str = "msteams"
    conversation_id: Optional[str] = None
    user_name: Optional[str] = None
    updated_at: str = ""

    def key(self) -> str:
        return (self.upn or self.aad_object_id or "").lower()


class _Backend:
    def load(self) -> dict[str, dict]:  # pragma: no cover - interface
        raise NotImplementedError

    def save(self, data: dict[str, dict]) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def save_named(self, name: str, content: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class _FileBackend(_Backend):
    def __init__(self, path: pathlib.Path):
        self._path = path

    def load(self) -> dict[str, dict]:
        try:
            return json.loads(self._path.read_text())
        except FileNotFoundError:
            return {}
        except Exception as e:  # corrupt file — start fresh but keep a backup
            logger.warning("teams_state: failed to read %s (%s); resetting", self._path, e)
            return {}

    def save(self, data: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._path)

    def save_named(self, name: str, content: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        (self._path.parent / name).write_text(content)


class _BlobBackend(_Backend):
    def __init__(self, container_url: str, blob_name: str):
        from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
        from azure.storage.blob import ContainerClient

        client_id = os.getenv("AZURE_CLIENT_ID")
        cred = ManagedIdentityCredential(client_id=client_id) if client_id else DefaultAzureCredential()
        self._container = ContainerClient.from_container_url(container_url, credential=cred)
        self._blob_name = blob_name
        try:
            self._container.create_container()
        except Exception:
            pass  # already exists / no permission to create — assume it exists

    def load(self) -> dict[str, dict]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            blob = self._container.download_blob(self._blob_name)
            return json.loads(blob.readall())
        except ResourceNotFoundError:
            return {}
        except Exception as e:
            logger.warning("teams_state: blob read failed (%s); treating as empty", e)
            return {}

    def save(self, data: dict[str, dict]) -> None:
        self._container.upload_blob(self._blob_name, json.dumps(data, indent=2), overwrite=True)

    def save_named(self, name: str, content: str) -> None:
        self._container.upload_blob(name, content, overwrite=True)


class ConversationStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backend = self._make_backend()

    @staticmethod
    def _make_backend() -> _Backend:
        if _BLOB_URL:
            try:
                logger.info("teams_state: using Azure Blob backend at %s", _BLOB_URL)
                return _BlobBackend(_BLOB_URL, _BLOB_NAME)
            except Exception as e:
                logger.error("teams_state: blob backend init failed (%s); falling back to file", e)
        logger.info("teams_state: using local file backend at %s (NOT cross-session in production)", _FILE_PATH)
        return _FileBackend(_FILE_PATH)

    def upsert(self, ref: ConversationRef) -> None:
        ref.updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            data = self._backend.load()
            existing = data.get(ref.key(), {})
            # Preserve a previously-captured conversation_id if the new ref lacks one.
            if not ref.conversation_id and existing.get("conversation_id"):
                ref.conversation_id = existing["conversation_id"]
            data[ref.key()] = asdict(ref)
            self._backend.save(data)
        logger.info("teams_state: upserted conversation ref for %s", ref.key())

    def get(self, key: str) -> Optional[ConversationRef]:
        with self._lock:
            data = self._backend.load()
        rec = data.get((key or "").lower())
        return ConversationRef(**rec) if rec else None

    def get_approver(self) -> Optional[ConversationRef]:
        """Return Jeff's record (APPROVER_UPN), else the most recently updated record."""
        with self._lock:
            data = self._backend.load()
        if not data:
            return None
        rec = data.get(APPROVER_UPN)
        if rec is None:
            rec = max(data.values(), key=lambda r: r.get("updated_at", ""))
        return ConversationRef(**rec)

    def debug_capture(self, activity: dict) -> None:
        """Persist the most recent raw inbound activity for diagnostics.

        Lets us confirm the channel's ``deliveryMode``/``serviceUrl``/``recipient``
        from outside the container (the container has no externally-readable stdout).
        """
        try:
            content = json.dumps(
                {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "build_tag": "v37-inbound-test",
                    "activity": activity,
                },
                indent=2,
            )
            with self._lock:
                self._backend.save_named("last-activity.json", content)
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("teams_state: debug_capture failed (%s)", e)


# Module-level singleton
store = ConversationStore()
