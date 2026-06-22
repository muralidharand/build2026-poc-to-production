<a name="start-building"></a>
<br>
<p align="center">
<img src="img/banner-build-26.png" alt="Microsoft Build 2026" width="1200"/>
</p>

# [Microsoft Build 2026](https://build.microsoft.com)

## 🚀 BRK241: From Prototype to Production — Build and Run Agents at Scale

### Session Description

Taking an AI agent from a working prototype to a reliable, scalable production
service involves real engineering: deployment, tools, memory, long-running work,
human oversight, and observability. This breakout walks that journey using two
sample agents you can run yourself on **Microsoft Foundry**:

- **`field-ops-agent`** — a voice-enabled field technician assistant built on the
  **Microsoft Agent Framework**, showing tools, an MCP **Toolbox** connection, an
  optional **Microsoft Fabric** data agent, and procedural memory.
- **`fibey-coordinator`** — a long-running network operations coordinator that
  monitors telemetry, persists context across sessions, **scales to zero** while
  waiting, gates actions behind **human-in-the-loop** approvals, and can work in
  **Microsoft Teams**.

Both agents deploy with a single `azd` command, emit traces to Application
Insights, and ship with sample tool data so they run end-to-end out of the box.

### 🚀 Getting started

You can deploy both agents to your own Microsoft Foundry project with the Azure
Developer CLI.

**Prerequisites**

- An Azure subscription with access to [Microsoft Foundry](https://learn.microsoft.com/azure/ai-foundry/)
- [Azure Developer CLI (`azd`)](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd) v1.24+
- The Foundry agents extension: `azd extension install azure.ai.agents`
- Python 3.12+

**Clone and deploy**

```bash
git clone https://github.com/microsoft/Build26-BRK241-from-prototype-to-production-build-and-run-agents-at-scale.git
cd Build26-BRK241-from-prototype-to-production-build-and-run-agents-at-scale

# Provision the Foundry project, model deployment, and supporting resources
azd provision

# Deploy both hosted agents
azd deploy
```

Deploy a single agent with `azd deploy field-ops-agent` or
`azd deploy fibey-coordinator`. Tear everything down with `azd down`.

See [`src/field-ops-agent/`](src/field-ops-agent/README.md) and
[`src/fibey-coordinator/`](src/fibey-coordinator/README.md) for per-agent details,
example prompts, and the optional integrations (Toolbox, Fabric, Teams,
Durable Task Scheduler).

### 🧠 Learning Outcomes

By the end of this session, you will be able to:

- Build an agent with the **Microsoft Agent Framework** and deploy it to
  **Microsoft Foundry** as a hosted agent using the Azure Developer CLI.
- Extend an agent with tools, **MCP/Toolbox** connections, data agents, and
  procedural memory — and run long-running agents with persistent sessions,
  scale-to-zero, and human-in-the-loop approvals.
- Operate agents in production with built-in **tracing** and **evaluation**.

### 💬 Keep Learning with Copilot

Try these prompts with GitHub Copilot to explore the topics from this session.
Open Copilot Chat in Visual Studio Code (`Ctrl+Alt+I` on Windows/Linux,
`Cmd+Shift+I` on Mac), paste a prompt, and see what you learn. Connect the
[Microsoft Learn MCP Server](#-microsoft-learn-mcp-server) for the latest official
documentation.

1. Understand the basics:

```
Explain what a hosted agent in Microsoft Foundry Agent Service is and how it differs from running an agent on my own infrastructure.
```

2. Go deeper:

```
Using the Microsoft Learn MCP Server, find the latest documentation on the Microsoft Agent Framework and show me how to define a tool and run an agent loop in Python.
```

3. Build something:

```
Help me create a hosted agent with the Microsoft Agent Framework that exposes one function tool, then deploy it to Microsoft Foundry with the Azure Developer CLI.
```

### 💻 Technologies Used

1. [Microsoft Foundry — Hosted Agents](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
1. [Microsoft Agent Framework](https://learn.microsoft.com/agent-framework/)
1. [Azure Developer CLI (`azd`)](https://learn.microsoft.com/azure/developer/azure-developer-cli/)
1. [Model Context Protocol (MCP) tools](https://learn.microsoft.com/agent-framework/agents/tools/)
1. [Microsoft Fabric data agents](https://learn.microsoft.com/fabric/data-science/concept-data-agent)
1. [Azure Monitor Application Insights](https://learn.microsoft.com/azure/azure-monitor/app/app-insights-overview)
1. [Durable Task Scheduler](https://learn.microsoft.com/azure/azure-functions/durable/durable-task-scheduler/durable-task-scheduler)
1. [Microsoft Teams bots (Bot Framework)](https://learn.microsoft.com/azure/bot-service/)

### 📚 Resources and Next Steps

| Resource | Description |
|:---------|:------------|
| [Build 2026 — Next Steps](https://aka.ms/build26-next-steps) | Explore lab and session repos to further your learning from Microsoft Build |
| [Quickstart: Deploy your first hosted agent](https://learn.microsoft.com/azure/foundry/agents/quickstarts/quickstart-hosted-agent) | Step-by-step quickstart for deploying a hosted agent to Microsoft Foundry |
| [Foundry Hosted Agents with the Agent Framework](https://learn.microsoft.com/agent-framework/hosting/foundry-hosted-agent) | How the Microsoft Agent Framework hosts and runs agents on Foundry |
| [Get started with the Agent Framework](https://learn.microsoft.com/agent-framework/get-started/) | Tutorials for building your first agent, adding tools, memory, and workflows |
| [Watch the session recording](https://aka.ms/build26/BRK241/youtube) | Watch the recorded Microsoft Build session. |

### 🌟 Microsoft Learn MCP Server

The Microsoft Learn MCP Server gives your AI agent direct access to Microsoft's official documentation — grounded, up-to-date answers about the products and services covered in this session.

**Visual Studio Code** — One click installation:

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_Microsoft_Learn_MCP-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=microsoft-learn&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Flearn.microsoft.com%2Fapi%2Fmcp%22%7D)


**GitHub Copilot CLI** — Run this to install the Learn MCP Server as a plugin:
```
/plugin install microsoftdocs/mcp
```

For more info, other clients, and to post questions, visit the [Learn MCP Server repo](https://aka.ms/learnmcp).

## Content Owners

<table>
<tr>
    <td align="center"><a href="http://github.com/jeffhollan">
        <img src="https://github.com/jeffhollan.png" width="100px;" alt="Jeff Hollan"/><br />
        <sub><b>Jeff Hollan</b></sub></a><br />
            <a href="https://github.com/jeffhollan" title="talk">📢</a>
    </td>
</tr></table>

## Contributing

This project welcomes contributions and suggestions.  Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
