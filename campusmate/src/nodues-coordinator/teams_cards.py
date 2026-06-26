"""Adaptive Card builders for the No-Dues Coordinator Teams integration."""
from __future__ import annotations

from typing import Any


def build_approval_card(
    approval_id: str,
    approval_url: str,
    action: str,
    description: str,
    student_name: str = "",
    student_id: str = "",
    severity: str = "critical",
    context: str = "",
) -> dict[str, Any]:
    """Build an Adaptive Card for a pending no-dues approval request."""
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "size": "Large",
            "weight": "Bolder",
            "text": "No-Dues Approval Request",
            "color": "Accent",
        },
        {
            "type": "FactSet",
            "facts": [
                {"title": "Approval ID", "value": approval_id},
                {"title": "Action", "value": action},
                {"title": "Severity", "value": severity.upper()},
                *([{"title": "Student", "value": f"{student_name} ({student_id})"}]
                  if student_name else []),
            ],
        },
        {
            "type": "TextBlock",
            "text": description,
            "wrap": True,
        },
    ]

    if context:
        body.append({
            "type": "TextBlock",
            "text": f"**Context:** {context}",
            "wrap": True,
            "isSubtle": True,
        })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": body,
        "actions": [
            {
                "type": "Action.OpenUrl",
                "title": "Open Approval Page",
                "url": approval_url,
                "style": "positive",
            },
        ],
    }


def build_result_card(
    action: str,
    result: str,
    student_id: str = "",
    student_name: str = "",
) -> dict[str, Any]:
    """Build an Adaptive Card showing the outcome of a no-dues action."""
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {
                "type": "TextBlock",
                "size": "Medium",
                "weight": "Bolder",
                "text": "No-Dues Update",
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Action", "value": action},
                    *([{"title": "Student", "value": f"{student_name} ({student_id})"}]
                      if student_name else []),
                    {"title": "Result", "value": result},
                ],
            },
        ],
    }
