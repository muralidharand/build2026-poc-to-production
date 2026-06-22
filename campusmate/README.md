# CampusMate — BRK241 Demo

> **Microsoft Build 2026 · BRK241 "From Prototype to Production: Build and Run Agents at Scale"**
> Demo audience: students and architects at Kovai Institute of Technology (KIT), Coimbatore.

CampusMate is a college assistant built on the Microsoft Agent Framework (MAF) and Azure AI Foundry.
It re-themes the official BRK241 sample from a data-center story into a **campus helpdesk** — the
platform code is **unchanged**, only the domain layer was swapped. That's the teaching point.

## Two agents, one deploy

```
azd auth login
azd up    # provision infra + deploy both agents (~8 min)
```

| Agent | What it shows |
|-------|---------------|
| `campusmate-helpdesk` | Voice routing pattern (router → worker), MAF `@tool` functions, Toolbox MCP integration |
| `nodues-coordinator` | Human-in-the-loop approval via Durable Task, scale-to-zero, Teams adaptive cards |

## On-stage demo commands

```bash
# Helpdesk — voice assistant
azd ai agent invoke --local "What classes does CSE have on Monday?"
azd ai agent invoke --local "Is Operating System Concepts in the library?"
azd ai agent invoke --local "Who teaches DBMS and when can I meet them?"

# No-dues — human-in-the-loop
azd ai agent invoke --local "I need my no-dues certificate"
# → agent finds ₹1,500 hostel due → calls request_approval → officer approves → certificate issued

# Teardown after the talk
azd down
```

## Project layout

```
campusmate/
├── azure.yaml                      ← azd wiring (two services)
├── infra/                          ← Bicep infrastructure (verbatim from upstream)
└── src/
    ├── campusmate-helpdesk/        ← Agent 1: schedule, library, faculty
    │   ├── agent.yaml
    │   ├── main.py                 ← ResponsesAgentServerHost + router dispatch
    │   ├── router_agent.py         ← Voice routing pattern (front desk)
    │   ├── worker_agent.py         ← MAF @tool functions + FoundryChatClient.as_agent()
    │   ├── toolbox.py              ← Optional MCP Toolbox integration
    │   ├── task_store.py
    │   ├── .agent_configs/baseline/
    │   │   ├── instructions.md     ← CampusMate system prompt
    │   │   └── tools.json          ← Tool descriptions for the optimizer
    │   └── eval/campusmate_golden.jsonl
    └── nodues-coordinator/         ← Agent 2: no-dues + human approval
        ├── agent.yaml
        ├── main.py                 ← Domain tools + MAF agent + server host
        ├── durable_orchestration.py← Durable Task: investigate → approve → execute
        ├── teams_bot.py            ← Teams adaptive card (officer approves here)
        └── TEAMS-SETUP.md
```

## What changed vs. upstream

See [CHANGES.md](../CHANGES.md) at the repo root for the exact list of changed vs. verbatim files.

## Framework versions (pinned — do not upgrade)

| Package | Version |
|---------|---------|
| `agent-framework` | `1.6.0` |
| `agent-framework-foundry` | `1.0.1` |
| `azure-ai-agentserver-core[tracing]` | `2.0.0b3` |
| `azure-ai-agentserver-responses` | `1.0.0b5` |
| Model deployment | `gpt-5` GlobalStandard `2025-08-07` capacity 50 |
