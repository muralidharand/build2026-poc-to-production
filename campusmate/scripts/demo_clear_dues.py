"""
Demo helper — clear student dues programmatically via the nodues-coordinator admin API.

The nodues-coordinator container exposes /admin/clear-dues and /admin/dues
as HTTP endpoints on the same port as the agent protocol. Foundry proxies all
paths through the project endpoint, so we route through the Foundry API gateway
with an Azure AD bearer token.

Usage:
    # Install deps (one-time):
    pip install azure-identity requests

    # Clear Arjun's hostel due:
    python demo_clear_dues.py --student 22CSE114 --dept hostel

    # Clear all dues for Kiran (already zero, but useful to reset after a demo run):
    python demo_clear_dues.py --student 21MECH045 --all

    # Check current dues for all students:
    python demo_clear_dues.py --check

Environment variables (or edit the constants below):
    FOUNDRY_PROJECT_ENDPOINT   e.g. https://<account>.services.ai.azure.com/api/projects/<project>
    AGENT_NAME                 defaults to nodues-coordinator
"""

import argparse
import json
import os
import sys

import requests
from azure.identity import DefaultAzureCredential, InteractiveBrowserCredential

# ── Config ─────────────────────────────────────────────────────────────────────

FOUNDRY_PROJECT_ENDPOINT = os.getenv(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://ai-campusmate-eastus2.services.ai.azure.com/api/projects/campusmate-kit",
)
AGENT_NAME = os.getenv("AGENT_NAME", "nodues-coordinator")

# Foundry API scope
FOUNDRY_SCOPE = "https://ai.azure.com/.default"


# ── Auth ───────────────────────────────────────────────────────────────────────

def get_token() -> str:
    """Get an Azure AD bearer token. Uses DefaultAzureCredential (works with
    az login, managed identity, env vars). Falls back to browser login if needed."""
    try:
        cred = DefaultAzureCredential()
        token = cred.get_token(FOUNDRY_SCOPE)
        return token.token
    except Exception:
        # Fall back to interactive browser login (useful on Windows without az CLI)
        cred = InteractiveBrowserCredential()
        token = cred.get_token(FOUNDRY_SCOPE)
        return token.token


# ── API helpers ────────────────────────────────────────────────────────────────

def _agent_url(path: str) -> str:
    base = FOUNDRY_PROJECT_ENDPOINT.rstrip("/")
    return f"{base}/agents/{AGENT_NAME}/{path.lstrip('/')}"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def check_dues(token: str, student_id: str = "") -> dict:
    """GET /admin/dues — returns all students or one student's dues."""
    url = _agent_url("/admin/dues")
    params = {"student_id": student_id} if student_id else {}
    resp = requests.get(url, headers=_headers(token), params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def clear_dues(token: str, student_id: str, department: str = "", clear_all: bool = False) -> dict:
    """POST /admin/clear-dues — clear one department or all dues for a student."""
    url = _agent_url("/admin/clear-dues")
    body: dict = {"student_id": student_id}
    if clear_all:
        body["all"] = True
    elif department:
        body["department"] = department
    else:
        raise ValueError("Provide --dept <name> or --all")
    resp = requests.post(url, headers=_headers(token), json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    global FOUNDRY_PROJECT_ENDPOINT

    parser = argparse.ArgumentParser(description="CampusMate NoDues demo helper")
    parser.add_argument("--student", help="Student ID (e.g. 22CSE114)")
    parser.add_argument("--dept", help="Department to clear (library|lab|hostel|fees)")
    parser.add_argument("--all", action="store_true", help="Clear ALL dues for the student")
    parser.add_argument("--check", action="store_true", help="Show current dues (no changes)")
    parser.add_argument(
        "--endpoint",
        help="Override FOUNDRY_PROJECT_ENDPOINT",
        default=FOUNDRY_PROJECT_ENDPOINT,
    )
    args = parser.parse_args()

    FOUNDRY_PROJECT_ENDPOINT = args.endpoint

    print(f"Agent: {_agent_url('...')}")
    print("Getting Azure AD token...")
    token = get_token()
    print("Token acquired.\n")

    if args.check:
        result = check_dues(token, args.student or "")
        print(json.dumps(result, indent=2))
        return

    if not args.student:
        parser.error("--student is required for clear operations")

    result = clear_dues(token, args.student, args.dept or "", args.all)
    print(json.dumps(result, indent=2))

    if result.get("all_cleared"):
        print(f"\n✓ All dues cleared for {result.get('student')}.")
        print(  f"  Now ask the agent: \"Approved. Please issue the No-Dues certificate for {args.student}.\"")
    else:
        remaining = {k: v for k, v in result.get("current_dues", {}).items() if v > 0}
        print(f"\n⚠ Remaining dues: {remaining}")


if __name__ == "__main__":
    main()
