"""FastAPI backend.

One endpoint, ``POST /chat/stream``, that streams one chat turn back over
Server-Sent Events. Every turn is routed first (see app/orchestration)
into one of three lanes: chitchat gets one direct reply, an off-topic
question gets an instant decline, and an analytical question goes to the
fast path (one agent, grounded, no manager relay) — see
app/orchestration/fast_path.py for why that is the default over the full
multi-level crew (app.crews.build_orchestrator_crew), which still lives in
this codebase for anyone who wants that deeper, slower path directly. Run
with::

    uv run uvicorn app.server.main:app --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.critique.evidence import start_evidence_log
from app.data import get_catalog
from app.events import SENTINEL, bind_new_queue, install_listener
from app.orchestration import Intent, classify_intent, decline_off_topic, run_chitchat, run_fast_analysis
from app.session import get_recent_history, record_turn, start_session_memory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chatbot.server")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Activate the CrewAI event bus -> SSE bridge and confirm the dynamic
    data catalog on boot — visible proof that a CSV dropped into data/
    needs no code change to become queryable."""
    install_listener()
    settings = get_settings()
    tables = get_catalog().list_tables()
    logger.info("LLM model: %s (%s)", settings.llm_model, settings.llm_base_url)
    logger.info("Discovered %d table(s) in data/: %s", len(tables), [t.name for t in tables])
    yield


app = FastAPI(title="Multi-Level CrewAI Analytics Chatbot", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.get("/health")
def health() -> dict[str, object]:
    settings = get_settings()
    tables = get_catalog().list_tables()
    return {
        "status": "ok",
        "model": settings.llm_model,
        "tables": [t.name for t in tables],
    }


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> EventSourceResponse:
    """Run one chat turn's crew and stream its trace + final answer as SSE."""

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        # Bind this turn's per-request state (event queue, session memory,
        # SQL evidence log) to the current context *before* the crew runs,
        # so every nested sub-crew/tool call — all executing synchronously
        # inside the same worker thread — resolves back to the same three
        # (see app/events, app/session, app/critique/evidence for why).
        event_queue = bind_new_queue()
        start_session_memory(request.session_id)
        start_evidence_log()

        def run_turn() -> str:
            """Runs in a worker thread (see asyncio.to_thread below). Routes
            first: chitchat gets one direct reply, an off-topic question
            gets an instant decline, and only a genuinely analytical
            question runs the (fast-path) analysis. The session's last few
            turns (see app.session.turn_history) are handed to whichever
            lane runs, so a follow-up isn't answered cold — and the new
            result is recorded back so the *next* turn sees it too."""
            history = get_recent_history(request.session_id)
            intent = classify_intent(request.message)
            event_queue.put({"type": "routed", "intent": intent.value})
            if intent is Intent.CHITCHAT:
                result = run_chitchat(request.message, history)
            elif intent is Intent.OFF_TOPIC:
                result = decline_off_topic()
            else:
                result = run_fast_analysis(request.message, history)
            record_turn(request.session_id, request.message, result)
            return result

        async def run_crew() -> None:
            try:
                result = await asyncio.to_thread(run_turn)
                event_queue.put({"type": "final", "text": result})
            except Exception as exc:  # surfaced to the UI, never swallowed
                logger.exception("Chat turn failed")
                event_queue.put({"type": "error", "text": str(exc)})
            finally:
                event_queue.put(SENTINEL)

        # Runs concurrently with the polling loop below (not awaited here)
        # so tokens/trace events can be forwarded to the client as they
        # happen, rather than only after the whole turn finishes.
        crew_task = asyncio.create_task(run_crew())

        while True:
            item = await asyncio.to_thread(event_queue.get)
            if item is SENTINEL:
                break
            yield {"event": item["type"], "data": json.dumps(item)}

        await crew_task

    return EventSourceResponse(event_generator())
