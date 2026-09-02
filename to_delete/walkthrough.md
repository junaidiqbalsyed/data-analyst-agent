Code Walkthrough — Report Generation Agent

A step-by-step guide for explaining this codebase to someone, in the order the
data actually flows.

The one-line pitch

> It takes an **NPI** and produces a confidential fraud-capture **Word
> document**. It gathers intelligence from three internal data sources, then uses
> a **Google ADK loop** where an AI writer drafts each section and an AI critic
> reviews it — rewriting until the prose is good enough — and finally renders it
> to a fixed-template .docx.

Data-flow diagram

```
             python -m ...main <NPI>
                      │
                      ▼
   ┌──────────────────────────────────────────┐
   │  main.py                                   │
   │  • ssl_patch.apply()  (before openai)      │
   │  • mandatory NPI arg                       │
   └───────────────┬────────────────────────────┘
                   ▼
   ┌──────────────────────────────────────────┐
   │  factory.py  (composition root)            │
   │  build_runtime() ─► summary service        │
   │  load_from_json,  web_search,  LLM_openai  │
   └───────────────┬────────────────────────────┘
                   ▼
   ┌──────────────────────────────────────────┐
   │  orchestrator.py    collect → write → render│
   └───┬───────────────┬──────────────────┬─────┘
       ▼               ▼                  ▼
 ┌───────────┐   ┌─────────────┐    ┌──────────────┐
 │collector  │   │ ADK loop     │    │ pdf_renderer │
 │(3 sources)│   │ per section  │    │ fixed template│
 └─────┬─────┘   └──────┬──────┘    └──────────────┘
       │                │
       ▼                ▼
 data_sources.py   adk_agents.py
 (Protocol ports)  LoopAgent =
   ├ ProviderModelSummarySource   WriterAgent  ─► writer.py SectionWriter
   ├ NpiContextSource             CriticAgent  ─► writer.py SectionCritic
   └ WebEvidenceSource            (escalate=accept breaks loop)
       │
       ▼
 profile_mapper.py  (NPPES/investigation JSON → ProviderProfile)

 models.py = shared contracts + fixed SECTION_SPECS
```

Runtime data-flow (what data exists at each stage)

How the data transforms as one report is built:

```
NPI (str)  e.g. "1780667832"
   │
   ▼  collector.collect(npi)
   │   ├─ NpiContextSource.fetch(npi)      → {nppes:{…}, investigation:{…}}
   │   ├─ profile_mapper.build_profile()   → ProviderProfile(name, address, …)
   │   ├─ ProviderModelSummarySource.fetch → {models:{…}, summary:{overall_risk…}}
   │   └─ WebEvidenceSource.search(q×3)    → {background, enforcement, specialty}
   ▼
ProviderIntelligence(profile, model_summary, npi_context, web_evidence)
   │
   ▼  writer.build_context_blob(intel)     → compact JSON string (the "context")
   │
   ▼  for each SectionSpec  (× 3, via ADK LoopAgent)
   │      session.state = { intel, context_blob }
   │      ┌────────────────── loop (max_iterations) ──────────────────┐
   │      │ WriterAgent  → SectionWriter.draft/revise → state[draft]   │
   │      │ CriticAgent  → SectionCritic.review       → state[accepted]│
   │      │              accepted? → escalate=True → break             │
   │      └───────────────────────────────────────────────────────────┘
   │      → ReportSection(title, body, iterations, accepted)
   ▼
GeneratedReport(profile, [ReportSection × 7], generated_at, intelligence)
   │
   ▼  docx_renderer.render(report, out_path)
   ▼
PreQualified_Lead_Report_<NPI>.docx  (cover · 7 chapters · tables)
```

The single most important state transition: context_blob is read-only for
the whole loop, while state[draft] and state[accepted] are the values the
writer and critic mutate to hand work back and forth.

Step-by-step (execution order)

1. main.py — entry point

Run with python -m src.agents.report_generation_agent.main <NPI>. NPI is
mandatory, no default. Applies ssl_patch before any OpenAI import so the
corporate TLS bundle is in place. Builds the pipeline via the factory, runs it,
prints a summary.

Why ssl_patch first: import order matters — the patch must run before
httpx/openai load.

2. factory.py — composition root

Wires the real dependencies: build_runtime() gives the same provider-model
summary service the live API uses; load_from_json and web_search are grabbed
directly; the existing LLM_openai object is constructed (reads .env). Nothing
is rebuilt — we reuse existing plumbing.

3. data_sources.py — the three sources (SOLID)

Three adapters, each wrapping the function behind an API, never an HTTP call:

|Adapter                     |Function                   |Behind API                  |
|----------------------------|---------------------------|----------------------------|
|`ProviderModelSummarySource`|`summarize_provider_models`|`/provider_model_summary`   |
|`NpiContextSource`          |`load_from_json`           |`/chatbot/npi/select`       |
|`WebEvidenceSource`         |`web_search`               |`/chatbot/web-search/stream`|

Each sits behind a Protocol (interface) → the rest of the code depends on the
abstraction, not the concrete service (Dependency Inversion). Enables fakes in
tests. Each adapter fails gracefully — empty result instead of a crash.

4. collector.py — gathering intelligence

Runs the three sources in order with a TQDM bar: NPI context → risk summary →
web evidence. The web-search queries are built from what the first two sources
found (name, specialty, state) so searches stay on-topic. Produces one
ProviderIntelligence object.

5. profile_mapper.py — facts → provider block

Extracts the clean provider fact block (name, address, taxonomy, license…) from
raw NPPES/investigation JSON. Defensive lookups so missing fields never break
rendering.

6. models.py — contracts + fixed structure

Plain data shapes everyone agrees on. SECTION_SPECS defines the fixed
report structure — Fraud Introduction, How this Fraud was Identified, Who was
Identified — same for every NPI, only values change. Each spec carries an
“intent” that feeds the AI prompt.

7. writer.py — the prose engine

Two LLM-driven classes:

• SectionWriter — drafts / revises a section in a senior-analyst persona, using
only facts from the context (never invents numbers).
• SectionCritic — reviews a draft, returns strict JSON {accept, notes}.
Fails open (keeps the draft) if the critic itself errors.

8. adk_agents.py — the loop engineering (centerpiece)

Wraps writer + critic as Google ADK agents inside a LoopAgent:

• WriterAgent drafts (pass 1) or revises (later passes)
• CriticAgent reviews; on accept sets escalate=True → ADK breaks the loop

State passes between them through ctx.session.state (draft, critique, count).
A max-iterations cap prevents runaway loops.

9. orchestrator.py — tying it together

Runs collect → write → render. For each section it spins up the ADK loop via an
InMemoryRunner, seeds session state with the intelligence, runs to completion,
pulls the final draft out. TQDM across sections. Assembles a GeneratedReport
and hands it to the renderer.

10. docx_renderer.py — the output

Pure layout, no AI/data logic. Fixed template: cover → the seven sections (each
on its own page) → the subject fact block and the three data tables, which are
built from the gathered data rather than the prose so their figures cannot drift.
Structure follows the PDF the business approved; the look follows the reference
Word report (Arial, half-inch margins, bold headings over a green rule).

Common questions

What makes it well-designed?

1. SOLID / Dependency Inversion — agents depend on Protocol interfaces.
2. Separation of concerns — collection, writing, rendering fully independent.
3. Loop engineering — self-critiquing write/revise cycle, not a one-shot prompt.
4. Resilience — every source fails gracefully; the PDF always renders.

Where does the AI actually run?
Only in writer.py (writer + critic). Everything else is deterministic plumbing.
It reuses the project’s LLM_openai object, inheriting model config, SSL setup,
and token tracking.