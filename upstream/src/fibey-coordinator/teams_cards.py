"""Adaptive Card builders for the Teams (Activity Protocol) experience.

These cards render natively in Teams. The approval card carries the Durable Task
orchestration ``instance_id`` in the Action.Submit ``data`` so that clicking
Approve/Reject posts back an activity the bot maps to ``durable_manager.approve``.
"""
from __future__ import annotations

from typing import Any

ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"

_SEVERITY_COLOR = {
    "critical": "attention",
    "warning": "warning",
    "info": "accent",
}


def _card(body: list[dict[str, Any]], actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": body,
    }
    if actions:
        card["actions"] = actions
    return card


def as_attachment(card: dict[str, Any]) -> dict[str, Any]:
    """Wrap a raw Adaptive Card in a Bot Framework attachment."""
    return {"contentType": ADAPTIVE_CARD_CONTENT_TYPE, "content": card}


def build_approval_card(
    *,
    action: str,
    description: str,
    severity: str,
    approval_id: str,
    site: str = "Quincy North DC",
) -> dict[str, Any]:
    """Approval request card with Approve / Reject buttons.

    The buttons submit ``{"kind": "approval_decision", "approval_id": ..., "decision": ...}``.
    """
    color = _SEVERITY_COLOR.get(severity.lower(), "accent")
    body = [
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "auto",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "⚠️",
                            "size": "ExtraLarge",
                        }
                    ],
                },
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "Approval required",
                            "weight": "Bolder",
                            "size": "Large",
                            "color": color,
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": f"Fibey · Network Operations · {site}",
                            "isSubtle": True,
                            "spacing": "None",
                            "wrap": True,
                        },
                    ],
                },
            ],
        },
        {
            "type": "TextBlock",
            "text": action,
            "weight": "Bolder",
            "wrap": True,
            "spacing": "Medium",
        },
        {
            "type": "TextBlock",
            "text": description,
            "wrap": True,
        },
        {
            "type": "FactSet",
            "facts": [
                {"title": "Severity", "value": severity.capitalize()},
                {"title": "Approval ID", "value": approval_id},
            ],
        },
    ]
    actions = [
        {
            "type": "Action.Submit",
            "title": "✅ Approve",
            "style": "positive",
            "data": {
                "kind": "approval_decision",
                "approval_id": approval_id,
                "decision": "approve",
            },
        },
        {
            "type": "Action.Submit",
            "title": "🛑 Reject",
            "style": "destructive",
            "data": {
                "kind": "approval_decision",
                "approval_id": approval_id,
                "decision": "reject",
            },
        },
    ]
    return _card(body, actions)


def build_decision_result_card(
    *,
    action: str,
    approval_id: str,
    approved: bool,
    approver: str,
) -> dict[str, Any]:
    """Card shown after a decision is made (replaces the approval card)."""
    if approved:
        headline, color, icon = "Approved", "good", "✅"
        detail = "Dispatch is proceeding."
    else:
        headline, color, icon = "Rejected", "attention", "🛑"
        detail = "No action will be taken."
    body = [
        {
            "type": "TextBlock",
            "text": f"{icon} {headline} by {approver}",
            "weight": "Bolder",
            "size": "Large",
            "color": color,
            "wrap": True,
        },
        {"type": "TextBlock", "text": action, "wrap": True},
        {"type": "TextBlock", "text": detail, "isSubtle": True, "wrap": True},
        {
            "type": "FactSet",
            "facts": [{"title": "Approval ID", "value": approval_id}],
        },
    ]
    return _card(body)
