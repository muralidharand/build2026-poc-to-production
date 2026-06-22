"""Durable Task startup integration for Fibey.

Import this module at the end of main.py (before responses.run()) to start
the DurableTask worker and approval HTTP server alongside the agent host.
"""
import asyncio
import logging
import os
import threading

from aiohttp import web

from durable_orchestration import durable_manager

logger = logging.getLogger(__name__)

APPROVAL_PORT = int(os.getenv("APPROVAL_SERVER_PORT", "8090"))


# ---------------------------------------------------------------------------
# Approval HTTP server (runs alongside agent on a separate port)
# ---------------------------------------------------------------------------

async def handle_approve(request: web.Request) -> web.Response:
    instance_id = request.match_info["instance_id"]
    body = await request.json() if request.can_read_body else {}
    durable_manager.approve(instance_id, approver=body.get("approver", "user"), feedback=body.get("feedback", ""))
    return web.json_response({"status": "approved", "instance_id": instance_id})

async def handle_reject(request: web.Request) -> web.Response:
    instance_id = request.match_info["instance_id"]
    body = await request.json() if request.can_read_body else {}
    durable_manager.reject(instance_id, approver=body.get("approver", "user"), feedback=body.get("feedback", ""))
    return web.json_response({"status": "rejected", "instance_id": instance_id})

async def handle_status(request: web.Request) -> web.Response:
    instance_id = request.match_info["instance_id"]
    state = durable_manager.get_status(instance_id)
    if state is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"instance_id": instance_id, "runtime_status": str(state.runtime_status)})

async def handle_investigate(request: web.Request) -> web.Response:
    body = await request.json()
    instance_id = durable_manager.start_investigation(
        session_id=body.get("session_id", "demo"),
        user_message=body.get("message", ""),
        destructive_action=body.get("destructive_action"),
        context=body.get("context"),
    )
    return web.json_response({
        "instance_id": instance_id,
        "approve_url": f"/approve/{instance_id}",
        "reject_url": f"/reject/{instance_id}",
    }, status=202)


def _start_approval_server():
    app = web.Application()
    app.router.add_post("/approve/{instance_id}", handle_approve)
    app.router.add_post("/reject/{instance_id}", handle_reject)
    app.router.add_get("/orchestration/{instance_id}", handle_status)
    app.router.add_post("/investigate", handle_investigate)

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "0.0.0.0", APPROVAL_PORT)
        loop.run_until_complete(site.start())
        logger.info(f"[DurableTask] Approval server listening on :{APPROVAL_PORT}")
        loop.run_forever()

    thread = threading.Thread(target=_run, name="approval-server", daemon=True)
    thread.start()


# ---------------------------------------------------------------------------
# Bootstrap — called when this module is imported
# ---------------------------------------------------------------------------

def bootstrap():
    """Start DurableTask worker + approval server. Call before responses.run()."""
    dts_endpoint = os.getenv("DURABLE_TASK_ENDPOINT", "")
    if not dts_endpoint:
        logger.warning("[DurableTask] DURABLE_TASK_ENDPOINT not set — skipping DTS startup")
        return

    try:
        logger.info("[DurableTask] Starting Durable Task extension...")
        durable_manager.start()
        _start_approval_server()
        logger.info("[DurableTask] ✅ Ready (worker + approval server)")
    except Exception as e:
        logger.error(f"[DurableTask] ⚠️ Failed to start: {e} — agent will serve without DTS")
        logger.exception(e)
