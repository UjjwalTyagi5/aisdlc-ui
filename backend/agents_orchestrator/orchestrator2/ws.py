"""The Orchestrator WebSocket — one authenticated socket, one named agent per turn.

Route: `/sdlc/agent/orchestrator2/ws?ticket=<single-use ticket>[&run=<run_id>]`

IN  : {"type": "user_message", "text": ..., "run_id": ..., "agent": <optional>}
      Those four fields and no others — anything else on the frame is ignored,
      `history` included (see below).
      `agent` is OPTIONAL and is an override; omitting it is the normal path and
      means "let the Orchestrator choose".
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

NOTHING IS CHOSEN SILENTLY. A frame naming no `agent` — the normal case — is ROUTED:
`router.route` reads the message, and this connection's earlier turns, for meaning and
picks one of the nine, or answers directly. The choice is always announced first, in
`agent.selected`, with the reason. An `agent` on the frame OVERRIDES the router and is
kept for exactly that: the router is a model, it will sometimes be wrong, and with no
way to force an agent a wrong decision would be unrecoverable inside the conversation.

Phase 2 refused a frame with no agent instead, because there was no router yet. The
invariant that refusal stood for is unchanged — the old engine picked an agent and told
nobody, so a wrong pick stayed invisible until the answer made no sense. A routed turn
is still a choice the user can see and correct in one turn.

THE CONVERSATION THE ROUTER READS IS THIS SOCKET'S, NOT THE CLIENT'S. It is held in
memory for the life of the connection (`_remember`), never taken from a frame: a
client-supplied history would be caller-controlled text steering a routing decision.
It is routing input only, and nothing about authority is read from it. A reconnect
starts empty, so the first turn after one routes on the message alone — recoverable,
because naming an agent always overrides.

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

from agents_orchestrator.orchestrator2.context import handoff_context
from agents_orchestrator.orchestrator2 import sessions
from agents_orchestrator.orchestrator2.dispatch import run_agent
from agents_orchestrator.orchestrator2.router import (
    _HISTORY_LIMIT as _ROUTER_HISTORY_LIMIT,
    route,
)
from config.auth.ws_ticket import redeem_ws_ticket as _redeem_ws_ticket
from config.env import AGENT_RUNTIME_MODE

logger = logging.getLogger(__name__)

#: The largest inbound frame this socket will parse.
#:
#: `_remember` bounds what is RETAINED — twenty 1 MB messages no longer means 20 MB
#: held and re-sent to the routing model every turn. It says nothing about what
#: ARRIVES, and a single 20 MB frame was received and json-parsed before anything
#: looked at its size. This is that half.
#:
#: 1 MB is far above any real message — a pasted PRD is a few tens of KB — and far
#: below a frame that could hurt. uvicorn's `ws_max_size` is the real defence, at the
#: protocol layer, and is set for the `python process_api.py` path; the CLI path takes
#: `--ws-max-size`, which this code cannot enforce, so the check below is the layer we
#: always control.
MAX_INBOUND_FRAME_BYTES = 1_000_000


def _frame_too_large(raw: str) -> bool:
    """True when `raw` must be refused unparsed.

    Measured in BYTES, not characters: a message of astral-plane characters is four
    bytes each, so a length check would let a frame four times the limit through —
    exactly what this bounds.
    """
    return len(raw.encode("utf-8", errors="ignore")) > MAX_INBOUND_FRAME_BYTES

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


async def _resolve_permissions(user_id: str, tenant_id: str) -> Optional[list]:
    """The caller's permission list, resolved once per CONNECTION — never from the client.

    Separate from `_resolve_platform_role`, which resolves its own copy internally. That
    is one extra read per connection (not per turn), and it is the price of leaving a
    security-critical function's signature alone; the alternative, threading the list
    out of the role resolver, touches every one of its call sites. Worth revisiting if a
    connection ever becomes expensive to open.

    FAILS CLOSED, and the distinction matters: `None` means "could not tell", `[]` means
    "this caller holds nothing". `read_scope`'s own docstrings warn against conflating
    those two — treating a real empty answer as "no filter" is what would show a
    brand-new account the whole organization — so a resolver failure returns `None` and
    the caller refuses the connection rather than continuing with an empty list that
    reads as a legitimate verdict.
    """
    if not user_id or not tenant_id:
        return None
    try:
        from shared.authz.resolver import resolve_permissions_for_user

        return list(await resolve_permissions_for_user(user_id, tenant_id))
    except Exception as exc:  # noqa: BLE001 — incl. PermissionResolutionError
        logger.warning(
            "orchestrator2 permission resolution failed for user=%s tenant=%s: %s — "
            "refusing (fail-closed)", user_id, tenant_id, exc,
        )
        return None


async def _project_admin_tier_for_run(
    project_id: str, tenant_id: str, *, user_id: str, permissions: list
) -> Optional[str]:
    """Does this caller RUN the project this run belongs to? The tier, or None.

    THE RULE IS NOT WRITTEN HERE. `shared.authz.project_scope.project_admin_tier_for` is
    the same function the HTTP routers reach through `project_admin_tier`, addressed by
    identity because a WebSocket has no `Request` to carry `request.state`. Writing a
    second `role_bindings` query on this socket is the mistake `read_scope.live_binding`
    records — one rule written four different ways, disagreeing, so an elevation that had
    lapsed kept granting on one path while being refused on another.

    The project row is read TENANT-SCOPED, the same shape as `_resolve_run`: under the
    caller's tenant GUC and with an explicit `Project.tenant_id` predicate, so a project
    id belonging to another tenant cannot be resolved even though the id reaching here
    came from a row already proven to be this tenant's.

    Returns None — a refusal — when the project cannot be found. An unresolvable project
    is not a licence to run against it.
    """
    from sqlalchemy import select

    from shared.authz.project_scope import project_admin_tier_for
    from shared.db import get_db_session_for_tenant
    from shared.models.orm import Project

    tenant_uuid = _as_run_uuid(tenant_id)
    project_uuid = _as_run_uuid(project_id)
    if project_uuid is None or tenant_uuid is None:
        return None

    async with get_db_session_for_tenant(tenant_id) as session:
        project = (
            await session.execute(
                select(Project).where(
                    Project.id == project_uuid,
                    Project.tenant_id == tenant_uuid,
                )
            )
        ).scalar_one_or_none()
        if project is None:
            return None
        return await project_admin_tier_for(
            session, user_id=user_id, permissions=permissions, project=project
        )


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
    carries an explicit `Run.tenant_id` predicate.

    THE PREDICATE IS THE ONE DOING THE WORK, and this sentence used to say the
    opposite: "either alone would do … neither is the only thing standing between
    tenants." In this deployment the GUC is INERT. `POSTGRES_CONN_STRING` connects as
    `postgres`, which is `rolsuper` and `rolbypassrls`; PostgreSQL superusers bypass
    row-level security unconditionally and `FORCE` does not apply to them, so the
    policy on `runs` never fires. Measured, not assumed — it is what
    `tests/test_m7_rbac.py::test_uwr_cross_tenant_empty` and
    `test_grant_cross_tenant_isolated` are failing on.

    So do not delete this predicate as redundant belt-and-braces. Until the
    application is given a non-superuser role (`POSTGRES_MIGRATIONS_CONN_STRING`
    already exists to keep `postgres` for migrations), it is the only thing standing
    between tenants on this read.

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


# How many turns of this connection the router is given. Matched to
# `router._HISTORY_LIMIT`, which truncates to the same number anyway — keeping the
# socket's own list at that size means the cap is enforced where the memory is held,
# not only where it is read.
_HISTORY_TURNS = _ROUTER_HISTORY_LIMIT

# And a bound in CHARACTERS, because a turn count is not a memory bound. Twenty turns
# of 1 MB each is 20 MB held on the socket and forwarded to the routing model on every
# subsequent turn; the frame size that gets it there is the server's inbound limit,
# which is not this module's to set. ~24k chars is roughly 6k tokens — enough
# conversation for "and now?" to be routable, and small enough that the routing call
# cannot grow without bound. Deliberately the same order as
# `context.MAX_CONTEXT_CHARS`, which bounds the other text a turn carries.
_HISTORY_MAX_CHARS = 24_000
# The longest a single turn may be before it is shortened. A fifth of the budget, so
# one long message cannot crowd out the four before it.
_HISTORY_MAX_ENTRY_CHARS = _HISTORY_MAX_CHARS // 5
_TRUNCATION_NOTE = "… [truncated]"


def _remember(history: list[dict], role: str, text: str) -> None:
    """Append one turn to this CONNECTION's conversation, oldest dropped first.

    `role` is `user` or `agent` — this platform's vocabulary
    (`conversation_messages.role`), which is what `router._history_messages` reads.

    THE HISTORY IS THE SOCKET'S, NOT THE CLIENT'S. A `history` field on an inbound
    frame is ignored like every other unexpected field: it would be caller-controlled
    text steering a routing decision, and while a caller can already force an agent
    outright with `agent`, that at least SAYS what it is doing in `agent.selected`.
    Nothing about authority is ever read from this list; it is routing input only.

    It lives for the connection and no longer. Server-backed sessions are not part of
    this phase, so a reconnect starts empty and the first turn after one routes on the
    message alone — recoverable, because naming an agent always overrides.

    BOUNDED IN TURNS AND IN CHARACTERS. The turn count alone was not a memory bound:
    twenty 1 MB messages meant 20 MB retained here and re-sent to the routing model on
    every later turn. A single long turn is shortened to `_HISTORY_MAX_ENTRY_CHARS`,
    and the oldest turns are then dropped until the whole list fits
    `_HISTORY_MAX_CHARS`.

    A shortened turn SAYS so, for the same reason `context.py` announces a truncated
    artifact: routing on text the user did not write is a mis-route, and a mis-route
    looks exactly like a correct one.
    """
    if not text:
        return
    if len(text) > _HISTORY_MAX_ENTRY_CHARS:
        keep = _HISTORY_MAX_ENTRY_CHARS - len(_TRUNCATION_NOTE)
        text = text[:keep] + _TRUNCATION_NOTE
    history.append({"role": role, "content": text})
    del history[:-_HISTORY_TURNS]
    # Oldest first, so what survives is the most recent conversation — which is what a
    # routing decision is actually made on.
    while len(history) > 1 and sum(len(e["content"]) for e in history) > _HISTORY_MAX_CHARS:
        history.pop(0)


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
    # Resolved once per connection and reused for the PER-PROJECT check on every turn
    # (`_project_admin_tier_for_run`). `None` is "could not tell" and refuses; `[]` is a
    # real answer meaning this caller holds nothing, and would refuse there instead.
    permissions = await _resolve_permissions(user_id, tenant_id)

    role = await _resolve_platform_role(user_id, tenant_id)
    if permissions is None or role != ORCHESTRATOR_ROLE:
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

    # This CONNECTION's conversation, for routing only. See `_remember`.
    history: list[dict] = []

    try:
        while True:
            raw = await websocket.receive_text()
            if _frame_too_large(raw):
                # Refused BEFORE json.loads: parsing it would already have paid the
                # memory cost this limit exists to avoid.
                #
                # `continue`, not close: an oversized paste is a user mistake, not an
                # attack, and leaving the socket open lets them send a smaller one.
                logger.warning(
                    "orchestrator2 refused an oversized frame (%d bytes) from "
                    "user=%s tenant=%s",
                    len(raw.encode("utf-8", errors="ignore")), user_id, tenant_id,
                )
                await _fail(
                    websocket,
                    "That message is too large to send.",
                    detail=f"limit {MAX_INBOUND_FRAME_BYTES} bytes",
                )
                continue
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

            # An agent named on the frame is an OVERRIDE, not the normal path.
            # Absent — the default, and what the UI sends unless the user picks one —
            # the Context Agent reads the message and chooses. The override stays
            # because the router is a model: it will sometimes be wrong, and with no
            # way to force an agent a wrong decision would be unrecoverable inside the
            # conversation.
            override_agent = (msg.get("agent") or "").strip()

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
                # NO `detail`. `RunNotAvailableError` carries one of four strings —
                # "no run identified", "'…' is not a run id", "no such run", "the run
                # could not be verified" — and every one of them says something about
                # the run that the caller is being refused knowledge of. Sending them
                # made this refusal an existence oracle: it was distinguishable, by the
                # PRESENCE of the key alone, from the per-project refusal below, which
                # fires only when the run does exist in this tenant. A caller holding a
                # run UUID learned whether it was real. The single exception type
                # upstream was chosen so the message could not reveal which case it
                # was; emitting the case as a detail gave it straight back.
                #
                # The reason is logged instead, which is who it was ever for.
                logger.info(
                    "orchestrator2 refused run=%s for tenant=%s user=%s: %s",
                    run_id, tenant_id, user_id, exc,
                )
                await _fail(websocket, "That run is not available.")
                continue

            # Everything from here to `run_agent` can fail, and every failure must
            # reach the user as a typed `error` rather than as a silent non-answer.
            # Bound BEFORE the try, because both handlers below log it and routing
            # can fail before it is chosen. An UnboundLocalError raised inside the
            # error handler would replace the report with a crash — the failure
            # channel losing the failure, which is the shape of bug this whole engine
            # is a response to.
            agent_id = override_agent or "(not yet chosen)"
            reason = ""

            # ── THE PER-PROJECT CHECK ────────────────────────────────────────
            # The check above, at connect, asks whether this caller is a Project Admin
            # ANYWHERE in the tenant. That is the wrong question for a turn: the
            # Orchestrator reaches all nine agents at once, and every turn spends the
            # RUN's project's model grant and the RUN's project's budget. Without this,
            # a Project Admin of project A could name a run belonging to project B in
            # the same tenant and drive everything against it — which tenant scoping
            # cannot see, because both are one tenant's.
            #
            # It lives HERE, not before `accept()`, because the run — and so the
            # project — arrives per turn. Refusing the TURN rather than the connection
            # is deliberate: the caller may well own the next run they name.
            try:
                tier = (
                    await _project_admin_tier_for_run(
                        project_id, tenant_id,
                        user_id=user_id, permissions=permissions,
                    )
                    if project_id
                    else None
                )
            except Exception as exc:  # noqa: BLE001 — cannot prove standing ⇒ refuse
                logger.warning(
                    "orchestrator2 could not resolve project standing for user=%s "
                    "project=%s: %s — refusing (fail-closed)",
                    user_id, project_id, exc,
                )
                tier = None
            if tier is None:
                # A run with NO project (nullable since migration 0005 — webhook runs
                # carry a provider key, not a local project UUID) lands here too, and
                # should: there is no project to administer, nothing to scope its models
                # or budget to, and "no project" must not become "no check".
                #
                # BYTE-IDENTICAL to the unavailable-run refusal above: the same
                # message, and — this is the part that was wrong for a while — the same
                # KEYS. A distinct "that project is not yours" would confirm the run
                # exists, which is the existence oracle `RunNotAvailableError` is worded
                # to avoid, arriving one step further along. So would a differing set of
                # keys: this branch fires only when the run DOES exist in this tenant,
                # so anything present here and absent above (or the reverse) is that
                # same oracle wearing a different hat. It was `detail`, which the
                # `_resolve_run` handler used to send and no longer does.
                # `test_the_refusal_does_not_reveal_whether_the_run_exists` compares the
                # whole event stream of both paths, not one key of it.
                logger.info(
                    "orchestrator2 refused user=%s run=%s project=%s — does not "
                    "administer the run's project", user_id, run_id, project_id,
                )
                await _fail(websocket, "That run is not available.")
                continue

            # The conversation row this run's transcript hangs off. AFTER the run and
            # the per-project check are verified, so a refused turn never creates one,
            # and BEFORE the first `record_turn`, because conversation_messages has a
            # foreign key to it and every persist would otherwise fail silently.
            #
            # Idempotent, so calling it every turn costs a no-op read and removes the
            # need to track "have I created it yet" on a socket that can reconnect
            # mid-conversation.
            await sessions.ensure_session(
                run_id,
                tenant_id=tenant_id,
                # From the verified `runs` row, like every other project-scoped value
                # on this path. Never from the client frame.
                project_id=project_id,
                user_id=user_id,
                first_message=text,
            )

            try:
                if override_agent:
                    agent_id = override_agent
                    reason = "You named this agent, so nothing was inferred."
                else:
                    decision = await route(
                        text,
                        history=list(history),
                        run_id=run_id,
                        tenant_id=tenant_id,
                        # From the verified `runs` row. The router makes its OWN model
                        # call, so this is the same authority claim it is for the
                        # agent: it decides which models the call may use and whose
                        # budget it spends. A frame-supplied project here would
                        # reintroduce the gap Phase 2 closed, on a new code path.
                        project_id=project_id,
                        model_id=model_id,
                        offering_id=offering_id,
                    )
                    if decision.agent_id is None:
                        # Answered without a delivery agent. NO `agent.selected`:
                        # its `agent` field is the strict nine-value enum in
                        # protocol.ts, so a null one fails validation and the frame
                        # vanishes in the browser. `direct_reply` is guaranteed
                        # non-empty by `_validated`, and it says what the reason
                        # would have said; the ABSENCE of the badge is what tells the
                        # user no agent ran.
                        _remember(history, "user", text)
                        _remember(history, "agent", decision.direct_reply or "")
                        # A direct answer is still part of the conversation. Skipping
                        # it here would leave a reopened chat with the user's question
                        # and no reply, which reads as a turn that failed.
                        await sessions.record_turn(
                            run_id, "user", text,
                            tenant_id=tenant_id, user_id=user_id,
                        )
                        await sessions.record_turn(
                            run_id, "agent", decision.direct_reply or "",
                            tenant_id=tenant_id, user_id=user_id,
                        )
                        await _send(websocket, {
                            "type": "stream_chunk",
                            "content": decision.direct_reply or "",
                        })
                        await _send(websocket, {"type": "stream_end"})
                        continue
                    agent_id = decision.agent_id
                    reason = decision.reason

                # What the run already holds, for whichever agent was chosen. This
                # RAISES if the artifacts could not be read, and that is deliberate:
                # the old engine returned "" on any failure, which told the agent
                # "nothing has been produced yet" and had it re-ask the user for work
                # the run already contained.
                context = await handoff_context(run_id, tenant_id, agent_id)

                _remember(history, "user", text)
                await sessions.record_turn(
                    run_id, "user", text, tenant_id=tenant_id, user_id=user_id,
                )
                reply_text: list[str] = []

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
                        # The AUTHENTICATED user, from the ticket claim — never from
                        # the frame. It selects this person's own project-scoped
                        # connector credential, so a client-supplied value here would
                        # be a way to borrow somebody else's PAT.
                        user_id=user_id,
                        context=context,
                        reason=reason,
                    )
                ) as events:
                    async for event in events:
                        if event.get("type") == "stream_chunk":
                            # Kept so the NEXT turn's routing can resolve "and now?"
                            # against what the agent actually said. Accumulated from
                            # the events rather than from the graph, so whatever the
                            # user saw is exactly what the router reads.
                            reply_text.append(str(event.get("content") or ""))
                        await _send(websocket, event)
                _remember(history, "agent", "".join(reply_text))
                # The same string `_remember` keeps — which is accumulated from the
                # EVENTS, so what is stored is exactly what the user saw.
                await sessions.record_turn(
                    run_id, "agent", "".join(reply_text),
                    tenant_id=tenant_id, user_id=user_id,
                )
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
