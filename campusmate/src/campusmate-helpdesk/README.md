# CampusMate Helpdesk Agent

Voice-enabled campus helpdesk assistant for students at **Kovai Institute of Technology (KIT)**.
It deploys as a Hosted Agent on Azure AI Foundry and runs with built-in sample data — no
external database required.

## What it does

Students can ask (by voice or text):
- **"What's my CSE schedule on Monday?"** → returns class time, subject, and room number
- **"Is Operating System Concepts in the library?"** → returns copies available and shelf location
- **"Who teaches DBMS?"** → returns faculty name, cabin number, and office hours

## Architecture (file map)

Open these files in order to follow the BRK241 demo walkthrough:

| # | File | Concept |
|---|------|---------|
| 1 | `agent.yaml` | Hosted agent config — Foundry deploys this |
| 2 | `toolbox.py` | Concept #1: Toolbox MCP integration (optional web search) |
| 3 | `worker_agent.py` | Concept #2: Agent Framework — `@tool` functions + `FoundryChatClient.as_agent()` |
| 4 | `router_agent.py` | Concept #3: Voice routing pattern (front desk / back office) |
| 5 | `task_store.py` | Supporting state for the voice pattern |
| 6 | `main.py` | Thin glue: server host, request handler, router dispatch, streaming wire-up |

## Tools

| Tool | Purpose |
|------|---------|
| `get_class_schedule(dept, day)` | Returns classes and labs for a department + weekday |
| `search_library_book(title)` | Returns availability, copies, and shelf for a book title |
| `find_faculty(subject)` | Returns faculty name, cabin, and office hours for a subject |

## Local run (dev/test)

```bash
cd src/campusmate-helpdesk
pip install -r requirements.txt
export FOUNDRY_PROJECT_ENDPOINT=https://<your-project>.api.azureml.ms
export MODEL_DEPLOYMENT_NAME=gpt-5
python main.py
```

## Deploy

```bash
azd up          # provision infra + deploy both agents
azd ai agent invoke --local "When is my next DBMS lab?"
```

## Demo prompts (BRK241 stage)

```
"What classes does CSE have on Monday?"
"Is Introduction to Algorithms available in the library?"
"Who teaches Operating Systems and when can I meet them?"
```

## Optional integrations

Set `TOOLBOX_ENDPOINT` to your Foundry Toolbox MCP URL to enable web search alongside
the built-in tools. No other setup is required to run the agent end-to-end.
