"""Conversation / session store — F2 §11A chat transcript rail.

Manages human-facing chat transcripts (distinct from LangGraph checkpoints and
orchestrator_state). All writes are best-effort: failures are logged, not raised,
so agents never block on transcript persistence.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import func, select

from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.models.orm import ConversationMessage, ConversationSession

logger = logging.getLogger(__name__)


def _ctx(tenant_id: Optional[str]):
    return get_db_session_for_tenant(tenant_id) if tenant_id else get_db_session_superuser()


def _derive_title(content: str) -> str:
    """First user turn → a short rail label (single line, ~60 chars)."""
    line = " ".join((content or "").split())
    return (line[:60] + "…") if len(line) > 60 else (line or "New chat")


async def create_agent_session(
    tenant_id: str,
    agent_id: str,
    *,
    created_by: Optional[str] = None,
    project_id: Optional[uuid.UUID] = None,
    title: Optional[str] = None,
) -> str:
    """Create a fresh per-(user, agent, project) chat session and return its id.

    Non-idempotent: each call is a new chat thread (scope_type="agent",
    scope_id=<new uuid> so many sessions coexist under uq_conversation_scope). The
    returned id is reused by the caller as the LangGraph thread_id so the human
    transcript and the agent's execution checkpoint stay correlated (§11A.2).
    """
    async with _ctx(tenant_id) as session:
        new_session = ConversationSession(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            scope_type="agent",
            scope_id=str(uuid.uuid4()),
            agent_id=agent_id,
            title=title,
            project_id=project_id,
            created_by=created_by,
            status="active",
        )
        session.add(new_session)
        await session.flush()
        return str(new_session.id)


async def list_sessions(
    tenant_id: str,
    *,
    created_by: str,
    agent_id: str,
    project_id: Optional[uuid.UUID] = None,
    limit: int = 50,
) -> list[dict]:
    """Creator-scoped agent sessions, newest-first. Empty list on failure (read is best-effort)."""
    try:
        async with _ctx(tenant_id) as session:
            stmt = (
                select(ConversationSession)
                .where(
                    ConversationSession.created_by == created_by,
                    ConversationSession.agent_id == agent_id,
                    ConversationSession.scope_type == "agent",
                    ConversationSession.status == "active",
                )
                .order_by(ConversationSession.updated_at.desc())
                .limit(limit)
            )
            if project_id is not None:
                stmt = stmt.where(ConversationSession.project_id == project_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "id": str(s.id),
                    "title": s.title or "New chat",
                    "agent_id": s.agent_id,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                }
                for s in rows
            ]
    except Exception as exc:
        logger.warning("list_sessions(%s, %s) failed: %s", created_by, agent_id, exc)
        return []


async def rename_session(
    session_id: str, title: str, *, tenant_id: str, created_by: str
) -> bool:
    """Rename an owned session. Returns True when a row was updated."""
    try:
        async with _ctx(tenant_id) as session:
            obj = (
                await session.execute(
                    select(ConversationSession).where(
                        ConversationSession.id == uuid.UUID(session_id),
                        ConversationSession.created_by == created_by,
                    )
                )
            ).scalar_one_or_none()
            if obj is None:
                return False
            obj.title = title[:255]
            return True
    except Exception as exc:
        logger.warning("rename_session(%s) failed: %s", session_id, exc)
        return False


async def delete_session(session_id: str, *, tenant_id: str, created_by: str) -> bool:
    """Soft-delete an owned session (status='deleted' → filtered from lists)."""
    try:
        async with _ctx(tenant_id) as session:
            obj = (
                await session.execute(
                    select(ConversationSession).where(
                        ConversationSession.id == uuid.UUID(session_id),
                        ConversationSession.created_by == created_by,
                    )
                )
            ).scalar_one_or_none()
            if obj is None:
                return False
            obj.status = "deleted"
            return True
    except Exception as exc:
        logger.warning("delete_session(%s) failed: %s", session_id, exc)
        return False


async def session_owner(session_id: str, *, tenant_id: str) -> Optional[str]:
    """Return the created_by of a session (for router ownership checks). None if missing."""
    try:
        async with _ctx(tenant_id) as session:
            obj = (
                await session.execute(
                    select(ConversationSession).where(
                        ConversationSession.id == uuid.UUID(session_id)
                    )
                )
            ).scalar_one_or_none()
            return obj.created_by if obj is not None else None
    except Exception as exc:
        logger.warning("session_owner(%s) failed: %s", session_id, exc)
        return None


async def create_session(
    tenant_id: str,
    scope_type: str,
    scope_id: str,
    *,
    project_id: Optional[uuid.UUID] = None,
    run_id: Optional[str] = None,
    created_by: Optional[str] = None,
) -> str:
    """Return an existing ACTIVE session id for (tenant, scope_type, scope_id), or create one.

    Idempotent: repeated calls with the same (tenant_id, scope_type, scope_id) return
    the same session id as long as the session is still active. The UniqueConstraint
    uq_conversation_scope enforces one active session per scope at the DB level.
    """
    try:
        async with _ctx(tenant_id) as session:
            existing = (
                await session.execute(
                    select(ConversationSession).where(
                        ConversationSession.tenant_id == uuid.UUID(tenant_id),
                        ConversationSession.scope_type == scope_type,
                        ConversationSession.scope_id == scope_id,
                        ConversationSession.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return str(existing.id)
            new_session = ConversationSession(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(tenant_id),
                scope_type=scope_type,
                scope_id=scope_id,
                project_id=project_id,
                run_id=run_id,
                created_by=created_by,
                status="active",
            )
            session.add(new_session)
            await session.flush()
            return str(new_session.id)
    except Exception as exc:
        logger.warning("create_session(%s, %s, %s) failed: %s", tenant_id, scope_type, scope_id, exc)
        raise  # create_session is not best-effort — callers need the id to proceed


async def ensure_session_with_id(
    session_id: str,
    tenant_id: str,
    *,
    scope_type: str,
    scope_id: str,
    run_id: Optional[str] = None,
    project_id: Optional[uuid.UUID] = None,
    created_by: Optional[str] = None,
) -> None:
    """Idempotently ensure a ConversationSession exists with id == session_id.

    Unlike create_session (which mints its own uuid), this pins the row's primary key
    to a caller-chosen id — needed by the Copilot, whose session_id IS the run_id
    (graph thread, ws_helper, persist_turn all key off run_id). Without this row the
    conversation_messages FK has nothing to reference and every persist_turn fails.
    Best-effort: swallows all errors so a persistence miss never blocks the socket."""
    if not (session_id and tenant_id):
        return
    try:
        async with _ctx(tenant_id) as session:
            existing = (
                await session.execute(
                    select(ConversationSession).where(
                        ConversationSession.id == uuid.UUID(session_id)
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return
            session.add(ConversationSession(
                id=uuid.UUID(session_id),
                tenant_id=uuid.UUID(tenant_id),
                scope_type=scope_type,
                scope_id=scope_id,
                run_id=run_id,
                project_id=project_id,
                created_by=created_by,
                status="active",
            ))
            await session.flush()
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort for the WS
        logger.warning("ensure_session_with_id(%s) failed: %s", session_id, exc)


async def append_message(
    session_id: str,
    role: str,
    content: str,
    *,
    tenant_id: str,
    author_id: Optional[str] = None,
    content_type: str = "markdown",
    tool_calls: Optional[Any] = None,
    artifact_refs: Optional[Any] = None,
    citations: Optional[Any] = None,
    model: Optional[str] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    cost_usd: Optional[float] = None,
    dedup_key: Optional[str] = None,
) -> str:
    """Append a message to a session and return its id.

    If dedup_key is provided and a message with (session_id, dedup_key) already exists,
    return the existing message id without inserting a duplicate (idempotent on retries).
    seq is computed as max(seq)+1 for the session, starting at 1.
    Best-effort: logs and re-raises so callers know persistence failed.
    """
    try:
        async with _ctx(tenant_id) as session:
            session_uuid = uuid.UUID(session_id)

            # Dedup check — agent retries with the same dedup_key skip insertion
            if dedup_key is not None:
                existing = (
                    await session.execute(
                        select(ConversationMessage).where(
                            ConversationMessage.session_id == session_uuid,
                            ConversationMessage.dedup_key == dedup_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return str(existing.id)

            # Compute next seq (max + 1, defaults to 1 when session has no messages yet)
            result = await session.execute(
                select(func.max(ConversationMessage.seq)).where(
                    ConversationMessage.session_id == session_uuid
                )
            )
            max_seq = result.scalar() or 0
            next_seq = max_seq + 1

            msg = ConversationMessage(
                id=uuid.uuid4(),
                session_id=session_uuid,
                tenant_id=uuid.UUID(tenant_id),
                seq=next_seq,
                role=role,
                author_id=author_id,
                content=content,
                content_type=content_type,
                tool_calls=tool_calls,
                artifact_refs=artifact_refs,
                citations=citations,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
                dedup_key=dedup_key,
            )
            session.add(msg)

            # Touch the parent session so the rail orders by recency; set its title
            # from the first user turn when it has none yet.
            parent = (
                await session.execute(
                    select(ConversationSession).where(ConversationSession.id == session_uuid)
                )
            ).scalar_one_or_none()
            if parent is not None:
                parent.updated_at = func.now()
                if not parent.title and role == "user" and content:
                    parent.title = _derive_title(content)

            await session.flush()
            return str(msg.id)
    except Exception as exc:
        logger.warning("append_message(%s) failed: %s", session_id, exc)
        raise


async def persist_turn(
    session_id: Optional[str],
    role: str,
    content: Optional[str],
    *,
    tenant_id: Optional[str],
    author_id: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Best-effort single-turn write for the WS handlers — never raises, never blocks chat.

    No-ops when session_id/tenant_id/content are missing (e.g. a chat not yet bound
    to a persisted session). Swallows all errors (transcript persistence must never
    fail an otherwise-healthy turn).
    """
    if not (session_id and tenant_id and content):
        return
    try:
        await append_message(
            session_id, role, content, tenant_id=tenant_id, author_id=author_id, **kwargs
        )
    except Exception:
        pass


async def get_transcript(
    session_id: str,
    *,
    tenant_id: str,
    after_seq: int = 0,
) -> list[dict]:
    """Return messages for the session with seq > after_seq, ordered by seq ascending.

    Returns empty list on failure (read is best-effort — never blocks the UI path).
    """
    try:
        async with _ctx(tenant_id) as session:
            rows = (
                await session.execute(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.session_id == uuid.UUID(session_id),
                        ConversationMessage.seq > after_seq,
                    )
                    .order_by(ConversationMessage.seq)
                )
            ).scalars().all()
            return [
                {
                    "id": str(m.id),
                    "session_id": str(m.session_id),
                    "seq": m.seq,
                    "role": m.role,
                    "author_id": m.author_id,
                    "content": m.content,
                    "content_type": m.content_type,
                    "tool_calls": m.tool_calls,
                    "artifact_refs": m.artifact_refs,
                    "citations": m.citations,
                    "model": m.model,
                    "tokens_in": m.tokens_in,
                    "tokens_out": m.tokens_out,
                    "cost_usd": float(m.cost_usd) if m.cost_usd is not None else None,
                    "dedup_key": m.dedup_key,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in rows
            ]
    except Exception as exc:
        logger.warning("get_transcript(%s) failed: %s", session_id, exc)
        return []


async def close_session(session_id: str, *, tenant_id: str) -> None:
    """Set session status to 'closed'. Best-effort — logs on failure."""
    try:
        async with _ctx(tenant_id) as session:
            obj = (
                await session.execute(
                    select(ConversationSession).where(
                        ConversationSession.id == uuid.UUID(session_id)
                    )
                )
            ).scalar_one_or_none()
            if obj is not None:
                obj.status = "closed"
    except Exception as exc:
        logger.warning("close_session(%s) failed: %s", session_id, exc)