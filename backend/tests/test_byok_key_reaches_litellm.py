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
            if src[max(0, m.start() - 6):m.start()] == "class ":
                continue  # shared/services/chat_litellm.py defines the class; it builds nothing
            # Look BEHIND as well as ahead: several sites build a `params` dict and
            # then splat it (`ChatLiteLLM(**params)`), so the credential kwargs sit
            # above the construction rather than inside it.
            window = src[max(0, m.start() - 1500):m.start() + 900]
            if "api_key" not in window:
                continue  # not a credentialed construction
            if "litellm_key_kwargs" not in window:
                offenders.append(f"{path.relative_to(root)}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, "ChatLiteLLM built with a key but no named-field override: " + ", ".join(offenders)


# ── one call's credentials must not steer every other call ─────────────────────
# Upstream `_client_params` runs on EVERY call and writes `api_base`, `api_key`, the
# named key fields, `organization` and `extra_headers` onto `self.client` — the litellm
# MODULE. Any later litellm call that does not pass its own value falls back to them.
#
# Live consequence (2026-09-15): a requirements run on azure/gpt-5-mini left
# `litellm.api_base` on the business unit's Azure AI Foundry endpoint. Every "Test" of a
# new Anthropic key after it — blank API base, valid sk-ant key — was sent THERE, came
# back 404 "Resource not found", and read as "Reached an endpoint, but it has no such
# model". The Anthropic key itself went to the Azure endpoint.
_UNIT_A_BASE = "https://unit-a.services.ai.azure.com"
_LITELLM_GLOBALS = (
    "api_base", "api_key", "organization", "extra_headers",
    "openai_key", "azure_key", "anthropic_key", "replicate_key", "cohere_key", "openrouter_key",
)


@pytest.fixture
def litellm_globals():
    """Snapshot litellm's module-level credentials and put them back afterwards, so a
    client that still leaks cannot poison the tests that run after it."""
    import litellm

    before = {name: getattr(litellm, name, None) for name in _LITELLM_GLOBALS}
    yield before
    for name, value in before.items():
        setattr(litellm, name, value)


def _unit_a_client(**overrides):
    from shared.services.chat_litellm import ChatLiteLLM

    params = dict(
        model="gpt-5-mini", custom_llm_provider="azure", api_base=_UNIT_A_BASE,
        api_key="sk-unit-a", **litellm_key_kwargs("azure", "sk-unit-a"),
    )
    params.update(overrides)
    return ChatLiteLLM(**params)


def test_a_call_leaves_litellm_global_credentials_untouched(litellm_globals):
    import litellm

    _ = _unit_a_client(organization="org-unit-a")._client_params  # built on every call

    assert {name: getattr(litellm, name, None) for name in _LITELLM_GLOBALS} == litellm_globals


def test_each_call_carries_its_own_base_and_key(litellm_globals):
    params = _unit_a_client()._client_params

    assert params.get("api_base") == _UNIT_A_BASE
    assert params.get("api_key") == "sk-unit-a"


def test_the_byok_key_beats_a_platform_key_in_the_environment(litellm_globals, monkeypatch):
    """ChatLiteLLM defaults `anthropic_api_key` from ANTHROPIC_API_KEY. The key this
    client was built with is the one the call must send, override kwargs or not."""
    from shared.services.chat_litellm import ChatLiteLLM

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-platform")
    llm = ChatLiteLLM(model="claude-haiku-4-5", custom_llm_provider="anthropic", api_key="sk-byok")

    assert llm._client_params.get("api_key") == "sk-byok"


def test_the_organization_travels_with_the_call(litellm_globals):
    params = _unit_a_client(organization="org-unit-a")._client_params

    assert params.get("organization") == "org-unit-a"


def test_no_code_builds_the_upstream_chatlitellm():
    """One site still importing langchain_litellm's own class is enough to redirect every
    other call in the process, so every construction goes through the shared one."""
    root = pathlib.Path(__file__).resolve().parents[1]
    allowed = root / "shared" / "services" / "chat_litellm.py"
    upstream = re.compile(
        r"from\s+langchain_litellm(?:\.[\w.]+)?\s+import\s+[^\n]*\bChatLiteLLM\b"
        r"|langchain_litellm\.ChatLiteLLM\b"
    )
    offenders = []
    for top in root.iterdir():
        if not top.is_dir() or top.name in {".venv", "files", "tests"} or top.name.startswith("."):
            continue
        for path in top.rglob("*.py"):
            if path == allowed or "tests" in path.parts or path.name.startswith("test_"):
                continue
            src = path.read_text(encoding="utf-8", errors="replace")
            for m in upstream.finditer(src):
                offenders.append(f"{path.relative_to(root)}:{src[:m.start()].count(chr(10)) + 1}")
    for path in root.glob("*.py"):
        src = path.read_text(encoding="utf-8", errors="replace")
        for m in upstream.finditer(src):
            offenders.append(f"{path.relative_to(root)}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, "Build ChatLiteLLM from shared.services.chat_litellm: " + ", ".join(offenders)


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
