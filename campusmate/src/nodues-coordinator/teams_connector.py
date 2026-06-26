"""Teams connector — sends proactive messages and Adaptive Cards to approvers.

Only active when TEAMS_APP_ID and TEAMS_APP_PASSWORD are set.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests


def _teams_configured() -> bool:
    return bool(
        os.getenv("TEAMS_APP_ID")
        and os.getenv("TEAMS_APP_PASSWORD")
        and os.getenv("TEAMS_TENANT_ID")
    )


def _get_token() -> str | None:
    tenant_id = os.getenv("TEAMS_TENANT_ID", "")
    app_id = os.getenv("TEAMS_APP_ID", "")
    app_password = os.getenv("TEAMS_APP_PASSWORD", "")
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    try:
        resp = requests.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": app_id,
                "client_secret": app_password,
                "scope": "https://api.botframework.com/.default",
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as exc:
        print(f"[teams_connector] Token fetch failed: {exc}")
        return None


def send_approval_card(
    approval_id: str,
    approval_url: str,
    action: str,
    description: str,
    card: dict[str, Any],
) -> bool:
    """Send an Adaptive Card to the configured approver via Teams.

    Returns True if the card was sent successfully, False otherwise.
    """
    if not _teams_configured():
        print("[teams_connector] Teams not configured — skipping card send.")
        return False

    service_url = os.getenv("TEAMS_SERVICE_URL", "https://smba.trafficmanager.net/apis/")
    conversation_id = os.getenv("TEAMS_APPROVER_CONVERSATION_ID", "")
    if not conversation_id:
        print("[teams_connector] TEAMS_APPROVER_CONVERSATION_ID not set — skipping.")
        return False

    token = _get_token()
    if not token:
        return False

    message = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }
    url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
    try:
        resp = requests.post(
            url,
            json=message,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        print(f"[teams_connector] Card sent for approval {approval_id}.")
        return True
    except Exception as exc:
        print(f"[teams_connector] Card send failed: {exc}")
        return False
