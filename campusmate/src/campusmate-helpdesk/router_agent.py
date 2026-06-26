"""CampusMate helpdesk — router agent.

Issues a single LLM call with tool_choice='required' to classify the
incoming student request into one of five meta-actions:
  respond_directly / start_task / check_task_status / cancel_task / get_task_result

The router sends an ~1-second acknowledgement to the student while the
worker agent runs in the background.
"""
from __future__ import annotations

import json
import os
from typing import Any

try:
    from agent_framework_foundry import FoundryChatClient  # type: ignore[import]
except ImportError:  # pragma: no cover
    FoundryChatClient = None  # type: ignore[assignment]

_ROUTER_SYSTEM_PROMPT = """You are the routing layer of CampusMate, the AI helpdesk
for Kovai Institute of Technology (KIT).

Your ONLY job is to decide HOW to handle the student's latest message — you do NOT
answer questions directly (unless the answer is trivially obvious from context).

Choose exactly one of the five meta-tools listed below:

• respond_directly   — the answer is already in the conversation or is a simple greeting
• start_task         — the request needs tools / research (schedule, library, faculty, etc.)
• check_task_status  — the student is asking about a previously started task
• cancel_task        — the student wants to cancel a running task
• get_task_result    — the student wants the final result of a completed task

When you pick start_task, set `ack_message` to a short, friendly acknowledgement
(≤ 15 words) so the student knows you are working on it.
Example: "Let me check that for you — just a moment!"
"""

_META_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "respond_directly",
            "description": (
                "Respond to the student directly without starting a background task. "
                "Use for greetings, clarifications, or when the answer is already known."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The reply to send to the student.",
                    }
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_task",
            "description": (
                "Start a background task to research the student's question using campus tools. "
                "Returns immediately with an acknowledgement while work continues."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "A concise description of what needs to be researched.",
                    },
                    "ack_message": {
                        "type": "string",
                        "description": (
                            "A short, friendly acknowledgement (≤ 15 words) "
                            "to send to the student immediately."
                        ),
                    },
                },
                "required": ["task_description", "ack_message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_task_status",
            "description": "Check the status of a previously started background task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The ID of the task to check.",
                    }
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_task",
            "description": "Cancel a running or pending background task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The ID of the task to cancel.",
                    }
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task_result",
            "description": "Retrieve the final result of a completed background task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The ID of the completed task.",
                    }
                },
                "required": ["task_id"],
            },
        },
    },
]


def route(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the router LLM call and return the parsed tool-call decision.

    Parameters
    ----------
    messages:
        The conversation history to classify.

    Returns
    -------
    dict with keys:
        action  — name of the chosen meta-tool
        args    — dict of arguments from the tool call
    """
    if FoundryChatClient is None:
        raise RuntimeError(
            "agent_framework_foundry is not installed. "
            "Run: pip install agent-framework-foundry"
        )

    model = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-5")
    client = FoundryChatClient(model=model)

    full_messages = [{"role": "system", "content": _ROUTER_SYSTEM_PROMPT}] + messages

    response = client.chat.completions.create(  # type: ignore[union-attr]
        model=model,
        messages=full_messages,
        tools=_META_TOOLS,
        tool_choice="required",
    )

    choice = response.choices[0]
    tool_call = choice.message.tool_calls[0]
    action = tool_call.function.name
    args: dict[str, Any] = json.loads(tool_call.function.arguments or "{}")

    return {"action": action, "args": args}
