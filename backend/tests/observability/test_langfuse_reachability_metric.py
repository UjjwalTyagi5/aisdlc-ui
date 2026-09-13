"""A Langfuse outage must be visible on /metrics, not only in the logs.

Every Langfuse read degrades to None, and the endpoints above it report that as zero
spend and an empty trace list behind HTTP 200. The only signal was a `logger.warning`,
and the 2026-09 ingestion failure duly ran for five days before anyone noticed. These
tests pin the gauge that makes it alertable.
"""
from __future__ import annotations

import httpx
import pytest

from shared.services.metrics import (
    LANGFUSE_API_UP,
    note_langfuse_read,
)


@pytest.mark.unit
def test_failed_read_drops_the_gauge():
    note_langfuse_read(True)
    assert LANGFUSE_API_UP._value.get() == 1
    note_langfuse_read(False, "http_error")
    assert LANGFUSE_API_UP._value.get() == 0


@pytest.mark.unit
def test_recovery_raises_the_gauge_again():
    note_langfuse_read(False, "http_error")
    note_langfuse_read(True)
    assert LANGFUSE_API_UP._value.get() == 1


@pytest.mark.unit
def test_note_never_raises():
    """A metrics failure must not turn a degraded read into a broken endpoint."""
    note_langfuse_read(False, None)  # type: ignore[arg-type]
    note_langfuse_read(True, "")


@pytest.mark.unit
async def test_lf_get_marks_the_gauge_down_on_transport_failure(monkeypatch):
    """The wiring, not just the helper: an unreachable host must move the gauge."""
    import shared.routers.traces as _traces

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(_traces.httpx, "AsyncClient", lambda **kw: _Boom())
    # Defeat the response cache so this actually performs a read.
    _traces._LF_CACHE.clear()

    note_langfuse_read(True)
    out = await _traces._lf_get("/api/public/metrics", {"query": "{}"},
                                host="https://lf.invalid",
                                public_key="pk-x", secret_key="sk-x")

    assert out is None  # still degrades rather than raising
    assert LANGFUSE_API_UP._value.get() == 0  # ...and says so
