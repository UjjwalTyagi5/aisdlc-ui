"""Tests for shared.services.notification_targets.

This module was silently absent from the working tree (present in git history,
missing from HEAD) despite sharepoint.py, msteams.py, figma.py and doc_tools.py all
importing it — meaning SharePoint publishing, Teams notification routing, and the
Figma default-file convenience were all broken at runtime with zero test coverage.
These tests exist so that gap cannot reopen silently again, and cover the new
confluence_target() resolver added alongside the recovery.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.services import notification_targets as nt


class _FakeSecretStore:
    """In-memory stand-in for shared.services.secret_store, keyed by (tenant, ref)."""

    def __init__(self, values: dict[str, str]):
        self._values = values
        self.DISCONNECTED_MARKER = "__disconnected__"

    async def get_secret(self, tenant_id: str, ref: str):
        return self._values.get(ref)


def _patch_store(monkeypatch, values: dict[str, str]):
    fake = _FakeSecretStore(values)
    import shared.services.secret_store as real_store

    monkeypatch.setattr(real_store, "get_secret", fake.get_secret)
    monkeypatch.setattr(real_store, "DISCONNECTED_MARKER", fake.DISCONNECTED_MARKER)


@pytest.mark.unit
async def test_teams_target_prefers_webhook_over_graph(monkeypatch):
    _patch_store(monkeypatch, {
        "msteams-webhook-url": "https://outlook.office.com/webhook/abc",
        "msteams-team-id": "team-1",
        "msteams-channel-id": "chan-1",
    })
    result = await nt.teams_target("tenant-1")
    assert result == {"webhook_url": "https://outlook.office.com/webhook/abc"}


@pytest.mark.unit
async def test_teams_target_falls_back_to_graph_pair(monkeypatch):
    _patch_store(monkeypatch, {"msteams-team-id": "team-1", "msteams-channel-id": "chan-1"})
    result = await nt.teams_target("tenant-1")
    assert result == {"team_id": "team-1", "channel_id": "chan-1"}


@pytest.mark.unit
async def test_teams_target_none_when_unconfigured(monkeypatch):
    _patch_store(monkeypatch, {})
    assert await nt.teams_target("tenant-1") is None


@pytest.mark.unit
async def test_sharepoint_target_requires_drive_id(monkeypatch):
    _patch_store(monkeypatch, {"sharepoint-site-id": "site-1"})  # no drive id
    assert await nt.sharepoint_target("tenant-1") is None


@pytest.mark.unit
async def test_sharepoint_target_defaults_folder(monkeypatch):
    _patch_store(monkeypatch, {"sharepoint-drive-id": "drive-1", "sharepoint-site-id": "site-1"})
    result = await nt.sharepoint_target("tenant-1")
    assert result == {
        "site_id": "site-1",
        "drive_id": "drive-1",
        "folder": nt.DEFAULT_SHAREPOINT_FOLDER,
    }


@pytest.mark.unit
async def test_confluence_target_none_when_unconfigured(monkeypatch):
    _patch_store(monkeypatch, {})
    assert await nt.confluence_target("tenant-1") is None


@pytest.mark.unit
async def test_confluence_target_returns_configured_space(monkeypatch):
    _patch_store(monkeypatch, {"confluence-space-key": "ENG"})
    result = await nt.confluence_target("tenant-1")
    assert result == {"space": "ENG"}


@pytest.mark.unit
async def test_figma_target_none_when_unconfigured(monkeypatch):
    _patch_store(monkeypatch, {})
    assert await nt.figma_target("tenant-1") is None


@pytest.mark.unit
async def test_disconnected_marker_reads_as_unconfigured(monkeypatch):
    """A tombstoned ref must read as absent, not as a literal channel/space value."""
    _patch_store(monkeypatch, {"confluence-space-key": "__disconnected__"})
    assert await nt.confluence_target("tenant-1") is None


@pytest.mark.unit
async def test_empty_tenant_id_short_circuits_without_raising():
    assert await nt.teams_target("") is None
    assert await nt.sharepoint_target("") is None
    assert await nt.confluence_target("") is None
