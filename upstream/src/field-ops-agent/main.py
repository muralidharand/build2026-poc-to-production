"""Field Operations Agent.

A voice-enabled field technician assistant for data center operations.

═══════════════════════════════════════════════════════════════════════════════
FILE MAP (open in this order for the walkthrough)

  1. agent.yaml              → Hosted agent config (Foundry deploys this).
  2. toolbox.py              → Concept #1: Toolbox MCP integration.
  3. worker_agent.py         → Concept #2: Agent Framework — tools, agent
                                 loop (FoundryChatClient.as_agent), and
                                 how Foundry-managed tools sit alongside
                                 local @tool functions.
  4. router_agent.py         → Concept #3: Voice routing pattern (front
                                 desk / back office) that fills the
                                 dead-space gap during tool calls.
  5. task_store.py           → Supporting state for the voice pattern.
  6. main.py (this file)     → Thin glue: server host, request handler,
                                 router dispatch, streaming wire-up.

═══════════════════════════════════════════════════════════════════════════════
RUNTIME ARCHITECTURE

  voice in ──► ResponsesAgentServerHost (this file)
                       │
                       ▼
              ┌── router_agent.py  ◄── tool_choice="required"
              │   (single LLM call, picks ONE of 5 meta-tools)
              │
       ┌──────┴──────┬───────────────┬─────────────────┐
       ▼             ▼               ▼                 ▼
   respond_      start_task     check_task_      get/cancel
   directly         │           status              │
       │            ▼                                │
       │   ┌────────────────────┐                    │
       │   │ worker_agent.py    │  fire-and-forget   │
       │   │ MAF agent.run()    │  asyncio.Task      │
       │   │ + @tool functions  │                    │
       │   │ + Toolbox MCP tool │                    │
       │   └────────────────────┘                    │
       │            │                                │
       └────────────┴────────────────────────────────┘
                       │
                       ▼
              streaming response (ack → wait fillers → final result)
═══════════════════════════════════════════════════════════════════════════════

Required environment variables:
    FOUNDRY_PROJECT_ENDPOINT        Foundry project endpoint (auto-injected)
    AZURE_AI_MODEL_DEPLOYMENT_NAME  Model deployment name

Optional:
    TOOLBOX_ENDPOINT                Enable Foundry Toolbox tools (see toolbox.py)
"""

import asyncio
import atexit
import json
import logging
import os
import pathlib
import re
from collections.abc import AsyncIterable
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

load_dotenv(override=False)

from azure.ai.projects import AIProjectClient
from azure.ai.projects.aio import AIProjectClient as AsyncAIProjectClient
from azure.identity import DefaultAzureCredential
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential

from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponseEventStream,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
)
from azure.ai.agentserver.responses.models import (
    MessageContentInputTextContent,
    MessageContentOutputTextContent,
)

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse as StarletteJSONResponse

from task_store import TaskStore, TaskStatus
from router_agent import (
    ROUTER_SYSTEM_PROMPT,
    ROUTER_TOOLS,
    ROUTER_TOOL_CHOICE,
    build_task_context,
    build_delivered_context,
    execute_router_tool,
)
from worker_agent import create_worker_agent, run_worker

from agent_framework.observability import enable_instrumentation

enable_instrumentation(enable_sensitive_data=True)


# ── Agent name and logger ─────────────────────────────────────────────────────


def _read_agent_name() -> str:
    try:
        yaml_text = pathlib.Path("agent.yaml").read_text()
        m = re.search(r"^name:\s*(.+)$", yaml_text, re.MULTILINE)
        return m.group(1).strip() if m else "field-ops-agent"
    except Exception:
        return "field-ops-agent"


AGENT_NAME = _read_agent_name()
logger = logging.getLogger(AGENT_NAME)


# ── Environment & Clients ─────────────────────────────────────────────────────

_endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
if not _endpoint:
    raise EnvironmentError("FOUNDRY_PROJECT_ENDPOINT not set")

_model = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME") or os.environ.get(
    "MODEL_DEPLOYMENT_NAME"
)
if not _model:
    raise EnvironmentError(
        "Model deployment not configured. Set AZURE_AI_MODEL_DEPLOYMENT_NAME "
        "(or the legacy MODEL_DEPLOYMENT_NAME fallback)."
    )

_credential = DefaultAzureCredential()
_async_credential = AsyncDefaultAzureCredential()
# Sync project client → sync OpenAI chat completions client for the router
# (router calls .create() with stream=True inside a thread executor, so a sync
# client is the right shape).
_project_client = AIProjectClient(endpoint=_endpoint, credential=_credential)
_openai_client = _project_client.get_openai_client()
_chat_completion_client = _openai_client.chat.completions

# Async project client → async OpenAI for the MAF worker.
# MAF's chat client awaits responses (`await client.chat.completions.create(...)`),
# so it requires an ``AsyncOpenAI``. ``azure.ai.projects.aio.AIProjectClient``
# is what returns one from ``get_openai_client()``.
_async_project_client = AsyncAIProjectClient(
    endpoint=_endpoint, credential=_async_credential
)

# MAF worker agent (shared instance, reused across requests).
# - `credential=_async_credential` → used by FoundryChatClient (it awaits get_token).
# - `toolbox_credential=_credential` → used by toolbox httpx auth flow, which is
#   synchronous and breaks if handed an AsyncTokenCredential ('coroutine' object
#   has no attribute 'token').
_worker_agent = create_worker_agent(
    project_client=_async_project_client,
    model=_model,
    credential=_async_credential,
    toolbox_credential=_credential,
    name=AGENT_NAME,
)

# ── Debug logging for chat.completions troubleshooting ───────────────────────
# Surface what model and endpoint each call site will use so we can match
# the 401s we see in httpx logs to the call site that produced them.
_openai_base_url = getattr(_openai_client, "base_url", None) or "<unknown>"
logger.warning(
    "[%s] startup config | router_model=%s | worker_fallback_model=%s | "
    "FOUNDRY_PROJECT_ENDPOINT=%s | openai_base_url=%s",
    AGENT_NAME,
    _model,
    _model,
    _endpoint,
    _openai_base_url,
)
logger.warning(
    "[%s] startup env | AZURE_AI_MODEL_DEPLOYMENT_NAME=%r | MODEL_DEPLOYMENT_NAME=%r | "
    "OPTIMIZATION_CONFIG_set=%s | OPTIMIZATION_CANDIDATE_ID=%r | OPTIMIZATION_RESOLVE_ENDPOINT=%r | "
    "OPTIMIZATION_LOCAL_DIR=%r",
    AGENT_NAME,
    os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME"),
    os.environ.get("MODEL_DEPLOYMENT_NAME"),
    bool(os.environ.get("OPTIMIZATION_CONFIG")),
    os.environ.get("OPTIMIZATION_CANDIDATE_ID"),
    os.environ.get("OPTIMIZATION_RESOLVE_ENDPOINT"),
    os.environ.get("OPTIMIZATION_LOCAL_DIR"),
)

logger.info(
    "[%s] starting up (model=%s, endpoint=%s)",
    AGENT_NAME,
    _model,
    _endpoint,
)


# ── Shared State ──────────────────────────────────────────────────────────────

_store = TaskStore()
_background_tasks: set[asyncio.Task] = set()  # prevent GC of fire-and-forget workers
_llm_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")
# Best-effort shutdown so threads don't outlive the process on host teardown.
atexit.register(_llm_executor.shutdown, wait=False)
_active_cancel_signals: dict[str, asyncio.Event] = (
    {}
)  # response_id -> cancellation signal


# ── App ───────────────────────────────────────────────────────────────────────

app = ResponsesAgentServerHost(
    options=ResponsesServerOptions(default_fetch_history_count=20),
)


# ── Cancel Middleware ─────────────────────────────────────────────────────────
# The framework's built-in /cancel handler only works for background responses.
# For streaming (non-background) responses, we intercept the cancel request here,
# set the cancellation signal ourselves, and return 200.


class _CancelMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and request.url.path.endswith("/cancel"):
            parts = request.url.path.rstrip("/").split("/")
            # Expected path: /responses/{response_id}/cancel
            if len(parts) >= 3 and parts[-1] == "cancel":
                response_id = parts[-2]
                signal = _active_cancel_signals.get(response_id)
                if signal:
                    logger.info(
                        "Cancel middleware: signalling response %s", response_id
                    )
                    signal.set()
                    return StarletteJSONResponse(
                        {"id": response_id, "status": "cancelled"},
                        status_code=200,
                    )
        return await call_next(request)


app.add_middleware(_CancelMiddleware)


# ── Helpers ───────────────────────────────────────────────────────────────────

_DEBUG_TAG_RE = re.compile(r"^\[(?:Router|Router-Ack|Router-Result|Worker)\]\s*")


def _strip_debug_tags(text: str) -> str:
    """Remove debug tags from text before feeding back to the model."""
    return _DEBUG_TAG_RE.sub("", text)


def _build_input(current_input: str, history: list) -> list[dict]:
    """Convert conversation history + current input into chat-completions message format."""
    input_items: list[dict] = []
    for item in history:
        if hasattr(item, "content") and item.content:
            for content in item.content:
                if (
                    isinstance(content, MessageContentOutputTextContent)
                    and content.text
                ):
                    input_items.append(
                        {
                            "role": "assistant",
                            "content": _strip_debug_tags(content.text),
                        }
                    )
                elif (
                    isinstance(content, MessageContentInputTextContent) and content.text
                ):
                    input_items.append({"role": "user", "content": content.text})
    input_items.append({"role": "user", "content": current_input})
    return input_items


# ── Streaming Helpers ─────────────────────────────────────────────────────────

_TASK_POLL_INTERVAL = 0.1  # seconds between status checks while waiting


def _emit_text_item(stream: ResponseEventStream, text: str):
    """Yield events for a complete text message output item."""
    item = stream.add_output_item_message()
    events = [item.emit_added()]
    tc = item.add_text_content()
    events.append(tc.emit_added())
    chunk_size = 40
    for i in range(0, len(text), chunk_size):
        events.append(tc.emit_delta(text[i : i + chunk_size]))
    events.append(tc.emit_text_done())
    events.append(tc.emit_done())
    events.append(item.emit_done())
    return events


async def _iter_llm_stream(model, instructions, messages, tools, tool_choice=None):
    """Run a streaming chat-completions call in a thread, yielding events."""
    import time as _time

    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    full_messages: list[dict] = []
    if instructions:
        full_messages.append({"role": "system", "content": instructions})
    full_messages.extend(messages)

    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": full_messages,
        "stream": True,
    }
    if tools:
        cc_tools = []
        for t in tools:
            if t.get("type") == "function" and "function" not in t:
                cc_tools.append(
                    {
                        "type": "function",
                        "function": {k: v for k, v in t.items() if k != "type"},
                    }
                )
            else:
                cc_tools.append(t)
        create_kwargs["tools"] = cc_tools
    if tool_choice:
        create_kwargs["tool_choice"] = tool_choice

    # Router chat.completions debug: log exactly what we're about to call.
    logger.warning(
        "[router] chat.completions.create | model=%r | base_url=%s | endpoint_env=%s | msgs=%d | tools=%d | tool_choice=%r",
        model,
        getattr(_openai_client, "base_url", None),
        _endpoint,
        len(full_messages),
        len(create_kwargs.get("tools") or []),
        tool_choice,
    )
    # Per-message roles + content lengths so we can compare against a working
    # local run if the server returns 500.
    for _idx, _m in enumerate(full_messages):
        _role = _m.get("role")
        _content = _m.get("content")
        if isinstance(_content, str):
            _len = len(_content)
            _preview = _content[:120]
        elif isinstance(_content, list):
            _len = len(_content)
            _preview = repr(_content)[:200]
        else:
            _len = 0
            _preview = repr(_content)[:200]
        logger.warning(
            "[router] msg[%d] role=%s content_len=%d preview=%r",
            _idx,
            _role,
            _len,
            _preview,
        )

    def _run():
        try:
            accumulated_tool_calls: dict = {}  # index -> {id, name, arguments}
            has_text = False

            stream = _chat_completion_client.create(**create_kwargs)
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta

                if delta.content:
                    has_text = True
                    loop.call_soon_threadsafe(
                        q.put_nowait,
                        SimpleNamespace(
                            type="response.output_text.delta", delta=delta.content
                        ),
                    )

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        if tc.id:
                            accumulated_tool_calls[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            accumulated_tool_calls[idx]["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            accumulated_tool_calls[idx][
                                "arguments"
                            ] += tc.function.arguments

                if choice.finish_reason:
                    break

            if has_text:
                loop.call_soon_threadsafe(
                    q.put_nowait,
                    SimpleNamespace(type="response.output_text.done"),
                )

            for idx in sorted(accumulated_tool_calls.keys()):
                tc_data = accumulated_tool_calls[idx]
                item = SimpleNamespace(
                    type="function_call",
                    name=tc_data["name"],
                    arguments=tc_data["arguments"],
                    call_id=tc_data["id"],
                )
                loop.call_soon_threadsafe(
                    q.put_nowait,
                    SimpleNamespace(type="response.output_item.done", item=item),
                )
        except Exception as exc:
            # Try to surface the upstream response body for 4xx/5xx errors so we can
            # tell whether it's a model/payload validation issue vs a true outage.
            _body = None
            try:
                _resp = getattr(exc, "response", None)
                if _resp is not None:
                    _body = _resp.text[:2000]
            except Exception:
                pass
            logger.error(
                "[router] chat.completions failed | model=%r | base_url=%s | error=%s: %s | upstream_body=%s",
                model,
                getattr(_openai_client, "base_url", None),
                type(exc).__name__,
                exc,
                _body,
                exc_info=True,
            )
            loop.call_soon_threadsafe(q.put_nowait, exc)
            return
        loop.call_soon_threadsafe(q.put_nowait, None)  # sentinel

    _stream_start = _time.monotonic()
    loop.run_in_executor(_llm_executor, _run)

    event_count = 0
    while True:
        event = await q.get()
        if event is None:
            break
        if isinstance(event, Exception):
            raise event
        event_count += 1
        yield event

    _stream_elapsed = _time.monotonic() - _stream_start
    logger.info(
        "LLM stream (chat completions): %d events in %.1fms",
        event_count,
        _stream_elapsed * 1000,
    )


# ── Request Handler ───────────────────────────────────────────────────────────


@app.response_handler
async def handler(
    request: CreateResponse,
    context: ResponseContext,
    _cancellation_signal: asyncio.Event,
) -> AsyncIterable[dict[str, Any]]:
    user_input = await context.get_input_text() or "Hello!"
    history = await context.get_history()
    input_items = _build_input(user_input, history)

    # Register cancel signal so middleware can find it
    _active_cancel_signals[context.response_id] = _cancellation_signal

    logger.info("Processing request %s", context.response_id)
    logger.info("User input: %s", user_input)

    stream = ResponseEventStream(
        response_id=context.response_id,
        model=getattr(request, "model", None),
    )

    yield stream.emit_created()
    yield stream.emit_in_progress()

    try:
        # ── Surface previously completed task results to the router ──
        # We *peek* here so unread results survive to the next turn if the
        # router picks a path (e.g., start_task for a new actionable request)
        # that doesn't actually restate them. Tasks transition to DELIVERED
        # only when we know they were emitted on this turn (see
        # `respond_directly` branch below).
        completed = _store.peek_completed()
        completed_context = ""
        if completed:
            parts = [f'Completed task "{t.query}": {t.result}' for t in completed]
            completed_context = "\n".join(parts)

        # Cleanup old tasks
        _store.cleanup()

        # Inject context: running tasks + completed results + delivered history for router to reference
        context_parts: list[str] = []
        task_context = build_task_context(_store)
        if task_context:
            context_parts.append(
                f"[INFO — do not mention to user unless asked] Running tasks:\n{task_context}"
            )
        if completed_context:
            context_parts.append(
                f"Recently completed (deliver these results to the user):\n{completed_context}"
            )
        delivered_context = build_delivered_context(_store)
        if delivered_context:
            context_parts.append(
                f"Recently delivered (already shown to user — use respond_directly to recall if asked):\n{delivered_context}"
            )
        if context_parts:
            input_items.insert(
                -1, {"role": "system", "content": "\n\n".join(context_parts)}
            )

        # ── Router Agent: single LLM call with tool_choice=required ──
        tool_calls: list = []

        async for event in _iter_llm_stream(
            _model,
            ROUTER_SYSTEM_PROMPT,
            input_items,
            ROUTER_TOOLS,
            tool_choice=ROUTER_TOOL_CHOICE,
        ):
            event_type = getattr(event, "type", None)
            if event_type == "response.output_item.done":
                if getattr(event.item, "type", None) == "function_call":
                    tool_calls.append(event.item)

        if not tool_calls:
            # Fallback (shouldn't happen with tool_choice=required)
            for evt in _emit_text_item(stream, "How can I help?"):
                yield evt
            yield stream.emit_completed()
            return

        # ── Dispatch based on which tool the router chose ──
        tc = tool_calls[0]
        arguments = (
            json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
        )
        tool_name = tc.name
        logger.info(
            "Router chose: %s(%s)",
            tool_name,
            json.dumps(arguments, ensure_ascii=False)[:200],
        )

        new_task = None

        if tool_name == "respond_directly":
            message = arguments.get("message", "")
            for evt in _emit_text_item(stream, message):
                yield evt
            # Router prompt rule 6 directs it to fold completed results into
            # this message. Mark them delivered now so we don't repeat them.
            if completed:
                _store.mark_delivered(t.task_id for t in completed)

        elif tool_name == "start_task":
            ack = arguments.get("ack_message", "On it.")
            for evt in _emit_text_item(stream, ack):
                yield evt
            _, new_task = execute_router_tool("start_task", arguments, _store)

        elif tool_name == "check_task_status":
            result_text, _ = execute_router_tool("check_task_status", arguments, _store)
            ack = arguments.get("ack_message", "")
            result_data = json.loads(result_text)
            if "error" in result_data:
                msg = ack or "I couldn't find that task."
            else:
                task_obj = _store.get(arguments.get("task_id", "latest"))
                if (
                    task_obj
                    and task_obj.status in (TaskStatus.COMPLETED, TaskStatus.DELIVERED)
                    and task_obj.result
                ):
                    msg = ack + "\n\n" + task_obj.result if ack else task_obj.result
                    if task_obj.status == TaskStatus.COMPLETED:
                        _store.mark_delivered([task_obj.task_id])
                else:
                    msg = (
                        ack
                        or f"Task {result_data.get('task_id', '?')}: {result_data.get('status', 'unknown')}"
                    )
            for evt in _emit_text_item(stream, msg):
                yield evt

        elif tool_name == "cancel_task":
            result_text, _ = execute_router_tool("cancel_task", arguments, _store)
            ack = arguments.get("ack_message", "Cancelled.")
            for evt in _emit_text_item(stream, ack):
                yield evt

        elif tool_name == "get_task_result":
            result_text, _ = execute_router_tool("get_task_result", arguments, _store)
            result_data = json.loads(result_text)
            msg = result_data.get("result") or result_data.get(
                "error", "No result yet."
            )
            for evt in _emit_text_item(stream, msg):
                yield evt
            if result_data.get("result"):
                task_obj = _store.get(arguments.get("task_id", "latest"))
                if task_obj and task_obj.status == TaskStatus.COMPLETED:
                    _store.mark_delivered([task_obj.task_id])

        else:
            for evt in _emit_text_item(stream, "I'm not sure how to help with that."):
                yield evt

        # ── Kick off Worker if a task was created ──
        if new_task:
            logger.info("Starting worker for task %s", new_task.task_id)
            task_handle = asyncio.create_task(
                run_worker(new_task, new_task.query, _worker_agent)
            )
            new_task.asyncio_task = task_handle
            _background_tasks.add(task_handle)
            task_handle.add_done_callback(_background_tasks.discard)

        # ── Wait for active tasks ──
        # Wait for any running/queued tasks to finish (including ones from prior turns).
        # If cancel signal fires, just stop waiting — workers keep running and
        # results are delivered next turn via collect_completed().
        active = _store.active_tasks()
        if active:
            logger.info(
                "Waiting for %d active task(s): %s",
                len(active),
                [t.task_id for t in active],
            )
            _wait_start = asyncio.get_event_loop().time()
            _last_status_time = _wait_start
            _status_count = 0
            _STATUS_UPDATE_INTERVAL = 5.0  # seconds between status updates
            _MAX_STATUS_UPDATES = 5  # cap to avoid infinite status spam

            while not _cancellation_signal.is_set():
                await asyncio.sleep(_TASK_POLL_INTERVAL)
                if all(
                    t.status
                    in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
                    for t in active
                ):
                    break

                # Emit an LLM-generated status update periodically while waiting
                now = asyncio.get_event_loop().time()
                if (
                    _status_count < _MAX_STATUS_UPDATES
                    and (now - _last_status_time) >= _STATUS_UPDATE_INTERVAL
                ):
                    _last_status_time = now
                    _status_count += 1
                    task_summaries = "; ".join(
                        f"'{t.query}' (step {t.rounds_completed + 1}, tool: {t.current_tool or 'thinking'})"
                        for t in active
                        if t.status == TaskStatus.RUNNING
                    )
                    recent_user_msgs = [
                        m["content"]
                        for m in input_items
                        if isinstance(m, dict) and m.get("role") == "user"
                    ][-3:]
                    language_hint = " | ".join(recent_user_msgs)
                    status_prompt = [
                        {
                            "role": "system",
                            "content": (
                                "Generate a very short status update (max 6 words, voice-friendly) "
                                "for a technician waiting on background work. Do NOT say 'task' or 'tool'. "
                                "Examples: 'Still working on it.' / 'Almost done.' / 'Pulling that up now.' "
                                "IMPORTANT: Reply in the SAME LANGUAGE as the user's messages below."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"User's recent messages (for language reference): {language_hint}\n\n"
                                f"Work in progress: {task_summaries}"
                            ),
                        },
                    ]
                    try:
                        status_text = ""
                        async for evt in _iter_llm_stream(
                            _model,
                            None,
                            status_prompt,
                            tools=None,
                        ):
                            if (
                                getattr(evt, "type", None)
                                == "response.output_text.delta"
                            ):
                                status_text += evt.delta
                        if status_text.strip():
                            for evt in _emit_text_item(stream, status_text.strip()):
                                yield evt
                    except Exception as e:
                        logger.warning("Status update LLM call failed: %s", e)

            # Deliver results for tasks that finished while we were waiting
            if not _cancellation_signal.is_set():
                for task in active:
                    if task.status == TaskStatus.COMPLETED and task.result:
                        task.status = TaskStatus.DELIVERED
                        for evt in _emit_text_item(stream, task.result):
                            yield evt
                    elif task.status == TaskStatus.FAILED:
                        for evt in _emit_text_item(
                            stream, f"Something went wrong: {task.result}"
                        ):
                            yield evt
            else:
                logger.info(
                    "Cancel signal received — stopping SSE, workers continue in background"
                )

        yield stream.emit_completed()
        logger.info("Finished processing request %s", context.response_id)

    except Exception as exc:
        logger.exception("Handler failed")
        for evt in _emit_text_item(stream, f"Sorry, something went wrong: {exc}"):
            yield evt
        yield stream.emit_completed()
    finally:
        _active_cancel_signals.pop(context.response_id, None)


app.run()
