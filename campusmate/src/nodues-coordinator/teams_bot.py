"""Teams bot activity handler for the No-Dues Coordinator.

Only active when Teams environment variables are configured.
"""
from __future__ import annotations

import os
from typing import Any


def is_teams_enabled() -> bool:
    """Return True when Teams integration is configured."""
    return bool(
        os.getenv("TEAMS_APP_ID")
        and os.getenv("TEAMS_APP_PASSWORD")
    )


async def handle_teams_activity(activity: dict[str, Any]) -> dict[str, Any] | None:
    """Process an incoming Teams activity and return a reply, or None to skip.

    This is a best-effort handler — errors are logged, not raised.
    """
    if not is_teams_enabled():
        return None

    activity_type = activity.get("type", "")

    if activity_type == "conversationUpdate":
        members_added = activity.get("membersAdded", [])
        bot_id = os.getenv("TEAMS_APP_ID", "")
        for member in members_added:
            if member.get("id", "") != bot_id:
                return {
                    "type": "message",
                    "text": (
                        "Hello! I am the No-Dues Coordinator for KIT. "
                        "I will notify you here when a student requires approval for a "
                        "no-dues certificate. You can also visit the approval portal to "
                        "review pending requests."
                    ),
                }

    if activity_type == "message":
        text = (activity.get("text") or "").strip().lower()
        if "help" in text:
            return {
                "type": "message",
                "text": (
                    "No-Dues Coordinator commands:\n"
                    "• Pending requests are automatically sent as cards when raised.\n"
                    "• Click the 'Open Approval Page' button in each card to approve or reject."
                ),
            }

    return None
