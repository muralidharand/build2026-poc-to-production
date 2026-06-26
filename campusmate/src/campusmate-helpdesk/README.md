# CampusMate Helpdesk

AI-powered campus assistant for **Kovai Institute of Technology (KIT)**.

Answers student queries about class schedules, library books, faculty contacts,
and more — using a router/worker architecture with Azure AI Foundry.

## Architecture

```
Student message
      │
      ▼
 router_agent.py   ← single LLM call, tool_choice=required
      │
      ├─ respond_directly  → immediate reply
      │
      └─ start_task        → background thread
                                  │
                                  ▼
                           worker_agent.py
                           ├─ get_class_schedule
                           ├─ search_library_book
                           ├─ find_faculty
                           ├─ recall_learned_procedures
                           └─ MCP Toolbox (optional)
```

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# edit .env with your Azure AI project endpoint

# 3. Run the agent
python main.py
```

## Debugging — F5 → Agent Inspector

1. Open this folder in VS Code.
2. Press **F5** (or run the "Attach to CampusMate Helpdesk" launch config).
3. The task runner starts the agent with `debugpy` listening on port 5679
   and automatically opens the **Agent Inspector** in the AI Toolkit sidebar.
4. Set breakpoints anywhere in `main.py`, `router_agent.py`, or `worker_agent.py`.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `AZURE_AI_PROJECT_ENDPOINT` | Yes | Foundry project endpoint URL |
| `MODEL_DEPLOYMENT_NAME` | No | Defaults to `gpt-5` |
| `ENABLE_INSTRUMENTATION` | No | `true` to enable OpenTelemetry |
| `ENABLE_SENSITIVE_DATA` | No | `true` to include message content in traces |
| `PROCEDURAL_MEMORY_MODE` | No | `hybrid` (default) / `live` / `seed` |
| `TOOLBOX_ENDPOINT` | No | MCP Toolbox HTTP endpoint |

## Deploying with azd

```bash
azd up
```

See the top-level `azure.yaml` for the full deployment configuration.
