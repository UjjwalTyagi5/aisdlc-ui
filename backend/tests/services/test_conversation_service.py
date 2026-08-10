"""DB-backed tests for conversation_service (F2 §11A).

Requires live Postgres on :5433 with migrations at head (0028_conversations).
Uses deterministic UUIDs so cleanup is precise and never races with other suites.
Skip gracefully when no DB connection is available.

All async tests share the session-scoped event loop from conftest.py — this is
required to prevent "Event loop is closed" errors when the module-level
SQLAlchemy engine (shared.db.engine) is reused across multiple test functions.
"""
from __future__ import annotations

import uuid

import pytest

# Use the resolved connection string (may come from Key Vault, not just the env var).
# The raw POSTGRES_CONN_STRING env var may be empty when KV supplies the URL.
try:
    from shared.db import RESOLVED_POSTGRES_CONN_STRING as _CONN
    _DB_AVAILABLE = bool(_CONN) and "placeholder" not in _CONN
except Exception:
    _DB_AVAILABLE = False

# Deterministic tenant for these tests — chosen in the 0003-xxxx range to avoid
# overlap with RLS isolation tests (0001/0002) and E2E tests (e2e0-*).
_TENANT = "00000000-0000-0000-0003-000000000001"
_SCOPE_PREFIX = "conv-svc-test"

_skip_no_db = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason="No DB connection available — skipping DB-dependent conversation_service tests",
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture(autouse=True)
async def _cleanup():
    """Delete test rows created by this module after each test (fresh slate).

    Messages deleted before sessions (FK constraint). Scoped to _SCOPE_PREFIX
    to avoid touching rows from other test suites.
    """
    from sqlalchemy import delete

    from shared.db import get_db_session_for_tenant
    from shared.models.orm import ConversationMessage, ConversationSession

    yield  # ---- test runs here ----

    async with get_db_session_for_tenant(_TENANT) as session:
        session_rows = (
            await session.execute(
                ConversationSession.__table__.select().where(
                    ConversationSession.scope_id.like(f"{_SCOPE_PREFIX}%")
                )
            )
        ).fetchall()
        session_ids = [r[0] for r in session_rows]
        if session_ids:
            await session.execute(
                delete(ConversationMessage).where(
                    ConversationMessage.session_id.in_(session_ids)
                )
            )
        await session.execute(
            delete(ConversationSession).where(
                ConversationSession.scope_id.like(f"{_SCOPE_PREFIX}%")
            )
        )


# ---------------------------------------------------------------------------
# Test 1: create_session is idempotent — same scope -> same id
# ---------------------------------------------------------------------------
@_skip_no_db
async def test_create_session_idempotent():
    from shared.services.conversation_service import create_session

    scope_id = f"{_SCOPE_PREFIX}-idem-{uuid.uuid4().hex[:8]}"
    id1 = await create_session(_TENANT, "run", scope_id)
    id2 = await create_session(_TENANT, "run", scope_id)  # same scope

    assert id1 == id2, f"Expected same id for same scope, got {id1} vs {id2}"


# ---------------------------------------------------------------------------
# Test 2: append_message increments seq (1, 2, 3) and get_transcript returns ordered
# ---------------------------------------------------------------------------
@_skip_no_db
async def test_append_message_seq_and_transcript_order():
    from shared.services.conversation_service import (
        append_message,
        create_session,
        get_transcript,
    )

    scope_id = f"{_SCOPE_PREFIX}-seq-{uuid.uuid4().hex[:8]}"
    session_id = await create_session(_TENANT, "run", scope_id)

    mid1 = await append_message(session_id, "user", "Hello", tenant_id=_TENANT)
    mid2 = await append_message(session_id, "agent", "Hi there", tenant_id=_TENANT)
    mid3 = await append_message(session_id, "user", "Thanks", tenant_id=_TENANT)

    transcript = await get_transcript(session_id, tenant_id=_TENANT)

    assert len(transcript) == 3, f"Expected 3 messages, got {len(transcript)}"
    assert [m["seq"] for m in transcript] == [1, 2, 3]
    assert [m["id"] for m in transcript] == [mid1, mid2, mid3]
    assert transcript[0]["role"] == "user"
    assert transcript[1]["role"] == "agent"
    assert transcript[2]["content"] == "Thanks"


# ---------------------------------------------------------------------------
# Test 3: append_message with repeated dedup_key does NOT duplicate
# ---------------------------------------------------------------------------
@_skip_no_db
async def test_append_message_dedup_key_no_duplicate():
    from shared.services.conversation_service import (
        append_message,
        create_session,
        get_transcript,
    )

    scope_id = f"{_SCOPE_PREFIX}-dedup-{uuid.uuid4().hex[:8]}"
    session_id = await create_session(_TENANT, "run", scope_id)

    dedup = f"dedup-key-{uuid.uuid4().hex}"
    id1 = await append_message(session_id, "agent", "First write", tenant_id=_TENANT, dedup_key=dedup)
    id2 = await append_message(session_id, "agent", "Second write (retry)", tenant_id=_TENANT, dedup_key=dedup)

    assert id1 == id2, f"Expected same id for duplicate dedup_key, got {id1} vs {id2}"
    transcript = await get_transcript(session_id, tenant_id=_TENANT)
    assert len(transcript) == 1, f"Expected 1 message (no duplicate), got {len(transcript)}"
    assert transcript[0]["content"] == "First write"


# ---------------------------------------------------------------------------
# Test 4: get_transcript(after_seq=N) returns only messages with seq > N
# ---------------------------------------------------------------------------
@_skip_no_db
async def test_get_transcript_after_seq():
    from shared.services.conversation_service import (
        append_message,
        create_session,
        get_transcript,
    )

    scope_id = f"{_SCOPE_PREFIX}-after-{uuid.uuid4().hex[:8]}"
    session_id = await create_session(_TENANT, "run", scope_id)

    await append_message(session_id, "user", "msg1", tenant_id=_TENANT)
    await append_message(session_id, "agent", "msg2", tenant_id=_TENANT)
    await append_message(session_id, "user", "msg3", tenant_id=_TENANT)

    # after_seq=2 should return only msg3 (seq=3)
    later = await get_transcript(session_id, tenant_id=_TENANT, after_seq=2)
    assert len(later) == 1, f"Expected 1 message after seq=2, got {len(later)}"
    assert later[0]["seq"] == 3
    assert later[0]["content"] == "msg3"


# ---------------------------------------------------------------------------
# Test 5: close_session sets status to "closed"
# ---------------------------------------------------------------------------
@_skip_no_db
async def test_close_session():
    from sqlalchemy import select

    from shared.db import get_db_session_for_tenant
    from shared.models.orm import ConversationSession
    from shared.services.conversation_service import close_session, create_session

    scope_id = f"{_SCOPE_PREFIX}-close-{uuid.uuid4().hex[:8]}"
    session_id = await create_session(_TENANT, "run", scope_id)

    await close_session(session_id, tenant_id=_TENANT)

    # Read back directly to confirm status
    async with get_db_session_for_tenant(_TENANT) as db:
        obj = (
            await db.execute(
                select(ConversationSession).where(
                    ConversationSession.id == uuid.UUID(session_id)
                )
            )
        ).scalar_one_or_none()

    assert obj is not None
    assert obj.status == "closed", f"Expected status='closed', got '{obj.status}'"