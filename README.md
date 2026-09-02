# Multi-Level CrewAI Analytics Chatbot

Developed by **Syed Junaid Iqbal** — connect on [LinkedIn](https://www.linkedin.com/in/syedjunaidiqbal/).

A workshop reference project: a chatbot that answers analytical questions
over a dynamic set of CSVs, built with [CrewAI](https://docs.crewai.com),
FastAPI, and Streamlit — demonstrating hierarchical **and** sequential
orchestration, multiple manager LLMs at different levels of the hierarchy,
memory, planning, reasoning, function-calling tools ("skills"), and a
grounded writer/critic guard-rail loop, all streamed live to the browser
over Server-Sent Events (SSE).

## Architecture

```
Streamlit chat  ──HTTP POST + SSE──▶  FastAPI  ──▶  Orchestrator Crew (LEVEL 1)
(frontend/streamlit_app.py)          (app/server/main.py)   Process.hierarchical
                                                              manager_agent = Chief Orchestrator
                                                                     │
                       ┌─────────────────────────────┼─────────────────────────────┐
                       ▼                              ▼                             ▼
        Quantitative Analysis (LEVEL 2)   Data Discovery (LEVEL 2)    Insight & Reporting (LEVEL 2)
        Process.hierarchical              Process.sequential          guard-railed writer/critic loop
        manager_agent = 2nd manager LLM   Schema Explorer ──▶         (app/critique/analyst_critic.py):
        + Data Analyst (SQL tools)        Data Dictionary Writer      draft → deterministic guards →
                                                                       LLM critic → revise, capped
```

Every LLM call — every manager, every specialist, planning, memory
analysis, the critic — goes through the **OpenAI Python SDK only**
(`crewai`'s native `OpenAICompletion` provider, forced with
`custom_openai=True`; see `app/llm/factory.py`). litellm is never invoked.

The dataset in [`data/`](data/) is **dynamic**: every table/column name is
discovered at call time from whatever CSVs are present (`app/data/catalog.py`,
DuckDB-backed) — drop in a new CSV and it's queryable immediately, no code
change or restart required to be *discovered* (a running server re-globs
`data/*.csv` on every tool call).

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and the three variables already
in `.env` (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` — an OpenAI-compatible
chat completions endpoint).

One more variable is *optional*: `LLM_MODEL_OVERRIDES` lets specific agent
roles use a different model than `LLM_MODEL`, against that same endpoint/key
— e.g. a lighter model for the liaison agents, a stronger one for the Data
Analyst. It's a JSON object, `role -> model`; see the commented-out example
and the full list of role keys in `.env`. Leave it unset and every agent
uses `LLM_MODEL`.

```bash
uv sync
```

## Run

Two processes, in two terminals:

```bash
uv run uvicorn app.server.main:app --reload                          # backend, http://localhost:8000
uv run streamlit run frontend/streamlit_app.py --server.port 80      # frontend, http://localhost
```

...or both at once, for convenience:

```bash
sudo uv run main.py
```

The UI listens on port 80 (plain `http://localhost`, no port needed) —
that's a privileged port, so it needs `sudo` (or a `sudo`-run terminal on
Windows/WSL). Change `UI_PORT` at the top of `main.py`, or drop
`--server.port 80` from the manual command above, to run on Streamlit's
default `:8501` without elevated privileges instead.

Then ask something like *"Which city has the most orders?"* or *"What data
do we have about returns?"* and watch the live trace: the Chief Orchestrator
delegates, sub-crews run their own queries, and the Insight & Reporting
manager drafts, self-checks, and streams the final grounded answer.

## Project layout

```
app/
  config.py                 Settings — reads only the 3 required .env vars
  llm/factory.py             build_llm(): every LLM instance, OpenAI SDK only
  data/catalog.py            DuckDB catalog, dynamically built from data/*.csv
  tools/                     "skills": list_tables, describe_table, quick_profile, run_sql_query
  memory/embedder.py         local, no-API-key embedder for crew memory (chromadb's bundled ONNX model)
  agents/agent_factory.py    every Agent used anywhere in the project
  crews/                     assembles the multi-level hierarchical + sequential crews
  critique/                  the grounded writer → guard rails → critic → revise loop
  events/stream_bus.py       bridges CrewAI's event bus to a per-request SSE queue
  session/conversation_store.py   per chat-session isolated crew memory
  server/main.py             FastAPI app — POST /chat/stream (SSE), GET /health
frontend/
  streamlit_app.py           the chat UI
main.py                      convenience launcher for both processes
```

## Diagrams

Everything below is generated straight from the classes and functions that
actually exist in this codebase (not an idealized sketch) — every name here
is grep-able. GitHub renders these Mermaid diagrams inline.

### Class diagram — data, evidence & guard rails

```mermaid
classDiagram
    class DataCatalog {
        -Path _data_dir
        -DuckDBPyConnection _conn
        -Lock _lock
        +list_tables() List~TableInfo~
        +describe_table(table, sample_size) TableInfo
        +known_identifiers() Set~str~
        +run_query(sql, row_limit) List~dict~
    }
    class TableInfo {
        <<frozen dataclass>>
        +str name
        +str source_file
        +int row_count
        +List~ColumnInfo~ columns
        +List~dict~ sample_rows
    }
    class ColumnInfo {
        <<frozen dataclass>>
        +str name
        +str dtype
    }
    class UnsafeQueryError {
        <<ValueError>>
    }
    class validate_select_only {
        <<function>>
        +validate_select_only(sql) None
    }
    DataCatalog "1" --> "*" TableInfo : produces
    TableInfo "1" *-- "*" ColumnInfo : columns
    validate_select_only ..> UnsafeQueryError : raises
    DataCatalog ..> validate_select_only : run_query() calls first

    class QueryEvidence {
        <<dataclass>>
        +str sql
        +str result_json
    }
    class EvidenceLog {
        <<per-turn, ContextVar-bound>>
        +List~QueryEvidence~ entries
        +record(sql, result_json)
        +combined_text() str
        +queries() List~str~
    }
    EvidenceLog "1" *-- "*" QueryEvidence : entries

    class GuardResult {
        <<frozen dataclass>>
        +bool passed
        +Tuple~str~ notes
    }
    class CriticVerdict {
        <<pydantic BaseModel>>
        +bool accept
        +str notes
    }
    class run_guard_rails {
        <<function>>
        +run_guard_rails(draft, evidence) GuardResult
    }
    class run_insight_loop {
        <<function, MAX_ITERATIONS = 3, fail-open>>
        +run_insight_loop(question, evidence, writer, critic) str
    }
    run_guard_rails ..> GuardResult : returns
    run_guard_rails ..> EvidenceLog : reads combined_text
    run_guard_rails ..> DataCatalog : known_identifiers for schema guard
    run_insight_loop ..> run_guard_rails : every pass, before the critic
    run_insight_loop ..> CriticVerdict : critic.kickoff(response_format=)
    run_insight_loop ..> EvidenceLog : reads
```

### Class diagram — settings, routing & streaming

```mermaid
classDiagram
    class Settings {
        <<pydantic BaseSettings>>
        +str llm_base_url
        +str llm_api_key
        +str llm_model
        +Dict~str, str~ llm_model_overrides
    }
    class get_settings {
        <<function, lru_cache>>
        +get_settings() Settings
    }
    class build_llm {
        <<function>>
        +build_llm(role, stream) LLM
    }
    get_settings ..> Settings : constructs once, cached
    build_llm ..> get_settings : reads base_url/api_key/model
    build_llm ..> LLM : constructs crewai.LLM, custom_openai=True

    class ChatRequest {
        <<pydantic BaseModel, FastAPI body>>
        +str session_id
        +str message
    }
    class Intent {
        <<enumeration>>
        CHITCHAT
        ANALYTICAL
        OFF_TOPIC
    }
    class _RouteDecision {
        <<pydantic BaseModel>>
        +str intent
    }
    class classify_intent {
        <<function, fails open to ANALYTICAL>>
        +classify_intent(message) Intent
    }
    classify_intent ..> _RouteDecision : router.kickoff(response_format=)
    classify_intent ..> Intent : returns
    ChatRequest ..> classify_intent : request.message

    class _RequestState {
        <<mutable, ContextVar-shared>>
        +SimpleQueue queue
        +str active_role
    }
    class WorkshopEventListener {
        <<BaseEventListener, crewai>>
        +setup_listeners(bus)
    }
    class bind_new_queue {
        <<function>>
        +bind_new_queue() SimpleQueue
    }
    bind_new_queue ..> _RequestState : creates, binds to ContextVar
    WorkshopEventListener ..> _RequestState : reads/mutates active_role, pushes events

    class turn_history {
        <<module — per-session transcript>>
        +record_turn(session_id, question, answer)
        +get_recent_history(session_id) List~Tuple~
        +format_history_for_prompt(history) str
    }
    ChatRequest ..> turn_history : keyed by session_id
```

### Sequence diagram — one chat turn (fast path, default)

```mermaid
sequenceDiagram
    autonumber
    actor U as User (browser)
    participant UI as Streamlit (frontend/streamlit_app.py)
    participant API as FastAPI /chat/stream (app/server/main.py)
    participant Bus as WorkshopEventListener (SSE bridge)
    participant Router as Router Agent (build_router)
    participant Chat as Assistant Agent (build_conversational_agent)
    participant Fast as Fast Analyst Agent (build_fast_analyst)
    participant Cat as DataCatalog (DuckDB)
    participant Hist as turn_history

    U->>UI: types a message
    UI->>API: POST /chat/stream {session_id, message}
    API->>API: bind_new_queue() / start_session_memory() / start_evidence_log()
    API->>Hist: get_recent_history(session_id)
    Hist-->>API: last 6 (question, answer) turns
    API->>Router: classify_intent(message)
    Router-->>API: Intent (chitchat | analytical | off_topic)
    API-->>UI: SSE "routed" {intent}

    alt intent == chitchat
        API->>Chat: kickoff(history + message)
        Chat-->>API: reply
    else intent == off_topic
        API->>API: decline_off_topic() — fixed string, no LLM call
    else intent == analytical (default path)
        API->>Fast: kickoff(history + question)
        loop as many times as the question needs
            Fast->>Cat: list_tables() / run_sql_query(sql)
            Cat-->>Fast: schema / rows, as JSON
            Fast->>Fast: run_sql_query records into EvidenceLog
        end
        Fast-->>API: structured Markdown answer
    end

    par live trace + tokens, throughout
        Bus-->>API: agent_started / tool_started / token ... via shared queue
        API-->>UI: SSE trace + token events
    end

    API->>Hist: record_turn(session_id, message, answer)
    API-->>UI: SSE "final" {text}
    UI-->>U: renders answer, trace auto-collapses
```

### Flow diagram — request routing

```mermaid
flowchart TD
    A["User message"] --> B{"classify_intent()<br/>Router Agent, structured output"}
    B -->|"chitchat"| C["run_chitchat()<br/>Assistant Agent — no crew, no SQL"]
    B -->|"off_topic"| D["decline_off_topic()<br/>fixed string, zero LLM cost"]
    B -->|"analytical, or a<br/>classification error"| E["run_fast_analysis()<br/>Fast Analyst Agent — default path"]

    E --> F["list_tables / run_sql_query<br/>against DataCatalog"]
    F --> G["Structured Markdown answer:<br/>headline in context + bullets + caveats"]

    C --> H["record_turn()"]
    D --> H
    G --> H
    H --> I["SSE 'final' event to Streamlit"]

    E -.->|"deep mode exists in code<br/>but the router never calls it"| J["build_orchestrator_crew()<br/>full hierarchical + sequential crew"]
```

### Flow diagram — deep mode: the full multi-level crew

Not on the default path (see above), but still fully wired and runnable
directly — this is the hierarchical + sequential, multi-manager-LLM
architecture the workshop set out to demonstrate.

```mermaid
flowchart TD
    subgraph L1["LEVEL 1 — Process.hierarchical"]
        Chief["Chief Orchestrator<br/>(manager_agent)"]
        QL["Quantitative Analysis Manager<br/>liaison — tool: delegate_to_quantitative_analysis"]
        DL["Data Discovery Manager<br/>liaison — tool: delegate_to_data_discovery"]
        IL["Insight & Reporting Manager<br/>liaison — tool: delegate_to_insight_reporting"]
        Chief -->|"delegates"| QL
        Chief -->|"delegates"| DL
        Chief -->|"delegates, always last"| IL
    end

    subgraph L2Q["LEVEL 2 — Quantitative (Process.hierarchical)"]
        QM["Quant Sub-Manager<br/>2nd manager LLM"]
        DA["Data Analyst<br/>tools: list_tables, describe_table, run_sql_query"]
        QM --> DA
    end

    subgraph L2D["LEVEL 2 — Discovery (Process.sequential)"]
        SE["Schema Explorer<br/>tools: list_tables, describe_table, quick_profile"]
        DW["Data Dictionary Writer"]
        SE --> DW
    end

    subgraph L2I["LEVEL 2 — Insight (guard-railed loop, not a Crew)"]
        W["Insight Writer<br/>drafts from EvidenceLog"]
        Gr{"Guard rails<br/>numeric + schema + plain-language"}
        Cr["Report Critic<br/>LLM rubric: accept / notes"]
        W --> Gr
        Gr -->|"fail"| W
        Gr -->|"pass"| Cr
        Cr -->|"reject, notes"| W
        Cr -->|"accept"| Done["final grounded answer"]
    end

    QL -.->|"kicks off"| L2Q
    DL -.->|"kicks off"| L2D
    IL -.->|"kicks off"| L2I
    L2Q -.->|"result text"| IL
    L2D -.->|"result text"| IL
```

The Insight loop is capped at `MAX_ITERATIONS = 3` and fails open: if it
never converges, the last draft ships anyway rather than blocking the user
(see `app/critique/analyst_critic.py`).

### Tools ("skills") reference

| Tool | Defined in | Used by | What it does |
| --- | --- | --- | --- |
| `list_tables` | `app/tools/schema_tools.py` | Fast Analyst, Data Analyst, Schema Explorer | Every table's columns + types + row counts, re-discovered from `data/*.csv` on every call |
| `describe_table` | `app/tools/schema_tools.py` | same | Schema + a few sample rows for one table |
| `quick_profile` | `app/tools/schema_tools.py` | Schema Explorer | Numeric/categorical summary stats for one column |
| `run_sql_query` | `app/tools/sql_tool.py` | Fast Analyst, Data Analyst | Validated, read-only SQL (`SELECT`/`WITH` only — see `validate_select_only`) against the DuckDB catalog; every call is recorded into that turn's `EvidenceLog` |

### SSE events reference

Every event `WorkshopEventListener` (or `app/server/main.py` itself) puts
on the queue, in the shape the frontend actually parses (`event: <type>`,
`data: <json>`):

| Event | Emitted when | Key fields |
| --- | --- | --- |
| `routed` | intent classification finishes | `intent` |
| `crew_started` / `crew_completed` / `crew_failed` | a `Crew.kickoff()` begins/ends — deep mode only | `crew_name` [, `error`] |
| `agent_started` / `agent_completed` / `agent_error` | an `Agent` or `LiteAgent` begins/ends | `role` [, `error`] |
| `agent_reasoning_started` / `agent_reasoning_completed` | a `reasoning=True` agent plans | `role` [, `plan`] |
| `task_started` / `task_completed` | a `Task` begins/ends — deep mode only | `description` |
| `tool_started` / `tool_finished` / `tool_error` | a tool call begins/ends | `tool`, `role` [, `args`/`error`] |
| `token` | one streamed LLM chunk | `role`, `text` |
| `final` | the turn's answer is ready | `text` |
| `error` | the turn raised an exception | `text` |

---

Developed by **Syed Junaid Iqbal** — let's connect on [LinkedIn](https://www.linkedin.com/in/syedjunaidiqbal/).

# data-analyst-agent
