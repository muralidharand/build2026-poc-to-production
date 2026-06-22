# CHANGES.md — CampusMate vs. Upstream (BRK241)

This file lists every file in `campusmate/` and whether it was **changed** (domain layer only)
or **copied verbatim** from the upstream repo at
`microsoft/Build26-BRK241-from-prototype-to-production-build-and-run-agents-at-scale`.

## Top-level

| File | Status | Notes |
|------|--------|-------|
| `azure.yaml` | **CHANGED** | Service names updated to `campusmate-helpdesk` / `nodues-coordinator`; project name to `campusmate-agents` |
| `README.md` | **CHANGED** | Rewritten for CampusMate |

## infra/

| File | Status |
|------|--------|
| `infra/**` (all Bicep + JSON) | **VERBATIM** — not touched |

## src/campusmate-helpdesk/ (was field-ops-agent/)

| File | Status | Notes |
|------|--------|-------|
| `worker_agent.py` | **CHANGED** | Replaced 4 mock data dicts + 4 `@tool` functions with KIT campus data (`SCHEDULE`, `LIBRARY`, `FACULTY`) and 3 new tools (`get_class_schedule`, `search_library_book`, `find_faculty`). Removed `fabric_tool` + `procedural_memory` imports. |
| `router_agent.py` | **CHANGED** | Updated `start_task` description and `ROUTER_SYSTEM_PROMPT` wording for campus helpdesk context. Logic unchanged. |
| `main.py` | **CHANGED** | Updated module docstring; changed fallback agent name from `"field-ops-agent"` to `"campusmate-helpdesk"`. All platform code verbatim. |
| `agent.yaml` | **CHANGED** | `name: campusmate-helpdesk`, campus description, tags. Removed Fabric/procedural-memory optional env vars. |
| `agent.manifest.yaml` | **CHANGED** | `name: campusmate-helpdesk`, campus description. |
| `eval.yaml` | **CHANGED** | Points to `eval/campusmate_golden.jsonl`; agent name updated. |
| `eval/campusmate_golden.jsonl` | **CHANGED** | Replaced 25 datacenter rows with 4 KIT campus rows (was `field_ops_golden.jsonl`). |
| `.agent_configs/baseline/instructions.md` | **CHANGED** | Replaced with CampusMate system prompt. |
| `.agent_configs/baseline/tools.json` | **CHANGED** | Replaced with 3-tool schema for `get_class_schedule`, `search_library_book`, `find_faculty`. |
| `.agent_configs/baseline/metadata.yaml` | **VERBATIM** |
| `.agent_configs/baseline/skills/.gitkeep` | **VERBATIM** |
| `task_store.py` | **VERBATIM** |
| `toolbox.py` | **VERBATIM** |
| `fabric_tool.py` | **VERBATIM** (kept for structural completeness; not imported by worker) |
| `procedural_memory.py` | **VERBATIM** (kept for structural completeness; not imported by worker) |
| `procedural_memory_seed.json` | **VERBATIM** |
| `requirements.txt` | **VERBATIM** |
| `Dockerfile` | **VERBATIM** |
| `.dockerignore` | **VERBATIM** |
| `.agentignore` | **VERBATIM** |
| `.env.example` | **VERBATIM** |
| `.vscode/launch.json` | **VERBATIM** |
| `.vscode/tasks.json` | **VERBATIM** |
| `route_worker_agent.md` | **VERBATIM** |
| `evaluators/field-ops-agent/rubric_dimensions.json` | **VERBATIM** |
| `wheels/*.whl` | **VERBATIM** |
| `README.md` | **CHANGED** | Rewritten for CampusMate helpdesk |

## src/nodues-coordinator/ (was fibey-coordinator/)

| File | Status | Notes |
|------|--------|-------|
| `main.py` | **CHANGED** | Replaced `MOCK_TELEMETRY`, `MOCK_INCIDENTS` and 6 domain tools with `STUDENT` dict and 5 KIT no-dues tools (`check_dues`, `clear_due`, `request_approval`, `issue_no_dues_certificate`, `save_request`). Updated system prompt. `request_approval` + Teams card wiring preserved verbatim. Module docstring and logger fallback name updated. |
| `agent.yaml` | **CHANGED** | `name: nodues-coordinator`, campus description, tags. |
| `durable_orchestration.py` | **CHANGED** | Module docstring, `DTS_TASKHUB` default (`nodues-hub`), `AGENT_NAME` default, one activity log message. Orchestration logic unchanged. |
| `TEAMS-SETUP.md` | **CHANGED** | Updated copy to reference no-dues certificate flow and officer instead of network operator. |
| `README.md` | **CHANGED** | Rewritten for NoDues Coordinator. |
| `durable_startup.py` | **VERBATIM** |
| `teams_bot.py` | **VERBATIM** |
| `teams_cards.py` | **VERBATIM** |
| `teams_connector.py` | **VERBATIM** |
| `teams_state.py` | **VERBATIM** |
| `requirements.txt` | **VERBATIM** |
| `Dockerfile` | **VERBATIM** |
| `.dockerignore` | **VERBATIM** |
| `.agentignore` | **VERBATIM** |
| `.env.example` | **VERBATIM** |
| `agent.manifest.yaml` | **VERBATIM** |
| `wheels/*.whl` | **VERBATIM** |

## Summary

- **Changed:** 17 files (domain layer only — data, tool functions, instructions, names, descriptions)
- **Verbatim:** ~35 files (all platform code — server host, router logic, durable orchestration engine, Teams wire-up, Bicep infra, Dockerfiles, pinned wheels)
