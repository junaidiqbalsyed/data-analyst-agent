"""LEVEL 2 — the three domain sub-crews the Chief Orchestrator delegates to.

Each domain is exposed to the top-level crew as one `@tool` function (the
"Crew-as-a-Tool" pattern): the Chief delegates a task to a domain-manager
worker agent, that agent's only real move is calling its one tool, and the
tool builds and runs an entire second-level crew before returning the
result as plain text. That is what makes this *multi-level* orchestration
rather than one flat crew — each tool call below is a fully independent
Crew.kickoff(), with its own process, its own manager (where applicable),
and its own memory.

    Quantitative Analysis -> its own Process.hierarchical crew (a 2nd
        manager LLM heading one Data Analyst specialist).
    Data Discovery         -> a Process.sequential crew (Schema Explorer's
        findings feed straight into a Data Dictionary Writer — a fixed,
        two-step pipeline needs no manager of its own).
    Insight & Reporting    -> not a Crew at all, but the guard-railed
        writer/critic loop in app.critique.analyst_critic — the ordering
        (draft, guard, critique, revise) is sequential by construction, and
        needs the kind of conditional control a fixed Crew task graph can't
        express, exactly like the reference report-writer this project's
        pattern is modeled on (see app/critique/analyst_critic.py's
        module docstring).
"""

from __future__ import annotations

from crewai import Crew, Process, Task
from crewai.tools import tool

from app.agents import (
    build_data_analyst,
    build_data_dictionary_writer,
    build_insight_writer,
    build_quant_sub_manager,
    build_report_critic,
    build_schema_explorer,
)
from app.critique.analyst_critic import run_insight_loop
from app.critique.evidence import get_evidence_log
from app.session import get_session_memory


@tool("delegate_to_quantitative_analysis")
def delegate_to_quantitative_analysis(question: str) -> str:
    """Delegate a numeric/aggregate/trend/KPI question to the Quantitative
    Analysis domain: its own manager LLM heading a Data Analyst who queries
    the dataset with SQL and reports exact figures.

    Args:
        question: The precise analytical question to answer with numbers.

    Returns the domain crew's final answer as plain text.
    """
    manager = build_quant_sub_manager()
    analyst = build_data_analyst()
    task = Task(
        description=(
            f"Get the exact figures to answer: {question}\n"
            "Give the Data Analyst this question directly, in one short "
            "sentence. Do not ask for an audit, an end-to-end review, or "
            "data-quality validation — a straightforward query is enough."
        ),
        expected_output="A precise answer with the exact figures found, and the SQL used to find them.",
    )
    # No planning=True here: the top-level orchestrator crew already plans
    # the whole turn (see build_orchestrator_crew) — a second planning pass
    # inside this sub-crew was redundant overhead, and in testing its
    # auto-generated plan is exactly where "audit-ready, end-to-end"
    # framing crept in, sending the Data Analyst into an open-ended
    # data-quality review instead of a direct query.
    crew = Crew(
        process=Process.hierarchical,
        manager_agent=manager,
        agents=[analyst],
        tasks=[task],
        memory=get_session_memory(),
        tracing=False,
        verbose=True,
    )
    return str(crew.kickoff())


@tool("delegate_to_data_discovery")
def delegate_to_data_discovery(question: str) -> str:
    """Delegate a "what data exists / how is it structured" question to the
    Data Discovery domain: a Schema Explorer profiles the dataset and a
    Data Dictionary Writer turns the findings into a clear description.

    Args:
        question: What the user wants to know about the dataset's structure.

    Returns the domain crew's final answer as plain text.
    """
    explorer = build_schema_explorer()
    writer = build_data_dictionary_writer()
    discover_task = Task(
        description=(
            f"Discover what's needed to answer: {question}\n"
            "List the relevant tables, their columns, and how they relate "
            "(shared id columns) using list_tables/describe_table/quick_profile."
        ),
        expected_output="The relevant tables, their columns and types, and how they relate.",
        agent=explorer,
    )
    write_task = Task(
        description="Write a short, clear, plain-language answer to the user's original question using the findings.",
        expected_output="A clear plain-language description answering the user's question about the data.",
        agent=writer,
        context=[discover_task],
    )
    crew = Crew(
        process=Process.sequential,
        agents=[explorer, writer],
        tasks=[discover_task, write_task],
        tracing=False,
        verbose=True,
    )
    return str(crew.kickoff())


@tool("delegate_to_insight_reporting")
def delegate_to_insight_reporting(question: str) -> str:
    """Delegate producing the final, grounded written answer to the Insight &
    Reporting domain. If no query evidence has been gathered yet this turn,
    it first runs its own Data Analyst pass to collect some — then drafts,
    guard-checks, and critiques the answer in a capped revise loop so every
    figure it states is traceable to an actual query result.

    Args:
        question: The user's original question to produce a final answer for.

    Returns the final, grounded natural-language answer.
    """
    evidence = get_evidence_log()
    if not evidence.entries:
        build_data_analyst().kickoff(
            f"Run whatever SQL queries are needed to gather the figures to answer: {question}"
        )
    writer = build_insight_writer()
    critic = build_report_critic()
    return run_insight_loop(question=question, evidence=evidence, writer=writer, critic=critic)
