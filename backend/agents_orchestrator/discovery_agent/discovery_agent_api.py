"""Discovery & Assessment — standalone chat socket (Track 3 — Code Modernization).

Mounted at `/sdlc/agent/discovery`. The ticket is redeemed HERE, before the handshake is accepted;
the turn contract, access (member, track, reach), run and connector handling after
that are shared with Track 3's other agent: `modernization_common/standalone.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket

from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket

discovery_router = APIRouter()

#: Sessions whose system message has been sent (the graph is checkpointed per session).
_initialized_sessions: set[str] = set()


@discovery_router.websocket("/ws")
async def discovery_ws(websocket: WebSocket) -> None:
    from agents_orchestrator.modernization_common.standalone import (  # noqa: PLC0415
        INVALID_TICKET_REASON,
        serve_agent_socket,
        tenant_mismatch,
    )
    from agents_orchestrator.discovery_agent.agents.assessor import (  # noqa: PLC0415
        AGENT_ID,
        DISCOVERY_SYS_MESSAGE,
        app,
    )

    ticket = websocket.query_params.get("ticket", "")
    claims = await _redeem_ws_ticket(ticket) if ticket else None
    if claims is None:
        await websocket.close(code=4401, reason=INVALID_TICKET_REASON)
        return
    if tenant_mismatch(websocket, claims):
        await websocket.close(code=4403, reason='{"error": "tenant_mismatch"}')
        return

    await serve_agent_socket(
        websocket, claims, agent_id=AGENT_ID, label="Discovery & Assessment agent", graph=app,
        system_prompt=DISCOVERY_SYS_MESSAGE, initialized=_initialized_sessions,
    )
