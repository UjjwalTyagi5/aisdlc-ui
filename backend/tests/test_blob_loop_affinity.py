"""The blob client survives the agents' sub-loops.

LIVE (2026-09-18, the day the storage account came back): every document an agent wrote
logged "Artifact upload failed for run ... RuntimeError" and then "page copy unreadable
... ResourceNotFoundError". The RuntimeError was "Task ... got Future ... attached to a
different loop": the SDK's aiohttp session belongs to the loop that opened it, and the
agents run their tools inside a fresh `asyncio.run()` loop — the same trap `shared/db.py`
answers with NullPool. Local disk storage has no loops, which is why three days on the
local backend hid it.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.storage import azure_blob  # noqa: E402


class _FakeBlob:
    def __init__(self, service, name):
        self._service, self.url = service, f"https://acct/{name}"

    async def upload_blob(self, *_a, **_kw):
        # Like aiohttp: the session is opened on first use and belongs to that loop
        # from then on. Used from another loop, it raises.
        self._service.bind()
        self._service.uploads += 1


class _FakeContainer:
    def __init__(self, service):
        self._service = service

    def get_blob_client(self, name):
        return _FakeBlob(self._service, name)


class _FakeService:
    """Holds a session bound to the loop of its FIRST use, as the real client does."""

    made: list["_FakeService"] = []

    def __init__(self, account_url=None, credential=None):
        self.account_url, self.closed, self.uploads = account_url, False, 0
        self.loop: asyncio.AbstractEventLoop | None = None
        _FakeService.made.append(self)

    def bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self.loop is None:
            self.loop = loop
        elif self.loop is not loop:
            raise RuntimeError("got Future attached to a different loop")

    def get_container_client(self, _name):
        return _FakeContainer(self)

    def get_blob_client(self, container=None, blob=None):  # noqa: ARG002 — get_url only
        return _FakeBlob(self, blob)

    async def close(self):
        self.closed = True


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(azure_blob, "BlobServiceClient", _FakeService)
    monkeypatch.setattr(azure_blob, "get_azure_credential", lambda: object())
    _FakeService.made = []
    return azure_blob.BlobStorageClient(account_url="https://acct", container="sdlc-artifacts")


def test_an_upload_from_an_agents_sub_loop_does_not_fail_and_leaves_no_open_session(client):
    """The shape that broke live: uvicorn's loop is still running when a tool's own loop uploads."""
    app_loop, agent_loop = asyncio.new_event_loop(), asyncio.new_event_loop()
    try:
        app_loop.run_until_complete(client.upload_bytes(b"x", "t/a.docx"))   # claims the client
        home = client._client
        assert home.uploads == 1

        agent_loop.run_until_complete(client.upload_bytes(b"x", "t/b.docx"))

        borrowed = [s for s in _FakeService.made if s is not home]
        assert borrowed, "the foreign loop must not reuse the client bound to another loop"
        assert all(s.closed for s in borrowed), "a borrowed client must be closed with the call"
        assert home.uploads == 1, "the home client must not be touched from the other loop"
        assert client._client is home, "the app's loop keeps its client"
    finally:
        app_loop.close()
        agent_loop.close()


def test_a_client_whose_loop_has_closed_is_replaced_rather_than_reused(client):
    asyncio.run(client.upload_bytes(b"x", "t/a.docx"))
    dead = client._client
    assert dead.loop.is_closed()

    asyncio.run(client.upload_bytes(b"x", "t/b.docx"))
    assert client._client is not dead, "the client of a closed loop is replaced, not reused"


def test_every_call_on_one_loop_shares_the_one_client(client):
    async def several():
        await client.upload_bytes(b"x", "t/a.docx")
        await client.upload_bytes(b"x", "t/b.docx")
        await client.upload_bytes(b"x", "t/c.docx")

    asyncio.run(several())
    assert len(_FakeService.made) == 1 and _FakeService.made[0].uploads == 3


def test_the_lifespan_hands_the_agents_path_the_client_it_built():
    """Otherwise the agents build their own, inside a graph node's throwaway loop."""
    from shared.services import artifact_store

    before, tried = artifact_store._process_blob_client, artifact_store._blob_client_tried
    try:
        sentinel = object()
        artifact_store.set_process_blob_client(sentinel)
        assert artifact_store.get_blob_client() is sentinel
    finally:
        artifact_store._process_blob_client, artifact_store._blob_client_tried = before, tried

    lifespan = (Path(__file__).resolve().parents[1] / "process_api.py").read_text(encoding="utf-8")
    assert "set_process_blob_client(app.state.blob_client)" in lifespan
