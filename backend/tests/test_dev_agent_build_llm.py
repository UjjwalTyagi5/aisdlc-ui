"""Regression test for _build_llm's temperature handling.

Found live during Task 10 verification (2026-08-31): _build_llm hardcoded
temperature=0.1 unconditionally for every model. gpt-5-family models
(including gpt-5-codex) reject any temperature other than the default 1 --
litellm raises UnsupportedParamsError pre-call, breaking the Development
agent's chat for any project on such a model. Confirmed live against a real
azure/gpt-5-mini deployment: the identical call succeeds the instant
temperature is omitted, and other models are unaffected by dropping the
param only for this one family.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_gpt5_family_models_get_no_temperature_kwarg():
    from agents_orchestrator.development_agent.agents import dev_agent

    dev_agent._LLM_CACHE.clear()
    llm = dev_agent._build_llm(
        "azure/gpt-5-mini", "azure", "fake-key", "https://example.invalid", "alias-1",
    )
    assert llm.temperature is None


async def test_gpt5_codex_also_gets_no_temperature_kwarg():
    from agents_orchestrator.development_agent.agents import dev_agent

    dev_agent._LLM_CACHE.clear()
    llm = dev_agent._build_llm(
        "openai/gpt-5-codex", "openai", "fake-key", None, "alias-2",
    )
    assert llm.temperature is None


async def test_non_gpt5_models_keep_the_low_deterministic_temperature():
    from agents_orchestrator.development_agent.agents import dev_agent

    dev_agent._LLM_CACHE.clear()
    llm = dev_agent._build_llm(
        "anthropic/claude-sonnet-4-6", "anthropic", "fake-key", None, "alias-3",
    )
    assert llm.temperature == 0.1


async def test_build_llm_still_caches_by_alias_and_model():
    from agents_orchestrator.development_agent.agents import dev_agent

    dev_agent._LLM_CACHE.clear()
    first = dev_agent._build_llm(
        "azure/gpt-5-mini", "azure", "fake-key", None, "alias-4",
    )
    second = dev_agent._build_llm(
        "azure/gpt-5-mini", "azure", "fake-key", None, "alias-4",
    )
    assert first is second
