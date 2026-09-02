"""The default path for analytical questions: one agent, minimal hops.

The full multi-level crew (``app.crews.build_orchestrator_crew``) still
lives in this codebase and demonstrates CrewAI's hierarchical + sequential
orchestration, per-level memory, planning, and a guard-railed
writer/critic loop — but it is 5+ sequential LLM round-trips against a
live endpoint (Router -> Chief -> Quant Manager -> Data Analyst -> Insight
Manager -> Writer/Critic), and it repeatedly measured well past 30 seconds
on real questions. Per an explicit call to trade some of that depth for
speed, this is the path ``app/server/main.py`` actually uses: one Fast
Analyst agent gathers evidence and drafts the answer itself, and that
answer is returned directly — no manager relay, no separate critic hop,
and (also per that same call) no guard-and-revise loop either.

That last part was tried and reverted: the deterministic numeric-grounding
guard (``app.critique.analyst_critic.run_guard_rails``) rejects any figure
that isn't a *verbatim* match in a query result, which also catches a
perfectly valid derived figure — "413,409 minus 296,776" for a
"how does X compare to Y" question — as if it were invented. In testing,
that sent the agent into a revise pass that burned its tool budget
re-exploring instead of just rephrasing, and returned a worse, hedgier
answer than the original draft. Given the instruction to prioritize speed
over strict grounding here, the fix is simpler: skip the loop, trust the
one draft.
"""

from __future__ import annotations

from app.agents import build_fast_analyst


def run_fast_analysis(question: str) -> str:
    """Answer one analytical question directly, in one pass."""
    return build_fast_analyst().kickoff(question).raw
