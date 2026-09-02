"""Application configuration.

Single source of truth for settings. The only *required* configuration is
the three variables already in ``.env``:

    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL

Everything else the app needs (ports, directories, iteration caps, ...) is a
code-level constant with a sensible default, not an environment variable —
that is a deliberate constraint from the workshop brief, not an oversight.

One *optional* fourth variable, ``LLM_MODEL_OVERRIDES``, lets different
agent roles use different models (still against the same endpoint/key)
without adding a whole new variable per role — see
``app.llm.factory.build_llm``.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# --- Fixed, non-secret locations -------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
MEMORY_DIR: Path = PROJECT_ROOT / ".crew_memory"


class Settings(BaseSettings):
    """The three required environment variables, plus one optional one.

    ``pydantic-settings`` reads them from ``.env`` (case-insensitively) and
    fails fast and loudly at startup if a required one is missing, rather
    than the app limping along and failing deep inside an LLM call later.
    """

    llm_base_url: str
    llm_api_key: str
    llm_model: str

    llm_model_overrides: dict[str, str] = {}
    """Optional per-agent-role model overrides, e.g. in .env::

        LLM_MODEL_OVERRIDES={"data-analyst": "deepseek-r1", "liaison-quant": "gpt-5-mini"}

    Any role not listed here falls back to ``LLM_MODEL``. See the role
    identifiers passed to ``build_llm(role=...)`` throughout app/agents and
    app/crews for the exact keys this accepts.
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("llm_model_overrides", mode="before")
    @classmethod
    def _parse_overrides(cls, value: object) -> object:
        """Accept ``LLM_MODEL_OVERRIDES`` as a JSON object string; tolerate
        it being unset or malformed rather than crashing the whole app over
        an optional convenience variable."""
        if not value or isinstance(value, dict):
            return value or {}
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            logger.warning("LLM_MODEL_OVERRIDES is not valid JSON; ignoring it: %r", value)
            return {}
        if not isinstance(parsed, dict):
            logger.warning("LLM_MODEL_OVERRIDES must be a JSON object; ignoring: %r", value)
            return {}
        return parsed


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide Settings singleton (constructed once, cached)."""
    return Settings()
