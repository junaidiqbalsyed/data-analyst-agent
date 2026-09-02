"""Agent construction — every Agent used anywhere in this project is built here."""

from app.agents.agent_factory import (
    build_chief_orchestrator,
    build_conversational_agent,
    build_data_analyst,
    build_data_dictionary_writer,
    build_fast_analyst,
    build_insight_writer,
    build_quant_sub_manager,
    build_report_critic,
    build_router,
    build_schema_explorer,
)

__all__ = [
    "build_chief_orchestrator",
    "build_conversational_agent",
    "build_data_analyst",
    "build_data_dictionary_writer",
    "build_fast_analyst",
    "build_insight_writer",
    "build_quant_sub_manager",
    "build_report_critic",
    "build_router",
    "build_schema_explorer",
]
