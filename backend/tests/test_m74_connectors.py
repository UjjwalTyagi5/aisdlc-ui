"""Connector onboarding tests — tenant-scoped credentials only.

WHAT THIS FILE USED TO BE. Most of it covered the OAuth 3LO onboarding flow: CSRF
state mint/verify, build_oauth_start_url for five providers, the Jira/GitHub/Slack
callbacks that exchanged a code for tokens, the connector:manage gate on /install and
/oauth/callback, and JiraConnector's OAuth Bearer mode.

That whole flow is gone. Every code-for-token exchange it tested POSTed the PLATFORM's
client_id and client_secret to the provider, which is precisely what made those
credentials process configuration held on every tenant's behalf. Each provider is
reachable with a credential the tenant pastes itself and which is stored only in that
tenant's secret store — so what survives here is the tenant-scoped Basic-Auth path and
the store_secret namespacing that keeps it per-tenant.

Cross-tenant isolation is pinned structurally in tests/test_connector_platform_fallback.py.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.asyncio


# ── store_secret: the namespacing that makes a secret a TENANT's ──────────────


class TestStoreSecret:
    """store_secret writes {tenant_id}-{name} to KV; returns bool; never raises."""

    async def test_store_secret_with_tenant_id_uses_namespaced_name(self):
        from shared import keyvault

        with patch.object(keyvault, "AZURE_KEY_VAULT_URL", "https://vault.test"):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.set_secret = AsyncMock(return_value=MagicMock())
            mock_cred = AsyncMock()
            mock_cred.close = AsyncMock()

            with (
                patch("shared.keyvault.SecretClient", return_value=mock_client),
                patch("shared.keyvault.get_azure_credential", return_value=mock_cred),
            ):
                result = await keyvault.store_secret("jira-api-token", "my-token", tenant_id="t1")

        assert result is True
        mock_client.set_secret.assert_called_once()
        call_args = mock_client.set_secret.call_args[0]
        # The tenant prefix IS the isolation — without it every tenant writes and
        # reads the same vault entry.
        assert call_args[0] == "t1-jira-api-token"
        assert call_args[1] == "my-token"

    async def test_store_secret_without_vault_url_returns_false(self):
        from shared import keyvault

        with patch.object(keyvault, "AZURE_KEY_VAULT_URL", ""):
            result = await keyvault.store_secret("test-secret", "value", tenant_id="t1")
        assert result is False

    async def test_store_secret_exception_returns_false_never_raises(self):
        from shared import keyvault

        with patch.object(keyvault, "AZURE_KEY_VAULT_URL", "https://vault.test"):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.set_secret = AsyncMock(side_effect=Exception("vault error"))
            mock_cred = AsyncMock()
            mock_cred.close = AsyncMock()

            with (
                patch("shared.keyvault.SecretClient", return_value=mock_client),
                patch("shared.keyvault.get_azure_credential", return_value=mock_cred),
            ):
                result = await keyvault.store_secret("sec", "val", tenant_id="t1")

        assert result is False  # never raises


# ── JiraConnector: Basic Auth is now the only mode ───────────────────────────


class TestJiraAuthAdapter:
    """auth_adapter resolves email + API token, tenant-scoped, and nothing else."""

    async def test_auth_adapter_returns_basic_mode(self):
        from config.connectors.jira import JiraConnector
        import shared.keyvault as _kv

        connector = JiraConnector("https://example.atlassian.net")

        async def fake_load(name, tenant_id=None):
            return {
                "jira-url": "https://example.atlassian.net",
                "jira-email": "user@example.com",
                "jira-api-token": "basic-api-token",
            }.get(name)

        with patch.object(_kv, "load_secret", side_effect=fake_load):
            auth = await connector.auth_adapter(tenant_id="t1")

        assert auth["mode"] == "basic"
        assert auth["email"] == "user@example.com"
        assert auth["token"] == "basic-api-token"

    async def test_an_oauth_token_no_longer_changes_the_mode(self):
        """jira-access-token used to switch the adapter to Bearer against
        api.atlassian.com/ex/jira/{cloud_id}. Only the deleted OAuth callback ever
        wrote it, so a leftover value must not resurrect a second auth path."""
        from config.connectors.jira import JiraConnector
        import shared.keyvault as _kv

        connector = JiraConnector("https://example.atlassian.net")

        async def fake_load(name, tenant_id=None):
            return {
                "jira-access-token": "stale-oauth-token",
                "jira-cloud-id": "cloud-123",
                "jira-url": "https://example.atlassian.net",
                "jira-email": "user@example.com",
                "jira-api-token": "basic-api-token",
            }.get(name)

        with patch.object(_kv, "load_secret", side_effect=fake_load):
            auth = await connector.auth_adapter(tenant_id="t1")

        assert auth["mode"] == "basic"
        assert "bearer" not in auth
        assert "api.atlassian.com" not in auth["jira_url"]

    async def test_auth_adapter_raises_value_error_without_tenant_id(self):
        from config.connectors.jira import JiraConnector

        connector = JiraConnector("https://example.atlassian.net")
        with pytest.raises(ValueError, match="tenant_id"):
            await connector.auth_adapter(tenant_id="")

    async def test_jira_request_sends_basic_auth(self):
        from config.connectors.jira import JiraConnector
        import shared.keyvault as _kv

        connector = JiraConnector("https://example.atlassian.net")

        async def fake_load(name, tenant_id=None):
            return {
                "jira-url": "https://example.atlassian.net",
                "jira-email": "me@example.com",
                "jira-api-token": "api-tok",
            }.get(name)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"{}"
        mock_resp.json = MagicMock(return_value={})
        mock_resp.raise_for_status = MagicMock()

        captured_kwargs = {}

        async def mock_request(method, url, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = mock_request

        with (
            patch.object(_kv, "load_secret", side_effect=fake_load),
            patch("config.connectors.jira.httpx.AsyncClient", return_value=mock_client),
        ):
            await connector._jira_request("GET", "/rest/api/3/project", tenant_id="t1")

        assert captured_kwargs["auth"] == ("me@example.com", "api-tok")
        # The Bearer branch is gone — no Authorization header is set by hand.
        assert "Authorization" not in (captured_kwargs.get("headers") or {})

    async def test_the_api_token_is_never_logged(self):
        """T-7.4-22, restated for the credential that actually exists now."""
        import io
        import logging
        from config.connectors.jira import JiraConnector
        import shared.keyvault as _kv

        connector = JiraConnector("https://example.atlassian.net")

        async def fake_load(name, tenant_id=None):
            return {
                "jira-url": "https://example.atlassian.net",
                "jira-email": "me@example.com",
                "jira-api-token": "SUPER-SECRET-TOKEN-12345",
            }.get(name)

        with patch.object(_kv, "load_secret", side_effect=fake_load):
            handler = logging.StreamHandler(stream := io.StringIO())
            logging.getLogger().addHandler(handler)
            try:
                await connector.auth_adapter(tenant_id="t1")
            finally:
                logging.getLogger().removeHandler(handler)

        assert "SUPER-SECRET-TOKEN-12345" not in stream.getvalue()
