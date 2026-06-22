# Sample agents

Two hosted agents for Microsoft Foundry, deployed together with the Azure
Developer CLI (`azd`). Both ship with sample tool data so they run end-to-end
without any external setup; the optional integrations are clearly marked.

| Agent | Theme | Highlights |
|-------|-------|-----------|
| [`field-ops-agent`](field-ops-agent/README.md) | Build a voice-enabled field assistant | Microsoft Agent Framework, function tools, MCP Toolbox, optional Microsoft Fabric data agent, procedural memory, a voice routing pattern, tracing + evaluation |
| [`fibey-coordinator`](fibey-coordinator/README.md) | Run a long-running coordinator | Persistent sessions, scale-to-zero, human-in-the-loop approvals via Durable Task Scheduler, optional Microsoft Teams (activity protocol) |

## Layout

```
src/
├── field-ops-agent/      # Voice-enabled field technician agent
│   ├── agent.yaml        # Hosted agent config (Foundry deploys this)
│   ├── main.py           # Server host, router dispatch, streaming
│   ├── worker_agent.py   # Agent Framework agent loop + @tool functions
│   ├── router_agent.py   # Voice routing pattern (fast ack + background work)
│   ├── toolbox.py        # Optional MCP Toolbox connection
│   ├── fabric_tool.py    # Optional Microsoft Fabric data agent tool
│   ├── procedural_memory.py  # Foundry Memory Store (with bundled seed fallback)
│   ├── eval.yaml + eval/ # Sample evaluation set and rubric
│   └── Dockerfile, requirements.txt
└── fibey-coordinator/    # Long-running network operations coordinator
    ├── agent.yaml        # Hosted agent config (responses + activity protocols)
    ├── main.py           # Agent loop, mock telemetry/incidents, $HOME persistence
    ├── durable_orchestration.py  # Optional Durable Task approval workflow
    ├── teams_*.py        # Optional Microsoft Teams integration
    └── Dockerfile, requirements.txt
```

## Run

```bash
# Provision Foundry project + model, then deploy both agents
azd provision
azd deploy

# Or deploy a single agent
azd deploy field-ops-agent
azd deploy fibey-coordinator
```

Each agent reads its model deployment from `AZURE_AI_MODEL_DEPLOYMENT_NAME` /
`MODEL_DEPLOYMENT_NAME` and its Foundry project from `FOUNDRY_PROJECT_ENDPOINT`
(auto-injected in hosted containers). Optional integrations are off by default —
see each agent's `agent.yaml` for the commented environment variables that enable
them.
