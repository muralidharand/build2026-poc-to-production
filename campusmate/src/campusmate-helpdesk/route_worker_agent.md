# Router + Worker Pattern — Voice Latency Design

## Problem this solves

Example voice interaction:

> "THIS WILL LIKELY TAKE IT A FEW SECONDS TO PULL UP, so we need to capture
> the dead space ideally."

A naïve voice agent runs the model + tools, then speaks. While tools execute
the user hears silence — for 5-15 seconds in real demos. That kills the
voice experience.

## Pattern

Two cooperating agents, both built on the Microsoft Agent Framework:

```
┌──────────────────────┐         ┌────────────────────────────┐
│   Router (front)     │         │   Worker (back office)     │
│   tool_choice=req'd  │         │   FoundryChatClient        │
│   5 meta-tools       │         │   .as_agent(tools=[...])   │
│   Always fast        │         │   Tools incl. Toolbox MCP  │
└──────────┬───────────┘         └──────────────▲─────────────┘
           │                                    │
           │  start_task / cancel_task /        │  fire-and-forget
           │  check_task_status / get_result    │  asyncio.create_task
           ▼                                    │
     ┌─────────────────────────────────────────┘
     │ task_store.py — shared state
     │ QUEUED → RUNNING → COMPLETED → DELIVERED
     └────────────────────────────────────────────
```

## Router (router_agent.py)

- One LLM call per turn with `tool_choice="required"`. No free-text path.
- The five meta-tools the model is forced to choose between:

  | Tool | Use when |
  | --- | --- |
  | `respond_directly` | greeting, chat, clarification, delivering a result |
  | `start_task` | user just asked for real work; takes `ack_message` for instant voice ack |
  | `check_task_status` | "how's it going?" |
  | `cancel_task` | "stop that" |
  | `get_task_result` | retrieve a previously-completed result |

- Duplicate-task guard: `start_task` returns the existing task instead of
  spawning a second worker for the same request.
- Each turn the handler injects context for the router:
  - Running tasks (so it doesn't double-dispatch)
  - Recently completed results (so it delivers them this turn)
  - Recently delivered results (so it can recall on follow-up)

## Worker (worker_agent.py)

- Plain MAF agent created with `FoundryChatClient.as_agent(...)`.
- `agent.run()` owns the entire tool loop — no manual rounds, no manual
  dispatch. Demo-friendly.
- Tools = local `@tool`-decorated functions + (optional) Toolbox MCP tool.
- Cancellation is via `asyncio.Task.cancel()`. `router_agent.execute_router_tool`
  on `cancel_task` looks up the task's `asyncio_task` handle and cancels it.
- Runs as a fire-and-forget background task. Survives client disconnect — the
  result is delivered on the next turn via `TaskStore.collect_completed()`.

## Task Store (task_store.py)

- In-memory registry keyed by short hex task IDs.
- Lifecycle: `QUEUED → RUNNING → COMPLETED → DELIVERED` (or `CANCELLED` / `FAILED`).
- `collect_completed()` is exactly-once delivery: it returns finished tasks
  *and* marks them delivered.
- `cleanup()` GCs delivered/cancelled/failed tasks older than 5 minutes.

## Streaming behaviour (main.py)

For one user turn:

1. Stream the router's chosen response immediately (ack or direct message).
2. Wait for any active worker tasks.
3. Every ~5 s while waiting, stream an LLM-generated filler ("Almost there.",
   "Pulling that up.") — voice-friendly, matches the user's language.
4. When the worker finishes, stream the final answer.
5. If the user cancels mid-wait (HTTP `/cancel`), close the SSE stream but
   keep the worker running. The next turn delivers the result.

## Cancellation flow

```
user says "cancel" ──► router picks cancel_task ──► execute_router_tool
                                                         │
                                                         ▼
                                  task.asyncio_task.cancel()
                                                         │
                                                         ▼
                                   MAF agent.run() raises CancelledError
                                                         │
                                                         ▼
                              run_worker marks task CANCELLED, sets result
```

## Why MAF instead of a hand-rolled loop?

- One pattern across all our agents (field-ops, fibey, future).
- `@tool` + type hints = no JSON schema to maintain.
- `as_agent()` handles model calls, tool dispatch, parallel tool calls,
  history, all of it.
- Trade-off: cancellation is coarse (whole `agent.run()`, not per-round).
  For a voice-latency demo this is fine.
