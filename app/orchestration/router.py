"""LEVEL 0 — decide how a message should be handled, before any expensive
work happens.

One cheap, non-streamed, structured-output classification call
(``Agent.kickoff`` outside any Crew — see ``app.agents.build_router``)
sorts every message into exactly one of three lanes:

  * **chitchat** — a greeting/thanks/small talk/"what can you do" message.
    Answered directly (``run_chitchat``), no crew, no SQL.
  * **off_topic** — not a real question about this dataset: unrelated
    requests, gibberish, or anything inappropriate. Declined immediately
    (``decline_off_topic``) — a guard rail against spending a full
    analysis run on a question that was never answerable to begin with.
  * **analytical** — needs the dataset. Handled by the fast path
    (``app.orchestration.fast_path``), not the full multi-level crew —
    see that module's docstring for why.
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, Field

from app.agents import build_conversational_agent, build_router
from app.session import format_history_for_prompt

logger = logging.getLogger(__name__)

OFF_TOPIC_DECLINE = (
    "I'm built specifically to answer analytical questions about this "
    "dataset — orders, customers, products, sales, and related trends. "
    "That's outside what I can help with here. Try asking something like "
    "\"what were our best-selling products?\" or \"how many orders came "
    "from each city?\""
)


class Intent(str, Enum):
    CHITCHAT = "chitchat"
    ANALYTICAL = "analytical"
    OFF_TOPIC = "off_topic"


class _RouteDecision(BaseModel):
    intent: str = Field(
        description=(
            "'chitchat' for a greeting, thanks, small talk, or a question "
            "about what the assistant is/can do. 'analytical' for anything "
            "that needs the connected business dataset — counts, "
            "comparisons, trends, schema questions, etc. 'off_topic' for "
            "anything else: unrelated requests (weather, poems, general "
            "knowledge, coding help), gibberish, or inappropriate content — "
            "this assistant only handles the connected dataset."
        )
    )


_VALID_INTENTS = {i.value for i in Intent}


def classify_intent(message: str) -> Intent:
    """Classify one message. Fails open to ANALYTICAL: an unnecessary fast-
    path run costs a little time, but wrongly declining or chitchatting
    away a real question costs the user a real answer — the worse of the
    failure modes here."""
    try:
        output = build_router().kickoff(
            f"Classify this user message: {message!r}",
            response_format=_RouteDecision,
        )
        decision = output.pydantic
        if decision is not None and decision.intent in _VALID_INTENTS:
            return Intent(decision.intent)
    except Exception:
        logger.exception("Intent classification failed; defaulting to the analytical fast path")
    return Intent.ANALYTICAL


def run_chitchat(message: str, history: list[tuple[str, str]] | None = None) -> str:
    """Answer a non-analytical message directly, with no crew involved.

    ``history`` — this session's recent (question, answer) pairs (see
    ``app.session.turn_history``) — is prepended as plain text so a
    follow-up like "what else can you help with?" doesn't get the exact
    same canned answer as the question right before it; the agent can see
    what it already said.
    """
    prompt = format_history_for_prompt(history or []) + f'The user now says: "{message}"'
    return build_conversational_agent().kickoff(prompt).raw


def decline_off_topic() -> str:
    """A fixed, instant response for questions this assistant can't help
    with — no LLM call needed, so it costs nothing beyond the classification
    that already happened."""
    return OFF_TOPIC_DECLINE
