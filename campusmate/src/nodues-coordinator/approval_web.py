"""Approval web server for the No-Dues Coordinator.

Serves a dark-themed HTML UI where an officer can review and
approve/reject pending no-dues certificate requests.

Runs on APPROVAL_SERVER_PORT (default 8090).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from aiohttp import web  # type: ignore[import]
except ImportError:  # pragma: no cover
    web = None  # type: ignore[assignment]

from durable_orchestration import durable_manager

_DATA_DIR = Path(os.path.expanduser("~")) / "nodues"
_APPROVALS_FILE = _DATA_DIR / "approvals.json"
_PORT = int(os.getenv("APPROVAL_SERVER_PORT", "8090"))

# ---------------------------------------------------------------------------
# In-memory + file registry
# ---------------------------------------------------------------------------

_registry: dict[str, dict[str, Any]] = {}


def _load_registry() -> None:
    global _registry
    if _APPROVALS_FILE.exists():
        try:
            _registry = json.loads(_APPROVALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            _registry = {}


def _save_registry() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _APPROVALS_FILE.write_text(json.dumps(_registry, indent=2), encoding="utf-8")


def register_approval(
    approval_id: str,
    action: str,
    description: str,
    student_id: str = "",
    student_name: str = "",
    severity: str = "critical",
    context: str = "",
    approval_url: str = "",
) -> None:
    """Register a pending approval in the registry."""
    _registry[approval_id] = {
        "id": approval_id,
        "action": action,
        "description": description,
        "student_id": student_id,
        "student_name": student_name,
        "severity": severity,
        "context": context,
        "approval_url": approval_url,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "decided_at": None,
    }
    _save_registry()


# ---------------------------------------------------------------------------
# HTML helpers (dark theme, no external deps)
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Segoe UI', system-ui, sans-serif;
  background: #0A1230;
  color: #c9d1e8;
  min-height: 100vh;
  padding: 2rem;
}
h1, h2 { color: #35D6EE; margin-bottom: 1rem; }
a { color: #35D6EE; text-decoration: none; }
a:hover { text-decoration: underline; }
.card {
  background: #111c40;
  border: 1px solid #1e2f5e;
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.25rem;
  max-width: 720px;
}
.badge {
  display: inline-block;
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  margin-left: 0.5rem;
}
.badge-pending  { background: #35D6EE22; color: #35D6EE; border: 1px solid #35D6EE55; }
.badge-approved { background: #1a4a2e; color: #4ade80; border: 1px solid #4ade8055; }
.badge-rejected { background: #3b1a1a; color: #f87171; border: 1px solid #f8717155; }
.badge-critical { background: #3b1a1a; color: #f97316; border: 1px solid #f9731655; }
.dues-clear { color: #4ade80; font-weight: 600; }
.fact { display: flex; gap: 0.5rem; margin: 0.35rem 0; font-size: 0.9rem; }
.fact-label { color: #6b7aa0; min-width: 140px; }
.actions { display: flex; gap: 0.75rem; margin-top: 1rem; }
button, .btn {
  cursor: pointer;
  border: none;
  border-radius: 8px;
  padding: 0.5rem 1.25rem;
  font-size: 0.9rem;
  font-weight: 600;
  transition: opacity 0.15s;
}
button:hover, .btn:hover { opacity: 0.85; }
.btn-approve { background: #166534; color: #4ade80; }
.btn-reject  { background: #374151; color: #9ca3af; }
.empty { color: #4a5a80; font-style: italic; margin-top: 2rem; }
@media (max-width: 600px) { body { padding: 1rem; } .actions { flex-direction: column; } }
"""

def _base(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — KIT No-Dues</title>
<style>{_CSS}</style>
</head>
<body>
<h1>KIT No-Dues Coordinator</h1>
{body}
</body>
</html>"""


def _status_badge(status: str) -> str:
    cls = {"pending": "badge-pending", "approved": "badge-approved",
           "rejected": "badge-rejected"}.get(status, "badge-pending")
    return f'<span class="badge {cls}">{status}</span>'


def _render_list() -> str:
    items = list(_registry.values())
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    if not items:
        return _base("Approvals", '<p class="empty">No pending approval requests.</p>')

    cards = []
    for item in items:
        status = item.get("status", "pending")
        sev = item.get("severity", "")
        sev_badge = f'<span class="badge badge-critical">{sev}</span>' if sev else ""
        dues_info = ""
        if "all dues cleared" in (item.get("context", "")).lower() or item.get("student_id"):
            dues_info = '<span class="dues-clear">all dues cleared ✓</span>'

        cards.append(f"""<div class="card">
  <h2><a href="/approvals/{item['id']}">{item.get('action','Request')}</a>
    {_status_badge(status)}{sev_badge}
  </h2>
  <div class="fact"><span class="fact-label">Approval ID</span><span>{item['id']}</span></div>
  <div class="fact"><span class="fact-label">Student</span>
    <span>{item.get('student_name','')} {item.get('student_id','')}</span></div>
  <div class="fact"><span class="fact-label">Description</span>
    <span>{item.get('description','')}</span></div>
  {f'<div class="fact"><span class="fact-label">Dues status</span><span>{dues_info}</span></div>' if dues_info else ''}
  <div class="fact"><span class="fact-label">Raised</span>
    <span>{item.get('created_at','')}</span></div>
  {"" if status != "pending" else f'''<div class="actions">
    <form method="post" action="/approvals/{item['id']}/approve" style="display:inline">
      <button class="btn btn-approve" type="submit">Approve</button>
    </form>
    <form method="post" action="/approvals/{item['id']}/reject" style="display:inline">
      <button class="btn btn-reject" type="submit">Reject</button>
    </form>
  </div>'''}
</div>""")

    return _base("Approvals", "\n".join(cards))


def _render_detail(item: dict[str, Any]) -> str:
    status = item.get("status", "pending")
    approval_id = item["id"]
    facts = [
        ("Approval ID", approval_id),
        ("Action", item.get("action", "")),
        ("Severity", item.get("severity", "")),
        ("Student", f"{item.get('student_name','')} ({item.get('student_id','')})"),
        ("Description", item.get("description", "")),
        ("Context", item.get("context", "")),
        ("Status", status),
        ("Raised", item.get("created_at", "")),
        ("Decided", item.get("decided_at", "—")),
    ]
    facts_html = "\n".join(
        f'<div class="fact"><span class="fact-label">{k}</span><span>{v}</span></div>'
        for k, v in facts if v
    )
    action_html = "" if status != "pending" else f"""<div class="actions">
  <form method="post" action="/approvals/{approval_id}/approve">
    <button class="btn btn-approve" type="submit">Approve</button>
  </form>
  <form method="post" action="/approvals/{approval_id}/reject">
    <button class="btn btn-reject" type="submit">Reject</button>
  </form>
</div>"""
    body = f"""
<p><a href="/approvals">← Back to all requests</a></p>
<div class="card">
  <h2>{item.get('action','Request')} {_status_badge(status)}</h2>
  {facts_html}
  {action_html}
</div>"""
    return _base(f"Approval {approval_id}", body)


# ---------------------------------------------------------------------------
# aiohttp routes
# ---------------------------------------------------------------------------

async def _handle_root(request: Any) -> Any:
    raise web.HTTPFound("/approvals")  # type: ignore[union-attr]


async def _handle_list(request: Any) -> Any:
    _load_registry()
    return web.Response(text=_render_list(), content_type="text/html")  # type: ignore[union-attr]


async def _handle_detail(request: Any) -> Any:
    _load_registry()
    approval_id = request.match_info["id"]
    item = _registry.get(approval_id)
    if not item:
        raise web.HTTPNotFound(text=f"Approval {approval_id} not found")  # type: ignore[union-attr]
    return web.Response(text=_render_detail(item), content_type="text/html")  # type: ignore[union-attr]


async def _handle_approve(request: Any) -> Any:
    approval_id = request.match_info["id"]
    _load_registry()
    item = _registry.get(approval_id)
    if item and item["status"] == "pending":
        item["status"] = "approved"
        item["decided_at"] = datetime.utcnow().isoformat() + "Z"
        _save_registry()
        try:
            durable_manager.approve(approval_id)
        except Exception as exc:
            print(f"[approval_web] durable approve failed: {exc}")
    raise web.HTTPFound("/approvals")  # type: ignore[union-attr]


async def _handle_reject(request: Any) -> Any:
    approval_id = request.match_info["id"]
    _load_registry()
    item = _registry.get(approval_id)
    if item and item["status"] == "pending":
        item["status"] = "rejected"
        item["decided_at"] = datetime.utcnow().isoformat() + "Z"
        _save_registry()
        try:
            durable_manager.reject(approval_id)
        except Exception as exc:
            print(f"[approval_web] durable reject failed: {exc}")
    raise web.HTTPFound("/approvals")  # type: ignore[union-attr]


async def _handle_approve_json(request: Any) -> Any:
    approval_id = request.match_info["id"]
    _load_registry()
    item = _registry.get(approval_id)
    if not item:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)  # type: ignore[union-attr]
    item["status"] = "approved"
    item["decided_at"] = datetime.utcnow().isoformat() + "Z"
    _save_registry()
    try:
        durable_manager.approve(approval_id)
    except Exception as exc:
        print(f"[approval_web] durable approve failed: {exc}")
    return web.json_response({"ok": True, "status": "approved"})  # type: ignore[union-attr]


async def _handle_reject_json(request: Any) -> Any:
    approval_id = request.match_info["id"]
    _load_registry()
    item = _registry.get(approval_id)
    if not item:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)  # type: ignore[union-attr]
    item["status"] = "rejected"
    item["decided_at"] = datetime.utcnow().isoformat() + "Z"
    _save_registry()
    try:
        durable_manager.reject(approval_id)
    except Exception as exc:
        print(f"[approval_web] durable reject failed: {exc}")
    return web.json_response({"ok": True, "status": "rejected"})  # type: ignore[union-attr]


async def _handle_healthz(request: Any) -> Any:
    return web.json_response({"ok": True})  # type: ignore[union-attr]


def create_app() -> Any:
    """Create and return the aiohttp Application."""
    if web is None:  # pragma: no cover
        raise RuntimeError("aiohttp is not installed")

    app = web.Application()
    app.router.add_get("/", _handle_root)
    app.router.add_get("/approvals", _handle_list)
    app.router.add_get("/approvals/{id}", _handle_detail)
    app.router.add_post("/approvals/{id}/approve", _handle_approve)
    app.router.add_post("/approvals/{id}/reject", _handle_reject)
    app.router.add_post("/approve/{id}", _handle_approve_json)
    app.router.add_post("/reject/{id}", _handle_reject_json)
    app.router.add_get("/healthz", _handle_healthz)
    return app


async def run_approval_server() -> None:
    """Start the approval web server (async, runs until cancelled)."""
    if web is None:  # pragma: no cover
        print("[approval_web] aiohttp not installed — skipping approval server.")
        return

    _load_registry()
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", _PORT)
    await site.start()
    print(f"[approval_web] Listening on http://0.0.0.0:{_PORT}/approvals")
