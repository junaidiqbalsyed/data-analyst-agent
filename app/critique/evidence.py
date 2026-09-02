"""Per-turn evidence log: every SQL query run during one chat turn.

The Insight & Reporting guard rails (`app.critique.analyst_critic`) need to
check "does every number in the draft actually come from a query we ran?".
That check has to look at exactly the queries *this* turn issued, not some
other concurrent user's — so the log lives behind a ``contextvars.ContextVar``
rather than a single global list.

The same pattern used for SSE routing (`app.events.stream_bus`) applies
here: the server sets a fresh log right before ``crew.kickoff()`` inside
``asyncio.to_thread``, which copies the current context into that thread, so
the tool call deep inside the crew and the guard rail reading it back at the
end are guaranteed to see the same, isolated log.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field


@dataclass
class QueryEvidence:
    sql: str
    result_json: str


@dataclass
class EvidenceLog:
    entries: list[QueryEvidence] = field(default_factory=list)

    def record(self, sql: str, result_json: str) -> None:
        self.entries.append(QueryEvidence(sql=sql, result_json=result_json))

    def combined_text(self) -> str:
        """All queries + results concatenated — the text guard rails scan for grounding."""
        if not self.entries:
            return "(no queries were run this turn)"
        return "\n\n".join(f"SQL:\n{e.sql}\nRESULT:\n{e.result_json}" for e in self.entries)

    def queries(self) -> list[str]:
        return [e.sql for e in self.entries]


_current_log: contextvars.ContextVar[EvidenceLog | None] = contextvars.ContextVar(
    "_current_evidence_log", default=None
)


def start_evidence_log() -> EvidenceLog:
    """Begin a fresh, empty evidence log for the current context (one chat turn)."""
    log = EvidenceLog()
    _current_log.set(log)
    return log


def get_evidence_log() -> EvidenceLog:
    """The active evidence log, creating an empty one if none was started yet."""
    log = _current_log.get()
    if log is None:
        log = start_evidence_log()
    return log


def record_query(sql: str, result_json: str) -> None:
    """Append one query+result pair to the active turn's evidence log."""
    get_evidence_log().record(sql, result_json)
