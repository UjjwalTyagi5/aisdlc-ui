"""Code Review Agent API — FastAPI router (interactive standalone path).

WebSocket /ws  — real-time streaming chat ("review this branch/PR")
POST /chat/    — REST (orchestrator / non-streaming)

The review TARGET (cloned repo + computed diff) is prepared out-of-band by
POST /code-review/{project_id}/review/prepare using the SAME session_id; this
handler reads that prepared ReviewSessionState, injects the diff into the agent's
context, streams the review, then persists the produced artifact to a Run.

Like the dev chat, this WS path bypasses the pipeline capability spine, so BYO
MCP tools are injected manually.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from uuid import uuid4
from typing import List

from fastapi import APIRouter, Form, Request, UploadFile, File, WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from agents_orchestrator.code_review_agent.agents.reviewer import app as review_app
from agents_orchestrator.code_review_agent.prompts.review_prompt import CODE_REVIEW_SYSTEM_PROMPT
from agents_orchestrator.code_review_agent.config.session_state import (
    clear_session,
    get_prepared,
    get_session,
)
from config.agent_context import parse_pipeline_context, set_agent_folder
from shared.tools.document_tools import (
    attachment_message_contents,
    attachment_paths_from_context,
)
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.connection_manager import manager
from config.env import AGENT_RUNTIME_MODE
from config.websocket_utils import set_websocket_context
from config.ws_helper import set_session_id, set_user_id
from shared.authz.agent_access import assert_agent_access_for_chat
from shared.audit import AuditCallbackHandler
from shared.observability import agent_trace
from shared.audit.service import audit_service
from shared.db import get_db_session_for_tenant
from shared.services.conversation_service import persist_turn
from shared.services.prompt_runtime import prompt_override_scope
from shared.services.skill_runtime import skill_context_scope
from shared.services.standalone_prompt import resolve_agent_turn


def _ctx_user_id():
    """The user in session context, or None. Never raises: these paths also run in a
    worker, where no user was ever set."""
    from config.ws_helper import get_user_id  # noqa: PLC0415 — import cycle at module load

    return get_user_id() or None


logger = logging.getLogger("code_review_agent")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(_h)

code_review_router = APIRouter()


def _project_id_from_message(message_data: dict) -> str | None:
    pc = parse_pipeline_context(message_data.get("pipeline_context") or {})
    return (
        message_data.get("project_id")
        or (message_data.get("context") or {}).get("project_id")
        or (pc.get("project_id") if isinstance(pc, dict) else None)
    )


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _review_context_block(s) -> str:
    """Render the prepared target for injection into the conversation.

    A WHOLE-BRANCH target has no diff: the block names the branch and lists its files, and
    the agent reads what it needs (list_repo_files / read_repo_file). A diff target shows
    the diff, as before.
    """
    if s.mode == "repo":
        inv = s.inventory or {}
        totals = inv.get("totals") or {}
        listed = [f for f in inv.get("files", []) if f.get("reviewable")][:200]
        lines = "\n".join(f"- {f['path']} ({f['lines']} lines, {f['language']})" for f in listed)
        languages = ", ".join(f"{k} ({v})" for k, v in (inv.get("languages") or {}).items())
        return (
            f"You are reviewing the WHOLE BRANCH '{s.source_branch}' in repo '{s.repo_name}' "
            f"at commit {s.head_sha[:12]} — every file on it, not a diff.\n"
            f"The branch has {totals.get('files', 0)} files, {totals.get('reviewable_files', 0)} of them "
            f"reviewable code ({totals.get('lines', 0)} lines). Languages: {languages or 'unknown'}.\n\n"
            f"Reviewable files:\n{lines}\n"
        )
    if not s.diff_text and not s.changed_files:
        return ""
    if s.mode == "pr":
        target = f"PR #{s.pr_id} \"{s.pr_title}\" ({s.source_branch} → {s.base_branch})"
    else:
        target = f"branch '{s.source_branch}' vs base '{s.base_branch}'"
    files = ", ".join(f["path"] for f in s.changed_files[:40]) or "(none)"
    return (
        f"You are reviewing {target} in repo '{s.repo_name}'.\n"
        f"Changed files ({len(s.changed_files)}): {files}\n\n"
        f"Unified diff under review:\n```diff\n{s.diff_text}\n```\n"
    )


async def _saved_review_note(s, tenant_id: str, project_id: str) -> str:
    """What is already on file for THIS target, for a conversation that starts after it.

    A new chat asked only "Send the review report for approval." ran a whole new review
    first — nothing told it a report already existed, or its name — and filed a seventh
    copy of the same report before it got to the request.
    """
    if not (s.repo_name and s.head_sha and tenant_id and project_id):
        return ""
    from shared.routers.code_review_workspace import _find_unchanged_review  # noqa: PLC0415

    async with get_db_session_for_tenant(tenant_id) as db:
        run = await _find_unchanged_review(
            db, tenant_id=tenant_id, project_id=project_id, repo_name=s.repo_name,
            head_sha=s.head_sha, base_sha=s.base_sha, mode=s.mode or None,
        )
    if run is None:
        return ""
    art = run.code_review_artifacts or {}
    report = (art.get("document") or {}).get("filename")
    when = f" on {run.created_at:%d %b %Y %H:%M} UTC" if getattr(run, "created_at", None) else ""
    return (
        f"\nA review of this exact target is already saved{when}: "
        f"{art.get('merge_recommendation') or 'no recommendation'}, "
        f"{len(art.get('findings') or [])} finding(s)"
        + (f"; its report is '{report}' in the project's Documents" if report else "")
        + ". Do NOT review again unless the user asks for a new review — a request to send, "
        "raise, publish or explain the report is about that saved report.\n"
    )


async def _load_mcp_tools(tenant_id: str, project_id: str | None) -> list:
    from config.env import MCP_ENABLED

    if not MCP_ENABLED or not tenant_id:
        return []
    try:
        from sqlalchemy import select
        from shared.services import mcp_registry, mcp_client
        from shared.models.orm import Project

        server_ids: list[str] | None = None
        if project_id:
            async with get_db_session_for_tenant(tenant_id) as session:
                proj = (
                    await session.execute(select(Project).where(Project.id == uuid.UUID(project_id)))
                ).scalar_one_or_none()
            server_ids = ((proj.mcp_servers if proj else None) or {}).get("code_review") or None
        # No fallback to all-active servers: only BYO servers explicitly assigned to
        # this agent (via the Agents & Capabilities panel) are loaded — otherwise the
        # agent picks up unrelated tools and wastes turns on them.
        if not server_ids:
            return []
        configs = await mcp_registry.resolve_server_configs(tenant_id, server_ids, agent_id="code_review")
        tools = await mcp_client.load_tools(configs)
        logger.info("Code-review chat: loaded %d MCP tool(s)", len(tools))
        return tools
    except Exception as exc:  # noqa: BLE001 — MCP must never break the chat
        logger.warning("Code-review chat MCP load failed: %s", exc)
        return []


async def _stream(
    state: dict, config: dict, websocket: WebSocket, session_id: str,
    before_end=None,
) -> str:
    """Stream the graph's text to the client. `before_end` runs BEFORE `stream_end`.

    ORDERING IS THE WHOLE POINT of that hook. `stream_end` is what flips the page out
    of its busy state, and the page reacts by refetching the review list and opening
    the newest one. Persisting after that signal is a race the UI loses every time:
    it refetched, the row did not exist yet, so a review that HAD been saved correctly
    still left the Summary/Findings tabs on "Diff ready — run the review". Persist
    first, then say the turn is over.
    """
    from langgraph.errors import GraphRecursionError

    from shared.services.answer_stream import AnswerStream  # noqa: PLC0415

    final, failure = "", None
    # THE AGENT'S ANSWERS ONLY — not the text it writes before calling a tool, not tool
    # results, not the graph's own submit nudge (a HumanMessage that once reached the chat
    # as "You wrote the review as prose instead of submitting it…"). See AnswerStream.
    answers = AnswerStream(_extract_text)

    async def _send(text: str) -> None:
        nonlocal final
        if not text:
            return
        final += text
        await manager.send_personal_message(
            json.dumps({"type": "stream_chunk", "content": text, "session_id": session_id}),
            websocket,
        )

    try:
        async for chunk in review_app.astream(state, stream_mode="messages", config=config):
            await _send(answers.feed(chunk[0] if isinstance(chunk, tuple) else chunk))
        await _send(answers.close())
    except GraphRecursionError:
        await _send(answers.close())
        notice = "Step limit reached for this review. Send another message to continue."
        await manager.send_personal_message(
            json.dumps({"type": "stream_chunk", "content": f"\n\n> ⚠️ {notice}", "session_id": session_id}),
            websocket,
        )
    except Exception as e:
        # A FAILURE AFTER SOME TEXT IS STILL A FAILURE. This used to be swallowed once
        # anything had streamed, and the turn went on to announce "Review complete" over
        # a review that stopped halfway. The caller reports it and ends the run as failed.
        logger.error("Code-review stream error: %s", e)
        failure = e
    if before_end is not None:
        # Runs on failure too: a review the agent SUBMITTED before the model failed (the
        # closing summary is the last call) is a real review and is kept.
        try:
            await before_end()
        except Exception:  # noqa: BLE001 — a failed save must not strand the client
            logger.exception("Code-review: before_end hook failed for session %s", session_id)
    if failure is not None:
        raise failure
    await manager.send_personal_message(
        json.dumps({"type": "stream_end", "session_id": session_id}), websocket
    )
    return final


async def _write_review_document(session_id: str, artifact: dict) -> dict:
    """Write the Code Review & Security Report, file it as a draft in the project's
    Documents and announce it. Returns {filename, url} — or {error}, which the review
    keeps and the page shows: a review whose report could not be written says so."""
    import os  # noqa: PLC0415

    from config import sdlcSettings  # noqa: PLC0415
    from config.env import AGENTIC_BASE_URL  # noqa: PLC0415
    from agents_orchestrator.code_review_agent.review_document import write_review_report  # noqa: PLC0415
    from shared.services.chat_artifacts import register_generated_file  # noqa: PLC0415

    s = get_session(session_id)
    user_id = _ctx_user_id() or s.owner_id or "code_review"
    out_dir = f"{sdlcSettings().FILES}/{user_id}/code_review/{session_id}/output"
    try:
        docx_path, _md_path = await asyncio.to_thread(write_review_report, artifact, out_dir)
    except Exception as exc:  # noqa: BLE001 — recorded on the review, not swallowed
        logger.exception("Code review report not written for session %s", session_id)
        return {"error": f"The report document could not be written ({type(exc).__name__}: {exc})"}
    filename = os.path.basename(docx_path)
    url = f"{AGENTIC_BASE_URL}/generated/{user_id}/code_review/{session_id}/output/{filename}"
    await register_generated_file(
        filename, docx_path, url, stage="code_review",
        note="Code review & security report generated by the Code Review agent.",
    )
    await manager.broadcast({
        "type": "file_generated",
        "session_id": session_id,
        "filename": filename,
        "url": url,
        "file_size": os.path.getsize(docx_path),
        "message": f"Generated file: {filename}",
    })
    return {"filename": filename, "url": url}


async def _persist_review_to_run(session_id: str, project_id: str | None, tenant_id: str) -> None:
    s = get_session(session_id)
    if not s.last_artifact or not project_id or not tenant_id:
        return
    from shared.models.orm import Run

    artifact = dict(s.last_artifact)
    # The report is part of the review: written before the row, so the saved review
    # carries its link (or the reason there is none).
    artifact["document"] = await _write_review_document(session_id, artifact)
    # EVERY COMPLETED REVIEW IS SAVED, including a re-review of a commit already
    # reviewed. This used to skip the insert when any run already carried this
    # head_sha, which SILENTLY DISCARDED the review the reader had just deliberately
    # run: the agent streamed a full review into the chat, nothing reached the
    # Summary/Findings tabs, and no error said why. Re-running a review on an
    # unchanged commit is a legitimate thing to ask for (a changed prompt, a
    # different model, a second opinion), and the run history is the point of the
    # Past reviews switcher.
    #
    # Idempotency never depended on that guard: `last_artifact` is falsy-checked
    # above and cleared below, so the two call sites (WS and REST) cannot persist one
    # artifact twice regardless of head_sha.
    try:
        async with get_db_session_for_tenant(tenant_id) as db:
            db.add(
                Run(
                    project_id=uuid.UUID(project_id),
                    tenant_id=uuid.UUID(tenant_id),
                    stage="code_review",
                    status="completed",
                    trigger="manual",
                    current_stage="code_review",
                    code_review_artifacts=artifact,
                    # Whoever is in session context, so this run is not exempt from the
                    # no-self-approval rule (0038). These rows are created already-completed
                    # with no gate pending, so the rule is not reachable through them today —
                    # recorded anyway, because "it cannot gate yet" is a property of this call
                    # site that a later change could quietly remove.
                    created_by=_ctx_user_id(),
                )
            )
        await manager.broadcast({
            "type": "review_created",
            "session_id": session_id,
            "merge_recommendation": artifact.get("merge_recommendation"),
            "findings_count": len(artifact.get("findings") or []),
        })
        s.last_artifact = None
    except Exception as exc:
        logger.warning("Failed to persist code review for session %s: %s", session_id, exc)


@code_review_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    ticket = websocket.query_params.get("ticket", "")
    claims = await _redeem_ws_ticket(ticket) if ticket else None
    if claims is None:
        await websocket.close(code=4401, reason='{"error": "invalid_or_expired_ticket"}')
        return
    if AGENT_RUNTIME_MODE == "enterprise":
        expected = websocket.query_params.get("tenant_id", "")
        if expected and claims.get("tenant_id", "") != expected:
            await websocket.close(code=4403, reason='{"error": "tenant_mismatch"}')
            return
    set_agent_folder("orchestrator")
    await manager.connect(websocket)
    user_id = claims.get("user_id", "")
    tenant_id = claims.get("tenant_id", "") if claims else ""
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            session_id = message_data.get("session_id", str(uuid4()))
            manager.register_session(websocket, session_id)
            set_websocket_context(manager, session_id)
            set_session_id(session_id)
            set_user_id(user_id)

            if message_data.get("type") in ("user_message_with_files", "user_message"):
                await _process_ws_message(message_data, websocket, user_id, tenant_id)
            elif message_data.get("type") == "session_cleanup":
                clear_session(session_id)
            else:
                await manager.send_personal_message(
                    json.dumps({"type": "echo", "message": data}), websocket
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("Code-review WS error: %s", e)
        manager.disconnect(websocket)


async def _process_ws_message(message_data: dict, websocket: WebSocket, user_id, tenant_id: str = ""):
    session_id = message_data.get("session_id", str(uuid4()))
    try:
        project_id = _project_id_from_message(message_data)
        s = get_session(session_id)
        if tenant_id and not s.tenant_id:
            s.tenant_id = tenant_id
        if project_id and not s.project_id:
            s.project_id = project_id

        # Gate every message, not just the first — a session can be reused across
        # projects on the client side, and the ticket only proves who the caller is,
        # not which project they may act on (nor, on its own, that they're even a
        # member of it — see assert_agent_access_for_chat's docstring).
        _effective_project = project_id or s.project_id
        _effective_tenant = tenant_id or s.tenant_id
        async with get_db_session_for_tenant(_effective_tenant) as _access_db:
            _effective_project = await assert_agent_access_for_chat(
                _access_db, tenant_id=_effective_tenant, project_id=_effective_project,
                user_id=user_id, agent_id="code_review",
            )
        project_id = _effective_project
        s.project_id = _effective_project

        # Bind the prepared review target (cloned repo + diff, or a whole branch) —
        # keyed by project, like the dev workspace, so it survives the chat's own
        # session id. Without this the agent has nothing to review.
        #
        # REBOUND WHEN A NEW TARGET IS PREPARED. This used to bind once per session, so
        # selecting a second target on the same page kept reviewing the first. The
        # security scan and the files read belong to a target, so they reset with it.
        prepared = get_prepared(tenant_id or s.tenant_id, project_id or s.project_id)
        new_target = bool(prepared) and (
            not s.target_bound
            or (prepared.get("prepared_at") and prepared.get("prepared_at") != s.prepared_at)
        )
        if new_target:
            # The token belongs to the target it was prepared with; a record restored
            # from disk has none, so it is re-resolved below rather than inherited.
            s.pat = ""
            for k, v in prepared.items():
                setattr(s, k, v)
            s.prepared_at = prepared.get("prepared_at") or ""
            s.security = None
            s.files_read = []
            s.last_artifact = None
            s.system_injected = False
            s.target_bound = True
        if new_target:
            # A RECORD RESTORED FROM DISK CARRIES NO TOKEN, deliberately — one
            # credential exists, in the credential store, and a copy beside the
            # checkout would outlive its revocation. So it is resolved here, as
            # the person the target was prepared for.
            #
            # An empty result is not fatal: reading a checked-out repository needs
            # no token, and only the push paths do — they say so themselves rather
            # than failing halfway through a git command.
            if not getattr(s, "pat", ""):
                from shared.services import prepared_targets  # noqa: PLC0415

                s.pat = await prepared_targets.resolve_secret(
                    tenant_id=s.tenant_id or tenant_id or "",
                    project_id=s.project_id or project_id or "",
                    owner_id=str(getattr(s, "owner_id", "") or ""),
                    provider=str(getattr(s, "provider", "") or ""),
                )

        incoming = message_data.get("messages", [])
        if incoming:
            user_text = "\n".join(
                (m.get("content", "") if isinstance(m, dict) else str(m)) for m in incoming
            )
        else:
            user_text = (
                message_data.get("task_intent")
                or message_data.get("text")
                or (
                    "Please review the whole branch, run the security review, and submit your findings."
                    if s.mode == "repo" else
                    "Please review the prepared change and submit your findings."
                )
            )

        # project_id is load-bearing for model resolution, not just for logging:
        # resolve_model_for_run filters the tenant's offerings through
        # effective_project_offerings(tenant, project), and a None project filters
        # EVERY offering out ("grants configured but none apply to this project").
        # This agent attaches AuditCallbackHandler directly rather than going through
        # shared/observability/callbacks.py, which is the only thing that threads the
        # run's project into the contextvar the resolver otherwise reads — so pass it
        # explicitly instead of depending on a contextvar nothing here sets.
        # offering_id likewise: AgentState has always declared it, but nothing filled it,
        # so a project pinned to a specific provider connection was silently ignored.
        _model_state = {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "model_id": message_data.get("model_id"),
            "offering_id": message_data.get("offering_id"),
        }

        first = not s.system_injected
        if first:
            ctx = _review_context_block(s)
            if ctx:
                ctx += await _saved_review_note(
                    s, tenant_id or s.tenant_id, project_id or s.project_id
                )
            content = (ctx + "\n" + user_text) if ctx else user_text
            state = {"messages": [HumanMessage(content=content)], **_model_state}
            s.system_injected = True
        else:
            state = {"messages": [HumanMessage(content=user_text)], **_model_state}

        # THE FILE THE PERSON ATTACHED, read here rather than left as a path. Uploads go
        # through POST /conversations/{id}/attachments and arrive as refs on
        # pipeline_context; `attachment_message_contents` extracts the text and names
        # anything it could not read.
        #
        # WITHOUT THIS the chip appears in the transcript and the agent answers "I don't
        # see any document attached" — the failure that looks like success, and one this
        # platform has already shipped twice on other agents.
        for _content in attachment_message_contents(
            attachment_paths_from_context(message_data.get("pipeline_context"))
        ):
            state["messages"].append(HumanMessage(content=_content))

        # THE TRANSCRIPT, like every other agent's (§11A). The chat drawer listed this
        # agent's past conversations and opened each one empty: nothing was ever written.
        # Best-effort — persist_turn never fails a turn.
        await persist_turn(
            session_id, "user", user_text, tenant_id=tenant_id or s.tenant_id or None,
            author_id=str(user_id) if user_id else None,
        )

        audit = AuditCallbackHandler(audit_service, run_id=session_id, tenant_id=tenant_id)
        _lf_cbs, _lf_meta = await agent_trace(session_id=session_id, tenant_id=tenant_id, user_id=user_id, agent_type="code_review", project_id=_project_id_from_message(message_data))
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 120, "callbacks": [audit, *_lf_cbs], "metadata": _lf_meta}

        await manager.broadcast(
            {"type": "message_received", "session_id": session_id, "message": "Reviewing…"}
        )

        from shared.tools.mcp_runtime import set_mcp_tools, clear_mcp_tools
        if not s.mcp_loaded:
            s.mcp_tools = await _load_mcp_tools(tenant_id, project_id)
            s.mcp_loaded = True
        set_mcp_tools(s.mcp_tools)
        # Agent-profile prompt layer (design §3.4): the reviewer graph node self-injects via
        # get_prompt_override("code_review") or CODE_REVIEW_SYSTEM_PROMPT, so resolve the
        # org/workspace/project profile over the BARE constant (the node re-appends its own
        # MCP note) and set it into the prompt_runtime contextvar for this turn. Fail-soft.
        _injected, _skills = await resolve_agent_turn(
            "code_review", CODE_REVIEW_SYSTEM_PROMPT,
            tenant_id or s.tenant_id, project_id or s.project_id,
        )
        # Saved BEFORE `stream_end` reaches the client — see `_stream`'s docstring. The
        # page opens the newest review the moment it stops being busy, so the row has to
        # exist by then or a correctly-saved review still shows as "Diff ready".
        async def _save() -> None:
            await _persist_review_to_run(
                session_id, project_id or s.project_id, tenant_id or s.tenant_id
            )

        try:
            async with prompt_override_scope("code_review", _injected):
                async with skill_context_scope("code_review", _skills):
                    reply = await _stream(state, config, websocket, session_id, before_end=_save)
        finally:
            clear_mcp_tools()
        await persist_turn(
            session_id, "agent", reply, tenant_id=tenant_id or s.tenant_id or None,
            author_id="code_review", model=message_data.get("model_id"),
        )
        await manager.broadcast({
            "type": "activity_update",
            "activity": {
                "id": str(uuid4()), "type": "complete",
                "session_id": session_id, "message": "Review complete", "time": "Just now",
            },
        })
    except Exception as e:
        logger.error("Code-review WS process error: %s", e)
        reason = _failure_reason(e)
        await manager.send_agent_response("Error Agent", f"An error occurred: {reason}", session_id)
        # A reopened conversation shows that this turn failed, and why.
        await persist_turn(
            session_id, "agent", f"An error occurred: {reason}", tenant_id=tenant_id or None,
            author_id="code_review",
        )
        # THE TURN MUST SAY IT IS OVER, and that it failed. The chat BFF ends a run on
        # activity_update{complete} or agent_completed{success: false}; this path sent
        # neither, so a model whose key had been revoked left the drawer on "Agent is
        # working" with the composer locked — the user could not even retry.
        await manager.broadcast({
            "type": "agent_completed", "session_id": session_id,
            "success": False, "error": reason,
        })
        await manager.send_personal_message(
            json.dumps({"type": "stream_end", "session_id": session_id}), websocket
        )
        await manager.broadcast({
            "type": "activity_update",
            "activity": {
                "id": str(uuid4()), "type": "complete",
                "session_id": session_id, "message": "Review failed", "time": "Just now",
            },
        })


#: Exceptions raised by a model provider's SDK. Their text can echo a BYOK key and does
#: not say who fixes the problem, so they are answered by `friendly_model_error`.
_PROVIDER_MODULES = frozenset({"litellm", "openai", "anthropic", "httpx"})


def _failure_reason(exc: BaseException) -> str:
    """What the person is told when a review turn fails.

    A provider failure gets the actionable sentence ("the provider rejected the configured
    credential…"), never the provider's own text. Anything else is this platform's own
    error — a denied project, a failed git command — and its message IS the reason.
    """
    from shared.services.model_errors import friendly_model_error  # noqa: PLC0415

    if (type(exc).__module__ or "").split(".")[0] in _PROVIDER_MODULES:
        return friendly_model_error(exc)
    return str(exc) or type(exc).__name__


@code_review_router.post("/chat/")
async def chat(
    request: Request,
    session_id: str = Form(...),
    user_id: str = Form(...),  # kept for wire compatibility; NOT trusted for identity
    text: str = Form(None),
    pipeline_context: str = Form(None),
    uploaded_files: List[UploadFile] = File(None),
):
    # Identity comes from the verified session, never from the form body — see
    # assert_agent_access_for_chat's docstring / the Security agent's chat() for why
    # the Form field alone can no longer be trusted here.
    real_user_id = getattr(request.state, "user_id", "") or ""
    real_tenant_id = getattr(request.state, "tenant_id", "") or ""

    s = get_session(session_id)
    # This route has no project_id Form field — the review target (and its project)
    # is bound out-of-band by POST /review/prepare into the session, same as the
    # diff/target fields below. Check against whatever project the session is
    # already bound to.
    async with get_db_session_for_tenant(str(real_tenant_id)) as _access_db:
        s.project_id = await assert_agent_access_for_chat(
            _access_db, tenant_id=str(real_tenant_id), project_id=s.project_id or "",
            user_id=str(real_user_id), agent_id="code_review",
        )

    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(real_user_id)
    set_agent_folder("orchestrator")

    first = not s.system_injected
    user_text = text or "Please review the prepared change and submit your findings."
    if first:
        ctx = _review_context_block(s)
        content = (ctx + "\n" + user_text) if ctx else user_text
        state = {"messages": [HumanMessage(content=content)]}
        s.system_injected = True
    else:
        state = {"messages": [HumanMessage(content=user_text)]}

    # Same as the socket path: refs uploaded to this session, extracted server-side.
    for _content in attachment_message_contents(
        attachment_paths_from_context(parse_pipeline_context(pipeline_context or {}))
    ):
        state["messages"].append(HumanMessage(content=_content))

    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 120}
    # Agent-profile prompt layer (design §3.4): resolve over the BARE constant (the reviewer
    # node re-appends its MCP note) using the session's bound tenant/project. Fail-soft.
    _injected, _skills = await resolve_agent_turn(
        "code_review", CODE_REVIEW_SYSTEM_PROMPT, s.tenant_id, s.project_id
    )
    final = ""
    async with prompt_override_scope("code_review", _injected), \
            skill_context_scope("code_review", _skills):
        async for chunk in review_app.astream(state, stream_mode="messages", config=config):
            msg = chunk[0] if isinstance(chunk, tuple) else chunk
            if isinstance(msg, ToolMessage):
                continue
            if hasattr(msg, "content") and msg.content and not (hasattr(msg, "tool_calls") and msg.tool_calls):
                final += _extract_text(msg.content)
    await _persist_review_to_run(session_id, s.project_id, s.tenant_id)
    return {"conversation_id": session_id, "responses": final or "No response generated."}


@code_review_router.get("/sessions")
async def get_sessions():
    return {"current_session": "code_review"}
