"""The BYOK key must be the key litellm actually uses.

ChatLiteLLM keeps a SEPARATE field per provider (`anthropic_api_key`, `azure_api_key`,
...), each defaulted from the matching environment variable. `_client_params` assigns
the generic `api_key` first and THEN writes whichever named fields are truthy onto the
litellm module — so the environment value wins, and because `client` is the litellm
module itself the write is process-global.

Live consequence (2026-09-04): a tenant's valid Anthropic key was ignored in favour of
the platform's stale ANTHROPIC_API_KEY, and every agent reported
"authentication_error: API key is invalid" about a key that worked when called
directly. Had the platform key been VALID this would have been silent instead: every
tenant served by the platform's credential, billed to the platform account.
"""
import pathlib
import re

import pytest

from shared.services.model_resolver import litellm_key_kwargs, temperature_kwargs


@pytest.mark.parametrize(
    "provider,expected_field",
    [
        ("anthropic", "anthropic_api_key"),
        ("azure", "azure_api_key"),
        ("openai", "openai_api_key"),
        ("cohere_chat", "cohere_api_key"),
        ("openrouter", "openrouter_api_key"),
    ],
)
def test_the_provider_specific_field_carries_the_byok_key(provider, expected_field):
    assert litellm_key_kwargs(provider, "sk-byok") == {expected_field: "sk-byok"}


def test_a_provider_with_no_named_field_uses_the_generic_api_key():
    """litellm reads `api_key` for these, which is already correct — adding a field
    that ChatLiteLLM does not declare would raise."""
    assert litellm_key_kwargs("mistral", "sk-byok") == {}
    assert litellm_key_kwargs("bedrock", "sk-byok") == {}


def test_no_key_means_nothing_to_override():
    assert litellm_key_kwargs("anthropic", None) == {}
    assert litellm_key_kwargs("anthropic", "") == {}


def test_the_named_fields_all_exist_on_chatlitellm():
    """A field ChatLiteLLM does not declare would be a construction error, and this
    mapping is only useful if it names the real ones."""
    from langchain_litellm import ChatLiteLLM

    from shared.services.model_resolver import _LITELLM_NAMED_KEY_FIELD

    for field in set(_LITELLM_NAMED_KEY_FIELD.values()):
        assert field in ChatLiteLLM.model_fields, field


def test_every_byok_client_passes_the_named_key():
    """Any site building a ChatLiteLLM from a resolved BYOK key must include it —
    one missed site is one agent silently using the platform's environment key."""
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in list((root / "agents_orchestrator").rglob("*.py")) + \
                list((root / "shared").rglob("*.py")):
        if "test" in path.parts or path.name.startswith("test_"):
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"ChatLiteLLM\(", src):
            # Look BEHIND as well as ahead: several sites build a `params` dict and
            # then splat it (`ChatLiteLLM(**params)`), so the credential kwargs sit
            # above the construction rather than inside it.
            window = src[max(0, m.start() - 1500):m.start() + 900]
            if "api_key" not in window:
                continue  # not a credentialed construction
            if "litellm_key_kwargs" not in window:
                offenders.append(f"{path.relative_to(root)}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, "ChatLiteLLM built with a key but no named-field override: " + ", ".join(offenders)


# ── temperature: a measured list, because provider metadata is wrong ──────────
@pytest.mark.parametrize("model", ["claude-opus-4-7", "claude-opus-4-8", "claude-sonnet-5"])
def test_models_measured_to_reject_temperature_get_none(model):
    """Verified live on 2026-09-04 with a real 16-token call each."""
    assert temperature_kwargs(model, 0.1) == {}


@pytest.mark.parametrize("model", ["claude-opus-4-5", "claude-haiku-4-5", "claude-sonnet-4-5"])
def test_models_measured_to_accept_temperature_keep_it(model):
    assert temperature_kwargs(model, 0.1) == {"temperature": 0.1}


def test_the_gpt5_family_is_still_covered():
    assert temperature_kwargs("azure/gpt-5-mini", 0.1) == {}
    assert temperature_kwargs("gpt-5-codex", 0.1) == {}


def test_an_ordinary_model_keeps_its_temperature():
    assert temperature_kwargs("gpt-4o", 0.1) == {"temperature": 0.1}
    assert temperature_kwargs("claude-3-opus-20240229", 0.1) == {"temperature": 0.1}


def test_an_unknown_newer_model_errs_toward_dropping():
    """Dropping temperature costs determinism; keeping it on a model that refuses it
    fails the request. Newer models land on the safe side."""
    assert temperature_kwargs("claude-opus-4-9", 0.1) == {}
    assert temperature_kwargs("claude-sonnet-6", 0.1) == {}
    assert temperature_kwargs("claude-haiku-5", 0.1) == {}
