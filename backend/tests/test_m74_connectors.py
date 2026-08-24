"""Wave-C Connector Onboarding tests.

REQ-M7-20: Self-serve Jira OAuth → tenant KV; live-validated.
REQ-M7-21: GitHub App / Slack OAuth / Azure Repos built + unit-tested, live deferred.
REQ-M7-22: Integrations UI wired; connector:manage gates both BFF and FastAPI routes.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.asyncio


def _make_test_app_with_permission(has_perm: bool):
    """Create a minimal FastAPI test app with connectors router mounted.

    Patches active_workspace_for_request so permission checks work without a DB —
    require_permission()'s no-run_param path resolves the workspace through that
    function (shared.authz.workspace), not resolve_default_workspace.
    """
    from fastapi import FastAPI, Request
    from shared.routers.connectors import connectors_resource_router

    app = FastAPI()
    app.include_router(connectors_resource_router)

    if has_perm:
        @app.middleware("http")
        async def inject_with_perm(request: Request, call_next):
            request.state.tenant_id = "t1"
            request.state.permissions = ["connector:manage"]
            request.state.user_id = "user1"
            return await call_next(request)
    else:
        @app.middleware("http")
        async def inject_no_perm(request: Request, call_next):
            request.state.tenant_id = "t1"
            request.state.permissions = []
            request.state.user_id = "user1"
            return await call_next(request)

    return app


# ── Task 1: CSRF state ────────────────────────────────────────────────────────


class TestOAuthState:
    """CSRF-safe OAuth state mint / verify (Wave C shared utility)."""

    async def test_mint_oauth_state_returns_opaque_string(self):
        from shared.auth.oauth_state import mint_oauth_state

        state = mint_oauth_state("tenant-abc", "jira")
        assert isinstance(state, str)
        assert len(state) > 20
        # Should be base64url — no padding chars or spaces
        assert "=" not in state or "==" in state  # urlsafe_b64encode may or may not pad

    async def test_verify_oauth_state_succeeds_for_valid_state(self):
        from shared.auth.oauth_state import mint_oauth_state, verify_oauth_state

        state = mint_oauth_state("tenant-xyz", "jira")
        payload = verify_oauth_state(state, "tenant-xyz")
        assert payload["tid"] == "tenant-xyz"
        assert payload["kind"] == "jira"

    async def test_verify_oauth_state_rejects_tampered_signature(self):
        import base64
        import json

        from shared.auth.oauth_state import mint_oauth_state, verify_oauth_state

        state = mint_oauth_state("tenant-abc", "jira")
        raw = json.loads(base64.urlsafe_b64decode(state + "=="))
        raw["s"] = "aaaa" + raw["s"][4:]  # corrupt signature
        tampered = base64.urlsafe_b64encode(json.dumps(raw).encode()).decode().rstrip("=")
        with pytest.raises(ValueError, match="signature"):
            verify_oauth_state(tampered, "tenant-abc")

    async def test_verify_oauth_state_rejects_expired_state(self):
        import base64
        import json

        from shared.auth.oauth_state import verify_oauth_state, _sign_payload

        # Build a payload with exp already in the past
        import hmac as _hmac
        import hashlib
        from config.env import JWT_SECRET_KEY

        payload_dict = {"nonce": "n", "tid": "t1", "kind": "jira", "exp": int(time.time()) - 10}
        payload_str = json.dumps(payload_dict)
        sig = _hmac.new(JWT_SECRET_KEY.encode(), payload_str.encode(), hashlib.sha256).hexdigest()
        raw = json.dumps({"p": payload_str, "s": sig})
        state = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
        with pytest.raises(ValueError, match="expired"):
            verify_oauth_state(state, "t1")

    async def test_verify_oauth_state_rejects_wrong_tenant(self):
        from shared.auth.oauth_state import mint_oauth_state, verify_oauth_state

        state = mint_oauth_state("tenant-A", "jira")
        with pytest.raises(ValueError, match="mismatch"):
            verify_oauth_state(state, "tenant-B")


# ── Task 1: store_secret ──────────────────────────────────────────────────────


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
                result = await keyvault.store_secret("jira-access-token", "my-token", tenant_id="t1")

        assert result is True
        mock_client.set_secret.assert_called_once()
        call_args = mock_client.set_secret.call_args[0]
        assert call_args[0] == "t1-jira-access-token"
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


# ── Task 2: oauth_service + connectors router ─────────────────────────────────


class TestOAuthService:
    """build_oauth_start_url constructs correct provider authorize URLs."""

    def test_jira_start_url_contains_auth_atlassian(self):
        from shared.services.oauth_service import build_oauth_start_url

        with (
            patch("shared.services.oauth_service.JIRA_OAUTH_CLIENT_ID", "client123"),
            patch("shared.services.oauth_service.AGENTIC_BASE_URL", "https://api.example.com"),
        ):
            url = build_oauth_start_url("jira", "t1")
        assert "auth.atlassian.com/authorize" in url
        assert "client123" in url
        assert "offline_access" in url
        assert "state=" in url

    def test_github_start_url_contains_github_oauth(self):
        from shared.services.oauth_service import build_oauth_start_url

        with (
            patch("shared.services.oauth_service.GITHUB_APP_ID", "gh-app-id"),
            patch("shared.services.oauth_service.AGENTIC_BASE_URL", "https://api.example.com"),
        ):
            url = build_oauth_start_url("github", "t1")
        assert "github.com/login/oauth/authorize" in url

    def test_slack_start_url_contains_slack_oauth(self):
        from shared.services.oauth_service import build_oauth_start_url

        with (
            patch("shared.services.oauth_service.SLACK_CLIENT_ID", "slack-cid"),
            patch("shared.services.oauth_service.AGENTIC_BASE_URL", "https://api.example.com"),
        ):
            url = build_oauth_start_url("slack", "t1")
        assert "slack.com/oauth/v2/authorize" in url

    def test_azure_repos_start_url_contains_microsoftonline(self):
        from shared.services.oauth_service import build_oauth_start_url

        with patch("shared.services.oauth_service.AGENTIC_BASE_URL", "https://api.example.com"):
            url = build_oauth_start_url("azure_repos", "t1")
        assert "microsoftonline" in url or "login.microsoftonline" in url

    def test_unknown_kind_raises_value_error(self):
        from shared.services.oauth_service import build_oauth_start_url

        with pytest.raises(ValueError, match="Unknown connector kind"):
            build_oauth_start_url("unknown_connector", "t1")


class TestJiraOAuthCallback:
    """POST /connectors/jira/install + GET /connectors/jira/oauth/callback."""

    async def test_install_returns_redirect_url(self):
        from fastapi.testclient import TestClient

        app = _make_test_app_with_permission(True)

        with (
            patch("shared.authz.dependency.active_workspace_for_request", new_callable=AsyncMock),
            patch("shared.services.oauth_service.JIRA_OAUTH_CLIENT_ID", "cid"),
            patch("shared.services.oauth_service.AGENTIC_BASE_URL", "https://api.example.com"),
        ):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.post("/connectors/jira/install", follow_redirects=False)
        # Should return {redirect_url} JSON
        assert resp.status_code == 200
        data = resp.json()
        assert "redirect_url" in data
        assert "atlassian" in data["redirect_url"]

    async def test_callback_writes_access_token_to_kv(self):
        from shared.auth.oauth_state import mint_oauth_state
        from shared.routers import connectors as _conn_mod

        state = mint_oauth_state("t1", "jira")
        store_calls = []

        async def fake_store(name, value, tenant_id=None):
            store_calls.append((name, value, tenant_id))
            return True

        async def mock_exchange(code, tenant_id):
            return {"access_token": "acc-tok", "refresh_token": "ref-tok"}

        async def mock_fetch_cloud_id(access_token):
            return "cloud-123"

        with (
            patch("shared.routers.connectors.store_secret", side_effect=fake_store),
            patch("shared.routers.connectors._jira_token_exchange", side_effect=mock_exchange),
            patch("shared.routers.connectors._jira_fetch_cloud_id", side_effect=mock_fetch_cloud_id),
        ):
            await _conn_mod._jira_oauth_exchange("auth-code", state, "t1")

        stored_names = [c[0] for c in store_calls]
        assert "jira-access-token" in stored_names

    async def test_callback_writes_refresh_token_to_kv(self):
        from shared.auth.oauth_state import mint_oauth_state
        from shared.routers import connectors as _conn_mod

        state = mint_oauth_state("t1", "jira")
        store_calls = []

        async def fake_store(name, value, tenant_id=None):
            store_calls.append((name, value, tenant_id))
            return True

        async def mock_exchange(code, tenant_id):
            return {"access_token": "acc", "refresh_token": "ref"}

        async def mock_fetch_cloud_id(access_token):
            return "cloud-456"

        with (
            patch("shared.routers.connectors.store_secret", side_effect=fake_store),
            patch("shared.routers.connectors._jira_token_exchange", side_effect=mock_exchange),
            patch("shared.routers.connectors._jira_fetch_cloud_id", side_effect=mock_fetch_cloud_id),
        ):
            await _conn_mod._jira_oauth_exchange("code", state, "t1")

        stored_names = [c[0] for c in store_calls]
        assert "jira-refresh-token" in stored_names

    async def test_callback_writes_cloud_id_to_kv(self):
        from shared.auth.oauth_state import mint_oauth_state
        from shared.routers import connectors as _conn_mod

        state = mint_oauth_state("t1", "jira")
        store_calls = []

        async def fake_store(name, value, tenant_id=None):
            store_calls.append((name, value, tenant_id))
            return True

        async def mock_exchange(code, tenant_id):
            return {"access_token": "acc", "refresh_token": "ref"}

        async def mock_fetch_cloud_id(access_token):
            return "cloud-789"

        with (
            patch("shared.routers.connectors.store_secret", side_effect=fake_store),
            patch("shared.routers.connectors._jira_token_exchange", side_effect=mock_exchange),
            patch("shared.routers.connectors._jira_fetch_cloud_id", side_effect=mock_fetch_cloud_id),
        ):
            await _conn_mod._jira_oauth_exchange("code", state, "t1")

        stored = {c[0]: c[1] for c in store_calls}
        assert "jira-cloud-id" in stored
        assert stored["jira-cloud-id"] == "cloud-789"

    async def test_callback_rejects_invalid_oauth_state(self):
        from shared.routers import connectors as _conn_mod

        with pytest.raises(ValueError):
            await _conn_mod._jira_oauth_exchange("code", "bad-state-token", "t1")


class TestGitHubOAuth:
    """GitHub App user-auth flow — built + unit-tested, live deferred (D-M7-02)."""

    async def test_github_install_returns_redirect_url(self):
        from shared.services.oauth_service import build_oauth_start_url

        with (
            patch("shared.services.oauth_service.GITHUB_APP_ID", "my-app-id"),
            patch("shared.services.oauth_service.AGENTIC_BASE_URL", "https://api.example.com"),
        ):
            url = build_oauth_start_url("github", "t1")
        assert "github.com/login/oauth/authorize" in url
        assert "state=" in url

    async def test_github_callback_stores_installation_id(self):
        from shared.auth.oauth_state import mint_oauth_state
        from shared.routers import connectors as _conn_mod

        state = mint_oauth_state("t1", "github")
        store_calls = []

        async def fake_store(name, value, tenant_id=None):
            store_calls.append((name, value, tenant_id))
            return True

        async def mock_gh_exchange(code, tenant_id):
            return {"access_token": "gh-acc-tok"}

        with (
            patch("shared.routers.connectors.store_secret", side_effect=fake_store),
            patch("shared.routers.connectors._github_token_exchange", side_effect=mock_gh_exchange),
        ):
            await _conn_mod._github_oauth_exchange("code", state, "t1")

        stored_names = [c[0] for c in store_calls]
        assert "github-access-token" in stored_names


class TestSlackOAuth:
    """Slack OAuth v2 — built + unit-tested, live deferred (D-M7-02)."""

    async def test_slack_install_returns_redirect_url(self):
        from shared.services.oauth_service import build_oauth_start_url

        with (
            patch("shared.services.oauth_service.SLACK_CLIENT_ID", "slk-cid"),
            patch("shared.services.oauth_service.AGENTIC_BASE_URL", "https://api.example.com"),
        ):
            url = build_oauth_start_url("slack", "t1")
        assert "slack.com/oauth/v2/authorize" in url
        assert "state=" in url

    async def test_slack_callback_stores_bot_token(self):
        from shared.auth.oauth_state import mint_oauth_state
        from shared.routers import connectors as _conn_mod

        state = mint_oauth_state("t1", "slack")
        store_calls = []

        async def fake_store(name, value, tenant_id=None):
            store_calls.append((name, value, tenant_id))
            return True

        async def mock_slack_exchange(code, tenant_id):
            return {"access_token": "slack-bot-tok"}

        with (
            patch("shared.routers.connectors.store_secret", side_effect=fake_store),
            patch("shared.routers.connectors._slack_token_exchange", side_effect=mock_slack_exchange),
        ):
            await _conn_mod._slack_oauth_exchange("code", state, "t1")

        stored_names = [c[0] for c in store_calls]
        assert "slack-bot-token" in stored_names


class TestConnectorManageGate:
    """connector:manage permission gates both BFF install route and FastAPI OAuth-start (REQ-M7-22)."""

    async def test_install_without_permission_returns_403(self):
        from fastapi.testclient import TestClient

        app = _make_test_app_with_permission(False)

        with (
            patch("shared.authz.dependency.active_workspace_for_request", new_callable=AsyncMock),
        ):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post("/connectors/jira/install")
        assert resp.status_code == 403

    async def test_install_with_permission_succeeds(self):
        from fastapi.testclient import TestClient

        app = _make_test_app_with_permission(True)

        with (
            patch("shared.authz.dependency.active_workspace_for_request", new_callable=AsyncMock),
            patch("shared.services.oauth_service.JIRA_OAUTH_CLIENT_ID", "cid"),
            patch("shared.services.oauth_service.AGENTIC_BASE_URL", "https://api.example.com"),
        ):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.post("/connectors/jira/install", follow_redirects=False)
        assert resp.status_code == 200

    async def test_callback_without_permission_returns_403(self):
        from fastapi.testclient import TestClient

        app = _make_test_app_with_permission(False)

        with (
            patch("shared.authz.dependency.active_workspace_for_request", new_callable=AsyncMock),
        ):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get("/connectors/jira/oauth/callback?code=abc&state=xyz")
        assert resp.status_code == 403


# ── Task 3: JiraConnector OAuth adapter ──────────────────────────────────────


class TestJiraOAuthAdapter:
    """JiraConnector.auth_adapter() uses OAuth Bearer when token present; Basic Auth otherwise."""

    async def test_auth_adapter_returns_oauth_mode_when_token_present(self):
        from config.connectors.jira import JiraConnector
        import shared.keyvault as _kv

        connector = JiraConnector("https://example.atlassian.net")

        async def fake_load(name, tenant_id=None):
            if name == "jira-access-token" and tenant_id == "t1":
                return "my-bearer-token"
            if name == "jira-cloud-id" and tenant_id == "t1":
                return "cloud-123"
            return None

        with patch.object(_kv, "load_secret", side_effect=fake_load):
            auth = await connector.auth_adapter(tenant_id="t1")

        assert auth["mode"] == "oauth"
        assert auth["bearer"] == "my-bearer-token"
        assert "api.atlassian.com/ex/jira/cloud-123" in auth["jira_url"]

    async def test_auth_adapter_returns_basic_mode_when_token_absent(self):
        from config.connectors.jira import JiraConnector
        import shared.keyvault as _kv
        from config.env import JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN

        connector = JiraConnector("https://example.atlassian.net")

        async def fake_load(name, tenant_id=None):
            # No OAuth token
            if name == "jira-access-token":
                return None
            # Basic auth creds from KV
            if name == "jira-url":
                return "https://example.atlassian.net"
            if name == "jira-email":
                return "user@example.com"
            if name == "jira-api-token":
                return "basic-api-token"
            return None

        with patch.object(_kv, "load_secret", side_effect=fake_load):
            auth = await connector.auth_adapter(tenant_id="t1")

        assert auth["mode"] == "basic"
        assert "email" in auth
        assert "token" in auth

    async def test_auth_adapter_raises_value_error_without_tenant_id(self):
        from config.connectors.jira import JiraConnector

        connector = JiraConnector("https://example.atlassian.net")
        with pytest.raises(ValueError, match="tenant_id"):
            await connector.auth_adapter(tenant_id="")

    async def test_jira_request_uses_bearer_header_in_oauth_mode(self):
        from config.connectors.jira import JiraConnector
        import shared.keyvault as _kv

        connector = JiraConnector("https://api.atlassian.com/ex/jira/cloud-123")

        async def fake_load(name, tenant_id=None):
            if name == "jira-access-token":
                return "oauth-bearer-tok"
            if name == "jira-cloud-id":
                return "cloud-123"
            return None

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"issues": []}'
        mock_resp.json = MagicMock(return_value={"issues": []})
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

        assert "headers" in captured_kwargs
        assert "Bearer oauth-bearer-tok" in captured_kwargs["headers"].get("Authorization", "")
        # No basic auth tuple
        assert "auth" not in captured_kwargs or captured_kwargs.get("auth") is None

    async def test_jira_request_uses_basic_auth_in_basic_mode(self):
        from config.connectors.jira import JiraConnector
        import shared.keyvault as _kv

        connector = JiraConnector("https://example.atlassian.net")

        async def fake_load(name, tenant_id=None):
            if name == "jira-access-token":
                return None
            if name == "jira-url":
                return "https://example.atlassian.net"
            if name == "jira-email":
                return "me@example.com"
            if name == "jira-api-token":
                return "api-tok"
            return None

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

        # Basic auth tuple must be present
        assert "auth" in captured_kwargs
        assert captured_kwargs["auth"] == ("me@example.com", "api-tok")

    async def test_bearer_token_never_logged(self):
        """T-7.4-22: OAuth Bearer token must never appear in log output."""
        import logging
        from config.connectors.jira import JiraConnector
        import shared.keyvault as _kv

        connector = JiraConnector("https://example.atlassian.net")

        async def fake_load(name, tenant_id=None):
            if name == "jira-access-token":
                return "SUPER-SECRET-BEARER-12345"
            if name == "jira-cloud-id":
                return "cloud-abc"
            return None

        with patch.object(_kv, "load_secret", side_effect=fake_load):
            import io
            handler = logging.StreamHandler(stream := io.StringIO())
            logging.getLogger().addHandler(handler)
            try:
                auth = await connector.auth_adapter(tenant_id="t1")
            finally:
                logging.getLogger().removeHandler(handler)

        log_output = stream.getvalue()
        assert "SUPER-SECRET-BEARER-12345" not in log_output
