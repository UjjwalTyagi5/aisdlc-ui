"""Regression tests for the three M7 live security bugs.

REQ-M7-01: load_secret tenant_id scoping + connector auth_adapter tenant_id requirement
REQ-M7-02: JWT _decode_rs256 audience validation (verify_aud:False removed)
REQ-M7-03: connector_factory rejects empty tenant_id

All tests are DB-independent and require no live Azure / OIDC services.
Mock-based only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_secret_client_class(return_value: str | None):
    """Return a mock SecretClient class whose get_secret() returns return_value."""
    mock_secret = MagicMock()
    mock_secret.value = return_value

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=MagicMock(
        get_secret=AsyncMock(return_value=mock_secret)
    ))
    async_cm.__aexit__ = AsyncMock(return_value=False)

    mock_class = MagicMock(return_value=async_cm)
    return mock_class


# ═══════════════════════════════════════════════════════════════════════════
# REQ-M7-02: JWT _decode_rs256 audience validation
# ═══════════════════════════════════════════════════════════════════════════

class TestJwtAudienceValidation:
    """verify_aud:False is gone; AUTH0_AUDIENCE drives audience enforcement."""

    def test_verify_aud_false_not_in_source(self):
        """verify_aud:False must not appear in jwt.py — structural check."""
        jwt_path = Path(__file__).resolve().parents[1] / "config" / "auth" / "jwt.py"
        source = jwt_path.read_text(encoding="utf-8")
        # Strip comment lines before checking
        non_comment_lines = [
            line for line in source.splitlines()
            if not line.lstrip().startswith("#")
        ]
        non_comment_source = "\n".join(non_comment_lines)
        assert "verify_aud" not in non_comment_source, (
            "verify_aud must not appear in jwt.py (REQ-M7-02). "
            "Remove options={'verify_aud': False} from _decode_rs256."
        )

    def test_auth0_audience_importable_from_env(self):
        """AUTH0_AUDIENCE must be importable from config.env and default to ''."""
        from config.env import AUTH0_AUDIENCE
        assert isinstance(AUTH0_AUDIENCE, str), "AUTH0_AUDIENCE must be a str"

    def test_enterprise_guard_raises_when_audience_empty(self):
        """The AUTH0_AUDIENCE guard now lives in the startup check, not in _decode_rs256.

        plan-01 had an in-function RuntimeError guard in _decode_rs256; plan-02 (D-03a)
        relocated it to _check_oidc_audience_guard (called at lifespan startup) to mirror
        the BYPASSRLS guard pattern.  _decode_rs256 no longer raises RuntimeError for an
        empty audience — it proceeds to decode and raises a JWT-level error instead.  The
        startup guard is tested in test_m7_oidc.py::test_startup_guard_fails_without_audience.
        """
        import config.auth.jwt as jwt_mod
        import process_api
        # _decode_rs256 with empty AUTH0_AUDIENCE proceeds to decode, not RuntimeError.
        mock_jwks = MagicMock()
        mock_jwks.get_signing_key_from_jwt.side_effect = Exception("JWKS error")
        with patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://login.example.com/v2.0"), \
             patch.object(jwt_mod, "AUTH0_AUDIENCE", ""), \
             patch.object(jwt_mod, "_jwks_client", mock_jwks):
            with pytest.raises(Exception, match="JWKS error"):
                jwt_mod._decode_rs256("fake.token.here")
        # The startup guard (relocated from _decode_rs256) still raises RuntimeError.
        with patch.object(process_api, "ENABLE_OIDC", True), \
             patch.object(process_api, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(process_api, "OIDC_ISSUER_URL", "https://login.example.com/v2.0"), \
             patch.object(process_api, "AUTH0_AUDIENCE", ""):
            with pytest.raises(RuntimeError, match="AUTH0_AUDIENCE"):
                process_api._check_oidc_audience_guard()

    def test_enterprise_guard_not_raised_when_audience_set(self):
        """When AUTH0_AUDIENCE is set, the enterprise guard must not raise before decode."""
        import config.auth.jwt as jwt_mod

        mock_jwks = MagicMock()
        mock_signing_key = MagicMock()
        mock_signing_key.key = "fake-key"
        mock_jwks.get_signing_key_from_jwt.return_value = mock_signing_key

        with patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "enterprise"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://login.example.com/v2.0"), \
             patch.object(jwt_mod, "AUTH0_AUDIENCE", "api://my-audience"), \
             patch.object(jwt_mod, "_jwks_client", mock_jwks), \
             patch("jwt.decode", side_effect=Exception("decode-error")) as mock_decode:
            with pytest.raises(Exception, match="decode-error"):
                jwt_mod._decode_rs256("fake.token.here")
            # Verify audience is passed to jwt.decode
            call_kwargs = mock_decode.call_args[1]
            assert call_kwargs.get("audience") == "api://my-audience", (
                "_decode_rs256 must pass audience=AUTH0_AUDIENCE to jwt.decode"
            )

    def test_decode_rs256_passes_audience_and_issuer_to_jwt_decode(self):
        """_decode_rs256 must pass audience=AUTH0_AUDIENCE and issuer=OIDC_ISSUER_URL."""
        import config.auth.jwt as jwt_mod

        mock_jwks = MagicMock()
        mock_signing_key = MagicMock()
        mock_signing_key.key = "test-key"
        mock_jwks.get_signing_key_from_jwt.return_value = mock_signing_key

        with patch.object(jwt_mod, "AGENT_RUNTIME_MODE", "local"), \
             patch.object(jwt_mod, "OIDC_ISSUER_URL", "https://login.example.com"), \
             patch.object(jwt_mod, "AUTH0_AUDIENCE", "api://audience"), \
             patch.object(jwt_mod, "_jwks_client", mock_jwks), \
             patch("jwt.decode", return_value={"sub": "user123"}) as mock_decode:
            result = jwt_mod._decode_rs256("fake.token")
            assert result == {"sub": "user123"}
            call_kwargs = mock_decode.call_args[1]
            assert call_kwargs.get("audience") == "api://audience"
            assert call_kwargs.get("issuer") == "https://login.example.com"

    def test_decode_hs256_unchanged(self):
        """HS256 local token path must be unaffected by RS256 changes."""
        import config.auth.jwt as jwt_mod
        with patch("jwt.decode", return_value={"sub": "test-user"}) as mock_decode:
            result = jwt_mod._decode_hs256("fake-hs256-token")
            assert result == {"sub": "test-user"}
            assert mock_decode.called


# ═══════════════════════════════════════════════════════════════════════════
# REQ-M7-03: connector_factory rejects empty tenant_id
# ═══════════════════════════════════════════════════════════════════════════

class TestConnectorFactoryTenantRejection:
    """get_connector_for_session must reject empty tenant_id fail-closed."""

    @pytest.mark.asyncio
    async def test_empty_tenant_id_raises_value_error(self):
        """get_connector_for_session with tenant_id='' must raise ValueError."""
        from config.connector_factory import get_connector_for_session
        with pytest.raises(ValueError, match="tenant_id"):
            await get_connector_for_session(kind="jira", tenant_id="")

    @pytest.mark.asyncio
    async def test_health_probe_sentinel_passes(self):
        """get_connector_for_session with tenant_id='__health_probe__' must not raise."""
        from config.connector_factory import get_connector_for_session
        # Should not raise ValueError for the sentinel — may raise other errors
        # if the connector itself has issues (import, missing config), but not tenant rejection.
        try:
            connector = await get_connector_for_session(kind="jira", tenant_id="__health_probe__")
            # If we reach here, the sentinel passed
            assert connector is not None
        except ValueError as exc:
            if "tenant_id" in str(exc).lower():
                pytest.fail(
                    f"Sentinel '__health_probe__' must not be rejected by tenant_id guard. Got: {exc}"
                )
            # A ValueError about something else (e.g. connector config) is acceptable

    @pytest.mark.asyncio
    async def test_valid_tenant_id_does_not_raise_tenant_error(self):
        """A non-empty tenant_id must not trigger the tenant rejection guard."""
        from config.connector_factory import get_connector_for_session
        try:
            await get_connector_for_session(kind="jira", tenant_id="tenant-abc-123")
        except ValueError as exc:
            if "tenant_id" in str(exc).lower():
                pytest.fail(
                    f"Valid tenant_id must not trigger tenant rejection. Got: {exc}"
                )


# ═══════════════════════════════════════════════════════════════════════════
# REQ-M7-01: load_secret tenant scoping
# ═══════════════════════════════════════════════════════════════════════════

class TestKeyvaultTenantScoping:
    """load_secret resolves {tenant_id}-{name} when tenant_id provided."""

    @pytest.mark.asyncio
    async def test_load_secret_with_tenant_id_resolves_scoped_name(self):
        """load_secret('jira-url', tenant_id='abc') resolves to 'abc-jira-url'."""
        captured_names: list[str] = []

        mock_secret = MagicMock()
        mock_secret.value = "https://abc.atlassian.net"

        async def fake_get_secret(name):
            captured_names.append(name)
            return mock_secret

        mock_client = MagicMock()
        mock_client.get_secret = fake_get_secret
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_credential = MagicMock()
        mock_credential.close = AsyncMock()

        with patch("shared.keyvault.AZURE_KEY_VAULT_URL", "https://vault.example.com"), \
             patch("shared.keyvault.SecretClient", return_value=mock_client), \
             patch("shared.keyvault.DefaultAzureCredential", return_value=mock_credential):
            from shared.keyvault import load_secret
            result = await load_secret("jira-url", tenant_id="abc")

        assert "abc-jira-url" in captured_names, (
            f"Expected 'abc-jira-url' to be fetched from KV; got {captured_names}"
        )
        assert result == "https://abc.atlassian.net"

    @pytest.mark.asyncio
    async def test_load_secret_without_tenant_id_uses_exact_name(self):
        """load_secret('jira-url') with no tenant_id resolves to 'jira-url' exactly."""
        captured_names: list[str] = []

        mock_secret = MagicMock()
        mock_secret.value = "https://global.atlassian.net"

        async def fake_get_secret(name):
            captured_names.append(name)
            return mock_secret

        mock_client = MagicMock()
        mock_client.get_secret = fake_get_secret
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_credential = MagicMock()
        mock_credential.close = AsyncMock()

        with patch("shared.keyvault.AZURE_KEY_VAULT_URL", "https://vault.example.com"), \
             patch("shared.keyvault.SecretClient", return_value=mock_client), \
             patch("shared.keyvault.DefaultAzureCredential", return_value=mock_credential):
            from shared.keyvault import load_secret
            result = await load_secret("jira-url")

        assert captured_names == ["jira-url"], (
            f"Expected only 'jira-url' to be fetched; got {captured_names}"
        )
        assert result == "https://global.atlassian.net"

    @pytest.mark.asyncio
    async def test_load_secret_returns_none_when_kv_url_not_set(self):
        """load_secret must return None (not raise) when AZURE_KEY_VAULT_URL is unset."""
        with patch("shared.keyvault.AZURE_KEY_VAULT_URL", ""):
            from shared.keyvault import load_secret
            result = await load_secret("some-secret", tenant_id="tenant1")
        assert result is None, "load_secret must return None when AZURE_KEY_VAULT_URL is unset"


# ═══════════════════════════════════════════════════════════════════════════
# REQ-M7-01: connector auth_adapter requires tenant_id
# ═══════════════════════════════════════════════════════════════════════════

class TestConnectorAuthAdapterTenantRequired:
    """All connector auth_adapters must raise ValueError when tenant_id is absent."""

    @pytest.mark.asyncio
    async def test_jira_auth_adapter_raises_without_tenant_id(self):
        """JiraConnector.auth_adapter(tenant_id='') raises ValueError."""
        from config.connectors.jira import JiraConnector
        connector = JiraConnector("https://test.atlassian.net")
        with pytest.raises(ValueError, match="tenant_id"):
            await connector.auth_adapter(tenant_id="")

    @pytest.mark.asyncio
    async def test_jira_auth_adapter_calls_load_secret_with_tenant_id(self):
        """JiraConnector.auth_adapter(tenant_id='t1') calls load_secret with tenant_id='t1'."""
        from config.connectors.jira import JiraConnector
        connector = JiraConnector("https://test.atlassian.net")

        captured_calls: list[tuple] = []

        async def fake_load_secret(name, tenant_id=None):
            captured_calls.append((name, tenant_id))
            return None  # triggers global fallback

        with patch("shared.keyvault.load_secret", side_effect=fake_load_secret), \
             patch("config.connectors.jira._keyvault.load_secret", side_effect=fake_load_secret):
            await connector.auth_adapter(tenant_id="t1")

        tenant_scoped = [(n, tid) for (n, tid) in captured_calls if tid == "t1"]
        assert tenant_scoped, (
            f"auth_adapter must call load_secret(..., tenant_id='t1'). Calls: {captured_calls}"
        )

    @pytest.mark.asyncio
    async def test_azure_devops_auth_adapter_raises_without_tenant_id(self):
        """AzureDevOpsConnector.auth_adapter(tenant_id='') raises ValueError."""
        from config.connectors.azure_devops import AzureDevOpsConnector
        connector = AzureDevOpsConnector("https://dev.azure.com/myorg")
        with pytest.raises(ValueError, match="tenant_id"):
            await connector.auth_adapter(tenant_id="")

    @pytest.mark.asyncio
    async def test_azure_repos_auth_adapter_raises_without_tenant_id(self):
        """AzureReposConnector.auth_adapter(tenant_id='') raises ValueError."""
        from config.connectors.azure_repos import AzureReposConnector
        connector = AzureReposConnector("https://dev.azure.com/myorg")
        with pytest.raises(ValueError, match="tenant_id"):
            await connector.auth_adapter(tenant_id="")

    @pytest.mark.asyncio
    async def test_slack_auth_adapter_raises_without_tenant_id(self):
        """SlackConnector.auth_adapter(tenant_id='') raises ValueError."""
        from config.connectors.slack import SlackConnector
        connector = SlackConnector()
        with pytest.raises(ValueError, match="tenant_id"):
            await connector.auth_adapter(tenant_id="")

    @pytest.mark.asyncio
    async def test_github_issues_auth_adapter_raises_without_tenant_id(self):
        """GitHubIssuesConnector.auth_adapter(tenant_id='') raises ValueError."""
        from config.connectors.github_issues import GitHubIssuesConnector
        connector = GitHubIssuesConnector()
        with pytest.raises(ValueError, match="tenant_id"):
            await connector.auth_adapter(tenant_id="")
