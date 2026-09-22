"""An unconnected user is told so — by name, with the fix — not `UnsupportedProtocol`.

THE LIVE FAILURE. An architect signed in, opened the standalone Design agent on a
project whose Requirements agent had just published to Confluence, and asked for the
same. The reply was "ERROR creating the page: UnsupportedProtocol". Confluence is a
PERSONAL credential (config.connectors.base.PERSONAL_CREDENTIAL_KINDS): the architect
had never saved one, the connector resolved to a blank token and a blank site URL, and
httpx named the blank URL. Nothing in that told the architect what to do.

Two layers, each on its own:

  * the connector refuses before any request leaves, with a message naming the cause
    and the fix (`ConfluenceNotConnected`);
  * the tools check once, in `_resolve`, so a publish of five documents does not
    report five identical failures — and when the connector's refusal does surface
    from a call, the tool relays the reason rather than the type name.
"""
from __future__ import annotations

import pytest

from config.connectors import confluence as cf
from shared.tools import confluence_artifacts as ca


class _Unconnected:
    """A connector whose auth resolves to no token — what a user with no personal
    credential gets from ConfluenceConnector.auth_adapter."""

    def __init__(self, token: str = ""):
        self.token = token
        self.calls = []

    async def auth_adapter(self, tenant_id: str = ""):
        return {"confluence_url": "", "email": "", "token": self.token}

    async def write_adapter(self, operation, **kwargs):
        self.calls.append(operation)
        return {"id": "1", "title": kwargs.get("title", "")}

    async def read_adapter(self, operation, **kwargs):
        self.calls.append(operation)
        return []


@pytest.fixture
def tools(monkeypatch):
    connector = _Unconnected()
    from config import connector_factory

    async def _session_connector(**kwargs):
        return connector

    monkeypatch.setattr(connector_factory, "get_connector_for_session", _session_connector)
    monkeypatch.setattr("config.ws_helper.get_tenant_id", lambda: "tenant")
    monkeypatch.setattr("config.ws_helper.get_project_id", lambda: "project")
    monkeypatch.setattr("config.ws_helper.get_user_id", lambda: "architect")
    made = {t.name: t for t in ca.make_confluence_tools(agent_id="design", stage="design")}
    return connector, made


async def test_the_connector_refuses_by_name_before_any_request_leaves(monkeypatch):
    connector = cf.ConfluenceConnector(org_url="", tenant_id="tenant")

    async def _auth(tenant_id=""):
        return {"confluence_url": "", "email": "", "token": ""}

    monkeypatch.setattr(connector, "auth_adapter", _auth)
    with pytest.raises(cf.ConfluenceNotConnected) as info:
        await connector._confluence_request("GET", "/spaces", tenant_id="tenant")
    assert "not connected for you" in str(info.value)
    assert "Integrations page" in str(info.value)


async def test_every_tool_says_so_once_and_sends_nothing(tools):
    connector, made = tools

    for name, args in (
        ("publish_approved_to_confluence", {"space": "ENG"}),
        ("create_confluence_page", {"space": "ENG", "title": "HLD"}),
        ("list_confluence_spaces", {}),
        ("search_confluence", {"query": "design"}),
    ):
        out = await made[name].ainvoke(args)
        assert out.startswith("ERROR: Confluence is not connected for you"), (name, out)
        assert "UnsupportedProtocol" not in out
        assert "their credential" in out, "the model is told to relay it, not to apologise for itself"
    assert connector.calls == [], "nothing was sent"


async def test_a_connected_user_passes_through(tools, monkeypatch):
    connector, made = tools
    connector.token = "atl-token"
    out = await made["list_confluence_spaces"].ainvoke({})
    assert not out.startswith("ERROR"), out
    assert connector.calls == ["list_spaces"]


async def test_the_refusal_is_relayed_when_it_surfaces_from_a_call():
    """Defence in depth: should the check in _resolve be bypassed, the connector's
    own refusal reads as its message, not as `ConfluenceNotConnected`."""
    assert ca._why(cf.ConfluenceNotConnected()) == cf.NOT_CONNECTED_MESSAGE
    assert ca._why(RuntimeError("Basic dXNlcjp0b2tlbg== leaked")) == "RuntimeError"
