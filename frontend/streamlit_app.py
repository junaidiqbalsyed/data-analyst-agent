"""Streamlit chatbot frontend — a clean, ChatGPT-style chat window.

A thin client: all orchestration lives in the FastAPI backend
(``app/server/main.py``). This file's only jobs are (1) rendering chat
history, (2) POSTing the user's message to ``/chat/stream``, and (3)
turning the Server-Sent Events response into a live-typing reply, with the
underlying agent trace tucked into a small collapsed "Details" expander
rather than front and center — most messages are simple, and most people
don't want to read an orchestration log to get a greeting answered.

Run with::

    uv run streamlit run frontend/streamlit_app.py
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import requests
import streamlit as st

BACKEND_URL = "http://localhost:8000"
CHAT_STREAM_ENDPOINT = f"{BACKEND_URL}/chat/stream"
HEALTH_ENDPOINT = f"{BACKEND_URL}/health"

# Human-readable labels for the (collapsed, optional) trace panel.
_TRACE_LABELS = {
    "crew_started": "🧭 Crew started: {crew_name}",
    "crew_completed": "✅ Crew finished: {crew_name}",
    "crew_failed": "❌ Crew failed: {crew_name} — {error}",
    "agent_started": "🤖 {role} started working",
    "agent_completed": "✔️ {role} finished",
    "agent_error": "⚠️ {role} hit an error: {error}",
    "agent_reasoning_started": "🧠 {role} is reasoning about the plan…",
    "agent_reasoning_completed": "🧠 {role} settled on a plan",
    "task_started": "📋 Task started: {description}",
    "task_completed": "📋 Task completed",
    "tool_started": "🔧 {role} called `{tool}`",
    "tool_finished": "🔧 `{tool}` returned",
    "tool_error": "🔧 `{tool}` failed — {error}",
}

_CUSTOM_CSS = """
<style>
/* Hide Streamlit's default chrome for a cleaner, app-like feel */
#MainMenu, footer, header {visibility: hidden;}
div[data-testid="stToolbar"] {display: none;}

/* Center a comfortable reading column, like a chat app */
.block-container {
    max-width: 780px;
    padding-top: 2.5rem;
    padding-bottom: 6rem;
}

/* Slightly quieter chat bubbles */
div[data-testid="stChatMessage"] {
    background: transparent;
    padding: 0.35rem 0;
}

/* Pill-style chat input, floating at the bottom of the column */
div[data-testid="stChatInput"] textarea {
    border-radius: 1.25rem;
}
</style>
"""


@dataclass
class SseEvent:
    event_type: str
    data: dict


def _iter_sse_events(response: requests.Response) -> Iterator[SseEvent]:
    """Parse a ``text/event-stream`` response into (event, data) pairs.

    Minimal hand-rolled parser (no extra dependency needed): an SSE stream
    is blocks of ``event: <type>`` / ``data: <json>`` lines separated by a
    blank line.
    """
    event_type = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        line = raw_line.strip("\r")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    data = {"raw": payload}
                yield SseEvent(event_type=event_type, data=data)
            event_type, data_lines = "message", []
            continue
        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())


def _trace_line(event: SseEvent) -> str | None:
    template = _TRACE_LABELS.get(event.event_type)
    if template is None:
        return None
    try:
        return template.format(**event.data)
    except KeyError:
        return template


def _run_chat_turn(session_id: str, message: str, status_box, trace_placeholder, answer_placeholder) -> tuple[str, list[str]]:
    """POST the message and render the agent trace live, in ``status_box``,
    as events arrive — then, once the final answer lands, write it to
    ``answer_placeholder`` (created *outside* ``status_box``, so it stays
    visible after the status collapses) and collapse the trace.

    Returns (final_answer, trace_lines) so the caller can keep the trace
    around for replay when this turn scrolls into history.
    """
    trace_lines: list[str] = []
    live_draft = ""
    final_answer = ""

    with requests.post(
        CHAT_STREAM_ENDPOINT,
        json={"session_id": session_id, "message": message},
        stream=True,
        timeout=600,
    ) as response:
        response.raise_for_status()
        for event in _iter_sse_events(response):
            if event.event_type == "routed":
                intent = event.data.get("intent")
                label = {
                    "chitchat": "💬 Answering directly…",
                    "off_topic": "🚧 That's outside what I can help with…",
                }.get(intent, "🔎 Looking into the data…")
                status_box.update(label=label)
                continue

            if event.event_type == "token":
                role = event.data.get("role")
                text = event.data.get("text", "")
                if role in ("Fast Analyst", "Insight Report Writer", "Assistant"):
                    live_draft += text
                    answer_placeholder.markdown(live_draft + "▌")
                continue

            if event.event_type == "agent_started" and event.data.get("role") in (
                "Fast Analyst",
                "Insight Report Writer",
            ):
                # A fresh draft is starting (first pass or a guard-rail revision) —
                # clear the live view so revisions don't visually stack on top
                # of the previous attempt.
                live_draft = ""
                status_box.update(label="✍️ Drafting the answer…")

            if event.event_type == "final":
                final_answer = event.data.get("text", "")
                answer_placeholder.markdown(final_answer)
                status_box.update(label="Done", state="complete", expanded=False)
                continue

            if event.event_type == "error":
                final_answer = f"⚠️ Something went wrong: {event.data.get('text')}"
                answer_placeholder.markdown(final_answer)
                status_box.update(label="Failed", state="error", expanded=True)
                continue

            line = _trace_line(event)
            if line:
                trace_lines.append(line)
                # Written live, inside status_box (trace_placeholder was
                # created there) — this is the "what the agent is doing
                # right now" feed the user watches while it runs.
                trace_placeholder.markdown("\n\n".join(f"- {ln}" for ln in trace_lines))

    return final_answer or live_draft, trace_lines


def _check_backend() -> dict | None:
    try:
        resp = requests.get(HEALTH_ENDPOINT, timeout=3)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def main() -> None:
    st.set_page_config(
        page_title="Analytics Chat",
        page_icon="📊",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)
    st.title("📊 Analytics Chat")

    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "history" not in st.session_state:
        # Each entry: {"role": "user"|"assistant", "content": str, "trace": list[str]}
        st.session_state.history = []

    with st.sidebar:
        st.subheader("Status")
        health = _check_backend()
        if health is None:
            st.error("Backend not reachable")
            st.caption("Start it with: `uv run uvicorn app.server.main:app --reload`")
        else:
            st.success(f"Model: {health['model']}")
            with st.expander("Tables in the dataset"):
                st.code("\n".join(health["tables"]), language=None)
        if st.button("🧹 New chat", use_container_width=True):
            st.session_state.session_id = str(uuid.uuid4())
            st.session_state.history = []
            st.rerun()

    for turn in st.session_state.history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            if turn["role"] == "assistant" and turn.get("trace"):
                with st.expander("What the agents did"):
                    st.markdown("\n\n".join(f"- {ln}" for ln in turn["trace"]))

    question = st.chat_input("Message Analytics Chat…")
    if not question:
        return

    st.session_state.history.append({"role": "user", "content": question, "trace": []})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        # The live trace: expanded and updating in real time while the turn
        # runs, then auto-collapses once the final answer is ready — "show
        # what the agent is thinking, then show the result."
        status_box = st.status("💭 Thinking…", expanded=True)
        with status_box:
            trace_placeholder = st.empty()
        # Created *outside* status_box so the answer stays visible even
        # after the status above it collapses.
        answer_placeholder = st.empty()
        try:
            final_answer, trace_lines = _run_chat_turn(
                st.session_state.session_id, question, status_box, trace_placeholder, answer_placeholder
            )
        except requests.RequestException as exc:
            final_answer, trace_lines = f"⚠️ Could not reach the backend: {exc}", []
            answer_placeholder.markdown(final_answer)
            status_box.update(label="Failed", state="error", expanded=True)

    st.session_state.history.append({"role": "assistant", "content": final_answer, "trace": trace_lines})


if __name__ == "__main__":
    main()
