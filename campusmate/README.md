# CampusMate — AI Agents for Kovai Institute of Technology

**Microsoft Build BRK241 demo** — two production-ready Azure AI agents serving
the students and staff of the fictional Kovai Institute of Technology (KIT).

## Agents

### 1. CampusMate Helpdesk (`src/campusmate-helpdesk`)

A voice/chat assistant that answers student queries about:
- Class and lab schedules
- Library book availability
- Faculty contacts and office hours

Uses a **router/worker** pattern: the router classifies each message in ~1 s,
then a background worker calls campus tools (MCP Toolbox, local data) and
returns the answer. Procedural memory (Foundry Memory Store + local seed)
lets the agent learn new guidelines over time.

### 2. No-Dues Coordinator (`src/nodues-coordinator`)

A long-running agent with **human-in-the-loop** approval for no-dues
clearance certificates:

1. Checks outstanding dues (hostel, library, lab, fees)
2. Clears dues on request
3. Raises a durable approval request visible in the web UI
4. Issues the certificate only after an officer approves

Ships with an aiohttp approval portal (dark theme, mobile-friendly) and
optional Microsoft Teams Adaptive Card notifications.

## One-command deploy

```bash
# Prerequisites: Azure CLI, azd, Docker
azd up
```

This provisions all Azure resources (AI Foundry account + project, gpt-5 model,
ACR, App Insights, Storage, AI Search) and deploys both agents as containers.

## Local development

```bash
# Helpdesk
cd src/campusmate-helpdesk
cp .env.example .env && pip install -r requirements.txt
python main.py           # or press F5 in VS Code

# No-Dues Coordinator
cd src/nodues-coordinator
cp .env.example .env && pip install -r requirements.txt
python main.py           # approval portal: http://localhost:8090/approvals
```

## Repository structure

```
campusmate/
  azure.yaml                  # azd service definitions
  infra/                      # Bicep infrastructure
  src/
    campusmate-helpdesk/      # Agent 1 — campus Q&A
    nodues-coordinator/       # Agent 2 — no-dues approval flow
```

## Stack

- Python 3.12
- `agent-framework` 1.6.0 + `agent-framework-foundry` 1.0.1
- `azure-ai-agentserver-responses` 1.0.0b5
- Azure Durable Task (coordinator only)
- aiohttp approval portal
- MCP Toolbox integration (optional)
