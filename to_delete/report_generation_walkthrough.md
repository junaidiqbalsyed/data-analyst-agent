Report Generation Agent — Presenter Reference

A one-page map of how the PreQualified Lead Report is built, with file and line
references so any question in the room can be answered by pointing at the code.

> Line numbers drift as the code changes. If one looks off, search for the symbol
> name in the same file — the names are stable, the line numbers are not.

Package note: the Word library is imported as docx but installed as
python-docx. The LLM wrapper is LLM_openai, pinned to gpt-5.6-sol for this
agent only (factory.py:32).

────────

The three phases

```
main.py:45  orchestrator.generate(npi)
   │
   ├─ PHASE 1  COLLECT   orchestrator.py:66   collector.collect(npi)
   ├─ PHASE 2  WRITE     orchestrator.py:71   _write_sections()  (the loop)
   └─ PHASE 3  RENDER    orchestrator.py:68   renderer.render()  (Word)
```

────────

Phase 1 — Collection

collector.py gathers four sources into one ProviderIntelligence object.

|Source        |What it provides                                                   |
|--------------|-------------------------------------------------------------------|
|Model Summary |the `/provider_model_summary` API roll-up (risk scores per model)  |
|Model Evidence|DB query for the NPI — claims, patients, paid, dates, service codes|
|NPI Context   |public-source provider profile (identity, licensure)               |
|Web Evidence  |researched corroborating enforcement cases                         |

Scope rule (important): the report describes only the models the summary API
returned data for. A model marked no_data is dropped even if the evidence tables
hold rows for it — so the report never claims a finding the UI does not show.

────────

Phase 2 — Loop engineering

Prompts do not live in the agent classes. adk_agents.py is thin ADK
plumbing; the prompts live in writer.py.

```
adk_agents.py:45  WriterAgent._run_async_impl (:53)
    ├─ pass 1 → writer.draft()    writer.py:281   DRAFT PROMPT
    └─ pass 2+ → writer.revise()  writer.py:371   REVISE PROMPT

adk_agents.py:80  CriticAgent._run_async_impl (:88)
    └─ critic.review()            writer.py:752   guards, then rubric

adk_agents.py:112  build_section_loop()  → ADK LoopAgent, max_iterations=3
```

Where each prompt is

|Prompt            |File : line        |Notes                                                                                                                |
|------------------|-------------------|---------------------------------------------------------------------------------------------------------------------|
|Analyst persona   |`writer.py:27`     |`ANALYST_PERSONA`, prepended to draft + revise. Sets voice: first-person Gainwell, never “system/AI”, hedged language|
|Draft prompt      |`writer.py:288`    |Assembled from persona + fixed rules + `spec.intent`                                                                 |
|Revise prompt     |`writer.py:378`    |Shorter; consumes the critic’s notes                                                                                 |
|Critic guards     |`writer.py:759-773`|Deterministic, run FIRST                                                                                             |
|Critic rubric     |`writer.py:777`    |LLM judgement, run only if guards pass                                                                               |
|Per-section intent|`models.py:114-345`|Each section’s own instructions (`spec.intent`)                                                                      |

The draft prompt is three parts glued together: persona (writer.py:27) + fixed
rules (writer.py:288) + the section’s intent (models.py).

The review order (guards before rubric)

Inside SectionCritic.review() (writer.py:752):

1. Identity guard — writer.py:760 → identity_leaks() :418
2. Structure guards — writer.py:767 → structure_gaps() :464
3. LLM rubric — writer.py:774 → _llm_review() :776 (reached only if guards pass)

Guards are cheap and deterministic, so they run first; if any fail, the LLM critic
is never called. This is the reverse of “critic then guards” — it is guards then
critic.

The seven structure guards (structure_gaps, writer.py:464)

|Guard                     |Line           |Rejects a draft that…                                      |
|--------------------------|---------------|-----------------------------------------------------------|
|`_peer_claim_gaps`        |`writer.py:517`|makes any peer / benchmark comparison (no peer data exists)|
|`_dead_link_gaps`         |`writer.py:534`|cites a URL that does not resolve                          |
|`_jargon_gaps`            |`writer.py:570`|uses unexpanded shorthand (DOJ, DOS, E/M)                  |
|`_voice_gaps`             |`writer.py:606`|reads as machine-authored instead of Gainwell              |
|`_out_of_scope_model_gaps`|`writer.py:623`|names or counts a model the API returned no data for       |
|`_claim_period_gaps`      |`writer.py:662`|omits the inferred time period on the claim totals         |
|`_model_coverage_gaps`    |`writer.py:695`|drops a model that did return findings                     |

The LLM rubric (_llm_review, writer.py:777)

Scores what code cannot: (1) factual fidelity, (2) fulfils section intent,
(3) senior-analyst tone, (4) concision. Returns strict JSON
{"accept": bool, "notes": "..."}.

What stops the loop

The critic sets escalate=True on acceptance (adk_agents.py:107); ADK treats
that as the break signal. If it never accepts, max_iterations=3 (factory.py:35)
caps it, and redact_identity() (orchestrator.py:105 → writer.py:442) is the
final safety net that scrubs the provider name if a leak survived all passes.

Context blob

build_context_blob() (writer.py:229) serializes the gathered data into the
JSON the prompts read, trimmed to 28k chars.

────────

Phase 3 — Word rendering

Package python-docx, file docx_renderer.py. Structure is code, not a template
file — a fresh Document() is built each run with the same fixed skeleton; only
the content changes.

|Step          |Line                  |What it does                                   |
|--------------|----------------------|-----------------------------------------------|
|`render()`    |`docx_renderer.py:70` |builds Document, saves .docx                   |
|`_configure()`|`docx_renderer.py:84` |Arial, 0.5” margins, footer, Heading 1/2 styles|
|`_cover()`    |`docx_renderer.py:122`|title block (constant layout)                  |
|`_sections()` |`docx_renderer.py:144`|loops 7 sections, each on a new page           |
|`_body()`     |`docx_renderer.py:161`|turns the writer’s Markdown-lite into Word runs|

Tables are built from data, not prose (docx_renderer.py:156): after the
Analysis section, _claim_history_table, _service_code_table, and
_evidence_table read straight from report.intelligence. Their headers and
styling are hardcoded; the rows come from Phase-1 data. This is why a number in a
table cannot drift from the model output — the LLM never touches it.

Markdown → Word: _parse_blocks() splits the body into headings / bullets /
paragraphs; _inline_runs() turns **bold** into bold runs and [label](url)
into a hyperlink — but only when the URL resolves, otherwise plain text.

────────

The seven sections (models.py:110, SECTION_SPECS)

|#|key                 |line           |
|-|--------------------|---------------|
|1|`fraud_introduction`|`models.py:112`|
|2|`how_identified`    |`models.py:147`|
|3|`who_identified`    |`models.py:202`|
|4|`analysis`          |`models.py:219`|
|5|`summary`           |`models.py:273`|
|6|`recommendations`   |`models.py:304`|
|7|`appendix`          |`models.py:343`|

────────

Likely questions, with answers

Why isn’t the prompt in WriterAgent?
Separation of concerns. adk_agents.py manages loop state and the escalate
signal; writer.py owns the LLM logic. The prompts are testable without an ADK
runner.

Guards or critic first?
Guards first (writer.py:759), then the LLM rubric (writer.py:776). A failed
guard short-circuits before any model call.

What if the loop never converges?
max_iterations=3 caps it (factory.py:35); the last draft is used, with
redact_identity as the final safety net.

Why did the guards move into code at all?
An all-caps prompt instruction not to name the provider was ignored, and the LLM
critic approved the draft anyway (writer.py:421 comment). Rules that carry real
risk are now checked deterministically; the LLM handles only judgement.