"""Builds every Agent used in this project.

Centralizing construction here keeps role/goal/backstory copy and default
wiring (llm, tools, reasoning) in one place: crews *assemble* agents, they
don't author them (Single Responsibility).

Every factory returns a **new** instance on every call. Crews are rebuilt
from scratch for each chat turn (see app/crews and app/server/main.py) so
that concurrent requests never share mutable Agent state — cheap to build,
and the only safe option once the server handles more than one request at
a time.
"""

from __future__ import annotations

from crewai import Agent

from app.llm import build_llm
from app.tools import describe_table, list_tables, quick_profile, run_sql_query


def build_chief_orchestrator() -> Agent:
    """LEVEL 1 — the single hierarchical manager LLM that owns delegation.

    Deliberately **not** ``reasoning=True``: on a manager agent, that turns
    on a heavyweight execute/observe/replan loop (several extra LLM calls
    plus a re-plan if a step is judged incomplete) *on top of* the crew's
    own ``planning=True`` step — for a plain delegation call, that's two
    overlapping planning systems fighting each other, not two useful ones.
    ``Crew(planning=True)`` alone already gives the Chief a plan; delegation
    itself doesn't need the Chief to re-reason about its own steps.
    """
    return Agent(
        role="Chief Orchestrator",
        goal=(
            "Understand the user's analytical question, delegate it to exactly "
            "the domain manager(s) that can answer it, and return one clear, "
            "well-grounded final answer. Never invent data yourself."
        ),
        backstory=(
            "You lead a small team of domain managers over a live business "
            "dataset: a Quantitative Analysis Manager (aggregates, KPIs, "
            "trends), a Data Discovery Manager (what data exists and how it "
            "relates), and an Insight & Reporting Manager (turns findings "
            "into a final, grounded, written answer). You decide who needs "
            "to be involved and in what order, then relay their work back "
            "to the user."
        ),
        llm=build_llm(role="chief-orchestrator"),
        allow_delegation=True,
        verbose=True,
    )


def build_quant_sub_manager() -> Agent:
    """LEVEL 2 manager — heads its own hierarchical sub-crew for numeric analysis.

    The brief this manager hands down matters a lot: an over-specified
    delegation ("explore every table with raw PRAGMA/sqlite_master SQL")
    sends the Data Analyst on an exhaustive, off-tool detour instead of
    just calling ``list_tables``/``describe_table``/``run_sql_query`` for
    the couple of tables the question actually needs. The goal/backstory
    below say so explicitly.
    """
    return Agent(
        role="Quantitative Analysis Manager",
        goal=(
            "Get exact aggregates/KPIs/trends by giving the Data Analyst the "
            "question in ONE short, plain sentence — nothing more. Trust "
            "the Data Analyst to use its own tools (list_tables, "
            "describe_table, run_sql_query). Never add words like 'audit', "
            "'end-to-end', 'validate', 'verify data quality', or an "
            "exhaustive 'check every table' instruction — those turn a "
            "simple question into an open-ended review."
        ),
        backstory=(
            "A second-tier manager who owns quantitative analysis. You "
            "never write SQL yourself, and you never over-brief — a short, "
            "clear ask gets a faster, more accurate answer than an "
            "exhaustive checklist."
        ),
        llm=build_llm(role="quant-manager"),
        allow_delegation=True,
        max_iter=3,
        verbose=True,
    )


def build_data_analyst() -> Agent:
    """Specialist: turns a business question into SQL and reports exact figures.

    Deliberately **not** ``reasoning=True``. That flag turns on a
    step-by-step execute/observe/replan loop with its own step budget,
    separate from ``max_iter`` — in practice it has produced a 19-step
    "profile every table before answering" plan for a moderately complex
    question, blowing straight through the ``max_iter`` cap below (which
    only bounds the plain tool-call loop, not a reasoning plan's steps).

    ``max_iter`` bounds LLM *round-trips*, not raw tool calls — some models
    batch several ``run_sql_query`` calls into one round-trip, so even a low
    cap can still add up to a couple dozen queries if the model insists on
    a full data-quality audit (null checks, duplicate checks, join-
    cardinality checks) before it will answer. That's what the goal below
    explicitly forbids: this agent answers directly with straightforward
    queries and assumes the data is clean unless the question is itself
    about data quality.
    """
    return Agent(
        role="Data Analyst",
        goal=(
            "Call list_tables ONCE — it already gives every table's columns "
            "and types, which is normally all the schema you need — then "
            "answer with as few SQL queries as possible (a join and GROUP "
            "BY/aggregate where needed) via run_sql_query. Only call "
            "describe_table afterward if you specifically need to see real "
            "sample values (e.g. the exact spelling of a status value), "
            "and only for the 1-2 tables that need it — never for every "
            "table just to be thorough. Do NOT run data-quality checks "
            "(nulls, duplicates, orphaned foreign keys, join-cardinality "
            "validation) unless the question explicitly asks about data "
            "quality — assume the data is clean and answer with a direct "
            "query, not an audit."
        ),
        backstory="A meticulous, efficient analyst who writes the right query on the first or second try and trusts the data.",
        llm=build_llm(role="data-analyst"),
        tools=[list_tables, describe_table, run_sql_query],
        max_iter=5,
        verbose=True,
    )


def build_schema_explorer() -> Agent:
    """Specialist: discovers and profiles the dataset's structure.

    Also not ``reasoning=True`` — see build_data_analyst's docstring.
    """
    return Agent(
        role="Schema Explorer",
        goal=(
            "Call list_tables ONCE — it already gives every table's columns "
            "and types — to discover exactly what tables, columns, and "
            "relationships are relevant to the question, only the tables "
            "it actually concerns. Reach for describe_table/quick_profile "
            "only when you need real sample values or column statistics, "
            "not to re-check schema list_tables already gave you."
        ),
        backstory="A specialist in reading an unfamiliar, changing schema quickly and accurately.",
        llm=build_llm(role="schema-explorer"),
        tools=[list_tables, describe_table, quick_profile],
        max_iter=5,
        verbose=True,
    )


def build_data_dictionary_writer() -> Agent:
    """Second step of the (sequential) discovery pipeline: writes up the findings."""
    return Agent(
        role="Data Dictionary Writer",
        goal="Turn raw schema findings into a short, clear description of the dataset for a business user.",
        backstory="A technical writer who explains a database schema in plain language.",
        llm=build_llm(role="dictionary-writer"),
        verbose=True,
    )


def build_insight_writer() -> Agent:
    """Drafts the final grounded natural-language answer (see app/critique)."""
    return Agent(
        role="Insight Report Writer",
        goal="Turn query evidence into a clear, correct, fully grounded answer for a business user.",
        backstory="A senior analyst-writer who never states a figure that isn't backed by a query result.",
        llm=build_llm(role="insight-writer"),
        verbose=True,
    )


def build_report_critic() -> Agent:
    """Judges draft answers for clarity/completeness (grounding is handled by guard rails)."""
    return Agent(
        role="Report Critic",
        goal="Judge whether a draft answer is clear and fully answers the user's question.",
        backstory="A rigorous editor who rejects vague, incomplete, or off-topic answers.",
        llm=build_llm(role="report-critic"),
        verbose=True,
    )


def build_router() -> Agent:
    """LEVEL 0 — classifies one message before any crew is built.

    See app/orchestration/router.py for why this exists: running the full
    hierarchical crew for "hi" is enormous overhead for zero benefit. This
    agent's only job is a single, fast, non-streamed structured-output call.
    """
    return Agent(
        role="Router",
        goal="Classify one message as chitchat, analytical, or off_topic — nothing else, no elaboration.",
        backstory="A fast, terse classifier with no other responsibilities.",
        llm=build_llm(role="router", stream=False),
        verbose=False,
    )


def build_conversational_agent() -> Agent:
    """LEVEL 0 — replies directly to chitchat, bypassing the crew entirely."""
    return Agent(
        role="Assistant",
        goal=(
            "Reply warmly and briefly to greetings, thanks, and small talk. "
            "If asked what you can do, explain you can answer analytical "
            "questions about the connected dataset (orders, customers, "
            "products, revenue, etc.) — give one or two example questions."
        ),
        backstory="A friendly front door to the analytics team, for everything that isn't itself an analytical question.",
        llm=build_llm(role="conversational"),
        verbose=False,
    )


def build_fast_analyst() -> Agent:
    """LEVEL 0 — the default path for analytical questions (see
    app/orchestration/fast_path.py).

    The full multi-level crew (app.crews.build_orchestrator_crew) is still
    in the codebase and demonstrates hierarchical/sequential orchestration,
    but costs 5+ sequential LLM round-trips — measured well past 30s for
    real questions. This single agent gathers evidence and writes the
    answer itself: no manager relay, no separate writer/critic hop, just
    one agent going straight from question to grounded answer.
    """
    return Agent(
        role="Fast Analyst",
        goal=(
            "Answer the question directly, but with an analyst's rigor — "
            "never silently paper over a data-coverage gap or an ambiguous "
            "metric definition. Call list_tables once — it already gives "
            "every table's columns and types — then investigate before "
            "answering:\n"
            "- If the question names a specific time period (e.g. 'last "
            "quarter', 'this month', 'last year'), first confirm what "
            "period the data actually covers via the relevant date column "
            "before computing anything. If the requested period isn't "
            "covered, say so plainly, state the actual date range "
            "available, and do NOT report zero or silently substitute a "
            "different period — a coverage gap is a real finding, not a "
            "failure. If the user then says to just use the data you have, "
            "answer directly against that available range and say what "
            "range you used.\n"
            "- If the requested metric is genuinely ambiguous (e.g. "
            "'sales'/'revenue' could mean gross line-item sales, net after "
            "refunds/returns, or payment/cash totals, and these numbers "
            "will differ) compute the most defensible primary measure and "
            "lead with it, but also surface the other candidate figures as "
            "labeled reference numbers rather than silently picking one and "
            "hiding the rest.\n"
            "- Note only the caveats that materially affect trust in this "
            "specific answer, briefly: missing currency/unit metadata, how "
            "figures were combined when multiple tables were involved (so "
            "nothing was double-counted), and any anomaly actually found "
            "(nulls, negatives, duplicates) — or that none were found. This "
            "is a short caveat note, not a full audit; do not go looking "
            "for problems unrelated to answering this question.\n"
            "- Then write it up as a short analyst narrative in Markdown: "
            "a short paragraph (2-3 sentences) stating the headline finding "
            "IN CONTEXT — not just a number, but what it means (bold the "
            "key figures) — followed by a bulleted list of reference/"
            "breakdown figures, and, only when the rules above call for it, "
            "a short closing note on caveats or assumptions.\n"
            "- Keep it business-readable: avoid phrases like 'I ran a SQL "
            "query' or naming raw table/column names, but plain descriptive "
            "words like 'combined', 'totaled per record', or 'joined' are "
            "fine when explaining how figures were derived — clarity and "
            "honesty about the method beats avoiding a word.\n"
            "- Stay concise: this is a tight analyst note, not a report — "
            "skip any of the steps above that plainly don't apply to the "
            "question asked."
        ),
        backstory=(
            "A fast, careful data analyst who answers directly but never "
            "fudges a gap: checks that the data actually covers what's "
            "being asked before answering, surfaces every reasonable "
            "reading of an ambiguous metric instead of silently picking "
            "one, and is upfront about what could make the number wrong. "
            "Writes it up the way a sharp analyst would for a stakeholder — "
            "a short framed narrative, clean reference figures, honest "
            "caveats — never a bare number and never a wall of prose."
        ),
        llm=build_llm(role="fast-analyst"),
        tools=[list_tables, describe_table, run_sql_query],
        # Slightly higher than before: checking date coverage and/or a
        # second candidate measure costs a couple of extra tool calls over
        # the bare-minimum "one query, one answer" path.
        max_iter=9,
        verbose=True,
    )
