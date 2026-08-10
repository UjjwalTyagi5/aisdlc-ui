"""M9.3 Plan 02 — EvalRecordService.emit + write_and_notify eval hook (REQ-M9-11).

All tests are offline/mocked — no live database connection (mirrors
tests/audit/test_dead_letter.py mocking style).

Covers:
  - test_emit_writes_one_record: emit() results in exactly one EvalRecord insert
    via get_db_session_for_tenant(tenant_id), carrying tenant_id/run_id/agent_type/
    score/signals.
  - test_emit_never_raises: emit() swallows a DB write failure and never propagates
    (T-9.3-05).
  - test_emit_is_nonblocking: emit() returns before the write task completes
    (asyncio.create_task + sleep(0), same shape as AuditEventService.emit).
  - test_write_and_notify_emits_eval: write_and_notify(...) triggers exactly one
    eval emit with the correct agent_type/run_id, for all 5 agent_type values.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


_TENANT_ID = "00000000-0000-0000-0000-000000000001"


class _FakeSession:
    """Minimal async session stub — records added ORM objects."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)


@asynccontextmanager
async def _fake_session_ctx(session: _FakeSession):
    yield session


# ---------------------------------------------------------------------------
# EvalRecordService.emit
# ---------------------------------------------------------------------------


async def test_emit_writes_one_record():
    from shared.eval.service import EvalRecordService

    svc = EvalRecordService()
    fake_session = _FakeSession()

    with patch(
        "shared.eval.service.get_db_session_for_tenant",
        return_value=_fake_session_ctx(fake_session),
    ):
        await svc.emit(
            tenant_id=_TENANT_ID,
            run_id="run-001",
            agent_type="requirements",
            score=0.8,
            signals={"strategy": "token_set_overlap"},
        )
        await asyncio.sleep(0)

    assert len(fake_session.added) == 1
    record = fake_session.added[0]
    assert str(record.tenant_id) == _TENANT_ID
    assert record.run_id == "run-001"
    assert record.agent_type == "requirements"
    assert float(record.score) == 0.8
    assert record.signals == {"strategy": "token_set_overlap"}


async def test_emit_never_raises():
    from shared.eval.service import EvalRecordService

    svc = EvalRecordService()
    svc._write = AsyncMock(side_effect=Exception("DB connection refused"))

    # Must not raise
    await svc.emit(
        tenant_id=_TENANT_ID,
        run_id="run-002",
        agent_type="design",
        score=0.5,
        signals={},
    )
    await asyncio.sleep(0)

    svc._write.assert_called_once()


async def test_emit_is_nonblocking():
    from shared.eval.service import EvalRecordService

    svc = EvalRecordService()

    write_started = asyncio.Event()
    write_can_finish = asyncio.Event()

    async def _slow_write(**kwargs):
        write_started.set()
        await write_can_finish.wait()

    svc._write = AsyncMock(side_effect=_slow_write)

    emit_coro = svc.emit(
        tenant_id=_TENANT_ID,
        run_id="run-003",
        agent_type="testing",
        score=0.9,
        signals={},
    )
    # emit() returns after sleep(0) — before _write completes.
    await asyncio.wait_for(emit_coro, timeout=1)

    assert write_started.is_set()
    write_can_finish.set()
    await asyncio.sleep(0)


async def test_emit_missing_tenant_id_skips_write():
    from shared.eval.service import EvalRecordService

    svc = EvalRecordService()
    svc._write = AsyncMock()

    # Must not raise even with no tenant_id.
    await svc.emit(tenant_id=None, run_id="run-004", agent_type="development", score=0.1, signals={})
    await asyncio.sleep(0)

    svc._write.assert_not_called()


# ---------------------------------------------------------------------------
# write_and_notify eval hook (_base.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_type,artifact_data",
    [
        ("requirements", {"agent_session_id": "s1", "brd_content": "Some BRD content", "version": 1}),
        ("design", {"hld": "## High-Level Design (HLD)\ncontent", "version": 1}),
        ("development", {"repo_url": "https://example.invalid/repo", "code_summary": "summary", "version": 1}),
        ("testing", {"test_plan": "plan text", "version": 1}),
        ("deployment", {"summary": "deployment summary", "version": 1}),
    ],
)
async def test_write_and_notify_emits_eval(agent_type, artifact_data):
    from workflows.activities import _base

    run_id = "run-" + agent_type
    tenant_id = _TENANT_ID

    with patch.object(_base, "_persist_and_notify", new=AsyncMock()) as mock_persist, \
            patch.object(_base.eval_service, "emit", new=AsyncMock()) as mock_emit:
        await _base.write_and_notify(run_id, agent_type, artifact_data, tenant_id=tenant_id)

    mock_persist.assert_called_once_with(run_id, agent_type, artifact_data, tenant_id=tenant_id)
    mock_emit.assert_called_once()
    _, kwargs = mock_emit.call_args
    assert kwargs["run_id"] == run_id
    assert kwargs["agent_type"] == agent_type
    assert kwargs["tenant_id"] == tenant_id
    assert kwargs["artifact_data"] == artifact_data
