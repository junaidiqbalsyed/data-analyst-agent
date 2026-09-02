"""LLM construction — the one place in the codebase allowed to build an LLM."""

from app.llm.factory import build_llm

__all__ = ["build_llm"]
