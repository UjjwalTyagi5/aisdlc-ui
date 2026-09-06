"""A rotated key must produce a new client, not reuse the one built with the old.

Every agent caches its ChatLiteLLM by alias, and the alias — `tenant:<tid>:<provider_id>`
— does not change when the key behind that provider is rotated or corrected. So a client
built with a bad key was handed back for the life of the process: fixing the key in the
UI could not fix the agent, and the symptom was a live "authentication_error: API key is
invalid" from a credential that worked when called directly.
"""
from shared.services.model_resolver import credential_fingerprint


def test_the_same_credential_is_the_same_entry():
    """Otherwise the cache never hits and every call rebuilds the client."""
    a = credential_fingerprint("sk-ant-aaa", "https://api.anthropic.com")
    b = credential_fingerprint("sk-ant-aaa", "https://api.anthropic.com")

    assert a == b


def test_a_rotated_key_is_a_different_entry():
    old = credential_fingerprint("sk-ant-old", "https://api.anthropic.com")
    new = credential_fingerprint("sk-ant-new", "https://api.anthropic.com")

    assert old != new


def test_a_changed_endpoint_is_a_different_entry():
    """An endpoint change reaches a different service with the same key."""
    a = credential_fingerprint("sk-ant-aaa", "https://api.anthropic.com")
    b = credential_fingerprint("sk-ant-aaa", "https://gateway.internal/v1")

    assert a != b


def test_a_missing_key_or_base_is_handled():
    assert credential_fingerprint(None, None)
    assert credential_fingerprint("", None) != credential_fingerprint("k", None)


def test_the_fingerprint_never_contains_the_secret():
    """Cache keys surface in reprs, debuggers and crash dumps."""
    secret = "sk-ant-api03-very-secret-value"

    fp = credential_fingerprint(secret, None)

    assert secret not in fp
    assert "sk-ant" not in fp
    assert len(fp) == 16


def test_key_and_base_cannot_be_confused_for_one_another():
    """A naive concatenation would make ("ab", "c") and ("a", "bc") collide."""
    assert credential_fingerprint("ab", "c") != credential_fingerprint("a", "bc")


def test_every_agent_that_caches_a_client_keys_it_on_the_credential():
    """The four agents that build and cache a ChatLiteLLM all had the same bug."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "agents_orchestrator"
    builders = [
        root / "development_agent" / "agents" / "dev_agent.py",
        root / "design_architecture_agent" / "agents" / "architecture.py",
        root / "pm_agent" / "agents" / "schedule.py",
        root / "requirements_agent" / "agents" / "planning.py",
    ]
    for path in builders:
        src = path.read_text(encoding="utf-8")
        for line in re.findall(r"^\s*cache_key = \(.*$", src, re.M):
            assert "credential_fingerprint" in line, f"{path.name}: {line.strip()}"
