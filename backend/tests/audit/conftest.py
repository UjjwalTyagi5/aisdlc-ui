"""Shared fixtures for milestone-8 audit test package.

Provides:
  mock_audit_service  — AsyncMock with emit() and emit_blocking() methods
  mock_redis_client   — AsyncMock Redis client (xadd, xread, set, get)
  mock_blob_client    — AsyncMock BlobStorageClient (upload_bytes returning a blob URL)

mint_token is inherited from the parent tests/conftest.py — pytest auto-discovers
parent conftest.py files, so no explicit re-export is needed.

All fixtures are function-scoped so each test starts with a clean mock state.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_audit_service():
    """Mock AuditEventService with async emit() and emit_blocking() methods.

    Pre-configured so:
      - emit() is a no-op coroutine (fire-and-forget path; does not raise)
      - emit_blocking() is a no-op coroutine (HITL blocking path; does not raise)

    Tests that assert failure paths should side_effect these to raise.
    """
    svc = MagicMock()
    svc.emit = AsyncMock(return_value=None)
    svc.emit_blocking = AsyncMock(return_value=None)
    svc._write = AsyncMock(return_value=None)
    svc._send_to_dead_letter = AsyncMock(return_value=None)
    # Expose _redact_pii as a real callable for unit tests that need to exercise it.
    # Tests importing the real service for _redact_pii should do so directly.
    return svc


@pytest.fixture
def mock_redis_client():
    """Mock async Redis client exposing the subset of methods used by AuditEventService.

    Methods:
      xadd(stream, fields)     — dead-letter write path
      xread(streams, count)    — read back from stream (assertion helper)
      xdel(stream, msg_id)     — retry worker removing a successfully re-emitted entry
      set(key, value, **kw)    — job status store (evidence export worker)
      get(key)                 — job status read (status polling endpoint)
      aclose()                 — cleanup (awaited in service teardown)
    """
    client = MagicMock()
    client.xadd = AsyncMock(return_value=b"1-0")
    client.xread = AsyncMock(return_value=[])
    client.xdel = AsyncMock(return_value=1)
    client.set = AsyncMock(return_value=True)
    client.get = AsyncMock(return_value=None)
    client.aclose = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_blob_client():
    """Mock BlobStorageClient with upload_bytes() returning a deterministic blob URL.

    Returns a pre-signed-style URL so evidence export tests can assert the URL shape
    without hitting Azure.
    """
    client = MagicMock()
    client.upload_bytes = AsyncMock(
        return_value="https://storageaccount.blob.core.windows.net/evidence/test-run/test-job.zip"
    )
    client._container = "evidence"
    client._client = MagicMock()
    client._client.account_name = "storageaccount"
    return client
