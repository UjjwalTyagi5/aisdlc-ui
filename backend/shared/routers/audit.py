"""Audit resource router.

Exposes read operations for the AuditEvent model. All routes are JWT-protected
(NOT in _EXEMPT_PATHS) and scope every query by request.state.tenant_id.

Routes:
  GET  /audit                          — paginated list (query: project_id, actor, action, page, page_size)
  GET  /runs/{run_id}/audit            — cursor-paginated, filterable, RBAC-gated run-scoped trail

Threat mitigations:
  - T-M4-01, T-M4-02: All queries filtered by tenant_id (no cross-tenant reads)
  - T-M8-13: run-scoped route requires resource_id == run_id AND tenant_id match
  - T-M8-14: require_permission("artifact:view", run_param="run_id") on run-scoped route
  - Route not in _EXEMPT_PATHS (JWT middleware enforces 401 without token)

Router mounting note (REQ-M8-06):
  audit_router       — mounted at /audit prefix in process_api.py
  audit_runs_router  — mounted WITHOUT a prefix in process_api.py
    so GET /runs/{run_id}/audit is the public path.
"""
from __future__ import annotations

import base64
import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.can_perform import visible_project_ids
from shared.authz.dependency import require_permission
from shared.authz.read_scope import allowed_workspace_ids
from shared.db import get_db_session
from shared.models.orm import AuditEvent
from shared.routers._schemas import (
    AuditEventOut,
    CursorPage,
    KeysetPage,
    derive_scope,
)

logger = logging.getLogger(__name__)

audit_router = APIRouter()

# Separate router for /runs/{run_id}/audit — mounted WITHOUT the /audit prefix
# so the public path resolves to /runs/{run_id}/audit (REQ-M8-06).
audit_runs_router = APIRouter()


def _actor_matches(actor: str):
    """Filter clause for one actor, mirroring how the response NAMES actors.

    `AuditEventOut` renders a null `actor_id` as the string "system" (see
    _schemas.py), and the UI builds its "Any actor" dropdown from the rows it was
    shown -- so it offers "system" and then filtered on `actor_id = 'system'`,
    which matches nothing: system events carry NULL, not the literal. On this
    database that is 27 of 28 rows, so choosing the only actor most events have
    emptied the table.

    The filter has to undo the same substitution the serializer applies, or the
    dropdown offers a value the query can never match.
    """
    if actor == "system":
        return AuditEvent.actor_id.is_(None)
    return AuditEvent.actor_id == actor


async def _search_clause(db: AsyncSession, q: str):
    """A free-text clause that stays INDEXABLE on a table that grows forever.

    The obvious implementation — ILIKE against every column, joined out to users and
    workspaces so a name matches — cannot use an index for any of it, so on a large
    trail every keystroke becomes a sequential scan plus a join per row. The audit
    table is the one table on this platform that only ever grows.

    So the term is resolved to IDS FIRST, in two small lookups against tables that are
    orders of magnitude smaller (a tenant has tens of users, not millions of events),
    and the audit query then filters on `actor_id` / `resource_id` — plain equality
    against indexed columns. Searching "akshat" still finds their events; it just does
    it by looking up who that is, rather than by reading the log.

    `event_type` keeps a real ILIKE: it is a short, low-cardinality string and the
    dropdown beside the box already offers the exact values.
    """
    like = f"%{q}%"
    ids: set[str] = set()
    for table, col in (("users", "email"), ("workspaces", "display_name"), ("projects", "display_name")):
        rows = (await db.execute(
            text(f"SELECT id::text AS id FROM {table} WHERE {col} ILIKE :q LIMIT 200"),
            {"q": like},
        )).fetchall()
        ids.update(r.id for r in rows)

    clauses = [
        AuditEvent.event_type.ilike(like),
        AuditEvent.resource_type.ilike(like),
        AuditEvent.resource_id == q,
        AuditEvent.actor_id == q,
    ]
    if ids:
        id_list = list(ids)
        clauses.append(AuditEvent.actor_id.in_(id_list))
        clauses.append(AuditEvent.resource_id.in_(id_list))
        # A project or unit is usually the event's SCOPE rather than its resource --
        # an artifact upload names the file and files the project in its payload -- so
        # searching "Dummy T1" has to reach the payload too or it finds nothing for
        # every event that happened IN the thing you searched for.
        clauses.append(AuditEvent.payload["project_id"].astext.in_(id_list))
        clauses.append(AuditEvent.payload["scope_id"].astext.in_(id_list))
        clauses.append(AuditEvent.payload["workspace_id"].astext.in_(id_list))
    return or_(*clauses)


# WHERE A RESOURCE ID CAN BE GIVEN A NAME: resource_type -> (table, name column).
#
# `role_binding` points at USERS on purpose. shared/authz/audit.py records the
# SUBJECT of the grant -- the person who received the role -- as the resource, not a
# row in role_bindings: of the 87 rbac events on this database, 75 join to users and
# NONE join to role_bindings. Looking them up in the table the type is named after
# would therefore name none of them.
#
# A type absent from here keeps its id, which is the honest answer for a resource
# this server cannot name.
# An export is a file a person opens, not a replication channel. Uncapped, on a
# table that only grows, this is how a memory limit gets discovered in production.
_EXPORT_MAX = 10_000

# There is no page cap any more: a cursor cannot be walked to an arbitrary depth the
# way `?page=999999` could, because each page is reached only by holding the previous
# one's last row. Page SIZE still needs a ceiling — that one is a memory bound.
_MAX_PAGE_SIZE = 200

_RESOURCE_NAME_SOURCES: dict[str, tuple[str, str]] = {
    "business_unit": ("workspaces", "display_name"),
    "workspace": ("workspaces", "display_name"),
    "project": ("projects", "display_name"),
    "user": ("users", "email"),
    "role_binding": ("users", "email"),
}

# Users have no display name on this schema -- `users` is (id, email, external_id,
# password_hash, tenant_id, created_at, active) -- so the email IS the human label.
_USER_LABEL = "email"


async def _lookup(db: AsyncSession, table: str, name_col: str, ids: set[str]) -> dict[str, str]:
    """`{id: name}` for the ids that exist, silently dropping the ones that do not.

    Compares `id::text` rather than casting the parameter: a resource_id is a free-form
    string column and holds things that are not UUIDs at all ("unknown", a run key), and
    a cast would turn one such row into a 500 for the whole page.

    `table` and `name_col` come from `_RESOURCE_NAME_SOURCES` above, never from the
    request -- they are interpolated into SQL and must stay that way.
    """
    if not ids:
        return {}
    rows = (await db.execute(
        text(f"SELECT id::text AS id, {name_col} AS name FROM {table} WHERE id::text = ANY(:ids)"),
        {"ids": list(ids)},
    )).fetchall()
    return {r.id: r.name for r in rows if r.name}


# The table that can name each scope kind. Mirrors `_RESOURCE_NAME_SOURCES`, kept
# separate because a scope kind is not a resource type: `organization` never appears
# as a resource, and `project` names the unit an event happened in rather than the
# thing it happened to.
_SCOPE_NAME_SOURCES: dict[str, tuple[str, str]] = {
    "organization": ("organizations", "display_name"),
    "business_unit": ("workspaces", "display_name"),
    "workspace": ("workspaces", "display_name"),
    "project": ("projects", "display_name"),
}


async def _resolve_names(db: AsyncSession, rows: list) -> tuple[dict, dict, dict, dict]:
    """Names for one page of events: `(actors, resources, projects, scopes)`.

    THE AUDIT TRAIL WAS THREE COLUMNS OF UUID. `actor_name` and `resource_name` are
    payload keys that nothing writes, so every row fell through to the id -- you could
    see that somebody was denied `role:manage` on a business unit, and not who or which.

    One query per distinct table per page, not per row: at most four extra queries for
    a page of fifty, all on the caller's own RLS session, so a name this caller may not
    read simply does not come back.
    """
    actor_ids = {e.actor_id for e in rows if e.actor_id}
    project_ids = {
        str((e.payload or {}).get("project_id"))
        for e in rows
        if (e.payload or {}).get("project_id")
    }

    # Group the resource ids by the table that can name them, so two types sharing a
    # table (user and role_binding both name a person) cost one query between them.
    by_source: dict[tuple[str, str], set[str]] = {}
    for e in rows:
        source = _RESOURCE_NAME_SOURCES.get(e.resource_type or "")
        if source and e.resource_id:
            by_source.setdefault(source, set()).add(e.resource_id)

    # Scopes, grouped the same way: `derive_scope` is the ONE place that decides where
    # an event happened, shared with the serializer so the id we look up is the id it
    # will render.
    scope_wanted: dict[tuple[str, str], set[str]] = {}
    for e in rows:
        kind, sid = derive_scope(e)
        source = _SCOPE_NAME_SOURCES.get(kind)
        if source and sid:
            scope_wanted.setdefault(source, set()).add(sid)

    actors = await _lookup(db, "users", _USER_LABEL, actor_ids)
    projects = await _lookup(db, "projects", "display_name", project_ids)

    resolved: dict[tuple[str, str], str] = {}
    for (table, name_col), ids in by_source.items():
        found = await _lookup(db, table, name_col, ids)
        for rtype, source in _RESOURCE_NAME_SOURCES.items():
            if source == (table, name_col):
                resolved.update({(rtype, rid): name for rid, name in found.items()})

    scopes: dict[str, str] = {}
    for (table, name_col), ids in scope_wanted.items():
        scopes.update(await _lookup(db, table, name_col, ids))
    return actors, resolved, projects, scopes


# --------------------------------------------------------------------------
# ── Keyset cursors ───────────────────────────────────────────────────────────
#
# A cursor is `(created_at, id)`, base64url-encoded so it is opaque on the wire and
# safe in a query string. BOTH HALVES ARE REQUIRED: `created_at` is not unique — the
# seeded personas were granted eleven roles inside the same second — and a cursor on
# the timestamp alone either re-shows or skips every row sharing it, silently, and
# only under load.
#
# It encodes nothing the caller cannot already see on the row it came from, so the
# encoding is for opacity of CONTRACT, not secrecy: it keeps clients from building
# their own cursors and pinning us to this shape.


def _encode_cursor(created_at: datetime, event_id: Any) -> str:
    raw = f"{created_at.isoformat()}|{event_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> Optional[tuple[datetime, _uuid.UUID]]:
    """`(created_at, id)`, or None if the cursor is unusable.

    A bad cursor is not an error: it means a stale link, a truncated copy-paste, or a
    client that built its own. Returning None starts from the top, which is a page the
    reader can act on — a 400 on the audit trail is not.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts, _, event_id = base64.urlsafe_b64decode(padded.encode()).decode().partition("|")
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        # Parsed as a UUID, not carried as text: `audit_events.id` is a uuid column
        # and a row comparison against a string is a cast error at query time rather
        # than a miss — which would surface as a 500 on a stale bookmark.
        return (parsed, _uuid.UUID(event_id)) if event_id else None
    except Exception:
        logger.debug("audit: unusable cursor %r — starting from the top", cursor[:24])
        return None


async def _query_audit_events(
    request: Request,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    q: Optional[str] = None,
    cursor: Optional[str] = None,
    direction: str = "next",
    sort: str = "newest",
    page_size: int = 20,
    db: AsyncSession = None,  # type: ignore[assignment]
) -> tuple[list[AuditEventOut], int, Optional[str], Optional[str]]:
    """The audit query itself: `(serialized events, total matching)`.

    SHARED WITH THE EXPORT ON PURPOSE. An export that filtered differently from the
    screen it was taken from would be the worst kind of wrong: a file that looks like
    what you were reading and is not. One query, two page sizes.

    Scoped to the requesting tenant.

    workspace_id filters to events whose payload.workspace_id matches — used by the
    workspace-scoped audit view and the org audit page's workspace picker.
    When absent, all tenant events are returned (org-level view).
    Falls back to the X-Workspace-Id header when workspace_id query param is absent,
    so the workspace audit tab scopes automatically without UI changes.
    """
    tenant_id = request.state.tenant_id

    # Resolve workspace scope: explicit query param → header → none (org-wide)
    effective_workspace = workspace_id
    if not effective_workspace:
        effective_workspace = request.headers.get("x-workspace-id") or None

    # WHICH units this caller may aggregate over. `audit:view` says they may read an
    # audit trail; it does not say whose. Both holders of it — bu_admin and
    # security_engineer — got the WHOLE tenant's trail, and could get it by simply
    # omitting the workspace filter, which is caller-supplied and therefore not a
    # control. See finding 4 in docs/rbac-audit-2026-08-17.md.
    allowed_ws = await allowed_workspace_ids(db, request)
    allowed_projects = (
        None
        if allowed_ws is None
        else await visible_project_ids(
            db,
            user_id=getattr(request.state, "user_id", "") or "",
            tenant_id=str(tenant_id),
        )
    )

    # A unit the caller cannot read is REFUSED rather than quietly ignored — mirroring
    # spend.py. Silently widening to "all of mine" answers a question about someone
    # else's unit with the viewer's own events.
    if effective_workspace and allowed_ws is not None:
        if effective_workspace not in allowed_ws:
            raise HTTPException(status_code=404, detail="not found")

    stmt = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)

    if allowed_ws is not None:
        # TWO PAYLOAD SHAPES, and missing either one makes the filter wrong in a
        # different direction. Resource events carry `workspace_id` / `project_id`;
        # RBAC events (shared/authz/audit.py) carry `scope_kind` + `scope_id` instead.
        # Filtering on `workspace_id` alone would hide a unit admin's own grants and
        # revocations from them — the events they are most accountable for.
        #
        # Anything matching NEITHER is organization-level, and stays hidden: an
        # org-settings change is a fact about a scope this caller does not administer.
        ws_txt = list(allowed_ws)
        proj_txt = list(allowed_projects or [])
        payload = AuditEvent.payload
        stmt = stmt.where(
            or_(
                payload["workspace_id"].astext.in_(ws_txt),
                payload["project_id"].astext.in_(proj_txt),
                and_(
                    payload["scope_kind"].astext == "business_unit",
                    payload["scope_id"].astext.in_(ws_txt),
                ),
                and_(
                    payload["scope_kind"].astext == "project",
                    payload["scope_id"].astext.in_(proj_txt),
                ),
            )
        )

    if effective_workspace:
        stmt = stmt.where(
            AuditEvent.payload["workspace_id"].astext == effective_workspace
        )
    if project_id:
        if allowed_projects is not None and project_id not in allowed_projects:
            raise HTTPException(status_code=404, detail="not found")
        stmt = stmt.where(
            AuditEvent.payload["project_id"].astext == project_id
        )
    if actor:
        stmt = stmt.where(_actor_matches(actor))
    if action:
        stmt = stmt.where(AuditEvent.event_type == action)
    # SEARCH IS THE SERVER'S JOB, and it was the browser's. The page filtered the 50
    # rows it had already been given, so on 132 events across three pages the box
    # searched a third of the trail and reported "32 shown" as though that were the
    # answer. On a real trail it would search a rounding error of it.
    if q and q.strip():
        stmt = stmt.where(await _search_clause(db, q.strip()))

    # The total is its own query, over the filtered set and before any windowing. It
    # is what tells a reader whether their filter did anything, and the
    # (tenant_id, created_at DESC) index answers it without touching the heap.
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    page_size = max(1, min(page_size, _MAX_PAGE_SIZE))

    # KEYSET, NOT OFFSET. The ordering key is `(created_at DESC, id DESC)` — `id`
    # because `created_at` is not unique, and a key that is not unique either repeats
    # or skips the rows that share a value.
    #
    # Fetch ONE MORE ROW than asked for. Its presence is what says there is another
    # page, and it is the only honest way to know: `total` cannot answer it once a
    # cursor is in play, and "we returned a full page so there is probably more" shows
    # a Next button that leads to nothing.
    anchor = _decode_cursor(cursor) if cursor else None
    going_back = direction == "prev" and anchor is not None

    # TWO DIRECTIONS COMPOSE HERE, and keeping them separate is what makes the walk
    # survive a sort flip. `newest_first` is which way the TRAIL runs; `going_back` is
    # which way the READER is moving through it. Previous always means "toward the end
    # you started at", whichever way the trail is pointing.
    #
    # Sorting is offered on this column ALONE. Actor and Scope are resolved after the
    # query returns (see `_resolve_names`) and Change is derived from the payload, so
    # ordering by them would mean ordering by a raw id the reader never sees, or
    # sorting the fifty rows already fetched and calling 133 rows sorted. Both are
    # worse than not offering it.
    newest_first = sort != "oldest"
    key = (AuditEvent.created_at, AuditEvent.id)
    walking_down = newest_first != going_back  # away from the starting end

    if walking_down:
        order = (AuditEvent.created_at.desc(), AuditEvent.id.desc())
    else:
        order = (AuditEvent.created_at.asc(), AuditEvent.id.asc())

    if anchor is not None:
        ts, ident = anchor
        # `<` pairs with DESC and `>` with ASC — a row comparison against the whole
        # ordering key, which is why the id belongs in it.
        stmt = stmt.where(
            tuple_(*key) < tuple_(ts, ident) if walking_down
            else tuple_(*key) > tuple_(ts, ident)
        )
    stmt = stmt.order_by(*order)

    fetched = list((await db.execute(stmt.limit(page_size + 1))).scalars().all())
    has_more = len(fetched) > page_size
    rows = fetched[:page_size]
    if going_back:
        rows.reverse()

    actors, resources, projects, scopes = await _resolve_names(db, list(rows))
    items = [
        AuditEventOut.from_orm_audit(
            e,
            actor_name=actors.get(e.actor_id or ""),
            resource_name=resources.get((e.resource_type or "", e.resource_id or "")),
            project_name=projects.get(str((e.payload or {}).get("project_id") or "")),
            scope_name=scopes.get(derive_scope(e)[1]),
        )
        for e in rows
    ]
    # THE CURSORS ARE BUILT FROM THE ROWS, not from a page number.
    #
    # `has_more` describes the direction just travelled, so which end it bounds depends
    # on which way we walked: going forward it means another older page exists; going
    # back it means another newer one does. Getting this backwards yields a Next button
    # on the last page and no Previous on the second — both of which look like data
    # loss to a reader.
    #
    # A first page (no cursor) has nothing before it, which is why `prevCursor` is null
    # there rather than pointing at its own first row.
    next_cursor = prev_cursor = None
    if rows:
        first, last = rows[0], rows[-1]
        if going_back:
            next_cursor = _encode_cursor(last.created_at, last.id)
            prev_cursor = _encode_cursor(first.created_at, first.id) if has_more else None
        else:
            next_cursor = _encode_cursor(last.created_at, last.id) if has_more else None
            prev_cursor = _encode_cursor(first.created_at, first.id) if cursor else None

    return items, total, next_cursor, prev_cursor


@audit_router.get(
    "",
    response_model=KeysetPage[AuditEventOut],
    # The ORGANISATION-WIDE trail, gated on the permission that names it. It sat on
    # the `artifact:view` floor that every role holds — including `contributor`, whose
    # entire point is holding nothing yet — so any signed-in account could read the
    # whole tenant's audit log over the API. The frontend already refused them the
    # page; this is the backend catching up to that decision.
    #
    # `audit:view` is held by bu_admin and security_engineer (plus admin:*). The
    # RUN-scoped trail below deliberately stays on the view floor: that is one run's
    # own timeline, and reading it is part of reading the run.
    dependencies=[Depends(require_permission("audit:view"))],
)
async def list_audit_events(
    request: Request,
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    q: Optional[str] = None,
    cursor: Optional[str] = None,
    direction: str = "next",
    sort: str = "newest",
    page_size: int = 20,
    db: AsyncSession = Depends(get_db_session),
):
    """One page of the tenant's audit trail, walked by cursor.

    `page` is GONE, and its absence is the point — see `KeysetPage`. Offset paging on
    an append-heavy table is not merely slow at depth, it is inconsistent: an event
    arriving between two clicks shifts every later row down one, so Next re-shows a row
    you just read and another slips past unseen.
    """
    items, total, next_cursor, prev_cursor = await _query_audit_events(
        request, project_id=project_id, workspace_id=workspace_id,
        actor=actor, action=action, q=q,
        cursor=cursor, direction=direction, sort=sort, page_size=page_size, db=db,
    )
    return KeysetPage(
        items=items, total=total, nextCursor=next_cursor, prevCursor=prev_cursor,
    )


@audit_router.get(
    "/export",
    response_model=list[AuditEventOut],
    dependencies=[Depends(require_permission("audit:view"))],
)
async def export_audit_events(
    request: Request,
    fmt: str = "csv",
    project_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    actor: Optional[str] = None,
    action: Optional[str] = None,
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    """The whole filtered trail, and a record that somebody took it.

    PRD §34.9: "Export is itself an audited event." It was not one. The page built the
    file in the browser out of rows it already had, so taking the trail left no trace —
    on the one screen whose entire purpose is that things leave traces.

    THE FILE IS PRODUCED HERE, and that is the control rather than an implementation
    detail. A client-side export can only be audited by asking the client to report
    itself, which is not a control at all. Producing it server-side makes the record a
    precondition of getting the data: the request that returns the rows is the request
    that writes the row saying who took them.

    It also fixes what the browser version actually exported. It serialised `items` —
    THE CURRENT PAGE — so "Export" on a four-thousand-event trail quietly handed you
    fifty rows in a file named after the audit log.

    `truncated` is on the record when the cap bites, so a partial export can never be
    mistaken for the whole trail afterwards.
    """
    from shared.authz.audit import AUDIT_EXPORTED, record_rbac_change  # noqa: PLC0415

    events, total, _next, _prev = await _query_audit_events(
        request, project_id=project_id, workspace_id=workspace_id,
        actor=actor, action=action, q=q, page_size=_EXPORT_MAX, db=db,
    )

    actor_id = getattr(request.state, "user_id", None)
    await record_rbac_change(
        db,
        tenant_id=str(request.state.tenant_id),
        actor_id=actor_id,
        event_type=AUDIT_EXPORTED,
        subject_id=str(actor_id or "system"),
        scope_kind="organization",
        scope_id=str(request.state.tenant_id),
        before="none",
        after=f"{len(events)} events exported as {fmt}",
        extra={
            "format": fmt,
            "row_count": len(events),
            # WHAT THEY WERE LOOKING AT. Exporting one person's events is a different
            # act from exporting everything, and the filters are the only record of
            # which one happened.
            "filters": {
                k: v for k, v in {
                    "project_id": project_id, "workspace_id": workspace_id,
                    "actor": actor, "action": action, "q": q,
                }.items() if v
            } or None,
            "truncated": total > len(events),
            "matched_total": total,
        },
    )
    return events


@audit_runs_router.get(
    "/runs/{run_id}/audit",
    response_model=CursorPage[AuditEventOut],
    dependencies=[Depends(require_permission("artifact:view", run_param="run_id"))],
)
async def get_run_audit(
    run_id: str,
    request: Request,
    agent: Optional[str] = None,
    actor: Optional[str] = None,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    cursor: Optional[str] = None,
    page_size: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> CursorPage[AuditEventOut]:
    """Return a cursor-paginated, filterable audit trail for a specific run.

    Scoped to the requesting tenant (T-M8-13: resource_id == run_id AND
    tenant_id == request.state.tenant_id).  Requires artifact:view permission
    (T-M8-14, REQ-M8-06).

    Cursor pagination: created_at < cursor ORDER BY created_at DESC LIMIT page_size.
    The cursor is an opaque ISO-8601 string encoding the last row's created_at.
    Pass nextCursor from the previous response to advance the page.
    """
    tenant_id = request.state.tenant_id

    # Base query: tenant-scoped + run-scoped (resource_id stores run_id per M8 plan)
    stmt = select(AuditEvent).where(
        AuditEvent.tenant_id == tenant_id,
        AuditEvent.resource_id == run_id,
    )

    # Optional filters
    if agent:
        # agent_type is stored inside the payload JSONB column
        stmt = stmt.where(AuditEvent.payload["agent_type"].astext == agent)
    if actor:
        stmt = stmt.where(_actor_matches(actor))
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    if since:
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        stmt = stmt.where(AuditEvent.created_at >= since_dt)
    if until:
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        stmt = stmt.where(AuditEvent.created_at <= until_dt)

    # Cursor pagination: created_at < cursor_dt ORDER BY created_at DESC LIMIT page_size
    if cursor:
        cursor_dt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        stmt = stmt.where(AuditEvent.created_at < cursor_dt)

    stmt = stmt.order_by(AuditEvent.created_at.desc()).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()

    # The same names here: one run's trail is mostly people acting on artifacts, and
    # "who approved this" is the question it exists to answer.
    actors, resources, projects, scopes = await _resolve_names(db, list(rows))
    items = [
        AuditEventOut.from_orm_audit(
            e,
            actor_name=actors.get(e.actor_id or ""),
            resource_name=resources.get((e.resource_type or "", e.resource_id or "")),
            project_name=projects.get(str((e.payload or {}).get("project_id") or "")),
            scope_name=scopes.get(derive_scope(e)[1]),
        )
        for e in rows
    ]

    # Build next cursor from the last row's created_at (none if fewer rows than page_size)
    next_cursor: Optional[str] = None
    if len(rows) == page_size:
        last_ts = rows[-1].created_at
        if last_ts is not None:
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            next_cursor = last_ts.isoformat()

    return CursorPage(items=items, nextCursor=next_cursor)
