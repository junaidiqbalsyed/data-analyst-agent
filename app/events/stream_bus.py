"""Bridges CrewAI's global event bus to a per-chat-turn SSE queue.

``crewai_event_bus`` is a process-wide singleton: every agent, task, tool
call, and streamed token — across every crew running anywhere in the
process, concurrently — raises events on the very same bus. Turning that
into "the SSE stream for *this one* chat request" needs a way to route each
event back to the queue for the request that caused it. The mechanism:

    1. The request handler calls :func:`bind_new_queue`, which creates a
       plain, thread-safe ``queue.SimpleQueue`` and binds it to a
       ``contextvars.ContextVar``.
    2. It then runs ``crew.kickoff()`` inside ``asyncio.to_thread(...)``.
       ``to_thread`` copies the *current* context into that worker thread,
       so the ContextVar's value — this request's queue — is still visible
       there, including inside every nested sub-crew (they all execute
       synchronously in that same worker thread; see
       ``app/crews/specialist_crews.py``).
    3. The listener below reads "the queue for the calling thread's
       context" on every event and pushes a small, JSON-friendly dict onto
       it. It holds no per-request state itself.
    4. The async SSE generator (``app/server/main.py``) drains the queue
       until it sees the :data:`SENTINEL` marking the turn complete.

A plain ``queue.SimpleQueue`` (not ``asyncio.Queue``) is used deliberately:
it is safe to write to from a worker thread, which an ``asyncio.Queue`` is
not.
"""

from __future__ import annotations

import contextvars
import queue
from typing import Any

from crewai.events import BaseEventListener
from crewai.events.types.agent_events import (
    AgentExecutionCompletedEvent,
    AgentExecutionErrorEvent,
    AgentExecutionStartedEvent,
    LiteAgentExecutionCompletedEvent,
    LiteAgentExecutionStartedEvent,
)
from crewai.events.types.crew_events import (
    CrewKickoffCompletedEvent,
    CrewKickoffFailedEvent,
    CrewKickoffStartedEvent,
)
from crewai.events.types.llm_events import LLMStreamChunkEvent
from crewai.events.types.reasoning_events import (
    AgentReasoningCompletedEvent,
    AgentReasoningStartedEvent,
)
from crewai.events.types.task_events import TaskCompletedEvent, TaskStartedEvent
from crewai.events.types.tool_usage_events import (
    ToolUsageErrorEvent,
    ToolUsageFinishedEvent,
    ToolUsageStartedEvent,
)

SENTINEL: Any = object()
"""Pushed onto a request's queue once its crew.kickoff() call returns."""

EventQueue = "queue.SimpleQueue[dict[str, Any]]"


class _RequestState:
    """Mutable, per-request state shared across every event this turn raises.

    ``crewai_event_bus.emit()`` runs each handler inside its own
    ``contextvars.copy_context()`` snapshot — so a plain
    ``ContextVar.set()`` inside one handler is invisible to a *different*
    handler call, even for the same request (confirmed empirically: tagging
    tokens with a role set this way always came back ``None``). Copying a
    context still copies *references*, though, so a single ``_RequestState``
    instance stored once in the ContextVar below and then **mutated in
    place** (never reassigned) is genuinely shared — every handler's copy
    holds a reference to the same object.
    """

    __slots__ = ("queue", "active_role")

    def __init__(self, q: EventQueue) -> None:
        self.queue = q
        self.active_role: str | None = None


_request_state: contextvars.ContextVar[_RequestState | None] = contextvars.ContextVar(
    "_request_state", default=None
)


def bind_new_queue() -> EventQueue:
    """Create a fresh event queue and bind it to the current context.

    Call this in the request handler *before* starting the worker thread
    that runs ``crew.kickoff()``.
    """
    q: EventQueue = queue.SimpleQueue()
    _request_state.set(_RequestState(q))
    return q


def _emit(event_type: str, **fields: Any) -> None:
    state = _request_state.get()
    if state is None:
        return  # e.g. a script/test run outside the server — fine to drop
    state.queue.put({"type": event_type, **fields})


def _set_active_role(role: str | None) -> None:
    """Record which agent is "currently speaking" — see ``_RequestState``
    for why this has to mutate a shared object rather than a ContextVar."""
    state = _request_state.get()
    if state is not None:
        state.active_role = role


def _get_active_role() -> str | None:
    state = _request_state.get()
    return state.active_role if state is not None else None


def _role_of(agent: Any) -> str | None:
    return getattr(agent, "role", None)


class WorkshopEventListener(BaseEventListener):
    """Routes a curated subset of CrewAI's events to the active request's queue.

    Deliberately curated rather than "every event": a raw firehose of every
    internal CrewAI event would overwhelm the chat UI. What's kept covers
    exactly the concepts this workshop demonstrates — orchestration
    (crew/agent/task lifecycle), tool/function calling, reasoning, and
    live token output.
    """

    def setup_listeners(self, bus: Any) -> None:
        @bus.on(CrewKickoffStartedEvent)
        def _(source: Any, event: CrewKickoffStartedEvent) -> None:
            _emit("crew_started", crew_name=event.crew_name)

        @bus.on(CrewKickoffCompletedEvent)
        def _(source: Any, event: CrewKickoffCompletedEvent) -> None:
            _emit("crew_completed", crew_name=event.crew_name)

        @bus.on(CrewKickoffFailedEvent)
        def _(source: Any, event: CrewKickoffFailedEvent) -> None:
            _emit("crew_failed", crew_name=event.crew_name, error=event.error)

        @bus.on(AgentExecutionStartedEvent)
        def _(source: Any, event: AgentExecutionStartedEvent) -> None:
            role = _role_of(event.agent)
            _set_active_role(role)
            _emit("agent_started", role=role)

        @bus.on(AgentExecutionCompletedEvent)
        def _(source: Any, event: AgentExecutionCompletedEvent) -> None:
            _emit("agent_completed", role=_role_of(event.agent))

        @bus.on(AgentExecutionErrorEvent)
        def _(source: Any, event: AgentExecutionErrorEvent) -> None:
            _emit("agent_error", role=_role_of(event.agent), error=event.error)

        @bus.on(LiteAgentExecutionStartedEvent)
        def _(source: Any, event: LiteAgentExecutionStartedEvent) -> None:
            # The chitchat reply and the Insight & Reporting writer/critic
            # loop both run via Agent.kickoff() (a "LiteAgent" call, not a
            # Task inside a Crew) — this is their equivalent start signal,
            # and the only place their role reaches the trace/UI at all.
            role = event.agent_info.get("role")
            _set_active_role(role)
            _emit("agent_started", role=role)

        @bus.on(LiteAgentExecutionCompletedEvent)
        def _(source: Any, event: LiteAgentExecutionCompletedEvent) -> None:
            _emit("agent_completed", role=event.agent_info.get("role"))

        @bus.on(AgentReasoningStartedEvent)
        def _(source: Any, event: AgentReasoningStartedEvent) -> None:
            _emit("agent_reasoning_started", role=event.agent_role)

        @bus.on(AgentReasoningCompletedEvent)
        def _(source: Any, event: AgentReasoningCompletedEvent) -> None:
            _emit("agent_reasoning_completed", role=event.agent_role, plan=event.plan)

        @bus.on(TaskStartedEvent)
        def _(source: Any, event: TaskStartedEvent) -> None:
            description = getattr(event.task, "description", None)
            _emit("task_started", description=description)

        @bus.on(TaskCompletedEvent)
        def _(source: Any, event: TaskCompletedEvent) -> None:
            _emit("task_completed")

        @bus.on(ToolUsageStartedEvent)
        def _(source: Any, event: ToolUsageStartedEvent) -> None:
            _emit("tool_started", tool=event.tool_name, role=event.agent_role, args=event.tool_args)

        @bus.on(ToolUsageFinishedEvent)
        def _(source: Any, event: ToolUsageFinishedEvent) -> None:
            _emit("tool_finished", tool=event.tool_name, role=event.agent_role)

        @bus.on(ToolUsageErrorEvent)
        def _(source: Any, event: ToolUsageErrorEvent) -> None:
            _emit("tool_error", tool=event.tool_name, role=event.agent_role, error=str(event.error))

        @bus.on(LLMStreamChunkEvent)
        def _(source: Any, event: LLMStreamChunkEvent) -> None:
            if event.chunk:
                _emit("token", role=_get_active_role(), text=event.chunk)


_listener: WorkshopEventListener | None = None


def install_listener() -> None:
    """Activate the event bridge. Safe to call more than once (idempotent)."""
    global _listener
    if _listener is None:
        _listener = WorkshopEventListener()
