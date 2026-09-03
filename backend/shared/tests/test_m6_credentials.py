"""Connector credential hygiene — REQ-M6-14.

A connector resolves credentials EPHEMERALLY inside auth_adapter(), tenant-scoped,
and keeps nothing on the instance. Two things this file pins:

1. auth_adapter() reaches the tenant secret store / tenant-scoped Key Vault.
2. Constructing a connector puts no credential on `self`, so a long-lived connector
   object cannot leak one through a repr, a pickle, or a stray log line.

WHAT THIS FILE NO LONGER ASSERTS. It used to contain
`test_jira_auth_adapter_env_fallback_when_kv_absent`, which required auth_adapter to
fall back to a `JIRA_API_TOKEN` environment variable when Key Vault held nothing.
That fallback is deliberately gone: it is platform-wide, so a tenant that never
connected Jira transacted with the PLATFORM's token — another tenant's data, billed
and audited as theirs. The absence is enforced structurally in
tests/test_connector_platform_fallback.py; the correct behaviour when a tenant has no
credential is now "not connected", which is what test_..._resolves_to_nothing covers.

Three further tests here were `assert True or ...` placeholders — they could not fail
and are removed rather than left looking like coverage.
"""
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.connectors.github_issues import GitHubIssuesConnector
from config.connectors.jira import JiraConnector
from config.connectors.slack import SlackConnector

# Attribute names that would mean a raw credential is being held on the instance.
CREDENTIAL_ATTRS = {
    "_pat", "_token", "_secret", "_api_key", "_api_token", "_password",
    "_private_key", "_private_key_pem", "_pem", "_bot_token", "_bot_token_hint",
    "_client_secret", "_access_token",
}


@pytest.mark.unit
async def test_jira_resolves_only_the_acting_users_own_credential():
    """Jira no longer reads a tenant-wide credential at all.

    That rung was removed (config.connectors.base.PERSONAL_CREDENTIAL_KINDS): it made
    the connector work for a project whose members never configured it, and attributed
    the work to whoever minted the shared token. The lookup that remains is still
    tenant-scoped, which is the property this file has always guarded.
    """
    connector = JiraConnector("https://test.atlassian.net")
    seen: list[tuple] = []

    # Patched on the CLASS, so it is an unbound function and receives self.
    async def fake_override(_self, tenant_id, target_id):
        seen.append((tenant_id, target_id))
        return None

    with patch.object(JiraConnector, "_resolve_credential_override", fake_override), \
         patch("shared.keyvault.load_secret", new_callable=AsyncMock,
               return_value="kv-api-token") as kv:
        auth = await connector.auth_adapter(tenant_id="test-tenant")

    assert seen == [("test-tenant", "jira")], f"lookup not tenant-scoped: {seen}"
    assert not kv.called, "the tenant-wide rung must not be consulted"
    assert auth["token"] == "", "no personal credential must mean NOT connected"


@pytest.mark.unit
async def test_slack_auth_adapter_resolves_to_nothing_without_a_tenant_credential():
    """No stored token → None, not a platform-wide token. Fails as 'not connected'."""
    connector = SlackConnector(tenant_id="test-tenant")
    with patch("shared.keyvault.load_secret", new_callable=AsyncMock, return_value=None):
        with patch.dict(
            "os.environ",
            {"SLACK_BOT_TOKEN": "xoxb-platform-token", "JIRA_API_TOKEN": "platform-token"},
        ):
            auth = await connector.auth_adapter(tenant_id="test-tenant")
    assert not auth["bot_token"], (
        f"resolved {auth['bot_token']!r} for a tenant with no credential — an env var "
        f"or another platform-wide rung has been reintroduced"
    )


@pytest.mark.unit
async def test_auth_adapter_requires_a_tenant():
    """A connector with no tenant context cannot authenticate at all (REQ-M7-01)."""
    with pytest.raises(ValueError):
        await SlackConnector().auth_adapter()


@pytest.mark.unit
@pytest.mark.parametrize(
    "connector",
    [
        JiraConnector("https://test.atlassian.net", tenant_id="test-tenant"),
        SlackConnector(tenant_id="test-tenant"),
        GitHubIssuesConnector(app_id="1234", installation_id="5678", tenant_id="test-tenant"),
    ],
    ids=["jira", "slack", "github_issues"],
)
def test_no_credential_stored_on_the_instance(connector):
    """Constructing a connector must not park a credential on `self` (REQ-M6-14).

    app_id / installation_id are exempt by name: a GitHub App id is platform identity
    and an installation id is a tenant-scoped identifier. Neither authenticates on
    its own — the private key does, and that is resolved per call.
    """
    stored = CREDENTIAL_ATTRS & set(connector.__dict__)
    assert not stored, (
        f"{type(connector).__name__} holds {sorted(stored)} on the instance; "
        f"credentials must be resolved inside auth_adapter() and discarded"
    )
