"""No-Dues Coordinator — entry point.

Long-running agent with human-in-the-loop approval flow for KIT no-dues
certificates. Uses durable orchestration to suspend/resume on officer decisions.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Instrumentation (before SDK imports)
# ---------------------------------------------------------------------------
try:
    from azure.ai.agentserver.core.tracing import enable_instrumentation  # type: ignore[import]

    enable_instrumentation(
        enable_sensitive_data=os.getenv("ENABLE_SENSITIVE_DATA", "false").lower() == "true"
    )
except ImportError:
    print("[main] Instrumentation not available — running without telemetry.")

# ---------------------------------------------------------------------------
# Agent server
# ---------------------------------------------------------------------------
try:
    from azure.ai.agentserver.responses import ResponsesAgentServerHost  # type: ignore[import]
except ImportError as exc:
    raise SystemExit("azure-ai-agentserver-responses is not installed.") from exc

try:
    from agent_framework_foundry import FoundryChatClient, FunctionTool  # type: ignore[import]
except ImportError as exc:
    raise SystemExit("agent_framework_foundry is not installed.") from exc

from approval_web import register_approval
from durable_orchestration import durable_manager
from durable_startup import start_background_services
from teams_cards import build_approval_card
from teams_connector import send_approval_card as teams_send_card

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
_HOME_DIR = Path(os.path.expanduser("~")) / "nodues"
_HOME_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Student roster (KIT sample data)
# ---------------------------------------------------------------------------
STUDENTS: dict[str, dict[str, Any]] = {
    "22CSE114": {
        "id": "22CSE114", "name": "Arjun", "dept": "CSE", "semester": 6,
        "dues": {"library": 0, "lab": 0, "hostel": 1500, "fees": 0},
    },
    "22ECE089": {
        "id": "22ECE089", "name": "Priya", "dept": "ECE", "semester": 6,
        "dues": {"library": 200, "lab": 500, "hostel": 0, "fees": 0},
    },
    "21MECH045": {
        "id": "21MECH045", "name": "Kiran", "dept": "MECH", "semester": 8,
        "dues": {"library": 0, "lab": 0, "hostel": 0, "fees": 0},
    },
    "22CSE058": {
        "id": "22CSE058", "name": "Deepa", "dept": "CSE", "semester": 6,
        "dues": {"library": 0, "lab": 0, "hostel": 0, "fees": 0},
    },
    "22EEE072": {
        "id": "22EEE072", "name": "Vignesh", "dept": "EEE", "semester": 6,
        "dues": {"library": 0, "lab": 0, "hostel": 0, "fees": 0},
    },
    "22CSE141": {
        "id": "22CSE141", "name": "Sanjay", "dept": "CSE", "semester": 6,
        "dues": {"library": 0, "lab": 0, "hostel": 3000, "fees": 8500},
    },
    "22IT020": {
        "id": "22IT020", "name": "Lakshmi", "dept": "IT", "semester": 6,
        "dues": {"library": 150, "lab": 0, "hostel": 0, "fees": 0},
    },
}

_VALID_IDS = ", ".join(sorted(STUDENTS.keys()))

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def check_dues(student_id: str, department: str = "") -> str:
    """Check outstanding dues for a student, optionally filtered by department."""
    student = STUDENTS.get(student_id)
    if not student:
        return json.dumps({
            "status": "error",
            "message": f"Student ID '{student_id}' not found.",
            "valid_ids": _VALID_IDS,
        })
    dues = student["dues"]
    if department:
        dept_key = department.lower().strip()
        if dept_key not in dues:
            return json.dumps({
                "status": "error",
                "message": f"Unknown department '{department}'. Valid: {list(dues.keys())}",
            })
        amount = dues[dept_key]
        return json.dumps({
            "status": "success",
            "student_id": student_id,
            "name": student["name"],
            "department": dept_key,
            "amount_due": amount,
            "currency": "INR",
        }, indent=2)
    return json.dumps({
        "status": "success",
        "student_id": student_id,
        "name": student["name"],
        "dues": {k: f"₹{v}" for k, v in dues.items()},
        "total_due": f"₹{sum(dues.values())}",
    }, indent=2)


def clear_due(student_id: str, department: str) -> str:
    """Clear dues for a specific department for a student."""
    student = STUDENTS.get(student_id)
    if not student:
        return json.dumps({"status": "error", "message": f"Student '{student_id}' not found."})
    dept_key = department.lower().strip()
    if dept_key not in student["dues"]:
        return json.dumps({"status": "error",
                           "message": f"Unknown department '{department}'."})
    old_amount = student["dues"][dept_key]
    student["dues"][dept_key] = 0
    return json.dumps({
        "status": "success",
        "student_id": student_id,
        "name": student["name"],
        "department": dept_key,
        "cleared": f"₹{old_amount}",
        "remaining_dues": {k: f"₹{v}" for k, v in student["dues"].items()},
    }, indent=2)


def pay_all_dues(student_id: str) -> str:
    """Clear all dues for a student."""
    student = STUDENTS.get(student_id)
    if not student:
        return json.dumps({"status": "error", "message": f"Student '{student_id}' not found."})
    zeroed = dict(student["dues"])
    student["dues"] = {k: 0 for k in student["dues"]}
    return json.dumps({
        "status": "success",
        "student_id": student_id,
        "name": student["name"],
        "zeroed_dues": {k: f"₹{v}" for k, v in zeroed.items()},
        "all_dues_cleared": True,
    }, indent=2)


def request_approval(
    action: str,
    description: str,
    severity: str = "critical",
    context: str = "",
) -> str:
    """Start a durable human-approval flow and return an approval ID + URL."""
    approval_server = os.getenv("APPROVAL_SERVER_URL", f"http://localhost:{os.getenv('APPROVAL_SERVER_PORT', '8090')}")

    # Extract student info from context if present
    student_id = ""
    student_name = ""
    for student in STUDENTS.values():
        if student["id"] in context or student["id"] in description:
            student_id = student["id"]
            student_name = student["name"]
            break

    instance_id = durable_manager.start_approval_orchestration(
        action=action,
        description=description,
        context=context,
        severity=severity,
        student_id=student_id,
    )

    approval_url = f"{approval_server}/approvals/{instance_id}"

    register_approval(
        approval_id=instance_id,
        action=action,
        description=description,
        student_id=student_id,
        student_name=student_name,
        severity=severity,
        context=context,
        approval_url=approval_url,
    )

    # Best-effort Teams card
    try:
        card = build_approval_card(
            approval_id=instance_id,
            approval_url=approval_url,
            action=action,
            description=description,
            student_name=student_name,
            student_id=student_id,
            severity=severity,
            context=context,
        )
        teams_send_card(instance_id, approval_url, action, description, card)
    except Exception as exc:
        print(f"[main] Teams card send skipped: {exc}")

    return json.dumps({
        "status": "approval_pending",
        "approval_id": instance_id,
        "approval_url": approval_url,
        "message": (
            f"Approval request raised. The officer can approve or reject at: {approval_url}"
        ),
    }, indent=2)


def issue_no_dues_certificate(student_id: str, officer_approved: bool = False) -> str:
    """Issue a no-dues certificate. Requires officer approval and zero dues."""
    student = STUDENTS.get(student_id)
    if not student:
        return json.dumps({"status": "error", "message": f"Student '{student_id}' not found."})

    total_dues = sum(student["dues"].values())
    if total_dues > 0:
        return json.dumps({
            "status": "blocked",
            "reason": f"Student has outstanding dues totalling ₹{total_dues}. "
                      "All dues must be cleared before issuing a certificate.",
            "dues": {k: f"₹{v}" for k, v in student["dues"].items()},
        })

    if not officer_approved:
        return json.dumps({
            "status": "blocked",
            "reason": "Officer approval is required before issuing the certificate. "
                      "Call request_approval first and wait for the officer to approve.",
        })

    cert_number = f"NOC-{student_id}-{date.today().strftime('%Y%m%d')}"
    return json.dumps({
        "status": "success",
        "certificate_number": cert_number,
        "student_id": student_id,
        "name": student["name"],
        "dept": student["dept"],
        "semester": student["semester"],
        "message": (
            f"No-Dues Certificate issued: {cert_number}. "
            "The student may collect the physical certificate from the Registrar's office."
        ),
    }, indent=2)


def save_request(filename: str, content: str, student_id: str = "") -> str:
    """Persist a request summary or document under ~/nodues/."""
    save_dir = _HOME_DIR / (student_id if student_id else "general")
    save_dir.mkdir(parents=True, exist_ok=True)
    target = save_dir / filename
    target.write_text(content, encoding="utf-8")
    return json.dumps({
        "status": "success",
        "path": str(target),
    })


# ---------------------------------------------------------------------------
# Agent setup
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = f"""You are the **No-Dues Coordinator** for Kovai Institute of Technology (KIT).
Your job is to help students obtain their no-dues clearance certificate by guiding them through
the following process:

1. **Check dues** — Call check_dues to see what amounts are outstanding.
2. **State the amounts** — Clearly tell the student what they owe, department by department.
3. **Clear dues** — Use clear_due per department, or pay_all_dues to zero everything at once.
4. **Request officer approval** — When all dues are zero, call request_approval. The officer
   will review the request via the approval portal.
5. **Issue certificate** — ONLY after the officer approves (officer_approved=True) AND all dues
   are zero, call issue_no_dues_certificate.

**NEVER** issue a certificate if:
- Any due is still non-zero, or
- officer_approved is False / approval has not been confirmed.

Valid student IDs: {_VALID_IDS}
"""

_TOOL_FUNCTIONS = [
    check_dues,
    clear_due,
    pay_all_dues,
    request_approval,
    issue_no_dues_certificate,
    save_request,
]

app = ResponsesAgentServerHost()

_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        model = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5")
        tools = [FunctionTool(func=fn, name=fn.__name__) for fn in _TOOL_FUNCTIONS]
        _agent = FoundryChatClient(model=model).as_agent(
            name="nodues-coordinator",
            instructions=_SYSTEM_PROMPT,
            tools=tools,
        )
    return _agent


@app.response_handler
async def handle_request(request: Any, response_stream: Any) -> None:
    """Forward each student message to the no-dues coordinator agent."""
    messages: list[dict[str, Any]] = request.messages or []
    agent = _get_agent()
    try:
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_text = msg.get("content", "")
                break

        result = agent.run(user_text, context=messages)  # type: ignore[union-attr]
        await response_stream.send_text(result or "I couldn't process that request.")
    except Exception as exc:
        await response_stream.send_text(
            f"Sorry, I ran into a problem: {exc}. Please try again."
        )


async def _async_main() -> None:
    await start_background_services()
    app.run()


if __name__ == "__main__":
    asyncio.run(_async_main())
