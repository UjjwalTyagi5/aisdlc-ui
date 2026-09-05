"""The Orchestrator WebSocket — one authenticated socket, one named agent per turn.

Route: `/sdlc/agent/orchestrator2/ws?ticket=<single-use ticket>[&run=<run_id>]`

IN  : {"type": "user_message", "text": ..., "agent": ..., "run_id": ..., "project_id": ...}
OUT : the events `run_agent` yields, forwarded verbatim as JSON —
      `agent.selected` | `stream_chunk` | `tool.call` | `error` | `stream_end`
      (the union in `frontend/lib/orchestrator/protocol.ts`).

WHO MAY USE IT — and why the check lives HERE
---------------------------------------------
Project Admin only, checked server-side, before the handshake is accepted.

In the previous phase the per-project Orchestrator page shipped with no access
check at all: the nav entry was gated, the global route was gated, the button was
gated, and a delivery role could still reach the whole Orchestrator by typing the
URL. Gating the UI is not access control. This socket therefore resolves the
caller's platform role itself, from the redeemed ticket's claims, and refuses
before `accept()` — a rejected caller never gets a socket to send a turn on.

`org_admin` and `bu_admin` are refused too, despite outranking Project Admin
everywhere else: the governance tier holds no agent access at all — it decides who
may run agents, it does not run them. This mirrors `canUseOrchestrator` in
`frontend/lib/orchestrator/access.ts` (`role === "project_admin"`) exactly, so the
server and the UI cannot disagree about who is allowed in. The reason the bar is
this high is that the Orchestrator reaches all nine agents at once: whoever can
drive it effectively holds every agent's access.

WHAT THIS SOCKET DELIBERATELY DOES NOT DO
-----------------------------------------
It is NOT a port of `agents_orchestrator/orchestrator/copilot_api.py`. Only that
file's ticket-redemption and connection lifecycle are reused. There are no
approval events, no sign-off, no positional progression, no stage index, no
sentinel-driven hand-off between agents, and nothing advances on its own — none of
that machinery exists in this engine, and re-importing its vocabulary is how a
rebuild quietly becomes the thing it replaced.

There is also NO DEFAULT AGENT. Phase 2 dispatches only an explicitly named agent
(routing arrives in Phase 3); a message without an `agent` field gets an `error`
saying so. A silent default is exactly how the old engine hid six missing prompts.

Every inbound frame terminates: the socket answers with the agent's events (which
always end in `stream_end`) or with an `error` followed by `stream_end`. A turn
that fails is never allowed to end in silence, and an exception never kills the
socket — the client keeps its connection and gets a typed failure it can show.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agents_orchestrator.orchestrator2.dispatch import run_agent
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE

logger = logging.getLogger(__name__)

orchestrator2_router = APIRouter()

# The single role that may drive the Orchestrator. Kept as a named constant so the
# check reads as one decision in one place rather than a literal buried in a branch.
ORCHESTRATOR_ROLE = "project_admin"


async def _send(websocket: WebSocket, payload: dict) -> None:
    """Best-effort JSON send — a dead socket ends the turn loop, it does not raise
    a stray exception into it."""
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception:  # noqa: BLE001
        raise WebSocketDisconnect()


async def _fail(websocket: WebSocket, message: str, detail: str = "") -> None:
    """Answer one inbound frame with a typed failure that still terminates the turn.

    `stream_end` always follows, so a client that disables its composer while a turn
    is in flight gets it back. The old engine's failures produced nothing at all;
    a refusal the user can read is the entire point of this phase.
    """
    event: dict[str, Any] = {"type": "error", "message": message}
    if detail:
        event["detail"] = detail
    await _send(websocket, event)
    await _send(websocket, {"type": "stream_end"})


async def _resolve_platform_role(user_id: str, tenant_id: str) -> Optional[str]:
    """The caller's platform role, resolved from the DB — never from the client.

    Ticket claims carry only `{user_id, tenant_id}` (see `config/auth/ws_ticket.py`),
    so the role is resolved here through the same pair of resolvers login uses:
    `resolve_permissions_for_user` then `resolve_platform_role_for_user`. Passing
    the real permission list matters — `platform_role_for` recognises org-wide
    standing reached through a permission (e.g. `settings:manage`) without an
    `org_admin` binding row, and passing `[]` would hide such a caller behind
    whatever lesser binding they also hold.

    FAILS CLOSED. Any resolver error, a missing user or tenant, or an unresolvable
    role returns None, which the caller treats as a refusal. A DB blip must deny
    the Orchestrator, never open it.
    """
    if not user_id or not tenant_id:
        return None
    try:
        from shared.authz.resolver import resolve_permissions_for_user
        from shared.authz.effective_role import resolve_platform_role_for_user

        permissions = await resolve_permissions_for_user(user_id, tenant_id)
        return await resolve_platform_role_for_user(user_id, tenant_id, permissions)
    except Exception as exc:  # noqa: BLE001 — incl. PermissionResolutionError
        logger.warning(
            "orchestrator2 role resolution failed for user=%s tenant=%s: %s — "
            "refusing (fail-closed)", user_id, tenant_id, exc,
        )
        return None


async def _run_model_offering(run_id: str) -> tuple[Optional[str], Optional[str]]:
    """The (model_id, offering_id) persisted on the run, or (None, None) on any miss.

    Same read `copilot_api._run_model_offering` does, for the same reason: the model
    is a property of the RUN, not something the client gets to name on the wire.
    Fail-soft — the graph falls back to the organization default on None.
    """
    if not run_id:
        return None, None
    try:
        from sqlalchemy import select

        from shared.db import get_db_session_superuser
        from shared.models.orm import Run

        async with get_db_session_superuser() as session:
            run = (
                await session.execute(select(Run).where(Run.id == _as_run_uuid(run_id)))
            ).scalar_one_or_none()
            if run is None:
                return None, None
            return getattr(run, "model_id", None), getattr(run, "offering_id", None)
    except Exception as exc:  # noqa: BLE001 — model selection is never fatal to a turn
        logger.warning("orchestrator2 _run_model_offering(%s) failed: %s", run_id, exc)
        return None, None


def _as_run_uuid(run_id: str) -> Any:
    import uuid

    try:
        return uuid.UUID(str(run_id))
    except (ValueError, TypeError, AttributeError):
        return run_id


@orchestrator2_router.websocket("/ws")
async def orchestrator2_ws(websocket: WebSocket) -> None:
    """Serve one Project Admin's Orchestrator session."""
    ticket = websocket.query_params.get("ticket", "")
    claims = await _redeem_ws_ticket(ticket) if ticket else None
    if claims is None:
        await websocket.close(
            code=4401,
            reason='{"error": "invalid_or_expired_ticket", "detail": "Provide a valid single-use ticket from POST /auth/ws-ticket"}',
        )
        return

    tenant_id = claims.get("tenant_id", "") or ""
    user_id = claims.get("user_id", "") or ""

    if AGENT_RUNTIME_MODE == "enterprise":
        expected_tenant = websocket.query_params.get("tenant_id", "")
        if expected_tenant and tenant_id != expected_tenant:
            await websocket.close(
                code=4403,
                reason='{"error": "tenant_mismatch", "detail": "Token tenant does not match requested tenant"}',
            )
            return

    # ── THE ACCESS CHECK ─────────────────────────────────────────────────────
    # Server-side, from the redeemed claims, BEFORE accept(): a caller who is not a
    # Project Admin never gets an open socket, so there is no turn to refuse later.
    # Never assume the caller came through the UI — last time, a URL was enough.
    role = await _resolve_platform_role(user_id, tenant_id)
    if role != ORCHESTRATOR_ROLE:
        logger.info(
            "orchestrator2 refused user=%s tenant=%s role=%s — Project Admin only",
            user_id, tenant_id, role,
        )
        await websocket.close(
            code=4403,
            reason='{"error": "forbidden", "detail": "The Orchestrator is available to Project Admins only"}',
        )
        return

    await websocket.accept()

    # A run may be pinned on the URL; each message may still carry its own, which
    # wins. Both are only ever a conversation key (the graph's thread_id) — this
    # socket reads no position from a run and writes none back.
    default_run_id = websocket.query_params.get("run", "") or ""

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                await _fail(websocket, "Malformed message (expected JSON).")
                continue
            if not isinstance(msg, dict):
                await _fail(websocket, "Malformed message (expected a JSON object).")
                continue

            mtype = msg.get("type")
            if mtype != "user_message":
                await _fail(websocket, f"Unsupported message type: {mtype!r}")
                continue

            text = (msg.get("text") or "").strip()
            if not text:
                await _fail(websocket, "Empty message.")
                continue

            # No default agent, on purpose. Phase 2 dispatches only what the client
            # names; routing is Phase 3. Guessing here would reintroduce exactly the
            # silent mis-dispatch this engine was rebuilt to eliminate.
            agent_id = (msg.get("agent") or "").strip()
            if not agent_id:
                await _fail(
                    websocket,
                    "This message named no agent. Include an 'agent' field — the "
                    "Orchestrator does not pick one for you yet.",
                )
                continue

            run_id = (msg.get("run_id") or default_run_id or "").strip()
            if not run_id:
                await _fail(websocket, "Missing run_id — provide it on the message or as ?run=.")
                continue

            model_id, offering_id = await _run_model_offering(run_id)

            try:
                async for event in run_agent(
                    agent_id,
                    text=text,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    model_id=model_id,
                    offering_id=offering_id,
                ):
                    await _send(websocket, event)
            except WebSocketDisconnect:
                raise
            except Exception as exc:  # noqa: BLE001 — a failed turn must be visible
                # `run_agent` handles its own failures; reaching here means something
                # outside it broke. Surfacing it keeps the promise that a turn never
                # ends in silence, and keeps the socket alive for the next one.
                logger.exception(
                    "orchestrator2 turn failed (agent=%s run=%s)", agent_id, run_id
                )
                await _fail(websocket, "The turn failed.", detail=str(exc))
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001
        logger.exception("orchestrator2 socket closed on an unexpected error")
        return
