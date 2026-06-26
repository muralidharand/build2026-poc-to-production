"""Teams conversation state management for the No-Dues Coordinator.

Persists Teams conversation references so we can proactively send
messages and Adaptive Cards to the approver.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_STATE_DIR = Path(os.path.expanduser("~")) / "nodues"


def _state_file() -> Path:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    return _STATE_DIR / "teams_state.json"


def load_state() -> dict[str, Any]:
    f = _state_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict[str, Any]) -> None:
    _state_file().write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_conversation_reference(user_id: str) -> dict[str, Any] | None:
    state = load_state()
    return state.get("conversations", {}).get(user_id)


def set_conversation_reference(user_id: str, reference: dict[str, Any]) -> None:
    state = load_state()
    state.setdefault("conversations", {})[user_id] = reference
    save_state(state)


def get_approver_upn() -> str:
    return os.getenv("APPROVER_UPN", "")
