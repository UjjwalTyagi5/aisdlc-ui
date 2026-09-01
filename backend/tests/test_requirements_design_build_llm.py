"""The gpt-5 temperature regression, for Requirements and Design.

SAME BUG, FOUR MORE PLACES. The Development agent hit this live on 2026-08-31:
`_build_llm` hardcoded `temperature=0.1` for every model, and gpt-5-family models
(including gpt-5-codex) reject any temperature other than their default 1 — litellm
raises `UnsupportedParamsError` BEFORE the call rather than clamping it, so every chat
turn failed for a project on such a model. `tests/test_dev_agent_build_llm.py` is the
control for that fix.

Requirements and Design had the identical hardcoded temperature in four places — two
`ChatLiteLLM` builds and two direct `litellm` calls — so a tenant on azure/gpt-5-mini
could not use either agent at all. These tests are the control for the shared helper
they now go through, `shared.services.model_resolver.temperature_kwargs`.

WHY A HELPER AND NOT A FIFTH COPY: five copies of a model-family condition is five
places to miss when the next family lands. The helper is deliberately narrow and is NOT
`litellm.drop_params=True`, which would silently swallow every other unsupported
parameter for every model everywhere.
"""
from __future__ import annotations

import pytest


# ── the helper itself ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("model", [
    "azure/gpt-5-mini",
    "openai/gpt-5-codex",
    "gpt-5",
    "azure/GPT-5-Mini",  # the check is case-insensitive
])
def test_gpt5_family_gets_no_temperature_at_all(model):
    """`{}`, not `{"temperature": 1}`. Passing the supported value explicitly would
    still be passing the parameter, and the point is to leave it to the provider."""
    from shared.services.model_resolver import temperature_kwargs

    assert temperature_kwargs(model, 0.3) == {}


@pytest.mark.unit
@pytest.mark.parametrize("model", [
    "anthropic/claude-sonnet-4-6",
    "azure/gpt-4o",
    "openai/gpt-4-turbo",
])
def test_every_other_model_keeps_its_agent_s_chosen_temperature(model):
    """The low temperature is a deliberate choice per agent — this must not become a
    global default of 1 for everyone as a side effect of the gpt-5 fix."""
    from shared.services.model_resolver import temperature_kwargs

    assert temperature_kwargs(model, 0.3) == {"temperature": 0.3}


@pytest.mark.unit
@pytest.mark.parametrize("model", ["", None])
def test_an_unknown_model_still_gets_a_temperature(model):
    """An empty or missing model name must not silently drop the parameter — that
    would hide a resolution bug behind a working-looking call."""
    from shared.services.model_resolver import temperature_kwargs

    assert temperature_kwargs(model, 0.2) == {"temperature": 0.2}


# ── it is actually wired into both agents ────────────────────────────────────


@pytest.mark.unit
def test_requirements_orchestrator_drops_temperature_for_gpt5():
    from agents_orchestrator.requirements_agent.agents import planning

    planning._ORCHESTRATOR_CACHE.clear()
    llm = planning._build_orchestrator(
        "azure/gpt-5-mini", "azure", "fake-key", "https://example.invalid", "alias-req-1",
    )
    assert llm.temperature is None


@pytest.mark.unit
def test_requirements_orchestrator_keeps_temperature_for_other_models():
    from agents_orchestrator.requirements_agent.agents import planning

    planning._ORCHESTRATOR_CACHE.clear()
    llm = planning._build_orchestrator(
        "anthropic/claude-sonnet-4-6", "anthropic", "fake-key", None, "alias-req-2",
    )
    assert llm.temperature == 0.3


@pytest.mark.unit
def test_design_orchestrator_drops_temperature_for_gpt5():
    from agents_orchestrator.design_architecture_agent.agents import architecture

    architecture._ORCHESTRATOR_CACHE.clear()
    llm = architecture._build_orchestrator(
        "openai/gpt-5-codex", "openai", "fake-key", None, "alias-des-1",
    )
    assert llm.temperature is None


@pytest.mark.unit
def test_design_orchestrator_keeps_temperature_for_other_models():
    from agents_orchestrator.design_architecture_agent.agents import architecture

    architecture._ORCHESTRATOR_CACHE.clear()
    llm = architecture._build_orchestrator(
        "azure/gpt-4o", "azure", "fake-key", None, "alias-des-2",
    )
    assert llm.temperature == 0.2


@pytest.mark.unit
def test_the_caches_still_key_on_alias_and_model():
    """The helper is applied inside the cached builder, so a mistake there could have
    turned every call into a cache miss."""
    from agents_orchestrator.design_architecture_agent.agents import architecture
    from agents_orchestrator.requirements_agent.agents import planning

    planning._ORCHESTRATOR_CACHE.clear()
    architecture._ORCHESTRATOR_CACHE.clear()
    args = ("azure/gpt-5-mini", "azure", "fake-key", None, "alias-cache")
    assert planning._build_orchestrator(*args) is planning._build_orchestrator(*args)
    assert architecture._build_orchestrator(*args) is architecture._build_orchestrator(*args)


# ── no hardcoded temperature is left behind ──────────────────────────────────


@pytest.mark.unit
def test_neither_agent_passes_a_bare_temperature_kwarg():
    """The direct `litellm.completion` / `acompletion` calls are easy to miss — they do
    not go through the cached builders, so a fix applied only to those would still
    leave a tenant on gpt-5 unable to generate a BRD or a design package."""
    import inspect
    import re

    from agents_orchestrator.design_architecture_agent.agents import architecture
    from agents_orchestrator.requirements_agent.agents import planning

    for mod in (planning, architecture):
        src = inspect.getsource(mod)
        bare = re.findall(r"^\s*temperature\s*=\s*[\d.]+\s*,", src, re.MULTILINE)
        assert not bare, f"{mod.__name__} still passes a literal temperature: {bare}"
