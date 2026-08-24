"""Tests for the Confluence tools on the standalone Documentation agent.

Mirrors the (untested, and turned out to be silently broken — see
test_notification_targets.py) SharePoint triple's shape: publish / list / ingest,
gated behind an explicit user ask, resolved through the session's tenant.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents_orchestrator.documentation_agent.config import session_state
from agents_orchestrator.documentation_agent.tools import doc_tools
from config import ws_helper


def _session(tenant_id: str = "tenant-1", generated_docs=None):
    sid = f"test-{uuid.uuid4().hex[:8]}"
    ws_helper.set_session_id(sid)
    s = session_state.get_session(sid)
    s.tenant_id = tenant_id
    s.generated_docs = generated_docs if generated_docs is not None else []
    return s


class _FakeConnector:
    def __init__(self):
        self.read_adapter = AsyncMock()
        self.write_adapter = AsyncMock()


def _patch_confluence(monkeypatch, connector, default_space=""):
    async def _fake_target(tenant_id):
        return {"space": default_space} if default_space else None

    async def _fake_get_connector(**kwargs):
        return connector

    monkeypatch.setattr(
        "shared.services.notification_targets.confluence_target", _fake_target
    )
    monkeypatch.setattr(
        "config.connector_factory.get_connector_for_session", _fake_get_connector
    )


@pytest.mark.unit
async def test_publish_to_confluence_requires_generated_docs(monkeypatch):
    _session(generated_docs=[])
    result = await doc_tools.publish_to_confluence.ainvoke({"space": "ENG"})
    assert "no documents generated" in result.lower()


@pytest.mark.unit
async def test_publish_to_confluence_requires_a_space(monkeypatch):
    _session(generated_docs=[{"filename": "a.md", "title": "A", "contents": "hi"}])
    connector = _FakeConnector()
    _patch_confluence(monkeypatch, connector, default_space="")
    result = await doc_tools.publish_to_confluence.ainvoke({})
    assert result.startswith("ERROR")
    assert "space" in result.lower()


@pytest.mark.unit
async def test_publish_to_confluence_creates_a_new_page(monkeypatch):
    s = _session(generated_docs=[{"filename": "a.md", "title": "Runbook", "contents": "hi"}])
    connector = _FakeConnector()
    connector.write_adapter.return_value = {"id": "222", "url": "/spaces/ENG/pages/222"}
    _patch_confluence(monkeypatch, connector, default_space="ENG")

    result = await doc_tools.publish_to_confluence.ainvoke({})

    assert "Published 1 document(s) to Confluence" in result
    connector.write_adapter.assert_awaited_once()
    call = connector.write_adapter.await_args
    assert call.args[0] == "create_page"
    assert call.kwargs["space"] == "ENG"
    assert s.generated_docs[0]["confluence_page_id"] == "222"


@pytest.mark.unit
async def test_republishing_updates_instead_of_duplicating(monkeypatch):
    """A doc that already carries a confluence_page_id must UPDATE that page, not
    create a second one — see the update_page branch in publish_to_confluence."""
    s = _session(generated_docs=[{
        "filename": "a.md", "title": "Runbook", "contents": "v2",
        "confluence_page_id": "222",
    }])
    connector = _FakeConnector()
    connector.write_adapter.return_value = {"id": "222", "url": "/spaces/ENG/pages/222"}
    _patch_confluence(monkeypatch, connector, default_space="ENG")

    await doc_tools.publish_to_confluence.ainvoke({})

    call = connector.write_adapter.await_args
    assert call.args[0] == "update_page"
    assert call.kwargs["page_id"] == "222"


@pytest.mark.unit
async def test_list_confluence_pages_formats_results(monkeypatch):
    _session()
    connector = _FakeConnector()
    connector.read_adapter.return_value = [{"id": "222", "title": "Runbook"}]
    _patch_confluence(monkeypatch, connector, default_space="ENG")

    result = await doc_tools.list_confluence_pages.ainvoke({})
    assert "Runbook" in result
    assert "222" in result


@pytest.mark.unit
async def test_ingest_confluence_page_requires_page_id():
    result = await doc_tools.ingest_confluence_page.ainvoke({"page_id": ""})
    assert result.startswith("ERROR")


@pytest.mark.unit
async def test_ingest_confluence_page_returns_content(monkeypatch):
    _session()
    connector = _FakeConnector()
    connector.read_adapter.return_value = {"title": "Runbook", "content": "hello world"}
    _patch_confluence(monkeypatch, connector)

    result = await doc_tools.ingest_confluence_page.ainvoke({"page_id": "222"})
    assert "hello world" in result
    assert "Runbook" in result


@pytest.mark.unit
async def test_no_tenant_context_refuses_cleanly():
    _session(tenant_id="")
    result = await doc_tools.publish_to_confluence.ainvoke({"space": "ENG"})
    assert "ERROR" in result
