"""In-process Postgres store for agent-session artifacts and orchestrator state.

Replaces the legacy Django HTTP endpoints (/api/agent_session/*, /orchestrator/state/*).
All access is async SQLAlchemy via shared.db — one fewer network hop than the old
httpx-to-Django path, and no Django process dependency.

Persistence is best-effort on the agent hot path: write failures are logged, not raised
(callers historically swallowed Django errors). Reads return None on miss/failure so
callers can treat the result as "no artifacts yet".
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import select

from shared.db import get_db_session_for_tenant, get_db_session_superuser
from shared.models.orm import AgentSession, OrchestratorState

logger = logging.getLogger(__name__)

# Strict superset of Django's _PATCHABLE_ARTIFACT_FIELDS (which dropped the last 3).
PATCHABLE_ARTIFACT_FIELDS: frozenset[str] = frozenset({
    "requirements_payload",
    "design_artifacts",
    "development_artifacts",
    "testing_artifacts",
    "code_review_artifacts",
    "security_artifacts",
    "deployment_artifacts",
    "documentation_artifacts",
    "last_handoff_event",
    "current_stage",
    "artifact_version",
})

_ARTIFACT_READ_FIELDS = (
    "requirements_payload", "design_artifacts", "development_artifacts",
    "testing_artifacts", "code_review_artifacts", "security_artifacts",
    "deployment_artifacts", "documentation_artifacts", "last_handoff_event",
    "artifact_version", "current_stage",
)


def coerce_json(value: Any) -> Any:
    """Coerce legacy stringified-JSON artifacts to dict (Django stored some as strings)."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


def session_artifact_dict(obj: AgentSession) -> dict:
    """The read-shape the old Django GET returned, with status=ok added by callers."""
    out = {
        "session_id": obj.session_id,
        "agent_type": obj.agent_type,
        "user_id": obj.user_id,
        "requirements_payload": coerce_json(obj.requirements_payload),
    }
    for f in _ARTIFACT_READ_FIELDS:
        if f == "requirements_payload":
            continue
        out[f] = getattr(obj, f)
    return out


def _ctx(tenant_id: Optional[str]):
    return get_db_session_for_tenant(tenant_id) if tenant_id else get_db_session_superuser()


async def fetch_session_artifacts(session_id: str, *, tenant_id: Optional[str] = None) -> Optional[dict]:
    try:
        async with _ctx(tenant_id) as session:
            obj = (await session.execute(
                select(AgentSession).where(AgentSession.session_id == session_id)
            )).scalar_one_or_none()
            if obj is None:
                return None
            return {"status": "ok", **session_artifact_dict(obj)}
    except Exception as exc:  # read never raises into the agent path
        logger.warning("fetch_session_artifacts(%s) failed: %s", session_id, exc)
        return None


async def patch_session_artifacts(session_id: str, fields: dict, *, tenant_id: Optional[str] = None) -> None:
    updates = {}
    for k, v in (fields or {}).items():
        if k not in PATCHABLE_ARTIFACT_FIELDS:
            continue
        updates[k] = coerce_json(v) if k == "requirements_payload" else v
    if not updates:
        return
    try:
        async with _ctx(tenant_id) as session:
            obj = (await session.execute(
                select(AgentSession).where(AgentSession.session_id == session_id)
            )).scalar_one_or_none()
            if obj is None:
                obj = AgentSession(session_id=session_id, tenant_id=tenant_id)
                session.add(obj)
            for k, v in updates.items():
                setattr(obj, k, v)
    except Exception as exc:  # best-effort — never break the agent
        logger.warning("patch_session_artifacts(%s) failed: %s", session_id, exc)


async def upsert_agent_session(
    session_id: str, *, agent_type: Optional[str] = None, user_id: Optional[str] = None,
    current_stage: Optional[str] = None, tenant_id: Optional[str] = None, **artifact_fields: Any,
) -> None:
    fields = {k: v for k, v in artifact_fields.items() if k in PATCHABLE_ARTIFACT_FIELDS}
    if current_stage is not None:
        fields["current_stage"] = current_stage
    try:
        async with _ctx(tenant_id) as session:
            obj = (await session.execute(
                select(AgentSession).where(AgentSession.session_id == session_id)
            )).scalar_one_or_none()
            if obj is None:
                obj = AgentSession(session_id=session_id, tenant_id=tenant_id)
                session.add(obj)
            if agent_type is not None:
                obj.agent_type = agent_type
            if user_id is not None:
                obj.user_id = user_id
            for k, v in fields.items():
                setattr(obj, k, coerce_json(v) if k == "requirements_payload" else v)
    except Exception as exc:
        logger.warning("upsert_agent_session(%s) failed: %s", session_id, exc)


async def clear_session_artifacts(session_id: str, *, tenant_id: Optional[str] = None) -> None:
    await patch_session_artifacts(session_id, {
        "requirements_payload": None, "design_artifacts": None, "development_artifacts": None,
        "testing_artifacts": None, "code_review_artifacts": None, "security_artifacts": None,
        "deployment_artifacts": None, "last_handoff_event": None, "current_stage": "",
    }, tenant_id=tenant_id)


async def get_orchestrator_state(chat_session_id: str) -> Optional[dict]:
    try:
        async with get_db_session_superuser() as session:
            obj = (await session.execute(
                select(OrchestratorState).where(OrchestratorState.chat_session_id == chat_session_id)
            )).scalar_one_or_none()
            if obj is None:
                return None
            return {
                "chat_session_id": obj.chat_session_id,
                "current_active_agent": obj.current_active_agent,
                "current_batch_id": obj.current_batch_id,
                "pending_user_gate": obj.pending_user_gate,
                "last_handoff_event": obj.last_handoff_event,
            }
    except Exception as exc:
        logger.warning("get_orchestrator_state(%s) failed: %s", chat_session_id, exc)
        return None


async def set_orchestrator_state(
    chat_session_id: str, *, current_active_agent: Optional[str] = None,
    current_batch_id: Optional[str] = None, pending_user_gate: Optional[str] = None,
    last_handoff_event: Optional[dict] = None,
) -> None:
    try:
        async with get_db_session_superuser() as session:
            obj = (await session.execute(
                select(OrchestratorState).where(OrchestratorState.chat_session_id == chat_session_id)
            )).scalar_one_or_none()
            if obj is None:
                obj = OrchestratorState(chat_session_id=chat_session_id)
                session.add(obj)
            if current_active_agent is not None:
                obj.current_active_agent = current_active_agent
            if current_batch_id is not None:
                obj.current_batch_id = current_batch_id
            if pending_user_gate is not None:
                obj.pending_user_gate = pending_user_gate
            if last_handoff_event is not None:
                obj.last_handoff_event = last_handoff_event
    except Exception as exc:
        logger.warning("set_orchestrator_state(%s) failed: %s", chat_session_id, exc)


async def clear_orchestrator_state(chat_session_id: str) -> None:
    try:
        async with get_db_session_superuser() as session:
            obj = (await session.execute(
                select(OrchestratorState).where(OrchestratorState.chat_session_id == chat_session_id)
            )).scalar_one_or_none()
            if obj is not None:
                await session.delete(obj)
    except Exception as exc:
        logger.warning("clear_orchestrator_state(%s) failed: %s", chat_session_id, exc)
