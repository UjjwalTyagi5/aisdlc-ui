"""Unit tests for shared/cost/service.py CostLogService (REQ-M9-06)."""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.cost.service import CostLogService

TENANT_ID = "00000000-0000-0000-0000-000000000001"


def _make_session_cm(session: MagicMock):
    @asynccontextmanager
    async def _cm(tenant_id: str):
        yield session

    return _cm


@pytest.mark.unit
async def test_emit_writes_one_agent_call_log(monkeypatch):
    session = MagicMock()
    session.add = MagicMock()

    monkeypatch.setattr(
        "shared.cost.service.get_db_session_for_tenant", _make_session_cm(session)
    )

    svc = CostLogService()
    await svc.emit(
        tenant_id=TENANT_ID,
        run_id="run-123",
        agent_type="requirements",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        duration_ms=250,
    )
    await asyncio.sleep(0)

    session.add.assert_called_once()
    row = session.add.call_args[0][0]
    assert row.tenant_id == uuid.UUID(TENANT_ID)
    assert row.run_id == "run-123"
    assert row.agent_type == "requirements"
    assert row.model == "claude-sonnet-4-6"
    assert row.input_tokens == 1000
    assert row.output_tokens == 500
    assert row.duration_ms == 250
    assert isinstance(row.cost_usd, Decimal)
    assert row.cost_usd > Decimal("0")


@pytest.mark.unit
async def test_emit_skips_empty_tenant_id(monkeypatch):
    session = MagicMock()
    session.add = MagicMock()

    monkeypatch.setattr(
        "shared.cost.service.get_db_session_for_tenant", _make_session_cm(session)
    )

    svc = CostLogService()
    await svc.emit(
        tenant_id="",
        run_id="run-123",
        agent_type="requirements",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        duration_ms=250,
    )
    await asyncio.sleep(0)

    session.add.assert_not_called()


@pytest.mark.unit
async def test_emit_skips_none_tenant_id(monkeypatch):
    session = MagicMock()
    session.add = MagicMock()

    monkeypatch.setattr(
        "shared.cost.service.get_db_session_for_tenant", _make_session_cm(session)
    )

    svc = CostLogService()
    await svc.emit(
        tenant_id=None,
        run_id="run-123",
        agent_type="requirements",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        duration_ms=250,
    )
    await asyncio.sleep(0)

    session.add.assert_not_called()


@pytest.mark.unit
async def test_emit_never_raises_on_session_error(monkeypatch):
    @asynccontextmanager
    async def _raising_cm(tenant_id: str):
        raise RuntimeError("db down")
        yield  # pragma: no cover

    monkeypatch.setattr(
        "shared.cost.service.get_db_session_for_tenant", _raising_cm
    )

    svc = CostLogService()
    # Must not raise.
    await svc.emit(
        tenant_id=TENANT_ID,
        run_id="run-123",
        agent_type="requirements",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        duration_ms=250,
    )
    await asyncio.sleep(0)


@pytest.mark.unit
def test_singleton_exported():
    from shared.cost.service import cost_service

    assert isinstance(cost_service, CostLogService)
