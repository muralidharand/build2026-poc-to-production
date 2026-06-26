# CampusMate — Change Log

## 2026-06-26 — Initial build (BRK241 demo)

Generated for Microsoft Build BRK241 "From Prototype to Production: Build and
Run Agents at Scale".

### campusmate-helpdesk

| Module | Description |
|---|---|
| `main.py` | `ResponsesAgentServerHost` entry point with instrumentation |
| `router_agent.py` | Single-LLM router using `tool_choice="required"` over 5 meta-tools |
| `worker_agent.py` | MAF worker with KIT campus data (`SCHEDULE`, `LIBRARY`, `FACULTY`) |
| `task_store.py` | Thread-safe in-memory task/status store |
| `toolbox.py` | Optional `MCPStreamableHTTPTool` from `TOOLBOX_ENDPOINT` |
| `procedural_memory.py` | Two-tier loader: live Foundry Memory Store → local seed |
| `procedural_memory_seed.json` | Two seed procedures (no-dues reminder, library waitlist) |
| `.agent_configs/baseline/` | Instructions, tool schemas, optimizer metadata |
| `eval/` | Golden JSONL for four KIT test queries |
| `evaluators/` | Four-dimension rubric (accuracy 50%, tool 25%, helpfulness 15%, conciseness 10%) |

### nodues-coordinator

| Module | Description |
|---|---|
| `main.py` | Agent entry point; 6 `FunctionTool` registrations; student roster |
| `durable_orchestration.py` | `durable_manager` with in-process fallback; `approval_orchestration` |
| `durable_startup.py` | Fires durable worker + approval web server as async background tasks |
| `approval_web.py` | aiohttp server (port 8090): list/detail/approve/reject/healthz routes |
| `approval_templates/` | Server-rendered dark-theme HTML (no external CDN) |
| `teams_bot.py` | Incoming Teams activity handler (no-op when Teams vars unset) |
| `teams_cards.py` | Adaptive Card builders for approval requests and outcomes |
| `teams_connector.py` | Sends proactive Teams messages via Bot Framework REST API |
| `teams_state.py` | Persists Teams conversation references to `~/nodues/teams_state.json` |
