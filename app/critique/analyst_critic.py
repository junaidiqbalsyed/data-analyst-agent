"""The grounded Insight & Reporting loop: draft -> guard rails -> LLM critic -> revise.

Modeled directly on the pattern documented in ``to_delete/report_guard_rails.md``
and ``to_delete/report_generation_walkthrough.md`` for a fraud-report writer/
critic pair, adapted here from "never invent a fraud figure" to "never invent
an analytics figure":

    1. A Writer agent drafts a natural-language answer from the SQL evidence
       gathered earlier in the turn (see app/critique/evidence.py).
    2. Deterministic guard rails run first, in code, not in a prompt — cheap
       and 100% reproducible. A failing guard means the LLM critic is never
       even called for that pass (same ordering as the reference: "guards
       before rubric").
    3. Only if every guard passes does an LLM Critic judge the draft against
       a rubric (does it actually answer the question, is it clear) and
       return strict {accept, notes} — exactly the reference's contract.
    4. The loop is capped at MAX_ITERATIONS. If it never converges, the last
       draft is returned anyway (fail-open, the same safety net the
       reference uses instead of blocking the user forever).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from crewai import Agent
from pydantic import BaseModel, Field

from app.critique.evidence import EvidenceLog
from app.data import get_catalog

MAX_ITERATIONS = 3

# Numbers with 2+ digits are treated as claimed data figures worth grounding;
# single digits are almost always prose ("one of the top three cities"), not
# a metric, so holding those to the same bar would just cause false rejects.
_NUMBER_RE = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})*(?:\.\d+)?%?(?![\w])")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class GuardResult:
    passed: bool
    notes: tuple[str, ...]


class CriticVerdict(BaseModel):
    """Strict structured output for the LLM rubric pass — mirrors the reference's
    ``{"accept": bool, "notes": "..."}`` critic contract."""

    accept: bool = Field(description="True only if the draft fully and clearly answers the question.")
    notes: str = Field(description="Actionable revision notes; empty string if accepted.")


def _normalize_number(raw: str) -> str:
    return raw.rstrip("%").replace(",", "")


def _numeric_grounding_guard(draft: str, evidence_text: str) -> GuardResult:
    """Reject a draft that states a figure no query result this turn produced."""
    evidence_numbers = {_normalize_number(n) for n in _NUMBER_RE.findall(evidence_text)}
    notes = [
        f"The figure '{match}' does not appear in any query result gathered this turn."
        for match in _NUMBER_RE.findall(draft)
        if len(_normalize_number(match).replace(".", "")) >= 2
        and _normalize_number(match) not in evidence_numbers
    ]
    return GuardResult(passed=not notes, notes=tuple(notes))


def _schema_grounding_guard(draft: str) -> GuardResult:
    """Reject a draft that cites a table/column (in `code spans`) that doesn't exist."""
    known = get_catalog().known_identifiers()
    notes = [
        f"'{span}' is not a real table/column in the current dataset."
        for span in _CODE_SPAN_RE.findall(draft)
        if (token := span.rpartition(".")[2].strip().lower()) and token not in known
    ]
    return GuardResult(passed=not notes, notes=tuple(notes))


_LEAKY_TERMS = ("sql", "query", "queries", "database", "csv", "table", "row", "join", "schema")
_LEAKY_TERM_RE = re.compile(r"\b(" + "|".join(_LEAKY_TERMS) + r")\b", re.IGNORECASE)


def _plain_language_guard(draft: str) -> GuardResult:
    """Reject a draft that leaks implementation details (SQL, tables, CSVs,
    "the database") instead of speaking in plain business language."""
    found = sorted({m.group(0).lower() for m in _LEAKY_TERM_RE.finditer(draft)})
    if not found:
        return GuardResult(passed=True, notes=())
    return GuardResult(
        passed=False,
        notes=(f"Rewrite in plain business language — remove technical terms like: {', '.join(found)}.",),
    )


def run_guard_rails(draft: str, evidence: EvidenceLog) -> GuardResult:
    """Run every deterministic guard and collect *all* failures from this pass.

    Cheap and synchronous — no LLM call is spent here, so a bad draft never
    reaches the (comparatively expensive) LLM critic below.
    """
    checks = (
        _numeric_grounding_guard(draft, evidence.combined_text()),
        _schema_grounding_guard(draft),
        _plain_language_guard(draft),
    )
    notes = tuple(n for check in checks for n in check.notes)
    return GuardResult(passed=not notes, notes=notes)


def _writer_prompt(question: str, evidence: EvidenceLog) -> str:
    return (
        "Write a clear, direct natural-language answer to the user's "
        "question, using ONLY the evidence below — never invent a figure "
        "that isn't in it. Write in plain business language, like an "
        "analyst briefing a colleague: never mention SQL, queries, tables, "
        "databases, CSV files, joins, or rows — just state the finding. "
        "Keep it to a short paragraph (plus a bullet list if there are "
        "multiple results worth listing).\n\n"
        f"User's question: {question}\n\n"
        f"Evidence gathered this turn:\n{evidence.combined_text()}"
    )


def _revise_prompt(question: str, evidence: EvidenceLog, draft: str, notes: tuple[str, ...]) -> str:
    joined_notes = "\n".join(f"- {n}" for n in notes)
    return (
        "Revise your previous answer to fix every issue listed below. Keep "
        "using ONLY the evidence — do not add new figures, and keep the "
        "language plain and non-technical.\n\n"
        f"User's question: {question}\n\n"
        f"Evidence gathered this turn:\n{evidence.combined_text()}\n\n"
        f"Previous draft:\n{draft}\n\n"
        f"Issues to fix:\n{joined_notes}"
    )


def run_insight_loop(question: str, evidence: EvidenceLog, writer: Agent, critic: Agent) -> str:
    """Draft -> guard rails -> LLM critic -> revise, capped, fail-open.

    Returns the final (or last, if never fully accepted) draft text.
    """
    draft = writer.kickoff(_writer_prompt(question, evidence)).raw

    for _ in range(MAX_ITERATIONS):
        guard_result = run_guard_rails(draft, evidence)
        if not guard_result.passed:
            draft = writer.kickoff(_revise_prompt(question, evidence, draft, guard_result.notes)).raw
            continue

        verdict_output = critic.kickoff(
            f"Question: {question}\n\nDraft answer:\n{draft}\n\n"
            "Judge only whether this draft fully and clearly answers the "
            "question. Do not second-guess the figures — grounding was "
            "already verified in code.",
            response_format=CriticVerdict,
        )
        verdict = verdict_output.pydantic
        if verdict is None or verdict.accept:
            return draft

        draft = writer.kickoff(_revise_prompt(question, evidence, draft, (verdict.notes,))).raw

    # Fail-open safety net, same as the reference pattern: after the cap is
    # hit, ship the last draft rather than blocking the user indefinitely.
    return draft
