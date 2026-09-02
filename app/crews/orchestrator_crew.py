"""LEVEL 1 — the top-level hierarchical crew.

One Chief Orchestrator (the "hierarchical LLM which will act as
orchestrator" the workshop brief asks for) delegates to three domain
liaison agents. Each liaison's only move is calling its one delegate tool
from ``app.crews.specialist_crews``, which is what actually kicks off that
domain's own second-level crew — see that module's docstring for why this
is genuinely multi-level rather than one flat crew with many tools.
"""

from __future__ import annotations

from crewai import Agent, Crew, Process, Task
from crewai.tools import BaseTool

from app.agents import build_chief_orchestrator
from app.crews.specialist_crews import (
    delegate_to_data_discovery,
    delegate_to_insight_reporting,
    delegate_to_quantitative_analysis,
)
from app.llm import build_llm
from app.session import get_session_memory


def _build_domain_liaison(
    *, role: str, llm_role: str, goal: str, backstory: str, delegate_tool: BaseTool
) -> Agent:
    """A thin worker agent whose only job is to call one domain's delegate tool.

    It sits *in* the top-level crew, so the Chief can delegate to it by
    role, but it does no reasoning of its own beyond invoking that tool —
    the real work happens inside the sub-crew the tool kicks off.

    ``llm_role`` is a separate, short, env-var-friendly identifier from the
    agent's display ``role`` (e.g. ``"liaison-quant"`` vs. "Quantitative
    Analysis Manager") — see ``app.llm.factory.build_llm`` for why: it is
    the key used to look up a per-agent model override in ``.env``.
    """
    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=build_llm(role=llm_role),
        tools=[delegate_tool],
        verbose=True,
    )


def build_orchestrator_crew(question: str) -> Crew:
    """Construct the full multi-level crew tree, fresh, for one chat turn.

    Rebuilt from scratch on every call rather than cached: Agents hold
    execution state during a kickoff, and the server may run more than one
    chat turn's crew concurrently in different worker threads (see
    app/server/main.py) — fresh instances are the only safe option.
    """
    chief = build_chief_orchestrator()

    quant_liaison = _build_domain_liaison(
        role="Quantitative Analysis Manager",
        llm_role="liaison-quant",
        goal="Get exact numeric answers (aggregates, KPIs, trends) via delegate_to_quantitative_analysis.",
        backstory="Represents the Quantitative Analysis domain on the Chief's team.",
        delegate_tool=delegate_to_quantitative_analysis,
    )
    discovery_liaison = _build_domain_liaison(
        role="Data Discovery Manager",
        llm_role="liaison-discovery",
        goal="Answer 'what data exists / how is it structured' questions via delegate_to_data_discovery.",
        backstory="Represents the Data Discovery domain on the Chief's team.",
        delegate_tool=delegate_to_data_discovery,
    )
    insight_liaison = _build_domain_liaison(
        role="Insight & Reporting Manager",
        llm_role="liaison-insight",
        goal="Produce the final grounded written answer via delegate_to_insight_reporting.",
        backstory="Represents the Insight & Reporting domain on the Chief's team; call this one last.",
        delegate_tool=delegate_to_insight_reporting,
    )

    task = Task(
        description=(
            f"Answer this user question as completely and accurately as "
            f"possible, with as few delegations as the question needs: "
            f"{question}\n\n"
            "Most analytical questions (counts, sums, averages, 'which/"
            "who/when has the most/least', trends, comparisons) need ONLY "
            "the Quantitative Analysis Manager to get the figures. Delegate "
            "to the Data Discovery Manager ONLY if the question is itself "
            "about the dataset's structure (e.g. 'what tables/columns do "
            "we have', 'how is X related to Y') — do not call it just to "
            "double-check a table you already have figures for. Then "
            "ALWAYS finish by delegating to the Insight & Reporting "
            "Manager to produce the final grounded answer."
        ),
        expected_output=(
            "The Insight & Reporting Manager's answer text, verbatim and "
            "unmodified — no preamble, no meta-commentary about what you "
            "are doing (e.g. do not say 'relaying the answer' or 'here it "
            "is'), just the answer itself starting from its first word."
        ),
    )

    return Crew(
        process=Process.hierarchical,
        manager_agent=chief,
        agents=[quant_liaison, discovery_liaison, insight_liaison],
        tasks=[task],
        planning=True,
        planning_llm=build_llm(role="orchestrator-planning", stream=False),
        memory=get_session_memory(),
        tracing=False,
        verbose=True,
    )
