"""The Orchestrator WebSocket — one authenticated socket, one named agent per turn.

Route: `/sdlc/agent/orchestrator2/ws?ticket=<single-use ticket>[&run=<run_id>]`

IN  : {"type": "user_message", "text": ..., "agent": ..., "run_id": ...}
      Those four fields and no others — anything else on the frame is ignored.
      `project_id` IS READ FOR EVERY TURN, but from the `runs` row, never from
      this frame: it decides which models the turn may use and whose budget it
      spends, so a client-named project would let a caller borrow another
      project's grant and another project's cap. A `project_id` on the wire is
      still ignored, and now that is a load-bearing refusal rather than an
      oversight (see `_resolve_run`).
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

THE `run_id` ON THE WIRE IS NOT TRUSTED EITHER. It becomes the LangGraph `thread_id`
against persistent checkpointers, so an unowned id would join someone else's
conversation. `_resolve_run` proves the run belongs to the caller's tenant before it
is used for anything; a run that fails that check never reaches the agent. Being a
Project Admin says you may drive the Orchestrator — it does not say which runs are
yours, and those are two different questions.

Every inbound frame terminates: the socket answers with the agent's events (which
always end in `stream_end`) or with an `error` followed by `stream_end`. A turn
that fails is never allowed to end in silence, and an exception never kills the
socket — the client keeps its connection and gets a typed failure it can show.
"""
from __future__ import annotations

import json
import logging
from contextlib import aclosing
from typing import Any, NamedTuple, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agents_orchestrator.orchestrator2.dispatch import run_agent
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE

logger = logging.getLogger(__name__)

orchestrator2_router = APIRouter()

# The single role that may drive the Orchestrator. Kept as a named constant so the
# check reads as one decision in one place rather than a literal buried in a branch.
ORCHESTRATOR_ROLE = "project_admin"


class EventSerializationError(Exception):
    """An event could not be encoded as JSON, so it never reached the client.

    Distinct from `WebSocketDisconnect` ON PURPOSE. Both used to be laundered into
    "the peer hung up", which meant an event the socket could not encode was
    indistinguishable from a client closing the tab: the turn loop unwound, the
    connection was dropped, and NOTHING was ever sent — the exact swallow-and-go-quiet
    failure this engine exists to remove. A dead peer is not an error to report (there
    is nobody to report it to); an unencodable event is, and the socket stays open to
    say so.
    """


class RunNotAvailableError(Exception):
    """The named run is not one this caller may use — absent, another tenant's, or
    unverifiable. One type for all three so the message back to the client cannot
    accidentally reveal which."""


async def _send(websocket: WebSocket, payload: dict) -> None:
    """Encode and send one event.

    Encoding and transport fail differently and must not be conflated (see
    `EventSerializationError`): a `TypeError`/`ValueError` out of `json.dumps` is a bug
    in the event we are trying to send and is reported to the client; a failed write
    means the peer is gone and ends the turn loop.
    """
    try:
        raw = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise EventSerializationError(str(exc)) from exc
    try:
        await websocket.send_text(raw)
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


class RunSelection(NamedTuple):
    """What a verified run tells the socket: which model it selected, and which
    project it belongs to. A NamedTuple rather than a bare tuple so the third
    field cannot be read positionally by accident at a call site that predates it.
    """

    model_id: Optional[str]
    offering_id: Optional[str]
    project_id: Optional[str]


async def _resolve_run(run_id: str, tenant_id: str) -> RunSelection:
    """Confirm `run_id` belongs to the caller's tenant, and return its model selection
    and its project.

    THIS IS AN OWNERSHIP CHECK, NOT A LOOKUP, and it is the reason the client's
    `run_id` may be used at all. `run_id` arrives on the wire and reaches two things
    that both take it at face value:

      · this query, and
      · `dispatch.run_agent`, which makes it the LangGraph `thread_id` against
        PERSISTENT checkpointers (see `design_architecture_agent/agents/architecture.py`
        and `pm_agent/agents/schedule.py`) — so an unowned id would join that run's
        conversation thread.

    The first version of this function was copied from
    `copilot_api._run_model_offering`, which reads through `get_db_session_superuser()`
    — a session that BYPASSES row-level security — with no tenant predicate. Under a
    superuser session RLS is not a backstop, so the tenant filter has to be written
    out, and its absence let a Project Admin in tenant A name a run in tenant B and
    read its model selection. Here the read runs under the caller's tenant GUC AND
    carries an explicit `Run.tenant_id` predicate: either alone would do, and the
    point of having both is that neither is the only thing standing between tenants.

    Raises `RunNotAvailableError` when the run is absent, belongs to another tenant,
    or could not be verified (a DB failure). All three refuse, and all three raise the
    SAME exception so the message back to the client cannot distinguish "no such run"
    from "not yours" — the caller learns nothing about what exists elsewhere. Note the
    deliberate change of posture from the code this replaces: an unverifiable run is a
    REFUSAL, not a fall-through to the organization default. A turn that cannot prove
    which run it belongs to must not run.

    Returns the run's `(model_id, offering_id, project_id)` — all three are properties
    of the run, never something the client names on the wire. `(None, None, None)` is a
    legitimate value; it is no longer overloaded to also mean "could not check".

    WHY `project_id` IS READ HERE AND NOT TAKEN FROM THE MESSAGE. It is the scope that
    `resolve_model_for_run` uses to decide WHICH models this turn may use
    (`effective_project_offerings`) and WHOSE monthly budget it spends against
    (`check_budgets`). Both of those are enforcement, so the project id is an authority
    claim, not a routing hint. A client-supplied one would let a Project Admin name a
    project whose grant includes a model theirs does not, or whose budget still has room
    — which is precisely the scoping this task exists to establish, handed straight back
    to the caller. This row is already proven to belong to the caller's tenant two lines
    up, so its project is the one scope the socket can actually stand behind.

    A run with NO project (`project_id` is nullable since migration 0005 — webhook runs
    carry a provider key, not a local project UUID) yields `None`, which is passed
    through honestly rather than papered over. On a tenant with any grant rows that
    fails closed downstream, and it should: an ungoverned run is not a licence to use
    every model in the org.
    """
    if not run_id or not tenant_id:
        raise RunNotAvailableError("no run identified")

    run_uuid = _as_run_uuid(run_id)
    if run_uuid is None:
        # Run ids in this system are UUIDs. Refusing a non-UUID here keeps a
        # client-invented conversation key from ever becoming a graph thread_id, and
        # avoids sending unvalidated text into a UUID column.
        raise RunNotAvailableError(f"'{run_id}' is not a run id")

    try:
        from sqlalchemy import select

        from shared.db import get_db_session_for_tenant
        from shared.models.orm import Run

        async with get_db_session_for_tenant(tenant_id) as session:
            run = (
                await session.execute(
                    select(Run).where(
                        Run.id == run_uuid,
                        Run.tenant_id == _as_run_uuid(tenant_id),
                    )
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — cannot prove ownership ⇒ refuse
        logger.warning(
            "orchestrator2 could not verify run=%s for tenant=%s: %s — refusing "
            "(fail-closed)", run_id, tenant_id, exc,
        )
        raise RunNotAvailableError("the run could not be verified") from exc

    if run is None:
        logger.info(
            "orchestrator2 refused run=%s for tenant=%s — not this tenant's run",
            run_id, tenant_id,
        )
        raise RunNotAvailableError("no such run")

    project_id = getattr(run, "project_id", None)
    return RunSelection(
        model_id=getattr(run, "model_id", None),
        offering_id=getattr(run, "offering_id", None),
        # `Run.project_id` is a UUID column; every consumer downstream compares it as
        # text (offering-grant sets, budget scope keys), so normalise once here.
        project_id=str(project_id) if project_id else None,
    )


def _as_run_uuid(value: str) -> Any:
    """The value as a UUID, or None when it is not one.

    Returning None rather than the raw string (which is what `copilot_api._as_run_uuid`
    does) is what lets `_resolve_run` refuse a non-UUID outright instead of pushing
    caller-supplied text into a UUID comparison.
    """
    import uuid

    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


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
    # wins. Neither is trusted: whichever is used is checked against this caller's
    # tenant by `_resolve_run` before it is used for anything, because it becomes the
    # graph's thread_id. The socket reads no position from a run and writes none back
    # — ownership is the only question it asks of one.
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

            # Ownership gate. `run_id` came from the client and is about to become a
            # LangGraph thread_id on a persistent checkpointer, so it is checked
            # against this caller's tenant FIRST. Nothing below this line runs for a
            # run the caller may not read: a run they cannot see must not become
            # their conversation thread either. This is also where the turn's
            # PROJECT comes from — the run's, not the frame's, because the project
            # decides which models may be used and whose budget pays for them.
            try:
                model_id, offering_id, project_id = await _resolve_run(run_id, tenant_id)
            except RunNotAvailableError as exc:
                # One message for absent and for another tenant's run — the two
                # `RunNotAvailableError` cases are worded identically upstream so this
                # cannot become an existence oracle for runs the caller cannot see.
                await _fail(
                    websocket, "That run is not available.", detail=str(exc)
                )
                continue

            try:
                # `aclosing` is not decoration. `run_agent` is an async generator
                # that clears the turn's resolved model and run project in a
                # `finally`, and this socket does NOT stop on a mid-stream failure:
                # the `EventSerializationError` branch below keeps serving. A plain
                # `async for` that breaks out on a raising `_send` ABANDONS the
                # generator, and an abandoned asyncgen's `finally` is run by
                # CPython's finalizer in a NEW TASK whose context is a copy — so the
                # clear would land on that copy and this socket's context would keep
                # the previous turn's BYOK key set. Closing it here runs that
                # `finally` inline, in this task, on every path including the raising
                # one. (`run_agent` also clears on entry, which is what covers a
                # consumer that forgets to do this; this makes the exit-clear real
                # rather than nominal.)
                async with aclosing(
                    run_agent(
                        agent_id,
                        text=text,
                        run_id=run_id,
                        tenant_id=tenant_id,
                        model_id=model_id,
                        offering_id=offering_id,
                        # From the verified `runs` row. `msg` may well carry a
                        # `project_id`; it is never consulted.
                        project_id=project_id,
                    )
                ) as events:
                    async for event in events:
                        await _send(websocket, event)
            except WebSocketDisconnect:
                raise
            except EventSerializationError as exc:
                # An event the socket could not encode. This branch exists because
                # without it the failure was laundered into WebSocketDisconnect and
                # the connection just went quiet: the client saw a hang-up it did not
                # cause and never learned that a frame had been dropped. The peer is
                # alive here, so tell it — and keep serving.
                logger.exception(
                    "orchestrator2 dropped an unencodable event (agent=%s run=%s)",
                    agent_id, run_id,
                )
                await _fail(
                    websocket,
                    "The agent produced an event that could not be sent and was dropped.",
                    detail=str(exc),
                )
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
