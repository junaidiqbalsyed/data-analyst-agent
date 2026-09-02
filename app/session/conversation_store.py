"""Gives each chat session its own isolated CrewAI memory.

CrewAI's ``Memory`` defaults its internal analysis LLM to a litellm model
string and its embedder to OpenAI — both would silently need a fourth,
disallowed environment variable (``OPENAI_API_KEY``). Every ``Memory``
built here pins both explicitly: the analysis LLM comes from
``app.llm.build_llm`` (OpenAI SDK, our three .env values) and the embedder
is the local, no-API-key one from ``app.memory``.

``Memory(storage=<path>)`` accepts a plain filesystem path, which is what
makes per-session isolation possible without any shared, racy global state:
each session gets its own on-disk LanceDB directory under
``.crew_memory/<session_id>/``, so one browser tab's conversation never
leaks into another's.

Like the evidence log and the SSE event queue, the *active* memory for the
turn currently executing is carried through a ``contextvars.ContextVar`` —
bound once by the request handler before ``crew.kickoff()``, then read
wherever a nested sub-crew needs it (see app/crews/specialist_crews.py).
"""

from __future__ import annotations

import contextvars
import re

from crewai.memory import Memory

from app.config import MEMORY_DIR
from app.llm import build_llm
from app.memory import LOCAL_EMBEDDER_CONFIG

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_-]")

_current_memory: contextvars.ContextVar[Memory | None] = contextvars.ContextVar(
    "_current_memory", default=None
)


def _safe_dir_name(session_id: str) -> str:
    """Turn an arbitrary session id into a safe, single path segment."""
    cleaned = _SAFE_SEGMENT.sub("_", session_id).strip("_")
    return cleaned or "default"


def start_session_memory(session_id: str) -> Memory:
    """Build (or rebuild) this session's isolated Memory and bind it to the
    current context. Call once per chat turn, before ``crew.kickoff()``."""
    storage_path = MEMORY_DIR / _safe_dir_name(session_id)
    storage_path.mkdir(parents=True, exist_ok=True)
    memory = Memory(
        llm=build_llm(role="memory-analysis", stream=False),
        storage=str(storage_path),
        embedder=LOCAL_EMBEDDER_CONFIG,
    )
    _current_memory.set(memory)
    return memory


def get_session_memory() -> Memory:
    """The active session's Memory, falling back to a shared default if none
    was explicitly started (e.g. when a crew is built outside the server)."""
    memory = _current_memory.get()
    if memory is None:
        memory = start_session_memory("default")
    return memory
