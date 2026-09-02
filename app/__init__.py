"""Multi-level CrewAI analytics chatbot.

Package layout (each module has one job — Single Responsibility Principle):

    config              -> reads the three required .env variables, nothing else
    llm                 -> builds LLM instances (OpenAI SDK only, no litellm)
    data                -> the dynamic DuckDB catalog over data/*.csv
    tools               -> "skills": function-calling tools shared by agents
    memory              -> local, no-API-key embedder configuration for crew memory
    agents              -> builds CrewAI Agents (role/goal/backstory/tools/llm)
    critique            -> the grounded writer/critic guard-rail loop
    crews               -> assembles the multi-level (hierarchical + sequential) crews
    events              -> bridges CrewAI's event bus to a per-request SSE queue
    session             -> per chat session memory isolation
    server              -> the FastAPI app (SSE endpoint)
"""
