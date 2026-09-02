"""Builds every LLM instance used in this project — and only via the OpenAI SDK.

CrewAI's plain ``LLM(model="...")`` normally routes through litellm. It also
ships a *native*, non-litellm code path built directly on the official
``openai`` Python package (``crewai.llms.providers.openai.completion.
OpenAICompletion``) for talking to OpenAI-compatible endpoints. Passing
``custom_openai=True`` forces that native path unconditionally — litellm is
never imported, let alone called.

That native provider already implements everything a "hand-rolled OpenAI SDK
LLM" would need to: real ``openai`` client streaming (chunk-by-chunk, emitted
as ``LLMStreamChunkEvent`` on the shared event bus — see
``app/events/stream_bus.py``), and the manual function/tool-calling loop
(send messages+tools -> execute any ``tool_calls`` -> feed results back ->
repeat until plain content). Reimplementing that would only add a second,
untested version of the same logic, so this factory simply configures the
official one — Dependency Inversion in spirit: the rest of the app depends on
``build_llm()``, never on how a completion actually gets made.

Every agent/manager/critic in this project gets its LLM from here, and every
one of them talks to the same endpoint/key from ``.env``. They do not all
have to use the same *model*, though: passing ``role`` looks that role up in
the optional ``LLM_MODEL_OVERRIDES`` setting, so e.g. a cheaper/faster model
can be assigned to a liaison agent while a stronger one handles the Data
Analyst — set per role in ``.env``, no code change required. A role with no
override, or no ``LLM_MODEL_OVERRIDES`` set at all, just gets ``LLM_MODEL``.
"""

from __future__ import annotations

from crewai import LLM

from app.config import Settings, get_settings


def build_llm(*, role: str, stream: bool = True) -> LLM:
    """Construct one OpenAI-SDK-backed LLM instance for a specific agent role.

    Deliberately does **not** set ``temperature``: several current models
    (this project has hit it on a gpt-5.x-family deployment) reject any
    non-default value outright —
    ``Unsupported value: 'temperature' does not support 0.2 with this
    model. Only the default (1) value is supported.`` Per-role model
    overrides (``LLM_MODEL_OVERRIDES``) mean any role can end up pointed at
    such a model, so no role can safely pass a custom temperature; omitting
    it lets every model use its own default instead of erroring.

    Args:
        role: A short, stable identifier for the calling agent (e.g.
            "chief-orchestrator", "data-analyst" — see the call sites in
            app/agents and app/crews for the full set). Used both for
            log/trace readability and as the lookup key into
            ``LLM_MODEL_OVERRIDES``.
        stream: Whether to stream tokens as they are generated. Left on by
            default so the SSE bridge can forward live output to the UI.

    Returns:
        A ``crewai.LLM`` whose concrete class is
        ``OpenAICompletion`` — the native OpenAI-SDK implementation, never
        litellm — configured from :class:`app.config.Settings`.
    """
    settings: Settings = get_settings()
    model = settings.llm_model_overrides.get(role, settings.llm_model)
    return LLM(
        model=model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        custom_openai=True,  # forces the native openai-SDK provider, bypassing litellm
        stream=stream,
    )
