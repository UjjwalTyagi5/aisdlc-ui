"""Regression tests for fix (a): build_llm must call a direct-BYOK provider
(e.g. Anthropic with no configured gateway `base_url`) natively, instead of
forcing the call through the LiteLLM proxy (`LITELLM_BASE_URL`, default
localhost:4000). Only a resolved model that explicitly sets `base_url` (a
real gateway) should route through a proxy.
"""
from __future__ import annotations

import pytest

from agents_orchestrator.testing_agent.config.shared import build_llm
from shared.services.model_resolver import ResolvedModel, set_resolved_model


@pytest.fixture(autouse=True)
def _clear_resolved_model():
    set_resolved_model(None)
    yield
    set_resolved_model(None)


def test_direct_byok_model_has_no_proxy_api_base():
    """A resolved model with no base_url (a direct provider, e.g. Anthropic)
    must produce a client with api_base=None — never the LiteLLM proxy URL."""
    set_resolved_model(ResolvedModel(
        provider="anthropic",
        litellm_provider="anthropic",
        model="claude-sonnet-4-6",
        api_key="sk-test-key",
        base_url=None,
        alias="test-anthropic",
    ))

    llm = build_llm()

    assert llm.api_base in (None, "")
    assert llm.custom_llm_provider == "anthropic"
    assert llm.model == "claude-sonnet-4-6"


def test_gateway_model_with_explicit_base_url_is_preserved():
    """A resolved model that DOES configure an explicit gateway base_url must
    keep using it — only the "no base_url" case should skip the proxy."""
    set_resolved_model(ResolvedModel(
        provider="openai",
        litellm_provider="openai",
        model="gpt-4o",
        api_key="sk-test-key",
        base_url="https://my-org-gateway.example.com/v1",
        alias="test-gateway",
    ))

    llm = build_llm()

    assert llm.api_base == "https://my-org-gateway.example.com/v1"
