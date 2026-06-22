"""Worker Agent — Concept #2: Microsoft Agent Framework.

═══════════════════════════════════════════════════════════════════════════════
DEMO POINT — "Agent Framework: tools, the agent loop, and Foundry"

Talk track (BRK241 demo steps 6-7):
    "The CLI pulls from our templates — here you can see it's using the
     Microsoft Agent Framework with the Copilot SDK harness. … The harness,
     the tool connections, the memory scope — this is production-ready
     scaffolding, not a toy."

What to show in this file (top-to-bottom):
  1. ``@tool``-decorated Python functions — *that's* a tool definition. No
     JSON schema. MAF introspects the type hints and docstring.
  2. ``create_worker_agent()`` — three lines that wire it together:
        FoundryChatClient(...).as_agent(name, instructions, tools=[...])
     ``as_agent`` is the entire agent loop: model call → tool dispatch →
     model call → … → final message. No manual orchestration.
  3. ``run_worker()`` — how the *outer* pattern (the router in
     ``router_agent.py``) drives this agent in the background.
  4. Toolbox tools come from ``toolbox.py`` and merge into the same
     ``tools=`` list — Foundry-managed tools look identical to local tools
     from the agent's perspective.

OPTIMIZABLE CONFIG (instructions / tool descriptions / skills):
  Sourced from ``.agent_configs/baseline/`` via
  ``azure.ai.agentserver.optimization.load_config()``. This lets
  ``azd ai agent optimize`` tune them without code changes:

    .agent_configs/baseline/
      ├── metadata.yaml      # pointers to the files below
      ├── instructions.md    # system prompt for the worker agent
      ├── tools.json         # tool definitions / descriptions
      └── skills/            # optional learned skills

  At runtime ``load_config()`` returns either the baseline above or an
  optimizer-deployed candidate (selected by the ``OPTIMIZATION_CONFIG`` env
  var). ``config.apply_tool_descriptions()`` patches the live ``@tool``
  functions so optimized descriptions reach the model without code edits.
═══════════════════════════════════════════════════════════════════════════════

Tools registered (all built-in sample data — no external setup required):
  - get_class_schedule   — class and lab timetable for a department + day
  - search_library_book  — library catalog availability check
  - find_faculty         — faculty cabin and office hours lookup
"""

import asyncio
import json
import logging
from pathlib import Path

from agent_framework import tool
from agent_framework_foundry import FoundryChatClient
from azure.ai.agentserver.optimization import load_config, load_skills_from_dir

from task_store import Task, TaskStatus
from toolbox import build_toolbox_tool

logger = logging.getLogger(__name__)


# ── Sample Data: Kovai Institute of Technology (KIT) ──────────────────────────

SCHEDULE = {
    ("CSE", "monday"):   [{"time": "09:00", "subject": "Data Structures",   "room": "B-204"},
                          {"time": "11:00", "subject": "DBMS Lab",          "room": "Lab-3"}],
    ("CSE", "tuesday"):  [{"time": "10:00", "subject": "Operating Systems", "room": "B-201"},
                          {"time": "14:00", "subject": "DBMS",              "room": "B-204"}],
    ("CSE", "wednesday"):[{"time": "09:00", "subject": "Computer Networks", "room": "B-205"}],
    "default": [{"time": "—", "subject": "No classes scheduled", "room": "—"}],
}

LIBRARY = {
    "operating system concepts":  {"author": "Galvin",       "copies_available": 2, "shelf": "CS-14"},
    "introduction to algorithms": {"author": "Cormen",       "copies_available": 0, "shelf": "CS-09"},
    "database system concepts":   {"author": "Silberschatz", "copies_available": 5, "shelf": "CS-11"},
}

FACULTY = {
    "data structures":   {"name": "Dr. Anitha R",   "cabin": "B-3-12", "hours": "Wed 2-4 PM"},
    "operating systems": {"name": "Prof. Karthik S", "cabin": "B-2-07", "hours": "Thu 11-1 PM"},
    "dbms":              {"name": "Dr. Priya M",     "cabin": "B-3-21", "hours": "Fri 3-5 PM"},
}


# ── Tools ──────────────────────────────────────────────────────────────────────
#
# DEMO POINT: each ``@tool`` decorator turns a regular Python function into
# a tool the agent can call. MAF reads the type hints + docstring to build
# the schema the model needs. No JSON to maintain.


@tool
def get_class_schedule(dept: str, day: str) -> str:
    """Look up the class and lab schedule for a department on a given weekday.

    Args:
        dept: Department code, e.g. 'CSE'.
        day: Weekday name, e.g. 'monday'.
    """
    rows = SCHEDULE.get((dept.upper(), day.lower()), SCHEDULE["default"])
    return json.dumps({"status": "success", "dept": dept, "day": day, "classes": rows}, indent=2)


@tool
def search_library_book(title: str) -> str:
    """Check whether a library book is available and where to find it.

    Args:
        title: Book title to search for.
    """
    hit = LIBRARY.get(title.lower().strip())
    if not hit:
        return json.dumps({"status": "not_found", "title": title,
                           "hint": "Not in local catalog — try the web search tool."})
    return json.dumps({"status": "success", "title": title, **hit}, indent=2)


@tool
def find_faculty(subject: str) -> str:
    """Find the faculty member for a subject, with cabin location and office hours.

    Args:
        subject: Subject name, e.g. 'DBMS'.
    """
    hit = FACULTY.get(subject.lower().strip())
    if not hit:
        return json.dumps({"status": "not_found", "subject": subject})
    return json.dumps({"status": "success", "subject": subject, **hit}, indent=2)


LOCAL_TOOLS = [
    get_class_schedule,
    search_library_book,
    find_faculty,
]


# ── Agent Factory ─────────────────────────────────────────────────────────────
#
# DEMO POINT: this is the entire agent loop. ``as_agent()`` returns a runner
# that handles model calls, tool dispatch, and result aggregation internally.


def create_worker_agent(project_client, model: str, credential, name: str = "campusmate-worker",
                        toolbox_credential=None):
    """Build the MAF worker agent.

    Instructions, tool descriptions, and skills are loaded from
    ``.agent_configs/baseline/`` (see
    ``azure.ai.agentserver.optimization.load_config``). At deploy time
    ``azd ai agent optimize`` can supply a tuned candidate via the
    ``OPTIMIZATION_CONFIG`` env var without touching code.

    Tools: local ``@tool`` functions above + Toolbox MCP tools (if configured).

    ``credential`` should be an *async* TokenCredential (used by
    FoundryChatClient, which awaits ``get_token``). The optional
    ``toolbox_credential`` should be a *sync* TokenCredential — Toolbox's
    httpx Auth flow is synchronous and crashes with ``'coroutine' object has
    no attribute 'token'`` if given an async credential. When not supplied,
    we fall back to a fresh ``DefaultAzureCredential()``.
    """
    # Resolve config (baseline by default; optimizer-tuned candidate when set).
    config = load_config()

    # Hydrate skills from the local skills/ dir when the config didn't ship them inline.
    if not config.skills and config.skills_dir:
        config.skills.extend(load_skills_from_dir(Path(config.skills_dir)))

    # Patch optimized descriptions onto the live @tool-decorated functions.
    config.apply_tool_descriptions(LOCAL_TOOLS)

    resolved_model = config.model or model
    instructions = config.compose_instructions()

    logger.warning(
        "[worker] config resolved | source=%s | config_model=%r | fallback_model=%r | "
        "resolved_model=%r | prompt_len=%d | skills=%d | tool_overrides=%d",
        config.source,
        config.model,
        model,
        resolved_model,
        len(instructions),
        len(config.skills),
        len(config.tool_definitions),
    )
    logger.warning(
        "[worker] FoundryChatClient will call deployment %r on project_client endpoint",
        resolved_model,
    )

    chat_client = FoundryChatClient(
        project_client=project_client,
        model=resolved_model,
        credential=credential,
        allow_preview=True,
    )

    tools: list = list(LOCAL_TOOLS)

    # Toolbox httpx auth flow is sync — use a sync credential. Falls back to a
    # fresh DefaultAzureCredential() so this stays backwards-compatible when
    # callers don't pass one through.
    if toolbox_credential is None:
        from azure.identity import DefaultAzureCredential as _SyncDAC
        toolbox_credential = _SyncDAC()
    toolbox_tool = build_toolbox_tool(toolbox_credential)
    if toolbox_tool is not None:
        tools.append(toolbox_tool)
        logger.info("Worker agent: Toolbox tool registered alongside %d local tools.", len(LOCAL_TOOLS))
    else:
        logger.info("Worker agent: running with %d local tools (Toolbox disabled).", len(LOCAL_TOOLS))

    return chat_client.as_agent(
        name=name,
        instructions=instructions,
        tools=tools,
    )


# ── Worker Runner ─────────────────────────────────────────────────────────────
#
# Called by the router (router_agent.py / main.py) as a fire-and-forget asyncio
# task. The router gives the user a fast ack while this runs in the background.

_WORKER_TIMEOUT_SEC = 120.0


async def run_worker(task: Task, query: str, agent) -> None:
    """Run the MAF worker for a queued task.

    Updates ``task.status`` and writes ``task.result`` on completion.

    Cancellation: the router cancels this coroutine's asyncio.Task to abort
    the in-flight ``agent.run()`` — ``CancelledError`` propagates up through
    MAF's await points. We catch it, mark CANCELLED, and re-raise.
    """
    # Honor a cancel that landed between scheduling and start.
    if task.cancel_event.is_set() or task.status == TaskStatus.CANCELLED:
        if task.status != TaskStatus.CANCELLED:
            task.status = TaskStatus.CANCELLED
            task.result = task.result or "(Cancelled before start)"
        logger.info("Worker skipping task %s — cancelled before start", task.task_id)
        return

    task.status = TaskStatus.RUNNING
    try:
        agent_model = getattr(agent, "model", None) or getattr(getattr(agent, "_chat_client", None), "model", None)
        logger.warning(
            "[worker] starting agent.run | task=%s | model=%r | query=%r",
            task.task_id,
            agent_model,
            query[:200],
        )
        result = await asyncio.wait_for(
            agent.run(messages=query, stream=False),
            timeout=_WORKER_TIMEOUT_SEC,
        )
        # AgentResponse exposes the final text via ``.text``.
        text = getattr(result, "text", None) or str(result)
        task.result = text or "(Agent completed without text response)"
        task.status = TaskStatus.COMPLETED
        logger.info("Worker completed task %s (%d chars)", task.task_id, len(task.result))
    except asyncio.CancelledError:
        task.status = TaskStatus.CANCELLED
        task.result = "(Cancelled by user)"
        logger.info("Worker cancelled for task %s", task.task_id)
        raise
    except asyncio.TimeoutError:
        task.status = TaskStatus.FAILED
        task.result = "(Worker timed out)"
        logger.warning("Worker timed out for task %s", task.task_id)
    except Exception as e:
        agent_model = getattr(agent, "model", None) or getattr(getattr(agent, "_chat_client", None), "model", None)
        logger.error(
            "[worker] agent.run failed | task=%s | model=%r | error=%s: %s",
            task.task_id,
            agent_model,
            type(e).__name__,
            e,
            exc_info=True,
        )
        task.status = TaskStatus.FAILED
        task.result = f"(Worker error: {e})"
