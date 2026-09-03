"""Project Manager agent chat API (PM agent, phase 2).

Two surfaces: a REST turn and the WebSocket the chat drawer actually uses.
Both run the same graph and both resolve the project through the same access check —
a WS turn that skipped it would be a way around the permission REST enforces.

WHAT THIS DOES SET, and why each matters — every one of these was a bug in another
agent's route before it was a line here:

  identity      from the verified session, NEVER the form body. The `user_id` field is
                kept for wire compatibility and ignored, because trusting it let any
                authenticated caller claim to be anyone.
  project       the RESOLVED, access-checked id that assert_agent_access_for_chat
                returns — not the raw form field.
  tenant+project in the tool context, or `register_generated_file` returns early and a
                generated document never becomes an artifact at all.
  model context project_id reaches model resolution; without it,
                effective_project_offerings fails CLOSED once a tenant has any
                org_model_grants row, and every call answers "no model configured".
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import json
from uuid import uuid4

from fastapi import (
    APIRouter, Form, HTTPException, Request, WebSocket, WebSocketDisconnect,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from config.agent_context import build_agent_input_text, set_agent_folder
from config.ws_helper import (
    set_project_id, set_provider_kind, set_run_id, set_session_id, set_tenant_id,
    set_user_id,
)
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.connection_manager import manager
from config.env import AGENT_RUNTIME_MODE
from config.websocket_utils import set_websocket_context
from shared.authz.agent_access import assert_agent_access_for_chat
from shared.errors import classify_error
from shared.db import get_db_session_for_tenant
from shared.observability.callbacks import langfuse_langchain_extras
from shared.services.budget_store import workspace_id_for_project

from .agents.schedule import PM_SYS_MESSAGE, app as planning_app

logger = logging.getLogger(__name__)

pm_router_orchestrator = APIRouter()

#: Sessions whose system message has already been sent. The prompt is large and the
#: graph is checkpointed, so re-sending it on every turn would pay for it repeatedly.
_initialized_sessions: set[str] = set()


async def _run_config(session_id: str, tenant_id: str, user_id: str, project_id: str) -> dict:
    """Graph config with the observability callbacks attached.

    NOT OPTIONAL PLUMBING. `langfuse_langchain_extras` is what threads the run's PROJECT
    into the async context via set_run_project, and `resolve_model_for_run` reads it:
    once a tenant has any org_model_grants row, effective_project_offerings fails CLOSED
    without a project. Skipping this makes every turn answer "No usable model is
    configured for your organization" and points an administrator at a model that was
    never the problem — which is exactly what this route did before the line existed.
    """
    workspace_id = await workspace_id_for_project(tenant_id or "", project_id)
    callbacks, metadata = langfuse_langchain_extras(
        session_id=session_id, tenant_id=tenant_id, user_id=user_id,
        agent_type="plan", project_id=project_id, workspace_id=workspace_id,
    )
    return {
        "configurable": {"thread_id": session_id},
        "recursion_limit": 100,
        "callbacks": list(callbacks),
        "metadata": metadata,
    }


@pm_router_orchestrator.post("/chat/")
async def pm_chat(
    request: Request,
    project_id: str = Form(...),
    conversation_context: str = Form(None),
    task_intent: str = Form(None),
    pipeline_context: str = Form(None),
    provider_kind: str = Form(None),
    session_id: str = Form(...),
    user_id: str = Form(None),  # wire compatibility only; NOT trusted for identity
    model_id: str = Form(None),
) -> Dict[str, Any]:
    """One planning turn."""
    real_user_id = getattr(request.state, "user_id", "") or ""
    real_tenant_id = getattr(request.state, "tenant_id", "") or ""

    async with get_db_session_for_tenant(real_tenant_id) as db:
        project_id = await assert_agent_access_for_chat(
            db, tenant_id=real_tenant_id, project_id=project_id,
            user_id=real_user_id, agent_id="plan",
        )

    set_session_id(session_id)
    set_user_id(real_user_id)
    set_tenant_id(real_tenant_id or None)
    # The RESOLVED id, which is what makes it safe as the isolation key for anything
    # this turn stores.
    set_project_id(project_id or None)
    set_run_id(None)  # a standalone chat turn belongs to no pipeline run
    set_provider_kind(provider_kind or "azure_devops")
    set_agent_folder("orchestrator")

    # `pipeline_sections` is the planner's whole context filter. It reads what was asked
    # for and what was designed; development and testing output would be the answers to
    # what it is planning.
    text = build_agent_input_text(
        conversation_context=conversation_context,
        task_intent=task_intent,
        pipeline_context=pipeline_context,
        pipeline_sections=("requirements", "design"),
    )

    messages: List[Any] = []
    if session_id not in _initialized_sessions:
        messages.append(SystemMessage(content=PM_SYS_MESSAGE))
        _initialized_sessions.add(session_id)
    messages.append(HumanMessage(content=text))

    state = {
        "messages": messages,
        "tenant_id": real_tenant_id,
        "model_id": model_id,
        "offering_id": None,
        "resolved_model": None,
    }
    config = await _run_config(session_id, real_tenant_id, real_user_id, project_id)

    try:
        final = await planning_app.ainvoke(state, config)
    except Exception as exc:  # noqa: BLE001
        # Type name only: a BYOK provider error can carry the tenant's own API key.
        logger.exception("PM agent turn failed (session=%s)", session_id)
        raise HTTPException(
            status_code=500,
            detail=f"The planner could not complete this turn ({type(exc).__name__}).",
        ) from exc

    reply = _last_reply(final.get("messages", []))
    return {"response": reply, "session_id": session_id}


def _last_reply(messages: List[Any]) -> str:
    """The assistant's own words for this turn.

    TOOL OUTPUT IS SKIPPED, not concatenated. A ToolMessage carries whatever the tool
    returned — a whole context dump, a JSON array of sprints — and appending it to the
    reply is how the Design agent once showed the user its own document twice.
    """
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            continue
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            content = msg.content
            if isinstance(content, list):
                return "".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            return str(content or "")
    return ""


@pm_router_orchestrator.get("/health")
async def pm_health() -> Dict[str, Optional[str]]:
    """Liveness for the stage, used by the pipeline view to tell built from planned."""
    return {"agent": "plan", "status": "ok"}


# ── WebSocket ────────────────────────────────────────────────────────────────


@pm_router_orchestrator.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """The surface the chat drawer actually uses.

    AUTHENTICATED BY A SINGLE-USE TICKET, not by the JWT: the HTTP middleware does not
    run for WebSocket connections, so a route that trusted the query string would be
    open. The ticket is minted behind a permission check and redeemed once here.
    """
    ticket = websocket.query_params.get("ticket", "")
    claims = await _redeem_ws_ticket(ticket) if ticket else None
    if claims is None:
        await websocket.close(
            code=4401,
            reason='{"error": "invalid_or_expired_ticket", "detail": '
                   '"Provide a valid single-use ticket from POST /auth/ws-ticket"}',
        )
        return
    if AGENT_RUNTIME_MODE == "enterprise":
        expected = websocket.query_params.get("tenant_id", "")
        if expected and claims.get("tenant_id", "") != expected:
            await websocket.close(
                code=4403,
                reason='{"error": "tenant_mismatch", "detail": '
                       '"Token tenant does not match requested tenant"}',
            )
            return

    set_agent_folder("orchestrator")
    await manager.connect(websocket)
    user_id = claims.get("user_id", "")
    tenant_id = claims.get("tenant_id", "") or ""

    try:
        while True:
            message_data = json.loads(await websocket.receive_text())
            session_id = message_data.get("session_id", str(uuid4()))
            manager.register_session(websocket, session_id)
            set_websocket_context(manager, session_id)

            if message_data.get("type") == "user_message_with_files":
                await _process_turn_ws(message_data, user_id, tenant_id, session_id)
            elif message_data.get("type") == "session_cleanup":
                _initialized_sessions.discard(session_id)
            else:
                await manager.send_personal_message(
                    json.dumps({"type": "echo"}), websocket
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except RuntimeError as exc:
        # Starlette raises RuntimeError("WebSocket is not connected"), not
        # WebSocketDisconnect, when the chat BFF closes its one-shot per-turn socket
        # after the agent finishes. That is a normal end of turn, not a failure.
        if "not connected" in str(exc).lower() or "disconnect" in str(exc).lower():
            logger.debug("PM WS closed by peer after turn: %s", exc)
        else:
            logger.error("PM agent WebSocket error: %s", classify_error(exc))
        manager.disconnect(websocket)
    except Exception as exc:  # noqa: BLE001
        logger.error("PM agent WebSocket error: %s", classify_error(exc))
        manager.disconnect(websocket)


async def _process_turn_ws(
    message_data: dict, user_id: Any, tenant_id: str, session_id: str
) -> None:
    """Run one turn and broadcast the reply.

    The project is resolved and ACCESS-CHECKED here exactly as on the REST route. A WS
    turn that skipped it would be a way around the permission the REST route enforces.
    """
    project_id = (message_data.get("pipeline_context") or {}).get("project_id")         if isinstance(message_data.get("pipeline_context"), dict) else None
    project_id = project_id or message_data.get("project_id") or ""

    if project_id and tenant_id:
        async with get_db_session_for_tenant(tenant_id) as db:
            project_id = await assert_agent_access_for_chat(
                db, tenant_id=tenant_id, project_id=project_id,
                user_id=str(user_id), agent_id="plan",
            )

    set_session_id(session_id)
    set_user_id(str(user_id))
    set_tenant_id(tenant_id or None)
    set_project_id(project_id or None)
    set_run_id(None)
    set_provider_kind(message_data.get("provider_kind") or "azure_devops")

    text = build_agent_input_text(
        conversation_context=message_data.get("conversation_context"),
        task_intent=message_data.get("task_intent"),
        pipeline_context=message_data.get("pipeline_context"),
        pipeline_sections=("requirements", "design"),
    )

    messages: List[Any] = []
    if session_id not in _initialized_sessions:
        messages.append(SystemMessage(content=PM_SYS_MESSAGE))
        _initialized_sessions.add(session_id)
    messages.append(HumanMessage(content=text))

    error: Optional[str] = None
    try:
        final = await planning_app.ainvoke(
            {
                "messages": messages,
                "tenant_id": tenant_id,
                "model_id": message_data.get("model_id"),
                "offering_id": None,
                "resolved_model": None,
            },
            await _run_config(session_id, tenant_id, str(user_id), project_id),
        )
        reply = _last_reply(final.get("messages", []))
    except Exception as exc:  # noqa: BLE001
        error = classify_error(exc, "planning")
        logger.error("PM turn failed: %s", error)
        reply = f"An error occurred: {error}"

    await manager.broadcast({
        "type": "agent_response",
        "session_id": session_id,
        "agent": "plan",
        "message": reply,
    })

    # THE TURN MUST SAY IT IS OVER. The chat BFF ends a run on
    # activity_update{type: "complete"} and arms its idle fallback on stream_end; with
    # neither, the composer stays disabled and the run hangs open, because nothing
    # closes the socket from this side either. Every other agent route emits both, and
    # this one emitted neither until a real socket turn showed the client waiting.
    #
    # Emitted on EVERY path, including the failure above — a turn that errors is the
    # case where a stuck composer is least forgivable.
    if error is not None:
        await manager.broadcast({
            "type": "agent_completed",
            "session_id": session_id,
            "success": False,
            "error": error,
        })
    await manager.broadcast({"type": "stream_end", "session_id": session_id})
    await manager.broadcast({
        "type": "activity_update",
        "activity": {
            "id": str(uuid4()),
            "type": "complete",
            "session_id": session_id,
            "message": "Processing complete",
            "time": "Just now",
        },
    })
