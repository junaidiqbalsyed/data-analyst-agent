"""Embedder configuration for CrewAI's short-term/long-term/entity memory.

CrewAI's memory (chroma-backed) needs an embedding function to turn text
into vectors. Left unconfigured, it defaults to OpenAI embeddings, which
would need an ``OPENAI_API_KEY`` — a fourth environment variable this
project isn't allowed to require.

CrewAI ships a fully local "onnx" embedder provider: it wraps chromadb's
bundled ``ONNXMiniLM_L6_V2`` model (~80MB, cached under ``~/.cache/chroma``
after the first run) and needs no network calls at inference time and no
API key at all. That keeps memory a zero-extra-config feature — it works
purely from the three ``.env`` values already required for the LLM.
"""

from __future__ import annotations

from typing import Final

LOCAL_EMBEDDER_CONFIG: Final[dict[str, str]] = {"provider": "onnx"}
