"""The Orchestrator's chat, persisted so it can be reopened and continued.

The rail was `zustand` + localStorage and said so on screen: "Sessions are stored in
this browser only." Spec §5.3 called for server-backed sessions in Phase 1; Phases 1-4
never built them, so a chat survived neither a cleared browser nor a change of device,
and nothing about it was auditable.

Almost none of this is new. `shared/services/conversation_service` is complete and every
standalone agent already uses it; `orchestrator2` only READ from it (the router pulls
history for routing) and never wrote. This module is the writer.

SESSION ID == RUN ID. One Orchestrator conversation is one run (spec §5.3), and
`ensure_session_with_id` exists precisely because the Copilot needed the same thing.
Keying on the run id is what makes reopening a chat also reopen its Deliverables, its
LangGraph thread and its project scope — with no mapping table anywhere.

NOTHING HERE MAY FAIL A TURN. `persist_turn` swallows its own errors by design; this
module keeps that property on every path. Losing a transcript row is a smaller harm than
losing the reply — the same trade deliverable capture makes.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from shared.services import conversation_service as cs

logger = logging.getLogger(__name__)

#: The `agent_id` Orchestrator sessions are filed under.
#:
#: NOT one of the nine. Sessions are listed per agent, so reusing an agent's id would
#: mix Orchestrator chats into that standalone agent's own history rail — two different
#: conversations with two different scopes shown as one list.
ORCHESTRATOR_AGENT_KEY = "orchestrator"

#: Longest title derived from a first message. Long enough to tell two chats apart in
#: the rail, short enough not to wrap.
_TITLE_CHARS = 60


def _title_from(first_message: str) -> str:
    """A rail label taken from what the person actually asked.

    A rail of chats all called "New chat" is a rail you cannot navigate, which is the
    whole reason the first message is carried in at all.
    """
    text = " ".join((first_message or "").split())
    if not text:
        return "New chat"
    if len(text) <= _TITLE_CHARS:
        return text
    return text[:_TITLE_CHARS].rstrip() + "…"


def _as_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


async def ensure_session(
    run_id: str,
    *,
    tenant_id: str,
    project_id: Optional[str],
    user_id: str,
    first_message: str,
) -> None:
    """Idempotently create this run's conversation row. Never raises.

    Called on every turn rather than only the first: it is a no-op once the row exists,
    and making it conditional would mean tracking "have I created it yet" on a socket
    that can reconnect mid-conversation.
    """
    try:
        await cs.ensure_session_with_id(
            run_id,
            tenant_id,
            scope_type="agent",
            scope_id=run_id,
            run_id=run_id,
            project_id=_as_uuid(project_id),
            created_by=user_id,
            agent_id=ORCHESTRATOR_AGENT_KEY,
            title=_title_from(first_message),
        )
    except Exception as exc:  # noqa: BLE001 — a lost transcript must not cost a turn
        logger.warning(
            "orchestrator2 could not ensure the session for run=%s: %s", run_id, exc
        )


async def record_turn(
    run_id: str,
    role: str,
    content: str,
    *,
    tenant_id: str,
    user_id: str,
    agent_id: Optional[str] = None,
) -> None:
    """Persist one side of a turn. Never raises.

    `author_id` is the turn's own user for BOTH sides — on the agent's reply it records
    whose conversation this was, which is what makes a transcript attributable at all.

    `agent_id` says WHICH of the nine replied, and is what makes the transcript usable
    as memory: `role` alone is `agent`, so a conversation fed to the next agent reads
    as one undifferentiated voice. Left `None` for user turns and for the Orchestrator
    answering directly — the router is not one of the nine, and saying it was would
    tell the next agent a delivery agent said something it did not.
    """
    try:
        await cs.persist_turn(
            run_id, role, content, tenant_id=tenant_id, author_id=user_id,
            agent_id=agent_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "orchestrator2 could not persist a %s turn on run=%s: %s", role, run_id, exc
        )
