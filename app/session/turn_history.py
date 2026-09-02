"""A short, plain-text conversation transcript per chat session.

``conversation_store.py`` already gives each session its own CrewAI
``Memory`` (semantic, LanceDB-backed) — but that's built for the deep
multi-level crew, wired in via ``Crew(memory=...)``. The default fast path
(``app/orchestration``) never builds a ``Crew`` at all — it's plain
``Agent().kickoff()`` calls (the "LiteAgent" pattern) — so that memory is
never actually consulted there, and every chitchat/fast-analyst call was
answering cold, with no idea what was said moments earlier in the same
session. That's why "what can you help with?" followed immediately by
"what else can you help with?" produced near-identical answers: the agent
had no way to know the first question had just been asked.

The fix doesn't need semantic memory or an embedding call — it needs the
last few turns' plain text, dropped straight into the next prompt. This
module is exactly that: an in-process, per-session ring buffer of
(question, answer) pairs, capped short so it stays fast (no extra LLM/
embedding round-trip, just a few more lines of prompt text).
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque

# Keep this small: it's plain text pasted into every prompt, not a search
# index — more history means a bigger, slower call for marginal benefit.
_MAX_TURNS_PER_SESSION = 6

_lock = threading.Lock()
_history: dict[str, deque[tuple[str, str]]] = defaultdict(lambda: deque(maxlen=_MAX_TURNS_PER_SESSION))


def record_turn(session_id: str, question: str, answer: str) -> None:
    """Append this turn to the session's transcript, once it's known."""
    with _lock:
        _history[session_id].append((question, answer))


def get_recent_history(session_id: str) -> list[tuple[str, str]]:
    """The last few (question, answer) pairs for this session, oldest first."""
    with _lock:
        return list(_history[session_id])


def format_history_for_prompt(history: list[tuple[str, str]]) -> str:
    """Render prior turns as plain text to prepend to the next prompt.

    Returns "" when there's no history yet, so callers can just concatenate
    the result without a conditional.
    """
    if not history:
        return ""
    lines = [f'User asked: "{q}"\nYou answered: "{a}"' for q, a in history]
    return "Earlier in this same conversation:\n" + "\n\n".join(lines) + "\n\n"
