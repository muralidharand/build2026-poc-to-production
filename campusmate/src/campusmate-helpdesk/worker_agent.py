"""CampusMate helpdesk — worker agent.

Handles long-running student queries using KIT campus data and tools.
"""
from __future__ import annotations

import json
import os

# agent_framework imports — degrade gracefully when not installed.
try:
    from agent_framework import tool  # type: ignore[import]
    from agent_framework.config import load_config  # type: ignore[import]
    from agent_framework_foundry import FoundryChatClient  # type: ignore[import]
except ImportError:  # pragma: no cover
    def tool(fn):  # type: ignore[misc]
        return fn
    load_config = None  # type: ignore[assignment]
    FoundryChatClient = None  # type: ignore[assignment]

from procedural_memory import recall_learned_procedures, render_procedures_block
from toolbox import build_toolbox_tool

# ---------------------------------------------------------------------------
# KIT sample data
# ---------------------------------------------------------------------------

SCHEDULE = {
    ("CSE", "monday"): [
        {"time": "09:00", "subject": "Data Structures", "room": "B-204"},
        {"time": "11:00", "subject": "DBMS Lab", "room": "Lab-3"},
    ],
    ("CSE", "tuesday"): [
        {"time": "10:00", "subject": "Operating Systems", "room": "B-201"},
        {"time": "14:00", "subject": "DBMS", "room": "B-204"},
    ],
    ("CSE", "wednesday"): [
        {"time": "09:00", "subject": "Computer Networks", "room": "B-205"},
    ],
    "default": [{"time": "—", "subject": "No classes scheduled", "room": "—"}],
}

LIBRARY = {
    "operating system concepts": {
        "author": "Galvin",
        "copies_available": 2,
        "shelf": "CS-14",
    },
    "introduction to algorithms": {
        "author": "Cormen",
        "copies_available": 0,
        "shelf": "CS-09",
    },
    "database system concepts": {
        "author": "Silberschatz",
        "copies_available": 5,
        "shelf": "CS-11",
    },
}

FACULTY = {
    "data structures": {
        "name": "Dr. Anitha R",
        "cabin": "B-3-12",
        "hours": "Wed 2-4 PM",
    },
    "operating systems": {
        "name": "Prof. Karthik S",
        "cabin": "B-2-07",
        "hours": "Thu 11-1 PM",
    },
    "dbms": {
        "name": "Dr. Priya M",
        "cabin": "B-3-21",
        "hours": "Fri 3-5 PM",
    },
}

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


@tool
def get_class_schedule(dept: str, day: str) -> str:
    """Look up the class and lab schedule for a department on a given weekday."""
    rows = SCHEDULE.get((dept.upper(), day.lower()), SCHEDULE["default"])
    return json.dumps(
        {"status": "success", "dept": dept, "day": day, "classes": rows},
        indent=2,
    )


@tool
def search_library_book(title: str) -> str:
    """Check whether a library book is available and where to find it."""
    hit = LIBRARY.get(title.lower().strip())
    if not hit:
        return json.dumps(
            {
                "status": "not_found",
                "title": title,
                "hint": "Try the web search tool.",
            }
        )
    return json.dumps({"status": "success", "title": title, **hit}, indent=2)


@tool
def find_faculty(subject: str) -> str:
    """Find the faculty member for a subject, with cabin and office hours."""
    hit = FACULTY.get(subject.lower().strip())
    return json.dumps(
        {
            "status": "success" if hit else "not_found",
            "subject": subject,
            **(hit or {}),
        },
        indent=2,
    )


LOCAL_TOOLS = [get_class_schedule, search_library_book, find_faculty, recall_learned_procedures]

_SYSTEM_PROMPT = """You are CampusMate, the AI assistant for Kovai Institute of Technology (KIT).
You help students with:
- Class and lab schedules
- Library book availability
- Faculty contact details and office hours
- General campus information

Be friendly, concise, and accurate. Always use the available tools to look up
real data before answering. If you cannot find the information, say so honestly
and suggest who the student might contact.

If asked "what have you learned?" or similar, call `recall_learned_procedures`
to retrieve any standing guidelines stored in memory.
"""


def create_worker_agent():
    """Build and return a MAF worker agent configured for CampusMate helpdesk."""
    if FoundryChatClient is None:
        raise RuntimeError(
            "agent_framework_foundry is not installed. "
            "Run: pip install agent-framework-foundry"
        )

    # Resolve tools
    tools = list(LOCAL_TOOLS)
    toolbox = build_toolbox_tool()
    if toolbox is not None:
        tools.append(toolbox)

    # Apply config overrides (supports `azd ai agent optimize`)
    instructions = _SYSTEM_PROMPT
    if load_config is not None:
        try:
            config = load_config()
            if config is not None:
                config.apply_tool_descriptions(LOCAL_TOOLS)
                if hasattr(config, "instructions") and config.instructions:
                    instructions = config.instructions
        except Exception as exc:
            print(f"[worker_agent] load_config skipped: {exc}")

    # Prepend procedural memory block
    try:
        proc_block = render_procedures_block()
        if proc_block:
            instructions = proc_block + "\n\n" + instructions
    except Exception as exc:
        print(f"[worker_agent] procedural memory skipped: {exc}")

    model = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-5")

    return FoundryChatClient(model=model).as_agent(
        name="campusmate-helpdesk",
        instructions=instructions,
        tools=tools,
    )
