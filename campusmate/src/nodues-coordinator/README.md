# No-Dues Coordinator

Human-in-the-loop no-dues clearance agent for **Kovai Institute of Technology (KIT)**.

Guides students through checking, clearing, and certifying no-dues status — with
mandatory officer approval via a web UI and optional Microsoft Teams integration.

## Flow

```
Student: "I need my no-dues certificate"
        │
        ▼
  check_dues()         ← show outstanding amounts
        │
  clear_due() / pay_all_dues()
        │
  request_approval()   ← starts durable orchestration
        │               registers entry in approval web UI
        │               sends Teams card (if configured)
        ▼
  Officer: visits http://localhost:8090/approvals
           clicks Approve
        │
        ▼
  issue_no_dues_certificate()  ← only when officer_approved=True AND dues=0
```

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# edit .env

# 3. Run the agent (also starts approval web server on port 8090)
python main.py
```

The approval portal is available at: http://localhost:8090/approvals

## Debugging — F5 → Agent Inspector

1. Open this folder in VS Code.
2. Press **F5** (or run the "Attach to No-Dues Coordinator" launch config).
3. The task runner starts the agent with `debugpy` listening on port 5680
   and opens the **Agent Inspector** in the AI Toolkit sidebar.
4. Set breakpoints in `main.py`, `approval_web.py`, or `durable_orchestration.py`.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `AZURE_AI_PROJECT_ENDPOINT` | Yes | Foundry project endpoint |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | No | Defaults to `gpt-5` |
| `APPROVAL_SERVER_PORT` | No | Port for the approval UI (default 8090) |
| `ENABLE_INSTRUMENTATION` | No | `true` for OpenTelemetry tracing |
| `DURABLE_TASK_ENDPOINT` | No | gRPC endpoint for Azure Durable Task |
| `TEAMS_APP_ID` | No | Bot app ID for Teams integration |
| `TEAMS_APP_PASSWORD` | No | Bot app secret |
| `TEAMS_TENANT_ID` | No | Azure AD tenant ID |

## Student roster (demo data)

| ID | Name | Hostel | Library | Lab | Fees |
|---|---|---|---|---|---|
| 22CSE114 | Arjun | ₹1,500 | 0 | 0 | 0 |
| 22ECE089 | Priya | 0 | ₹200 | ₹500 | 0 |
| 21MECH045 | Kiran | 0 | 0 | 0 | 0 |
| 22CSE058 | Deepa | 0 | 0 | 0 | 0 |
| 22EEE072 | Vignesh | 0 | 0 | 0 | 0 |
| 22CSE141 | Sanjay | ₹3,000 | 0 | 0 | ₹8,500 |
| 22IT020 | Lakshmi | 0 | ₹150 | 0 | 0 |

## Deploying with azd

```bash
azd up
```
