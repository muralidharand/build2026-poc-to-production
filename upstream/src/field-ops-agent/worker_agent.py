"""Worker Agent — Concept #2: Microsoft Agent Framework.

═══════════════════════════════════════════════════════════════════════════════
DEMO POINT — "Agent Framework: tools, the agent loop, and Foundry"

Talk track (from Demo-script.md, steps 6-7):
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

Tools currently registered (all mock — feature teams swap with real backends):
  - search_site_specs       — site infrastructure lookup
  - search_work_iq          — active work orders, maintenance tasks
  - get_repair_procedure    — step-by-step repair guides
  - analyze_document        — Document Understanding placeholder
  - query_site_reliability  — Fabric data agent (REAL — wired to OneLake telemetry)
  - recall_learned_procedures — Procedural memory recall (REAL — Foundry Memory Store)
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
from fabric_tool import query_site_reliability
from procedural_memory import recall_learned_procedures, render_procedures_block

logger = logging.getLogger(__name__)


# ── Sample Tool Data (rich, deterministic demo data) ───────────────────────────

MOCK_SITE_SPECS = {
    "quincy_north_b": {
        "site": "Quincy North",
        "building": "B-side",
        "fiber_type": "OS2 Single-Mode",
        "connector": "LC/UPC Duplex",
        "panel_count": 48,
        "patch_density": "Ultra-High (144 per RU)",
        "cable_plant": "Trunk cables: MTP-24, Breakout: LC Duplex",
        "max_loss_budget_db": 2.5,
        "cleaning_protocol": "IBC one-click + visual inspect before every mate",
        "last_audit": "2026-04-15",
        "notes": "B-side upgraded to 400G-capable structured cabling Q1 2026. All trunks tested to IL < 0.15 dB."
    },
    "default": {
        "site": "Generic Data Center",
        "fiber_type": "OS2 Single-Mode / OM5 Multimode",
        "connector": "LC/UPC or MPO-16",
        "panel_count": 24,
        "patch_density": "High (72 per RU)",
        "cable_plant": "Standard trunk + breakout topology",
        "max_loss_budget_db": 3.0,
        "cleaning_protocol": "IBC one-click before every mate",
        "notes": "Contact site lead for site-specific details."
    }
}

MOCK_WORK_ORDERS = [
    {
        "id": "WO-20260524-001",
        "title": "Replace failed QSFP-DD transceiver in Rack B-14",
        "priority": "P1 - Critical",
        "status": "Assigned",
        "assignee": "Field Tech On-Site",
        "site": "Quincy North B-side",
        "created": "2026-05-24T08:30:00Z",
        "description": "Transceiver in port 3/1/12 showing CRC errors above threshold. Replace with spare from cage locker B-3.",
        "parts_needed": ["QSFP-DD 400G SR8", "IBC Cleaner", "Dust caps (2)"]
    },
    {
        "id": "WO-20260524-002",
        "title": "Fiber re-route: Tray 7 to new panel in Row C",
        "priority": "P2 - High",
        "status": "Scheduled",
        "assignee": "Field Tech On-Site",
        "site": "Quincy North B-side",
        "created": "2026-05-24T06:15:00Z",
        "description": "New customer circuit requires 12-strand trunk from Tray 7 splice enclosure to Panel C-22. Pre-staged cable in pathway.",
        "parts_needed": ["MTP-12 trunk (15m)", "Breakout cassette LC-12", "Velcro ties"]
    },
    {
        "id": "WO-20260523-008",
        "title": "Scheduled PM: Clean all patch panels Row A",
        "priority": "P3 - Normal",
        "status": "In Progress",
        "assignee": "Field Tech On-Site",
        "site": "Quincy North B-side",
        "created": "2026-05-23T14:00:00Z",
        "description": "Quarterly preventive maintenance - inspect and clean all 12 patch panels in Row A using IBC one-click cleaners. Document any damaged connectors.",
        "parts_needed": ["IBC one-click cleaners (box of 25)", "Inspection scope", "Dust caps"]
    }
]

MOCK_REPAIR_PROCEDURES = {
    "qsfp_dd_replacement": {
        "procedure": "QSFP-DD Transceiver Replacement",
        "steps": [
            "1. Verify link is administratively down or traffic is failed over",
            "2. Unlatch the QSFP-DD pull tab and extract module straight out",
            "3. Inspect receptacle with fiber scope - ensure no debris",
            "4. Remove dust caps from new transceiver",
            "5. Clean fiber end-faces with IBC one-click cleaner",
            "6. Insert new QSFP-DD firmly until latch clicks",
            "7. Wait 30s for module initialization",
            "8. Verify link LED goes green and check optical power levels",
            "9. Run BER test for 60 seconds - confirm zero errors",
            "10. Update asset tag in WorkIQ"
        ],
        "safety_notes": "ESD wrist strap required. Do not exceed 5N insertion force.",
        "estimated_time_min": 15,
        "tools_required": ["ESD wrist strap", "Fiber inspection scope", "IBC one-click cleaner", "OTDR (if loss verification needed)"]
    },
    "fiber_splice": {
        "procedure": "Fiber Fusion Splice",
        "steps": [
            "1. Prepare work area - clean surface, set up splice machine",
            "2. Strip fiber jacket to expose 40mm of bare fiber",
            "3. Clean bare fiber with lint-free wipe + IPA",
            "4. Cleave fiber at 90 degrees - inspect cleave angle < 1 degree",
            "5. Load fibers into fusion splicer V-grooves",
            "6. Run automatic splice cycle",
            "7. Verify estimated loss < 0.02 dB",
            "8. Apply splice protector sleeve and shrink",
            "9. Place in splice tray - maintain minimum bend radius",
            "10. Test with OTDR from both ends"
        ],
        "safety_notes": "Fiber shards are sharp - use fiber scrap container. Eye protection required during cleaving.",
        "estimated_time_min": 20,
        "tools_required": ["Fusion splicer", "Fiber cleaver", "Fiber stripper", "OTDR", "Splice protector sleeves"]
    },
    "default": {
        "procedure": "General Repair Procedure",
        "steps": [
            "1. Identify affected equipment and confirm work order scope",
            "2. Review safety requirements and obtain necessary permits",
            "3. Perform repair following manufacturer guidelines",
            "4. Test and verify repair",
            "5. Update documentation and close work order"
        ],
        "safety_notes": "Follow all site-specific safety protocols. Wear required PPE.",
        "estimated_time_min": 30,
        "tools_required": ["Site-specific - check work order"]
    }
}

MOCK_DOCUMENT_ANALYSIS = {
    "default": {
        "document_type": "Technical Specification",
        "summary": "Document analyzed successfully. Key findings extracted.",
        "extracted_data": {
            "title": "Network Equipment Specification",
            "revision": "Rev C",
            "key_parameters": [
                "Operating temperature: -5C to 70C",
                "Power consumption: 12W typical, 15W max",
                "MTBF: 300,000 hours",
                "Wavelength: 1310nm (SMF)"
            ],
            "compliance": ["IEEE 802.3bs", "MSA QSFP-DD", "RoHS", "REACH"]
        }
    }
}


# ── Tools (this is what the agent calls) ──────────────────────────────────────
#
# DEMO POINT: each ``@tool`` decorator turns a regular Python function into
# a tool the agent can call. MAF reads the type hints + docstring to build
# the schema the model needs. No JSON to maintain.


@tool
def search_site_specs(query: str, site: str = "") -> str:
    """Search site specifications and infrastructure details for a data center site.

    Args:
        query: What to look up (e.g., 'fiber termination spec', 'power capacity').
        site: Site name filter (e.g., 'Quincy North B-side').
    """
    key = "quincy_north_b" if "quincy" in query.lower() or "quincy" in site.lower() or "b-side" in query.lower() else "default"
    result = MOCK_SITE_SPECS[key]
    return json.dumps({"status": "success", "query": query, "results": [result]}, indent=2)


@tool
def search_work_iq(query: str = "", status: str = "", priority: str = "") -> str:
    """Search WorkIQ for active work orders, maintenance tasks, and assignments.

    Args:
        query: Free-text search across work orders.
        status: Filter by status (e.g., 'Assigned', 'In Progress', 'Scheduled').
        priority: Filter by priority (e.g., 'P1', 'P2', 'P3').
    """
    results = MOCK_WORK_ORDERS
    if status:
        if status.lower() in ("open", "active"):
            results = [wo for wo in results if wo["status"].lower() not in ("closed", "completed", "cancelled")]
        else:
            results = [wo for wo in results if status.lower() in wo["status"].lower()]
    if priority:
        results = [wo for wo in results if priority.lower() in wo["priority"].lower()]
    if query:
        results = [wo for wo in results if query.lower() in wo["title"].lower() or query.lower() in wo["description"].lower()]
    return json.dumps({"status": "success", "count": len(results), "work_orders": results}, indent=2)


@tool
def get_repair_procedure(equipment_type: str, issue: str = "") -> str:
    """Look up step-by-step repair procedures for data center equipment.

    Args:
        equipment_type: Type of equipment (e.g., 'QSFP-DD transceiver', 'fiber splice').
        issue: Specific issue description for context.
    """
    key = "default"
    equip_lower = equipment_type.lower()
    if "qsfp" in equip_lower or "transceiver" in equip_lower:
        key = "qsfp_dd_replacement"
    elif "splice" in equip_lower or "fusion" in equip_lower:
        key = "fiber_splice"
    result = MOCK_REPAIR_PROCEDURES[key]
    return json.dumps({"status": "success", "equipment": equipment_type, "procedure": result}, indent=2)


@tool
def analyze_document(document_url: str, analysis_type: str = "summary") -> str:
    """Analyze a technical document or specification sheet using Document Understanding.

    Args:
        document_url: URL or path to the document to analyze.
        analysis_type: Type of analysis - 'summary', 'extract_specs', 'compliance_check'.
    """
    result = dict(MOCK_DOCUMENT_ANALYSIS["default"])
    result["source"] = document_url
    result["analysis_type"] = analysis_type
    return json.dumps({"status": "success", "analysis": result}, indent=2)


LOCAL_TOOLS = [
    search_site_specs,
    search_work_iq,
    get_repair_procedure,
    analyze_document,
    query_site_reliability,
    recall_learned_procedures,
]


# ── Agent Factory ─────────────────────────────────────────────────────────────
#
# DEMO POINT: this is the entire agent loop. ``as_agent()`` returns a runner
# that handles model calls, tool dispatch, and result aggregation internally.


def create_worker_agent(project_client, model: str, credential, name: str = "field-ops-worker",
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

    # ── DEMO POINT: Procedural Memory injection ───────────────────────────────
    #
    # The agent learns from past technician conversations. Procedures it has
    # picked up (e.g., "always ask internal vs third-party for 'last service
    # date'") are loaded at startup from the Foundry Memory Store and prepended
    # to the system prompt as native skills — NOT as tool output. This is what
    # makes the agent get smarter over time without code changes.
    #
    # See procedural_memory.py for the loader and seed file. Wrapped in a
    # broad try/except so any procedural-memory failure (auth, network, JSON
    # parse) is logged but never blocks agent startup.
    try:
        procedures_block = render_procedures_block()
        if procedures_block:
            instructions = procedures_block + "\n" + instructions
            logger.warning("[worker] procedural memory: %d learned procedure(s) injected into system prompt",
                           procedures_block.count("- applicable_to:"))
        else:
            logger.warning("[worker] procedural memory: 0 items loaded; system prompt unchanged")
    except Exception as e:  # noqa: BLE001
        logger.warning("[worker] procedural memory: failed to load (%s: %s); proceeding without it",
                       type(e).__name__, e)

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
