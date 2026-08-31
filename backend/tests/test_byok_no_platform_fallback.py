"""Guards the BYOK boundary: an enterprise run must NEVER reach for the platform key.

Four agents (code_review, security, deployment, documentation) used to wrap their
resolver call in a bare `except Exception`. The symbol they imported —
`resolve_chat_model` — did not exist, so the ImportError was swallowed and EVERY run,
for EVERY tenant, silently fell back to the platform's ANTHROPIC_API_KEY. That bypassed
per-project budgets, org/BU model grants, rpm/tpm caps, the no-training call kwargs, and
cost attribution.

These tests fail if that shape ever comes back.
"""
from __future__ import annotations

import importlib

import pytest

from shared.services import model_resolver as mr


# The four agents whose _resolve_model reads the run's resolved model.
AGENT_MODULES = [
    "agents_orchestrator.code_review_agent.agents.reviewer",
    "agents_orchestrator.security_agent.agents.scanner",
    "agents_orchestrator.deployment_agent.agents.deployer",
    "agents_orchestrator.documentation_agent.agents.compiler",
]


@pytest.fixture(autouse=True)
def _clear_resolved_model():
    """Every test starts with no model resolved for the run."""
    mr.set_resolved_model(None)
    yield
    mr.set_resolved_model(None)


def test_resolve_chat_model_exists():
    """The regression that started it all: the symbol the agents import must exist.

    An ImportError here is invisible at runtime because it looks like any other
    resolution failure — which is exactly how this went unnoticed.
    """
    assert hasattr(mr, "resolve_chat_model"), (
        "resolve_chat_model is missing from model_resolver — every agent that imports "
        "it will fall through to its fallback path"
    )
    assert callable(mr.resolve_chat_model)


def test_enterprise_fails_closed_with_no_resolved_model(monkeypatch):
    """No BYOK model + enterprise mode => raise. Never a platform-key client."""
    monkeypatch.setattr("config.env.AGENT_RUNTIME_MODE", "enterprise", raising=False)
    monkeypatch.setattr("config.env.ANTHROPIC_API_KEY", "sk-ant-platform-key", raising=False)

    with pytest.raises(mr.NoModelConfiguredError):
        mr.resolve_chat_model(model_id="claude-opus-4-5", tools=[])


def test_local_dev_may_fall_back(monkeypatch):
    """Local dev keeps working off .env — the fallback is allowed, just not in enterprise."""
    monkeypatch.setattr("config.env.AGENT_RUNTIME_MODE", "local", raising=False)
    monkeypatch.setattr("config.env.ANTHROPIC_API_KEY", "sk-ant-local", raising=False)

    llm = mr.resolve_chat_model(model_id="claude-opus-4-5", tools=[])
    assert llm is not None


def test_local_dev_without_a_key_still_fails_closed(monkeypatch):
    """No resolved model AND no .env key is an error, not a silent broken client."""
    monkeypatch.setattr("config.env.AGENT_RUNTIME_MODE", "local", raising=False)
    monkeypatch.setattr("config.env.ANTHROPIC_API_KEY", "", raising=False)

    with pytest.raises(mr.NoModelConfiguredError):
        mr.resolve_chat_model(model_id="claude-opus-4-5", tools=[])


def test_resolved_model_is_used_and_carries_base_url():
    """When the run resolved a model, its key and custom endpoint are what get used."""
    mr.set_resolved_model(mr.ResolvedModel(
        provider="anthropic",
        litellm_provider="anthropic",
        model="claude-opus-4-5",
        api_key="sk-ant-TENANT-key",
        base_url="https://gateway.example.internal/v1",
        alias="tenant:t1:p1",
        offering_id="off-1",
    ))

    llm = mr.resolve_chat_model(model_id="claude-opus-4-5", tools=[])

    # The tenant's key, not the platform's, and the provider's custom endpoint.
    assert llm.api_key == "sk-ant-TENANT-key"
    assert llm.api_base == "https://gateway.example.internal/v1"


@pytest.mark.parametrize("module_name", AGENT_MODULES)
def test_agent_has_no_bare_except_around_the_resolver(module_name):
    """The four agents must not re-introduce a catch-all around model resolution.

    Asserted on source rather than behaviour because the failure mode is precisely
    that the catch-all makes the bug invisible to behavioural tests.
    """
    import inspect

    mod = importlib.import_module(module_name)
    # Strip comments — the fix leaves a comment explaining the old shape, and the
    # point here is what the code DOES, not what it says about itself.
    src = "\n".join(
        line for line in inspect.getsource(mod._resolve_model).splitlines()
        if not line.lstrip().startswith("#")
    )

    assert "except Exception" not in src, (
        f"{module_name}._resolve_model catches Exception around model resolution — "
        "that is what silently routed every tenant run onto the platform key"
    )
    assert "ChatAnthropic" not in src, (
        f"{module_name}._resolve_model constructs a direct ChatAnthropic fallback — "
        "the platform-key decision belongs to resolve_chat_model, which gates on "
        "AGENT_RUNTIME_MODE"
    )
    assert "resolve_chat_model" in src


@pytest.mark.parametrize("module_name", AGENT_MODULES)
def test_agent_fails_closed_in_enterprise(module_name, monkeypatch):
    """End to end per agent: enterprise + no resolved model => the node raises."""
    monkeypatch.setattr("config.env.AGENT_RUNTIME_MODE", "enterprise", raising=False)
    monkeypatch.setattr("config.env.ANTHROPIC_API_KEY", "sk-ant-platform-key", raising=False)

    mod = importlib.import_module(module_name)
    state = {"messages": [], "tenant_id": "t1", "model_id": None, "offering_id": None}

    with pytest.raises(mr.NoModelConfiguredError):
        mod._resolve_model(state)
