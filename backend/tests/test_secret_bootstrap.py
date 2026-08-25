"""Where secrets come from, and what happens when the vault does not answer.

THE TEST THAT MATTERS MOST is `test_a_missing_vault_refuses_to_boot`. Everything else
here is naming and wiring; that one is the difference between a misconfigured production
API refusing to start and one that starts happily on `JWT_SECRET_KEY=change-me-in-
production`, signing tokens anybody can forge, with nothing in the logs that reads like
an incident.

No network. `hydrate_environment` is driven with a fake SecretClient injected through
sys.modules, so these run on a laptop with no Azure access and no `az login` — which is
the same property that lets `ENV=dev` be the default everywhere.
"""
from __future__ import annotations

import sys
import types

import pytest

from config import secret_bootstrap as sb


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts from a known environment, whatever the developer's .env holds."""
    for var in ("ENV", "AZURE_KEY_VAULT_URL", "KV_SECRET_PREFIX", *sb.PLATFORM_SECRETS):
        monkeypatch.delenv(var, raising=False)
    yield


def _install_fake_azure(monkeypatch, *, secrets: dict, auth_error=None, get_error=None):
    """Stand in for azure.identity + azure.keyvault.secrets.

    `hydrate_environment` imports them INSIDE the function precisely so this is possible
    without the real SDK being involved.
    """
    class _Secret:
        def __init__(self, value):
            self.value = value

    class _NotFound(Exception):
        pass
    _NotFound.__name__ = "ResourceNotFoundError"

    class _Client:
        def __init__(self, vault_url, credential):
            self.vault_url = vault_url

        def get_secret(self, name):
            if get_error is not None:
                raise get_error
            if name not in secrets:
                raise _NotFound(name)
            return _Secret(secrets[name])

        def close(self):
            pass

    class _Credential:
        def __init__(self, *a, **k):
            if auth_error is not None:
                raise auth_error

    identity = types.ModuleType("azure.identity")
    identity.DefaultAzureCredential = _Credential
    kv = types.ModuleType("azure.keyvault.secrets")
    kv.SecretClient = _Client
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", kv)


# ── naming ───────────────────────────────────────────────────────────────────

def test_env_var_becomes_a_legal_key_vault_name(monkeypatch):
    """Key Vault permits only alphanumerics and dashes — underscores would be rejected."""
    monkeypatch.setenv("ENV", "prod")
    assert sb.secret_name_for("JWT_SECRET_KEY") == "sdlc-prod-jwt-secret-key"
    assert sb.secret_name_for("SMTP_PASSWORD") == "sdlc-prod-smtp-password"
    assert "_" not in sb.secret_name_for("GITHUB_APP_PRIVATE_KEY")


def test_the_prefix_separates_environments_in_one_vault(monkeypatch):
    """Staging and production must not read each other's credentials from a shared vault."""
    monkeypatch.setenv("ENV", "staging")
    staging = sb.secret_name_for("JWT_SECRET_KEY")
    monkeypatch.setenv("ENV", "prod")
    assert staging != sb.secret_name_for("JWT_SECRET_KEY")


def test_the_prefix_can_be_overridden(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("KV_SECRET_PREFIX", "")
    assert sb.secret_name_for("JWT_SECRET_KEY") == "jwt-secret-key"


# ── the dev path ─────────────────────────────────────────────────────────────

def test_dev_is_the_default_when_env_is_unset():
    """A laptop or CI runner with no ENV must not take the Key Vault path and fail closed
    on a vault it was never meant to reach."""
    assert sb.current_env() == "dev"
    assert sb.is_dev()


def test_dev_never_contacts_azure(monkeypatch):
    """The whole reason ENV=dev exists. If this ever imports the SDK, the test suite and
    every offline laptop start needing Azure credentials."""
    def _explode(*a, **k):
        raise AssertionError("dev must not import or call the Azure SDK")

    identity = types.ModuleType("azure.identity")
    identity.DefaultAzureCredential = _explode
    monkeypatch.setitem(sys.modules, "azure.identity", identity)

    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://should-not-be-read.vault.azure.net")
    assert sb.hydrate_environment() == 0


def test_dev_leaves_dotenv_values_alone(monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("JWT_SECRET_KEY", "from-dotenv")
    sb.hydrate_environment()
    import os
    assert os.environ["JWT_SECRET_KEY"] == "from-dotenv"


# ── the vault path ───────────────────────────────────────────────────────────

def test_secrets_land_in_os_environ(monkeypatch):
    """The point of the whole module: env.py's os.environ.get lines need no change."""
    import os
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://v.vault.azure.net")
    _install_fake_azure(monkeypatch, secrets={
        "sdlc-prod-jwt-secret-key": "the-real-signing-key",
        "sdlc-prod-smtp-password": "the-real-smtp-password",
    })
    assert sb.hydrate_environment() == 2
    assert os.environ["JWT_SECRET_KEY"] == "the-real-signing-key"
    assert os.environ["SMTP_PASSWORD"] == "the-real-smtp-password"


def test_the_vault_overrides_a_stale_dotenv(monkeypatch):
    """In a deployed environment the vault is authoritative. A leftover .env on the host
    silently winning would defeat the entire point of moving the secret."""
    import os
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://v.vault.azure.net")
    monkeypatch.setenv("JWT_SECRET_KEY", "stale-value-from-a-file-on-the-host")
    _install_fake_azure(monkeypatch, secrets={"sdlc-prod-jwt-secret-key": "authoritative"})
    sb.hydrate_environment()
    assert os.environ["JWT_SECRET_KEY"] == "authoritative"


def test_a_secret_absent_from_the_vault_is_not_fatal(monkeypatch):
    """A connector nobody configured has no token. Requiring every entry would make every
    optional integration mandatory."""
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://v.vault.azure.net")
    _install_fake_azure(monkeypatch, secrets={"sdlc-prod-jwt-secret-key": "k"})
    assert sb.hydrate_environment() == 1  # the other ~29 are 404s, and that is fine


# ── failing closed ───────────────────────────────────────────────────────────

def test_a_missing_vault_url_refuses_to_boot(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(sb.SecretBootstrapError, match="AZURE_KEY_VAULT_URL"):
        sb.hydrate_environment()


def test_a_missing_vault_refuses_to_boot(monkeypatch):
    """THE ONE THAT MATTERS.

    A vault that cannot be reached must stop the process, not quietly leave the app
    running on whatever .env defaults happened to be present — which for JWT_SECRET_KEY
    is the literal string 'change-me-in-production'.
    """
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://v.vault.azure.net")
    _install_fake_azure(
        monkeypatch, secrets={},
        get_error=ConnectionError("vault unreachable"),
    )
    with pytest.raises(sb.SecretBootstrapError, match="Refusing to start"):
        sb.hydrate_environment()


def test_a_credential_failure_refuses_to_boot(monkeypatch):
    """Expired az login locally, or a managed identity with no role assignment."""
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://v.vault.azure.net")
    _install_fake_azure(monkeypatch, secrets={}, auth_error=RuntimeError("no credential"))
    with pytest.raises(sb.SecretBootstrapError, match="authenticate"):
        sb.hydrate_environment()


def test_a_partial_read_refuses_to_boot(monkeypatch):
    """Half a configuration is worse than none: which secrets loaded would be arbitrary,
    so the process must not run on whichever ones happened to succeed."""
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AZURE_KEY_VAULT_URL", "https://v.vault.azure.net")

    class _Flaky(Exception):
        pass

    calls = {"n": 0}
    real_install = _install_fake_azure

    class _Secret:
        def __init__(self, v):
            self.value = v

    class _Client:
        def __init__(self, vault_url, credential):
            pass

        def get_secret(self, name):
            calls["n"] += 1
            if calls["n"] > 3:
                raise _Flaky("transport blew up midway")
            return _Secret("ok")

        def close(self):
            pass

    identity = types.ModuleType("azure.identity")
    identity.DefaultAzureCredential = lambda *a, **k: object()
    kv = types.ModuleType("azure.keyvault.secrets")
    kv.SecretClient = _Client
    monkeypatch.setitem(sys.modules, "azure.identity", identity)
    monkeypatch.setitem(sys.modules, "azure.keyvault.secrets", kv)

    with pytest.raises(sb.SecretBootstrapError, match="Refusing to start"):
        sb.hydrate_environment()


# ── the catalogue itself ─────────────────────────────────────────────────────

def test_the_database_dsns_are_not_in_the_list():
    """They already resolve from Key Vault under KV_SECRET_POSTGRES_* in shared/db.py.
    Two mechanisms writing the same value diverge the moment one convention changes, and
    an app talking to a different database than its migrations is a bad afternoon."""
    for var in sb.PLATFORM_SECRETS:
        assert "POSTGRES" not in var, f"{var} is handled by shared/db.py, not here"


def test_no_plain_configuration_crept_into_the_secret_list():
    """A URL, a model name or a timeout is configuration. Putting it in a vault only
    makes it harder to change and tells a reader it is more sensitive than it is."""
    not_secret = {"JIRA_URL", "CONFLUENCE_URL", "SONARQUBE_URL", "ADO_ORG_URL",
                  "SMTP_HOST", "SMTP_PORT", "PUBLIC_APP_URL", "LANGFUSE_HOST",
                  "ANTHROPIC_MODEL", "AZURE_OPENAI_ENDPOINT"}
    assert not (set(sb.PLATFORM_SECRETS) & not_secret)


def test_every_name_is_a_legal_key_vault_secret_name(monkeypatch):
    """Key Vault rejects anything outside ^[0-9a-zA-Z-]+$, at request time rather than
    at deploy time — so a bad name here is a 400 during startup in production."""
    import re
    monkeypatch.setenv("ENV", "prod")
    for var in sb.PLATFORM_SECRETS:
        name = sb.secret_name_for(var)
        assert re.fullmatch(r"[0-9a-zA-Z-]+", name), name
        assert len(name) <= 127, name
