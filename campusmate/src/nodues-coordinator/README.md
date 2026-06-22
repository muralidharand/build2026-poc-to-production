# NoDues Coordinator

Long-running no-dues coordinator for **Kovai Institute of Technology (KIT)** — checks student
dues across departments, gates the final certificate behind human-in-the-loop officer approval,
and persists the dossier to survive scale-to-zero.

## What it does

Students ask for their no-dues certificate:

```
Student: "I need my no-dues certificate."
Agent:   Checks all departments → finds ₹1,500 outstanding in hostel.
         Calls request_approval → officer gets a Teams card (Approve / Reject).
Officer: Clicks Approve.
Agent:   Issues certificate NOC-22CSE114-20260622.
```

This demonstrates three production patterns on stage:
1. **Human-in-the-loop** via durable orchestration (`durable_orchestration.py`)
2. **Scale-to-zero while waiting** — DTS persists the approval gate; no compute consumed
3. **Teams integration** — proactive adaptive card to the officer (`teams_bot.py`)

## Architecture

| File | Role |
|------|------|
| `agent.yaml` | Hosted agent config — declares both `responses` + `activity_protocol` |
| `main.py` | Domain tools (`check_dues`, `clear_due`, `request_approval`, `issue_no_dues_certificate`, `save_request`) + MAF agent setup + server host |
| `durable_orchestration.py` | Durable Task orchestration: investigate → approve → execute |
| `durable_startup.py` | Bootstrap: starts the DurableTask worker when `DURABLE_TASK_ENDPOINT` is set |
| `teams_bot.py` | `POST /api/messages` handler + proactive card sender |
| `teams_cards.py` | Adaptive Cards: approval (Approve/Reject) + decision result |
| `teams_connector.py` | Bot Connector client with streaming helpers |
| `teams_state.py` | Durable conversation store (Azure Blob with local-file fallback) |

## Tools

| Tool | Purpose |
|------|---------|
| `check_dues(department?)` | Returns pending dues in rupees for one or all departments |
| `clear_due(department)` | Marks a department's due as cleared (simulates department response) |
| `request_approval(action, description, ...)` | Human-in-the-loop gate — pauses until officer approves |
| `issue_no_dues_certificate(officer_approved)` | Issues final NOC — blocked until approval + all dues cleared |
| `save_request(filename, content, student_id?)` | Saves dossier to `$HOME/nodues/` for audit trail |

## Sample student data

The built-in demo student has one deliberate pending due so the approval flow triggers on stage:

```
Student: Arjun (22CSE114, CSE Sem 6)
Dues:    library=0, lab=0, hostel=1500, fees=0
```

## Deploy

```bash
azd up
azd ai agent invoke --local "I need my no-dues certificate"
```

## Optional integrations

| Env var | Purpose |
|---------|---------|
| `DURABLE_TASK_ENDPOINT` | Azure Durable Task Scheduler endpoint (enables durable approval gate) |
| `DURABLE_TASK_TASKHUB` | Task hub name (default: `nodues-hub`) |
| `APPROVER_UPN` | Officer UPN for proactive Teams card |
| `TEAMS_STATE_BLOB_URL` | Blob URL for cross-session Teams conversation store |

All optional — the agent runs end-to-end with built-in data and an in-process approval
fallback when these are not set.

## Teams setup

See [TEAMS-SETUP.md](./TEAMS-SETUP.md) for the one-time enablement steps.
