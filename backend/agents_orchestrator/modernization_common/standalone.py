"""The standalone chat socket both Track 3 agents serve their own page from.

The same wire contract every Portfolio 1 agent page speaks (the chat BFF,
`frontend/app/api/chat/route.ts`, bridges to it): one `user_message_with_files` frame
per turn; `stream_chunk` frames while the agent answers; then `stream_end` and a
terminal `activity_update{type: "complete"}` on EVERY path — without the terminal the
composer stays disabled and the turn never ends.

What each turn establishes before the graph runs, and why — every line of this list
was a bug in some Portfolio 1 wrapper before it was a line somewhere:

  identity    from the redeemed single-use ticket, never from the frame.
  access      the project from the frame is RESOLVED and checked: the caller is a
              member, the agent belongs to the project's TRACK, and the caller's role
              reaches the agent (`assert_agent_access_for_chat_on_track`).
  run         the per-project chat run for this stage, so what the agent records
              (the brief, the assessment) lands on a run the project's pages read.
  connector   the project's connector for THIS stage, bound for the turn only —
              the same adapter the Orchestrator's dispatch uses, so an agent reaches
              exactly the same tools standalone as it does orchestrated.
  consent     recomputed from THIS turn's message: a "yes" authorises one
              Consequential action, never every later turn.
  model       project-scoped BYOK, resolved in the graph's agent node with the
              project id carried in state. No env-key fallback.
"""
from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

logger = logging.getLogger(__name__)

_MAX_MESSAGE_BYTES = 50_000


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) and b.get("type") == "text"
            else (b if isinstance(b, str) else "")
            for b in content
        )
    return ""


async def _send(websocket: WebSocket, payload: dict) -> None:
    from config.connection_manager import manager  # noqa: PLC0415

    await manager.send_personal_message(json.dumps(payload), websocket)


async def _finish(websocket: WebSocket, session_id: str, *, error: str | None = None) -> None:
    """The end of a turn, on every path. See the module docstring."""
    try:
        if error is not None:
            await _send(websocket, {"type": "agent_completed", "session_id": session_id,
                                    "success": False, "error": error})
        await _send(websocket, {"type": "stream_end", "session_id": session_id})
        await _send(websocket, {"type": "activity_update", "activity": {
            "id": str(uuid4()), "type": "complete", "session_id": session_id,
            "message": "Processing complete", "time": "Just now",
        }})
    except Exception:  # noqa: BLE001 — the peer may already be gone
        logger.debug("could not send the end of turn for session %s", session_id)


async def _chat_run(tenant_id: str, project_id: str, agent_id: str) -> str | None:
    """The project's chat run for this stage — one per project and stage, reused."""
    from config.ws_helper import set_run_id  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.services.chat_artifacts import _get_or_create_chat_run  # noqa: PLC0415

    set_run_id(None)  # never inherit a previous turn's run
    async with get_db_session_for_tenant(tenant_id) as session:
        run_id = await _get_or_create_chat_run(session, tenant_id, project_id, agent_id)
        await session.commit()
    return run_id


async def _run_config(session_id: str, tenant_id: str, user_id: str, project_id: str,
                      agent_id: str) -> dict:
    from shared.audit import AuditCallbackHandler  # noqa: PLC0415
    from shared.audit.service import audit_service  # noqa: PLC0415
    from shared.observability.callbacks import agent_trace  # noqa: PLC0415

    # THROUGH `agent_trace`, NOT `langfuse_langchain_extras` DIRECTLY.
    #
    # The attribution this passed was complete, so the drift guard
    # (tests/observability/test_trace_attribution.py) looked pedantic here. It was not:
    # `agent_trace` also resolves the project's LANGFUSE BINDING, which is what decides
    # WHICH Langfuse project the turn writes to. Without it these agents fall back to the
    # single default client, so Track 3's traces land in a shared bucket instead of their
    # own project — losing the per-project isolation migration 0057 exists to provide, and
    # losing it silently, because traces still arrive.
    #
    # It also resolves `workspace_id` from the project (so the lookup here is redundant)
    # and binds the run's client to a contextvar for agents that do not go through
    # LangChain.
    callbacks, metadata = await agent_trace(
        session_id=session_id,
        agent_type=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        project_id=project_id,
    )
    audit = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=tenant_id)
    return {"configurable": {"thread_id": session_id}, "recursion_limit": 100,
            "callbacks": [audit, *callbacks], "metadata": metadata}


async def run_turn(
    websocket: WebSocket,
    message: dict,
    *,
    agent_id: str,
    label: str,
    graph: Any,
    system_prompt: str,
    user_id: str,
    tenant_id: str,
    initialized: set[str],
) -> None:
    """One chat turn. Never raises into the socket loop; always ends the turn."""
    from agents_orchestrator.orchestrator2 import connectors, mcp  # noqa: PLC0415
    from config.agent_context import build_agent_input_text  # noqa: PLC0415
    from config.context_broker import build_context_for_project  # noqa: PLC0415
    from config.ws_helper import (  # noqa: PLC0415
        reset_session_id, set_consequential_approved, set_orchestrator_run,
        set_project_id, set_provider_kind, set_run_id, set_session_id, set_tenant_id,
        set_user_id,
    )
    from shared.authz.agent_access import assert_agent_access_for_chat_on_track  # noqa: PLC0415
    from shared.authz.consequential import is_approval_message  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415
    from shared.errors import classify_error  # noqa: PLC0415
    from shared.services.conversation_service import persist_turn  # noqa: PLC0415
    from shared.services.skill_runtime import skill_context_scope  # noqa: PLC0415
    from shared.services.standalone_prompt import (  # noqa: PLC0415
        resolve_agent_skills,
        resolve_agent_turn,
    )
    from shared.tools.document_tools import (  # noqa: PLC0415
        attachment_message_contents,
        attachment_paths_from_context,
    )

    session_id = str(message.get("session_id") or uuid4())
    context = message.get("pipeline_context") if isinstance(message.get("pipeline_context"), dict) else {}
    task_intent = str(message.get("task_intent") or message.get("text") or "")
    requested_project = str(context.get("project_id") or message.get("project_id") or "")

    # ── access: member, track, reach ─────────────────────────────────────────
    try:
        if not requested_project:
            raise HTTPException(status_code=400, detail=(
                f"Open the {label} from a project — this turn named no project."))
        async with get_db_session_for_tenant(tenant_id) as db:
            project_id = await assert_agent_access_for_chat_on_track(
                db, tenant_id=tenant_id, project_id=requested_project,
                user_id=str(user_id), agent_id=agent_id,
            )
    except HTTPException as exc:
        await _send(websocket, {"type": "agent_response", "agent_name": "Error Agent",
                                "message": str(exc.detail), "session_id": session_id})
        await _finish(websocket, session_id, error=str(exc.detail))
        return
    except Exception as exc:  # noqa: BLE001 — cannot prove access ⇒ refuse
        logger.exception("%s: access check failed", agent_id)
        await _finish(websocket, session_id, error=classify_error(exc, "access check"))
        return

    token = set_session_id(session_id)
    set_user_id(str(user_id))
    set_tenant_id(tenant_id or None)
    set_project_id(project_id)
    set_orchestrator_run(False)
    set_consequential_approved(is_approval_message(task_intent))
    reply_parts: list[str] = []
    error: str | None = None
    try:
        set_run_id(await _chat_run(tenant_id, project_id, agent_id))
        try:
            set_provider_kind(await connectors.connector_kind_for(
                agent_id, tenant_id=tenant_id, project_id=project_id))
        except Exception:  # noqa: BLE001 — a refinement, never fatal
            pass

        messages: list[Any] = []
        if session_id not in initialized:
            prompt, _skills = await resolve_agent_turn(agent_id, system_prompt, tenant_id or None, project_id)
            messages.append(SystemMessage(content=prompt or system_prompt))
            initialized.add(session_id)
        upstream = await build_context_for_project(project_id, tenant_id, agent_id)
        if upstream:
            messages.append(HumanMessage(content=(
                "--- WORK ALREADY ON THIS PROJECT ---\n" + upstream + "\n--- END ---")))
        messages.append(HumanMessage(content=build_agent_input_text(
            conversation_context=message.get("conversation_context"),
            task_intent=task_intent, pipeline_context=context, pipeline_sections=(),
        ) or task_intent))
        files = [f.get("path") for f in (message.get("files") or []) if isinstance(f, dict) and f.get("path")]
        for content in attachment_message_contents(attachment_paths_from_context(context) + files):
            messages.append(HumanMessage(content=content))

        await persist_turn(session_id, "user", task_intent, tenant_id=tenant_id or None,
                           author_id=str(user_id), artifact_refs=context.get("attachments") or None)

        state = {"messages": messages, "tenant_id": tenant_id, "project_id": project_id,
                 "model_id": message.get("model_id"), "offering_id": message.get("offering_id")}
        config = await _run_config(session_id, tenant_id, str(user_id), project_id, agent_id)
        skills = await resolve_agent_skills(agent_id, tenant_id or None, project_id)
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(connectors.bound_connector(
                agent_id, tenant_id=tenant_id, project_id=project_id, owner_id=str(user_id)))
            await stack.enter_async_context(mcp.bound_mcp_tools(
                agent_id, tenant_id=tenant_id, project_id=project_id, owner_id=str(user_id)))
            await stack.enter_async_context(skill_context_scope(agent_id, skills))
            async for chunk, _meta in graph.astream(state, stream_mode="messages", config=config):
                if isinstance(chunk, ToolMessage):
                    continue  # a tool's output is not the agent speaking
                text = _text(getattr(chunk, "content", None))
                if text:
                    reply_parts.append(text)
                    await _send(websocket, {"type": "stream_chunk", "content": text,
                                            "session_id": session_id})
        if not reply_parts:
            error = "The agent finished without producing a reply. Try again, or rephrase."
            await _send(websocket, {"type": "agent_response", "agent_name": "Error Agent",
                                    "message": error, "session_id": session_id})
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001 — a failed turn is shown, never silent
        logger.exception("%s turn failed (session=%s)", agent_id, session_id)
        error = classify_error(exc, "agent processing")
        await _send(websocket, {"type": "agent_response", "agent_name": "Error Agent",
                                "message": f"An error occurred: {error}", "session_id": session_id})
    finally:
        if reply_parts:
            await persist_turn(session_id, "agent", "".join(reply_parts),
                               tenant_id=tenant_id or None, author_id=agent_id,
                               model=message.get("model_id"))
        await _finish(websocket, session_id, error=error)
        try:
            reset_session_id(token)
        except Exception:  # noqa: BLE001
            pass
        set_user_id("")
        set_tenant_id(None)
        set_project_id(None)
        set_run_id(None)
        set_consequential_approved(False)
        set_provider_kind("")


INVALID_TICKET_REASON = (
    '{"error": "invalid_or_expired_ticket", '
    '"detail": "Provide a valid single-use ticket from POST /auth/ws-ticket"}'
)


def tenant_mismatch(websocket: WebSocket, claims: dict) -> bool:
    """Enterprise mode: the socket's `tenant_id` query param must match the ticket's."""
    from config.env import AGENT_RUNTIME_MODE  # noqa: PLC0415

    if AGENT_RUNTIME_MODE != "enterprise":
        return False
    expected = websocket.query_params.get("tenant_id", "")
    return bool(expected) and claims.get("tenant_id", "") != expected


async def serve_agent_socket(
    websocket: WebSocket, claims: dict, *, agent_id: str, label: str, graph: Any,
    system_prompt: str, initialized: set[str],
) -> None:
    """The socket loop, one turn per frame, for a caller whose ticket the ENDPOINT
    has already redeemed.

    Redemption stays in each endpoint on purpose: the boot scan's audit
    (`tests/test_ws_route_coverage.py`) reads every socket handler and requires it to
    redeem its ticket before accepting, so the authentication decision is visible
    where the route is declared rather than one call away.
    """
    from config.connection_manager import manager  # noqa: PLC0415
    from config.websocket_utils import set_websocket_context  # noqa: PLC0415

    await manager.connect(websocket)
    user_id = str(claims.get("user_id", ""))
    tenant_id = str(claims.get("tenant_id", "") or "")
    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode()) > _MAX_MESSAGE_BYTES:
                await _send(websocket, {"type": "error", "message": "Message too large (max 50 KB)."})
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await _send(websocket, {"type": "error", "message": "Malformed message."})
                continue
            session_id = str(message.get("session_id") or uuid4())
            message["session_id"] = session_id
            manager.register_session(websocket, session_id)
            set_websocket_context(manager, session_id)
            if message.get("type") == "user_message_with_files":
                await run_turn(websocket, message, agent_id=agent_id, label=label, graph=graph,
                               system_prompt=system_prompt, user_id=user_id,
                               tenant_id=tenant_id, initialized=initialized)
            elif message.get("type") == "session_cleanup":
                initialized.discard(session_id)
            else:
                await _send(websocket, {"type": "echo"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except RuntimeError as exc:
        # The chat BFF closes its per-turn socket once the turn ends; Starlette reports
        # that as RuntimeError("WebSocket is not connected"). A normal end, not a fault.
        if "not connected" not in str(exc).lower() and "disconnect" not in str(exc).lower():
            logger.error("%s socket error: %s", agent_id, exc)
        manager.disconnect(websocket)
    except Exception:  # noqa: BLE001
        logger.exception("%s socket closed on an unexpected error", agent_id)
        manager.disconnect(websocket)
