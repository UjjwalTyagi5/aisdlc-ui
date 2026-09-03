"""Unit tests for the github_actions, ms_teams and sharepoint connectors.

Covers the three properties that keep the connector layer honest:
  1. Credential resolution is per-tenant and honours the disconnect tombstone.
  2. health_check NEVER raises — a raising probe is dropped from the health cache,
     which makes GET /connectors/health re-probe inline on every request.
  3. Credentials never reach the logs.
"""
import logging
import contextlib
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.connectors.github_actions import GitHubActionsConnector  # noqa: E402
from config.connectors.msteams import MSTeamsConnector  # noqa: E402
from config.connectors.sharepoint import SharePointConnector  # noqa: E402
from config.connectors import msgraph  # noqa: E402
from config.connectors.models import ConnectorHealth  # noqa: E402

_ALL = [GitHubActionsConnector, MSTeamsConnector, SharePointConnector]


class _Store:
    """Minimal stand-in for shared.services.secret_store."""

    DISCONNECTED_MARKER = "__disconnected__"

    def __init__(self, data=None):
        self.data = data or {}

    async def get_secret(self, tenant_id, ref):
        return self.data.get(ref)

    async def put_secret(self, tenant_id, ref, value):
        self.data[ref] = value

    async def delete_secret(self, tenant_id, ref):
        self.data.pop(ref, None)


@contextlib.contextmanager
def _patch_store(store):
    """Patch the lazily-imported secret_store seen by every connector.

    BOTH sys.modules AND the package attribute, because which one a connector sees
    depends on whether the real module was already imported. `from shared.services
    import secret_store` reads the ATTRIBUTE on the `shared.services` package, and
    Python sets that attribute the first time the submodule is imported — after which
    patching sys.modules alone is ignored entirely.

    That made these tests order-dependent in a way that looked like a connector bug:
    run alone they passed, run after anything that imports `process_api` (which pulls
    in the real secret_store) every Graph test failed with GraphCredentialsMissing.
    Patching both covers the module whether or not it has been imported yet.
    """
    import shared.services

    real = getattr(shared.services, "secret_store", None)
    with patch.dict(sys.modules, {"shared.services.secret_store": store}):
        setattr(shared.services, "secret_store", store)
        try:
            yield store
        finally:
            if real is not None:
                setattr(shared.services, "secret_store", real)
            else:
                delattr(shared.services, "secret_store")


# ── auth_adapter contract ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("cls", _ALL)
async def test_auth_adapter_requires_tenant(cls):
    """Empty tenant_id fails closed — connector credentials are per-tenant (REQ-M7-01)."""
    with pytest.raises(ValueError, match="tenant_id is required"):
        await cls().auth_adapter("")


@pytest.mark.unit
async def test_github_actions_ignores_the_tenant_store_entirely():
    """The tenant-wide rung is gone (config.connectors.base.PERSONAL_CREDENTIAL_KINDS).

    It let this connector work for a project whose members never gave it a credential,
    and GitHub then recorded the work against whoever minted the shared PAT. A stored
    tenant token must now be ignored rather than borrowed.
    """
    store = _Store({"gha-pat": "ghp_from_store", "gha-owner": "acme"})
    with _patch_store(store), patch("shared.keyvault.load_secret", return_value=None):
        auth = await GitHubActionsConnector().auth_adapter("t-1")
    assert auth["pat"] == "", "a tenant-wide PAT must not be used"


@pytest.mark.unit
async def test_github_actions_uses_the_acting_users_own_credential():
    """The one rung that remains."""
    from shared.authz.project_credential import ProjectCredentialFields

    async def _mine(_self, _tenant, _target):
        return ProjectCredentialFields(base_url=None, account="acme", token="ghp_mine")

    with patch.object(GitHubActionsConnector, "_resolve_credential_override", _mine):
        auth = await GitHubActionsConnector().auth_adapter("t-1")
    assert auth == {"pat": "ghp_mine", "owner": "acme"}


@pytest.mark.unit
async def test_github_actions_never_falls_through_to_a_global_credential():
    """What the disconnect tombstone used to be the only guard against.

    There is now no rung below the personal credential at all, so a global PAT cannot
    win whether the tenant is disconnected or was never connected.
    """
    store = _Store({"gha-pat": _Store.DISCONNECTED_MARKER})
    with _patch_store(store), patch(
        "shared.keyvault.load_secret", return_value="ghp_global_should_not_win"
    ):
        auth = await GitHubActionsConnector().auth_adapter("t-1")
    assert auth["pat"] == ""


# ── health_check must never raise ─────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("cls", _ALL)
async def test_health_check_never_raises_without_credentials(cls):
    """No credentials anywhere → a ConnectorHealth, never an exception.

    Guards the inline-reprobe trap: _probe_all_connectors drops connectors whose probe
    raises, so the name never lands in the cache and /connectors/health re-probes on
    every single request forever.
    """
    store = _Store({})
    with _patch_store(store), patch("shared.keyvault.load_secret", return_value=None):
        health = await cls().health_check()

    assert isinstance(health, ConnectorHealth)
    assert health.status in ("healthy", "degraded", "unhealthy")
    # A bare identifier proves type(exc).__name__ was used, never str(exc), which can
    # carry credential material (the M1 decision).
    if health.error:
        assert health.error.isidentifier() or health.error.startswith("http_")


# ── Microsoft Graph: one token mint serves both connectors ────────────────────


@pytest.mark.unit
@respx.mock
async def test_graph_token_is_minted_once_for_both_connectors():
    """ms_teams and sharepoint share one Entra app, so they must share one mint.

    This is the test that justifies a module-level cache instead of two class-level
    ones — two caches would double the token round-trips and throttling exposure.
    """
    msgraph._clear_token_cache()
    store = _Store(
        {
            "msgraph-tenant-id": "dir-1",
            "msgraph-client-id": "app-1",
            "msgraph-client-secret": "shhh",
        }
    )
    route = respx.post("https://login.microsoftonline.com/dir-1/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    with _patch_store(store), patch("shared.keyvault.load_secret", return_value=None):
        a = await msgraph.get_graph_token("t-1")
        b = await msgraph.get_graph_token("t-1")
    assert a == b == "tok-1"
    assert route.call_count == 1
    msgraph._clear_token_cache()


@pytest.mark.unit
@respx.mock
async def test_graph_credentials_missing_is_explicit():
    msgraph._clear_token_cache()
    with _patch_store(_Store({})), patch("shared.keyvault.load_secret", return_value=None):
        with pytest.raises(msgraph.GraphCredentialsMissing):
            await msgraph.get_graph_token("t-1")


# ── Teams: explicit target + content guard ────────────────────────────────────


@pytest.mark.unit
async def test_notify_teams_requires_an_explicit_target():
    """No default channel, ever (REQ-M6-04 parity with Slack)."""
    with pytest.raises(ValueError, match="explicitly configured"):
        await MSTeamsConnector().notify_teams("hello")
    # A half-specified Graph target is still no target.
    with pytest.raises(ValueError, match="explicitly configured"):
        await MSTeamsConnector().notify_teams("hello", team_id="T")


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        "deploy failed, ANTHROPIC_API_KEY=sk-ant-123456789",
        "here you go: Bearer abcdefghijklmnop",
        "x" * 5000,
    ],
)
async def test_notify_teams_content_guard_rejects_secrets_and_overlength(message):
    """The Slack content guard is reused, not re-implemented — so it applies here too."""
    with pytest.raises(ValueError):
        await MSTeamsConnector().notify_teams(
            message, webhook_url="https://outlook.office.com/webhook/x"
        )


@pytest.mark.unit
@respx.mock
async def test_notify_teams_prefers_webhook_over_graph():
    """The webhook transport needs no Entra app, so it wins when both are configured."""
    hook = respx.post("https://outlook.office.com/webhook/x").mock(
        return_value=httpx.Response(200, text="1")
    )
    graph = respx.post("https://graph.microsoft.com/v1.0/teams/T/channels/C/messages").mock(
        return_value=httpx.Response(201, json={})
    )
    await MSTeamsConnector().notify_teams(
        "gate awaiting approval",
        webhook_url="https://outlook.office.com/webhook/x",
        team_id="T",
        channel_id="C",
        title="Approval needed",
        link_url="https://app/runs/1",
    )
    assert hook.call_count == 1
    assert graph.call_count == 0


@pytest.mark.unit
@respx.mock
async def test_teams_webhook_card_carries_a_deep_link_not_an_inline_action():
    """Approvals deep-link into the authenticated UI; no inline approve button."""
    captured = {}

    def _capture(request):
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(200, text="1")

    respx.post("https://outlook.office.com/webhook/x").mock(side_effect=_capture)
    await MSTeamsConnector().notify_teams(
        "Run r1 is awaiting deployment approval.",
        webhook_url="https://outlook.office.com/webhook/x",
        title="Approval needed",
        link_url="https://app/runs/r1",
    )
    actions = captured.get("potentialAction", [])
    assert actions and actions[0]["@type"] == "OpenUri"
    assert actions[0]["targets"][0]["uri"] == "https://app/runs/r1"


# ── SharePoint: upload ceiling ────────────────────────────────────────────────


@pytest.mark.unit
async def test_publish_document_rejects_oversize_upload():
    """4 MB is the single-PUT ceiling; chunked upload is deliberately not implemented."""
    with pytest.raises(ValueError, match="single-PUT limit"):
        await SharePointConnector().publish_document(
            "drive-1", "docs/big.md", b"x" * (4 * 1024 * 1024 + 1)
        )


@pytest.mark.unit
async def test_publish_document_requires_a_path():
    with pytest.raises(ValueError, match="path is required"):
        await SharePointConnector().publish_document("drive-1", "", b"content")


@pytest.mark.unit
@respx.mock
async def test_publish_document_puts_to_the_content_endpoint():
    msgraph._clear_token_cache()
    store = _Store(
        {
            "msgraph-tenant-id": "dir-1",
            "msgraph-client-id": "app-1",
            "msgraph-client-secret": "shhh",
        }
    )
    respx.post("https://login.microsoftonline.com/dir-1/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    put = respx.put(
        "https://graph.microsoft.com/v1.0/drives/d1/root:/SDLC%20Documentation/brd.md:/content"
    ).mock(
        return_value=httpx.Response(
            201, json={"id": "i1", "name": "brd.md", "webUrl": "https://sp/brd.md", "size": 7}
        )
    )
    with _patch_store(store), patch("shared.keyvault.load_secret", return_value=None):
        result = await SharePointConnector(tenant_id="t-1").publish_document(
            "d1", "SDLC Documentation/brd.md", b"content"
        )
    assert put.call_count == 1
    assert result["webUrl"] == "https://sp/brd.md"
    msgraph._clear_token_cache()


@pytest.mark.unit
async def test_create_subscription_demands_high_entropy_client_state():
    """clientState is the ONLY authentication Graph offers on inbound notifications."""
    with pytest.raises(ValueError, match="at least 32 characters"):
        await SharePointConnector().create_subscription(
            "d1", "https://app/webhooks/msgraph/t1", "short"
        )


# ── dispatch maps ─────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("cls", _ALL)
async def test_unknown_write_operation_raises(cls):
    with pytest.raises((ValueError, NotImplementedError)):
        await cls().write_adapter("no_such_operation")


@pytest.mark.unit
async def test_teams_is_write_only():
    with pytest.raises(NotImplementedError):
        await MSTeamsConnector().read_adapter("anything")


# ── credentials never logged ──────────────────────────────────────────────────


@pytest.mark.unit
@respx.mock
async def test_graph_client_secret_never_logged(caplog):
    msgraph._clear_token_cache()
    secret = "super-secret-value-9876"
    store = _Store(
        {
            "msgraph-tenant-id": "dir-1",
            "msgraph-client-id": "app-1",
            "msgraph-client-secret": secret,
        }
    )
    respx.post("https://login.microsoftonline.com/dir-1/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok-xyz", "expires_in": 3600})
    )
    with caplog.at_level(logging.DEBUG), _patch_store(store), patch(
        "shared.keyvault.load_secret", return_value=None
    ):
        await msgraph.get_graph_token("t-1")
    assert secret not in caplog.text
    assert "tok-xyz" not in caplog.text
    msgraph._clear_token_cache()


@pytest.mark.unit
async def test_github_actions_pat_never_logged(caplog):
    pat = "ghp_supersecrettoken123"
    store = _Store({"gha-pat": pat})
    with caplog.at_level(logging.DEBUG), _patch_store(store), patch(
        "shared.keyvault.load_secret", return_value=None
    ):
        await GitHubActionsConnector().health_check()
    assert pat not in caplog.text
    assert "Bearer " not in caplog.text
