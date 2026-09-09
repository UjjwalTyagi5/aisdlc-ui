"""Redaction is a compliance control (PRD 34.8), so it gets its own tests.

Traces carry prompts, tool arguments and model output into a Langfuse instance shared
with other products and retained on its own schedule. "Secrets are redacted at write
time" is a promise the product makes; these assertions are what make it checkable.

The corpus below mirrors the credential SHAPES this platform actually handles — its own
Langfuse key pair, model-provider keys, Postgres DSNs, PATs — because a redactor is only
as good as the patterns it was aimed at.

EVERY VALUE HERE IS SYNTHETIC, AND MUST STAY THAT WAY. Pasting a real key into a test to
prove it gets redacted commits that key to git in the same breath, where redaction cannot
reach it. Match the shape; invent the value.
"""
from __future__ import annotations

import pytest

from shared.observability.redaction import REDACTED, mask_sensitive, redact_text

SECRETS = [
    pytest.param("sk-lf-00000000-1111-2222-3333-444444444444", id="langfuse-secret-key"),
    pytest.param("pk-lf-55555555-6666-7777-8888-999999999999", id="langfuse-public-key"),
    pytest.param("sk-ant-api03-" + "A" * 40, id="anthropic"),
    pytest.param("sk-" + "b" * 32, id="openai-style"),
    pytest.param("AIza" + "C" * 32, id="google"),
    pytest.param("ghp_" + "d" * 36, id="github-pat"),
    pytest.param("github_pat_" + "e" * 40, id="github-fine-grained"),
    pytest.param("xoxb-1234567890-abcdefghijkl", id="slack"),
    pytest.param("AKIAIOSFODNN7EXAMPLE", id="aws-access-key-id"),
    pytest.param("pcsk_" + "f" * 40, id="pinecone"),
    pytest.param(
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g",
        id="jwt",
    ),
]


@pytest.mark.parametrize("secret", SECRETS)
def test_known_credential_shapes_never_survive(secret):
    """Each shape must be gone from the output, wherever it sits in the text."""
    masked = redact_text(f"here is the value {secret} in a sentence")
    assert secret not in masked
    assert REDACTED in masked


def test_private_key_block_is_removed_whole():
    """Not just the header — the key material is the part that matters."""
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAx7Qm2v9pS0mVerySecretKeyMaterialHere\n"
        "-----END RSA PRIVATE KEY-----"
    )
    masked = redact_text(f"key follows:\n{pem}\nend")
    assert "SecretKeyMaterialHere" not in masked
    assert "BEGIN RSA PRIVATE KEY" not in masked


def test_connection_string_loses_the_password_but_keeps_the_service():
    """Which database it was is diagnostic; the password is the only secret part."""
    dsn = "postgresql://pgadmin:hunter2SuperSecret@db.example.postgres.database.azure.com:5432/langfuse"
    masked = redact_text(dsn)
    assert "hunter2SuperSecret" not in masked
    assert "postgresql://pgadmin:" in masked
    assert "db.example.postgres.database.azure.com" in masked


def test_authorization_header_keeps_its_scheme():
    masked = redact_text("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345")
    assert "abcdefghijklmnopqrstuvwxyz012345" not in masked
    assert "Bearer" in masked


def test_value_is_redacted_by_key_name_even_when_shapeless():
    """The half that catches high-entropy secrets with no recognisable prefix.

    An Azure client secret is ~40 random characters and matches no pattern; it is only
    ever caught because of the key it sits under.
    """
    out = mask_sensitive(
        data={"azure_client_secret": "Xy7Q~Fake0ClientSecret0ForTests0Only~aB", "model": "sonnet"}
    )
    assert out["azure_client_secret"] == REDACTED
    assert out["model"] == "sonnet"  # non-secret fields are untouched


def test_masking_reaches_into_nested_structures():
    """Tool arguments and model output arrive nested; a top-level-only scan would miss them."""
    out = mask_sensitive(
        data={
            "messages": [
                {"role": "user", "content": "deploy with sk-lf-abcdefghij1234567890"},
                {"role": "assistant", "content": {"tool": {"api_key": "zzzzzzzzzzzz"}}},
            ]
        }
    )
    assert "sk-lf-abcdefghij1234567890" not in str(out)
    assert out["messages"][1]["content"]["tool"]["api_key"] == REDACTED


def test_ordinary_content_passes_through_unchanged():
    """Redaction must not eat the trace. An over-broad mask makes traces useless."""
    text = "Refactor the payments module and add a test for the retry path."
    assert redact_text(text) == text

    data = {"prompt": text, "tokens": 42, "ok": True, "nothing": None}
    assert mask_sensitive(data=data) == data


@pytest.mark.parametrize(
    "key",
    ["tokens", "input_tokens", "outputTokens", "totalTokens", "token_count", "max_tokens"],
)
def test_token_counts_are_not_mistaken_for_credentials(key):
    """The over-redaction bug, pinned.

    "token" is a substring of "tokens", so substring key matching blanked the usage
    figures on every generation span — the cost and token data the Traces and Cost pages
    exist to show, destroyed by the control meant to protect them. Key matching is on
    whole word parts for exactly this reason.
    """
    out = mask_sensitive(data={key: 1234})
    assert out[key] == 1234


@pytest.mark.parametrize(
    "key", ["access_token", "refreshToken", "api_key", "x-api-key", "APIKey", "client_secret"]
)
def test_credential_shaped_keys_are_still_caught(key):
    """The other side of that fix: narrowing the match must not open a hole."""
    out = mask_sensitive(data={key: "aaaaaaaaaaaaaaaa"})
    assert out[key] == REDACTED


def test_deeply_nested_payload_is_bounded_not_fatal():
    """A pathological structure must be truncated, never a stack overflow mid-trace."""
    deep: dict = {}
    node = deep
    for _ in range(200):
        node["next"] = {}
        node = node["next"]
    node["secret_token"] = "abcdefghijkl"

    out = mask_sensitive(data=deep)
    assert isinstance(out, dict)  # returned something, did not raise


def test_masking_fails_closed(monkeypatch):
    """If the walker raises, the payload must NOT pass through unredacted.

    A redactor that degrades to "send everything" on an unexpected input is worse than
    no redactor, because the promise is still being made.
    """
    import shared.observability.redaction as red

    def _boom(*_a, **_k):
        raise RuntimeError("unexpected shape")

    monkeypatch.setattr(red, "_walk", _boom)
    out = red.mask_sensitive(data={"password": "hunter2"})
    assert out == "[REDACTION FAILED]"
    assert "hunter2" not in str(out)


def test_client_attaches_the_mask(monkeypatch):
    """The hook must actually be wired, or every test above is theatre."""
    import config.env as env
    import shared.observability.client as client_mod

    monkeypatch.setattr(env, "ENABLE_LANGFUSE", True, raising=False)
    monkeypatch.setattr(client_mod, "ENABLE_LANGFUSE", True)
    monkeypatch.setattr(client_mod, "LANGFUSE_HOST", "https://langfuse.invalid")
    monkeypatch.setattr(client_mod, "LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setattr(client_mod, "LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setattr(client_mod, "_client", None)
    monkeypatch.setattr(client_mod, "_init_attempted", False)

    captured = {}

    class _FakeLangfuse:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import langfuse

    monkeypatch.setattr(langfuse, "Langfuse", _FakeLangfuse)

    client_mod.get_langfuse_client()

    from shared.observability.redaction import mask_sensitive as expected

    assert captured.get("mask") is expected
