"""Credential storage, validation, and disconnect for ms_teams and sharepoint.

The sharp edge these guard: ms_teams and sharepoint share ONE Entra app registration,
so a naive disconnect that deletes the shared secret would silently break whichever
Graph connector is still connected.

These exercise the storage helpers directly rather than through HTTP — the endpoint
itself needs a DB session for the workspace-enablement row, and the branching logic
under test is entirely in the helpers.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import shared.routers.connectors as conn  # noqa: E402
from shared.services import secret_store as real_secret_store  # noqa: E402

_T = "tenant-1"


class _Store:
    DISCONNECTED_MARKER = "__disconnected__"

    def __init__(self, data=None):
        self.data = dict(data or {})

    async def get_secret(self, tenant_id, ref):
        return self.data.get(ref)

    async def put_secret(self, tenant_id, ref, value):
        self.data[ref] = value

    async def delete_secret(self, tenant_id, ref):
        self.data.pop(ref, None)

    @property
    def refs(self):
        return sorted(self.data)


def _body(**kw):
    return conn.SetCredentialsIn(**kw)


def _patch_real_store(store):
    """Patch the module-level secret_store that disconnect helpers import lazily."""
    return patch.multiple(
        real_secret_store,
        get_secret=store.get_secret,
        put_secret=store.put_secret,
        delete_secret=store.delete_secret,
    )


# ── Validation ────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "body,fragment",
    [
        (_body(), "delivery target"),                                  # nothing at all
        (_body(team_id="T"), "delivery target"),                       # half a Graph target
        (_body(channel_id="C"), "delivery target"),
        (_body(team_id="T", channel_id="C"), "app registration"),      # target, no credentials
    ],
)
async def test_ms_teams_rejects_incomplete_payloads(body, fragment):
    with pytest.raises(HTTPException) as exc:
        await conn._store_ms_teams_credentials(_T, body, _Store())
    assert exc.value.status_code == 422
    assert fragment in exc.value.detail


@pytest.mark.unit
@pytest.mark.parametrize(
    "body,fragment",
    [
        (_body(msgraph_tenant_id="a", msgraph_client_id="b", msgraph_client_secret="c"), "site_url"),
        (_body(site_url="https://x.sharepoint.com/sites/D"), "app registration"),
        # A partial app registration must not be written — it would fail confusingly
        # later at token-mint time instead of here.
        (_body(site_url="https://x.sharepoint.com/sites/D", msgraph_tenant_id="a"), "app registration"),
    ],
)
async def test_sharepoint_rejects_incomplete_payloads(body, fragment):
    with pytest.raises(HTTPException) as exc:
        await conn._store_sharepoint_credentials(_T, body, _Store())
    assert exc.value.status_code == 422
    assert fragment in exc.value.detail


# ── Storage ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_teams_webhook_only_needs_no_entra_app():
    """The Incoming Webhook transport is the one that works with zero admin consent."""
    store = _Store()
    account = await conn._store_ms_teams_credentials(
        _T, _body(webhook_url="https://outlook.office.com/webhook/x"), store
    )
    assert account == "Incoming webhook"
    assert store.refs == ["msteams-connected", "msteams-webhook-url"]
    assert not any(r.startswith("msgraph-") for r in store.refs)


@pytest.mark.unit
async def test_teams_graph_path_stores_shared_app_and_target():
    store = _Store()
    account = await conn._store_ms_teams_credentials(
        _T,
        _body(
            msgraph_tenant_id="dir", msgraph_client_id="app", msgraph_client_secret="shh",
            team_id="T", channel_id="19:abc",
        ),
        store,
    )
    assert account == "19:abc"
    assert store.refs == [
        "msgraph-client-id", "msgraph-client-secret", "msgraph-tenant-id",
        "msteams-channel-id", "msteams-connected", "msteams-team-id",
    ]


@pytest.mark.unit
async def test_sharepoint_stores_app_and_defaults_the_folder():
    store = _Store()
    account = await conn._store_sharepoint_credentials(
        _T,
        _body(
            msgraph_tenant_id="dir", msgraph_client_id="app", msgraph_client_secret="shh",
            site_url="https://x.sharepoint.com/sites/Delivery",
        ),
        store,
    )
    assert account == "https://x.sharepoint.com/sites/Delivery"
    assert store.data["sharepoint-folder-path"] == "SDLC Documentation"
    assert store.data["sharepoint-connected"] == account


# ── The shared-secret hazard ──────────────────────────────────────────────────


@pytest.mark.unit
async def test_disconnecting_one_graph_kind_keeps_the_shared_app():
    """Disconnecting Teams must NOT break SharePoint, and vice versa."""
    store = _Store(
        {
            "msgraph-tenant-id": "dir", "msgraph-client-id": "app", "msgraph-client-secret": "shh",
            "msteams-connected": "19:abc", "sharepoint-connected": "https://x/sites/D",
        }
    )
    with _patch_real_store(store):
        await conn._maybe_purge_shared_graph_secrets(_T, "ms_teams")

    assert "msgraph-client-secret" in store.data
    assert "msgraph-tenant-id" in store.data


@pytest.mark.unit
async def test_disconnecting_the_last_graph_kind_removes_the_shared_app():
    store = _Store(
        {
            "msgraph-tenant-id": "dir", "msgraph-client-id": "app", "msgraph-client-secret": "shh",
            # Teams already disconnected — disconnect_connector tombstones the marker.
            "msteams-connected": _Store.DISCONNECTED_MARKER,
            "sharepoint-connected": "https://x/sites/D",
        }
    )
    with _patch_real_store(store):
        await conn._maybe_purge_shared_graph_secrets(_T, "sharepoint")

    assert not any(r.startswith("msgraph-") for r in store.refs)


@pytest.mark.unit
async def test_purge_is_a_noop_for_non_graph_kinds():
    store = _Store({"msgraph-client-secret": "shh", "ado-pat": "p"})
    with _patch_real_store(store):
        await conn._maybe_purge_shared_graph_secrets(_T, "azure_devops")
    assert store.data["msgraph-client-secret"] == "shh"


# ── Registration maps stay consistent ─────────────────────────────────────────


@pytest.mark.unit
def test_new_kinds_are_wired_into_every_constant_map():
    for kind in ("ms_teams", "sharepoint"):
        assert kind in conn._KNOWN_KINDS
        assert kind in conn._CREDENTIAL_KINDS
        assert kind in conn._KIND_SECRET_STORE_REFS
        assert kind in conn._KIND_PRIMARY_CREDENTIAL
        assert any(k == kind for k, _, _ in conn._CREDENTIAL_CONNECTORS)


@pytest.mark.unit
def test_primary_credential_is_a_marker_not_the_shared_secret():
    """Tombstoning msgraph-client-secret on disconnect would cross-kill the sibling."""
    for kind in ("ms_teams", "sharepoint"):
        assert conn._KIND_PRIMARY_CREDENTIAL[kind] not in conn._MSGRAPH_SHARED_REFS
        # And the shared refs must not be listed as either kind's own refs, or the
        # generic disconnect loop would delete them without the sibling check.
        assert not set(conn._KIND_SECRET_STORE_REFS[kind]) & set(conn._MSGRAPH_SHARED_REFS)


@pytest.mark.unit
def test_existing_credential_payloads_still_validate():
    """Additive-only: the shapes the Integrations dialog already sends must still parse."""
    assert _body(org_url="https://dev.azure.com/o", pat="p").pat == "p"
    assert _body(base_url="https://x.atlassian.net", email="a@b.c", api_token="t").email == "a@b.c"
    assert _body(pat="ghp_x", owner="acme").owner == "acme"
