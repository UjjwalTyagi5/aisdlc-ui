"""Conversation/session REST API — the per-user, per-agent chat history rail (§11A).

Every route is creator-scoped to request.state.user_id (the JWT sub) and tenant-scoped
via FORCE-RLS in conversation_service._ctx. A user only ever sees their own sessions.
Mounted behind _VIEW_DEP in process_api.py.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from shared.services import conversation_service as cs
from shared.services.attachment_store import (
    AttachmentError,
    list_attachments,
    save_attachment,
)

logger = logging.getLogger(__name__)

conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


def _ctx_ids(request: Request) -> tuple[str, str]:
    """(tenant_id, user_id) from the authenticated request; 403 if either is missing."""
    tid = getattr(request.state, "tenant_id", "") or ""
    uid = getattr(request.state, "user_id", "") or ""
    if not tid or not uid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return tid, uid


class SessionOut(BaseModel):
    id: str
    title: str
    agent_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CreateSessionIn(BaseModel):
    agent_id: str
    project_id: Optional[str] = None
    title: Optional[str] = None


class RenameIn(BaseModel):
    title: str


class MessageOut(BaseModel):
    id: str
    seq: int
    role: str
    content: str
    content_type: str
    created_at: Optional[str] = None
    artifact_refs: Optional[list] = None


@conversations_router.get("", response_model=list[SessionOut])
async def list_conversations(
    request: Request, agent_id: str, project_id: Optional[str] = None
) -> list[SessionOut]:
    """The caller's agent sessions, newest-first (blank when none)."""
    tid, uid = _ctx_ids(request)
    pid = _uuid.UUID(project_id) if project_id else None
    rows = await cs.list_sessions(tid, created_by=uid, agent_id=agent_id, project_id=pid)
    return [SessionOut(**r) for r in rows]


@conversations_router.post("", response_model=SessionOut)
async def create_conversation(request: Request, body: CreateSessionIn) -> SessionOut:
    """Start a fresh chat session; the returned id is reused as the LangGraph thread_id."""
    tid, uid = _ctx_ids(request)
    pid = _uuid.UUID(body.project_id) if body.project_id else None
    sid = await cs.create_agent_session(
        tid, body.agent_id, created_by=uid, project_id=pid, title=body.title
    )
    return SessionOut(id=sid, title=body.title or "New chat", agent_id=body.agent_id)


@conversations_router.get("/{session_id}/messages", response_model=list[MessageOut])
async def get_messages(request: Request, session_id: str) -> list[MessageOut]:
    """Transcript for an owned session (ascending seq)."""
    tid, uid = _ctx_ids(request)
    owner = await cs.session_owner(session_id, tenant_id=tid)
    if owner is None:
        raise HTTPException(status_code=404, detail="Not found")
    if owner != uid:
        raise HTTPException(status_code=403, detail="Forbidden")
    msgs = await cs.get_transcript(session_id, tenant_id=tid)
    return [
        MessageOut(
            id=m["id"],
            seq=m["seq"],
            role=m["role"],
            content=m["content"],
            content_type=m["content_type"],
            created_at=m["created_at"],
            artifact_refs=m.get("artifact_refs"),
        )
        for m in msgs
    ]


@conversations_router.post("/{session_id}/attachments")
async def upload_attachments(
    request: Request,
    session_id: str,
    files: List[UploadFile] = File(...),
) -> dict:
    """Store chat attachments under files/{user}/attachments/{session}/ and return refs."""
    tid, uid = _ctx_ids(request)
    owner = await cs.session_owner(session_id, tenant_id=tid)
    if owner is None:
        raise HTTPException(status_code=404, detail="Not found")
    if owner != uid:
        raise HTTPException(status_code=403, detail="Forbidden")
    refs = []
    for f in files:
        data = await f.read()
        try:
            refs.append(save_attachment(uid, session_id, f.filename or "upload", data))
        except AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"attachments": refs}


@conversations_router.get("/{session_id}/attachments")
async def get_attachments(request: Request, session_id: str) -> dict:
    """List a session's stored attachments (download view on session reopen)."""
    tid, uid = _ctx_ids(request)
    owner = await cs.session_owner(session_id, tenant_id=tid)
    if owner is None:
        raise HTTPException(status_code=404, detail="Not found")
    if owner != uid:
        raise HTTPException(status_code=403, detail="Forbidden")
    return {"attachments": list_attachments(uid, session_id)}


@conversations_router.patch("/{session_id}", response_model=SessionOut)
async def rename_conversation(request: Request, session_id: str, body: RenameIn) -> SessionOut:
    tid, uid = _ctx_ids(request)
    ok = await cs.rename_session(session_id, body.title, tenant_id=tid, created_by=uid)
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    return SessionOut(id=session_id, title=body.title)


@conversations_router.delete("/{session_id}")
async def delete_conversation(request: Request, session_id: str) -> dict:
    tid, uid = _ctx_ids(request)
    ok = await cs.delete_session(session_id, tenant_id=tid, created_by=uid)
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}
