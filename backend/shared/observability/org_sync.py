"""Keep each business unit's Langfuse organization in step with this platform's RBAC.

WHAT THIS FIXES. A Langfuse organization used to appear only when somebody first ran an
agent inside one of the unit's projects, and the only people ever granted access were a
fixed list of addresses in `LANGFUSE_BOOTSTRAP_USER_EMAIL`. Creating a business unit and
appointing its admin therefore did nothing at all in Langfuse: the organization was not
there, and when it eventually appeared the person who runs the unit had no access to it.

THE MAPPING IT ENFORCES (0058):

    this platform's org admins  ->  OWNER  of every unit's Langfuse organization
    the unit's bu_admin         ->  ADMIN  of that unit's organization
    everyone else               ->  no Langfuse access; they read traces through /traces

Org-level roles are enough because project-level RBAC is a Langfuse Enterprise feature:
an org ADMIN reaches every project in that organization — and since a business unit IS the
organization, that is exactly "this unit's projects and no others".

ONE CONVERGE FUNCTION, NOT FOUR HOOKS. `sync_unit` is idempotent and computes the whole
desired state from the database, so create, rename, appoint, un-appoint and un-archive all
call the same thing. A hook per event would need five correct implementations of the same
rule and would drift the moment a sixth way to change a role appeared — which is how the
fifteen drifted `langfuse_langchain_extras` call sites happened.

EVERYTHING HERE IS FAIL-SOFT. Langfuse being unreachable must never stop somebody creating
a business unit or appointing its admin — observability is not allowed to fail the product.
The cost is that drift is possible, which is why `scripts/sync_langfuse_orgs.py` exists to
converge it later; that script is a thin wrapper over these same functions.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text

from config.env import ENABLE_LANGFUSE, LANGFUSE_MANAGE_MEMBERSHIPS
from shared.observability.provisioning import strongest_role

logger = logging.getLogger(__name__)

# Which platform role earns which Langfuse organization role. Deliberately short: every
# other role in this product reads traces through /traces, where org > unit > project
# scoping is already enforced. Handing more people a Langfuse login would widen who can
# read raw prompts and completions without any of that scoping applying.
_ROLE_MAP = {
    "org_admin": "OWNER",
    "bu_admin": "ADMIN",
}

# Which PROJECT-scoped platform role earns which Langfuse project role.
#
# DELIBERATELY ONLY THE ROLES THAT ALREADY HOLD `trace:view`. Nine roles bind at project
# scope; only `project_admin` and `security_engineer` may read traces in this product. If
# a `developer` were given a Langfuse VIEWER login they could open every prompt and
# completion for their project by going to Langfuse directly — data this platform
# deliberately refuses them — and the two systems would then disagree about who may read
# what. Widening this map is a permissions decision, not a configuration one: grant
# `trace:view` in shared/authz/permissions.py first, and the two stay honest.
_PROJECT_ROLE_MAP = {
    "project_admin": "MEMBER",
    "security_engineer": "VIEWER",
}


def _enabled() -> bool:
    return bool(ENABLE_LANGFUSE and LANGFUSE_MANAGE_MEMBERSHIPS)


# One in-flight sync per business unit. The re-read above makes a concurrent sync
# CORRECT; this keeps it from being wasteful — creating a unit and appointing its admin
# fire two syncs seconds apart, and without serialising they both make the same round
# trips to Langfuse and log contradictory-looking grant/revoke pairs. Per process only:
# several workers can still interleave, which is why correctness does not depend on it.
_UNIT_LOCKS: dict = {}


def _unit_lock(workspace_id: str):
    import asyncio  # noqa: PLC0415

    lock = _UNIT_LOCKS.get(workspace_id)
    if lock is None:
        lock = _UNIT_LOCKS[workspace_id] = asyncio.Lock()
    return lock


async def owned_org_ids(session) -> frozenset:
    """Every Langfuse organization id this platform has recorded against a unit.

    This is what lets provisioning tell our organizations from the sibling product's on
    the shared instance, so a unit named `Payments` creates its own rather than adopting
    theirs. See `provisioning._available_org_name`.
    """
    try:
        rows = await session.execute(
            text("select langfuse_org_id from workspaces where langfuse_org_id is not null")
        )
        return frozenset(str(r[0]) for r in rows.all())
    except Exception:
        logger.debug("owned_org_ids lookup failed (swallowed)", exc_info=True)
        return frozenset()


async def desired_access(session, *, tenant_id: str, workspace_id: str) -> dict:
    """{email: langfuse_role} for one unit, derived from role bindings.

    Organization admins are read at the ORGANIZATION scope and apply to every unit, which
    is what makes them owners of all of them. The unit admin is read at this unit's scope
    only.

    An address is required: Langfuse identifies people by email, so a user row without one
    simply cannot be granted and is skipped rather than failing the sync.
    """
    access: dict = {}
    try:
        rows = await session.execute(
            text(
                "select rb.role_name, u.email "
                "from role_bindings rb join users u on u.id = rb.user_id "
                "where rb.tenant_id = cast(:t as uuid) "
                "  and u.email is not null "
                "  and ( (rb.scope_kind = 'organization' and rb.role_name = 'org_admin') "
                "     or (rb.scope_kind = 'business_unit' and rb.scope_id = cast(:w as uuid) "
                "         and rb.role_name = 'bu_admin') )"
            ),
            {"t": str(tenant_id), "w": str(workspace_id)},
        )
    except Exception:
        logger.warning("langfuse: role lookup failed for unit=%s", workspace_id, exc_info=True)
        return {}

    for role_name, email in rows.all():
        langfuse_role = _ROLE_MAP.get(str(role_name))
        if not langfuse_role or not email:
            continue
        key = str(email).strip().lower()
        # Somebody who is both an organization admin and this unit's admin keeps the
        # stronger role, rather than whichever binding row happened to come back last.
        access[key] = strongest_role(access.get(key, ""), langfuse_role)

    # ANYBODY WITH A PROJECT-LEVEL GRANT INSIDE THIS UNIT BELONGS HERE TOO, at the NONE
    # floor. Two reasons, and the second is a bug that would otherwise be very hard to see:
    #
    #   1. `project_memberships.org_membership_id` is NOT NULL, so a project role is
    #      impossible without an organization membership. NONE is the value that creates
    #      one without granting anything — `projectAccessRights.NONE` is empty.
    #   2. `sync_unit` revokes every grant it did not just make. Without this, the unit
    #      sync would delete the organization membership the project sync depends on, the
    #      project sync would recreate it, and the two would undo each other on every
    #      role change — each looking correct in isolation.
    #
    # `strongest_role` keeps a unit admin who also runs a project at ADMIN rather than
    # lowering them to the floor.
    try:
        project_rows = await session.execute(
            text(
                "select rb.role_name, u.email "
                "from role_bindings rb "
                "join users u on u.id = rb.user_id "
                "join projects p on p.id = rb.scope_id "
                "where rb.tenant_id = cast(:t as uuid) "
                "  and rb.scope_kind = 'project' "
                "  and p.workspace_id = cast(:w as uuid) "
                "  and u.email is not null"
            ),
            {"t": str(tenant_id), "w": str(workspace_id)},
        )
        for role_name, email in project_rows.all():
            if str(role_name) not in _PROJECT_ROLE_MAP or not email:
                continue
            key = str(email).strip().lower()
            access[key] = strongest_role(access.get(key, ""), "NONE")
    except Exception:
        logger.warning(
            "langfuse: project-grantee lookup failed for unit=%s", workspace_id, exc_info=True
        )
    return access


async def desired_project_access(session, *, tenant_id: str, project_id: str) -> dict:
    """{email: langfuse_project_role} for one project, from its project-scoped bindings.

    Only the roles in `_PROJECT_ROLE_MAP` — see the note there on why that is two roles
    and not nine.
    """
    access: dict = {}
    try:
        rows = await session.execute(
            text(
                "select rb.role_name, u.email "
                "from role_bindings rb join users u on u.id = rb.user_id "
                "where rb.tenant_id = cast(:t as uuid) "
                "  and rb.scope_kind = 'project' and rb.scope_id = cast(:p as uuid) "
                "  and u.email is not null"
            ),
            {"t": str(tenant_id), "p": str(project_id)},
        )
    except Exception:
        logger.warning(
            "langfuse: project role lookup failed for project=%s", project_id, exc_info=True
        )
        return {}

    for role_name, email in rows.all():
        langfuse_role = _PROJECT_ROLE_MAP.get(str(role_name))
        if not langfuse_role or not email:
            continue
        key = str(email).strip().lower()
        access[key] = strongest_role(access.get(key, ""), langfuse_role)
    return access


async def sync_project(
    session,
    *,
    tenant_id: str,
    project_id: str,
    actor_email: Optional[str] = None,
) -> Optional[dict]:
    """Converge one project's Langfuse access. Never raises.

    Runs `sync_unit` FIRST, deliberately. The unit sync is what creates the organization
    membership a project role has to hang off (see `desired_access`), and it is also what
    creates the organization itself on a unit that predates this. Doing it the other way
    round leaves the project grant with nothing to attach to on a cold start.
    """
    if not _enabled():
        return None
    try:
        row = (
            await session.execute(
                text(
                    "select p.workspace_id, p.display_name, p.archived, "
                    "       w.langfuse_org_id, b.langfuse_project_id "
                    "from projects p "
                    "join workspaces w on w.id = p.workspace_id "
                    "left join langfuse_bindings b "
                    "  on b.project_id = p.id and b.is_active = true "
                    "where p.id = cast(:p as uuid) and p.tenant_id = cast(:t as uuid)"
                ),
                {"p": str(project_id), "t": str(tenant_id)},
            )
        ).first()
    except Exception:
        logger.warning("langfuse: project lookup failed for %s", project_id, exc_info=True)
        return None
    if row is None:
        return None

    workspace_id, project_name, archived = str(row[0]), str(row[1]), bool(row[2])
    org_id, langfuse_project_id = row[3], row[4]
    if archived:
        # An archived project keeps its traces but takes no new grants; `deactivate_binding`
        # already stopped ingestion.
        return None

    # The organization and its memberships first — a project role needs both.
    await sync_unit(
        session, tenant_id=tenant_id, workspace_id=workspace_id, actor_email=actor_email
    )

    if not org_id:
        org_id = (
            await session.execute(
                text("select langfuse_org_id from workspaces where id = cast(:w as uuid)"),
                {"w": workspace_id},
            )
        ).scalar()
    if not langfuse_project_id:
        # NO LANGFUSE PROJECT YET, SO MAKE ONE. `project_memberships` references a project
        # row, so there is nothing to grant a role ON until this runs — which is why
        # provisioning moved off "first traced agent run" and onto project creation.
        # `ensure_binding` is the existing, idempotent path: it provisions the project
        # inside the unit's organization, mints its key pair and records the binding.
        from shared.observability.bindings import ensure_binding  # noqa: PLC0415

        binding = await ensure_binding(
            session, tenant_id=str(tenant_id), project_id=str(project_id)
        )
        if binding is not None:
            langfuse_project_id = binding.langfuse_project_id
            org_id = org_id or getattr(binding, "langfuse_org_id", None)

    if not org_id or not langfuse_project_id:
        # Langfuse was unreachable. Fail soft — the project exists here regardless, and
        # `scripts/sync_langfuse_orgs.py` converges the grant later.
        logger.info(
            "langfuse: project %s has no Langfuse project yet — grants deferred", project_id
        )
        return None

    from shared.observability.provisioning import LangfuseProvisioner  # noqa: PLC0415

    provisioner = LangfuseProvisioner()
    wanted = await desired_project_access(
        session, tenant_id=tenant_id, project_id=project_id
    )

    revoked: dict = {}
    try:
        current = await provisioner.project_access(
            org_id=str(org_id), project_id=str(langfuse_project_id)
        )
        stale = [
            email
            for email in set(current["members"]) | set(current["invitations"])
            if email not in wanted
        ]
        if stale:
            logger.info(
                "langfuse: removing %d stale project grant(s) from %r: %s",
                len(stale), project_name, ", ".join(sorted(stale)),
            )
            revoked = await provisioner.sync_project_access(
                org_id=str(org_id), project_id=str(langfuse_project_id), revoke=stale
            )
    except Exception:
        logger.warning(
            "langfuse stale project-grant cleanup failed for %s", project_id, exc_info=True
        )

    applied = await provisioner.sync_project_access(
        org_id=str(org_id),
        project_id=str(langfuse_project_id),
        grants=wanted,
        invited_by_email=actor_email,
    )
    return {
        "org_id": str(org_id),
        "langfuse_project_id": str(langfuse_project_id),
        "project_name": project_name,
        "access": applied,
        "revoked": revoked,
    }


async def _persist_org(tenant_id: str, workspace_id: str, org_id: str, org_name: str) -> None:
    """Record the organization on the workspace, on ITS OWN session.

    A SEPARATE SESSION, DELIBERATELY — the same trap `bindings.ensure_binding` documents.
    `get_db_session_for_tenant` sets `app.current_tenant_id` with SET LOCAL, which is
    transaction-scoped, so committing on the caller's session would drop the GUC and make
    every later RLS query in that request return zero rows instead of failing loudly.
    """
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    async with get_db_session_for_tenant(str(tenant_id)) as write_session:
        await write_session.execute(
            text(
                "update workspaces set langfuse_org_id = :oid, langfuse_org_name = :oname "
                "where id = cast(:w as uuid) and organization_id = cast(:t as uuid)"
            ),
            {"oid": org_id, "oname": org_name, "w": str(workspace_id), "t": str(tenant_id)},
        )
        await write_session.commit()


async def sync_unit(
    session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_email: Optional[str] = None,
) -> Optional[dict]:
    """Converge one business unit's Langfuse organization. Never raises.

    Idempotent and complete: ensures the organization exists and is recorded, that its name
    matches the unit's, that the right people hold OWNER/ADMIN, and that anybody else's
    access is removed. Safe to call on every event that could change any of those.

    Returns a small report, or None when nothing was attempted.
    """
    if not _enabled():
        return None
    async with _unit_lock(str(workspace_id)):
        return await _sync_unit_locked(
            session, tenant_id=tenant_id, workspace_id=workspace_id, actor_email=actor_email
        )


async def _sync_unit_locked(
    session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_email: Optional[str] = None,
) -> Optional[dict]:
    try:
        row = (
            await session.execute(
                text(
                    "select display_name, status, langfuse_org_id, langfuse_org_name "
                    "from workspaces where id = cast(:w as uuid) "
                    "  and organization_id = cast(:t as uuid)"
                ),
                {"w": str(workspace_id), "t": str(tenant_id)},
            )
        ).first()
    except Exception:
        logger.warning("langfuse: unit lookup failed for %s", workspace_id, exc_info=True)
        return None
    if row is None:
        return None

    unit_name, status, org_id, org_name = str(row[0]), str(row[1]), row[2], row[3]
    if status == "archived":
        # Archived units are torn down, not synced — otherwise this would re-grant the
        # access `teardown_unit` just removed.
        return None

    from shared.observability.provisioning import (  # noqa: PLC0415
        LangfuseProvisioner,
        LangfuseProvisioningError,
    )

    owned = await owned_org_ids(session)
    access = await desired_access(session, tenant_id=tenant_id, workspace_id=workspace_id)
    provisioner = LangfuseProvisioner()

    try:
        result = await provisioner.provision_org(
            unit_name=unit_name,
            org_id=str(org_id) if org_id else None,
            owned_org_ids=owned,
            access=access,
            invited_by_email=actor_email,
        )
    except LangfuseProvisioningError as exc:
        logger.warning("langfuse org sync unavailable for unit=%s: %s", workspace_id, exc)
        return None
    except Exception:
        logger.warning("langfuse org sync failed for unit=%s", workspace_id, exc_info=True)
        return None

    resolved_id, resolved_name = result.langfuse_org_id, result.langfuse_org_name

    # The unit was renamed here while Langfuse still holds the old name. Not cosmetic: the
    # Langfuse UI lists organizations by name, so drift leaves whoever opens it hunting for
    # a unit that no longer exists under that name in this product.
    if resolved_name != unit_name:
        renamed = await provisioner.rename_org(
            org_id=resolved_id, new_name=unit_name, owned_org_ids=owned
        )
        if renamed:
            resolved_name = renamed

    # Anybody holding access we did not just grant is no longer entitled to it — a
    # bu_admin who was replaced, or somebody added by hand in the Langfuse UI. Removing
    # them is what makes "revoke the role in this product" actually revoke the ability to
    # read that unit's prompts and completions.
    revoked: dict = {}
    try:
        current = await provisioner.org_access(org_id=resolved_id)
        # RE-READ, DO NOT REUSE `result.access`. That set was computed before
        # `provision_org` made its round trips to Langfuse, and `current` is read now — so
        # comparing them treats anything granted by a CONCURRENT sync in between as stale
        # and revokes it. Observed live: creating a unit and appointing its admin schedule
        # two syncs, the slower one revoked the admin the faster one had just granted, and
        # only a third sync put it back. Both reads have to describe the same moment.
        fresh = await desired_access(
            session, tenant_id=tenant_id, workspace_id=workspace_id
        )
        entitled = set(fresh) | set(result.access) | {
            e.strip().lower() for e in provisioner._bootstrap_emails()
        }
        stale = [
            email
            for email in set(current["members"]) | set(current["invitations"])
            if email not in entitled
        ]
        if stale:
            logger.info(
                "langfuse: removing %d stale grant(s) from unit %r: %s",
                len(stale), unit_name, ", ".join(sorted(stale)),
            )
            revoked = await provisioner.sync_org_access(org_id=resolved_id, revoke=stale)
    except Exception:
        logger.warning("langfuse stale-grant cleanup failed for unit=%s", workspace_id, exc_info=True)

    if str(org_id or "") != resolved_id or str(org_name or "") != resolved_name:
        try:
            await _persist_org(tenant_id, workspace_id, resolved_id, resolved_name)
        except Exception:
            # The organization exists but the unit does not know its id, so the next sync
            # would search by name and could create a second one. The reconciler repairs
            # this; log loudly enough that it gets noticed first.
            logger.warning(
                "langfuse org %s created for unit=%s but could not be recorded — run "
                "scripts/sync_langfuse_orgs.py", resolved_id, workspace_id, exc_info=True,
            )

    return {
        "org_id": resolved_id,
        "org_name": resolved_name,
        "created": result.created,
        "access": result.access,
        "revoked": revoked,
    }


async def sync_unit_for_project(session, *, tenant_id: str, project_id: str, **kw) -> Optional[dict]:
    """`sync_unit` addressed by a project — for role changes scoped to a project.

    A project admin gets no Langfuse access, but appointing one can coincide with unit
    membership changes, and resolving the unit here keeps the caller from having to.
    """
    try:
        row = (
            await session.execute(
                text(
                    "select workspace_id from projects "
                    "where id = cast(:p as uuid) and tenant_id = cast(:t as uuid)"
                ),
                {"p": str(project_id), "t": str(tenant_id)},
            )
        ).first()
    except Exception:
        return None
    if row is None or row[0] is None:
        return None
    return await sync_unit(session, tenant_id=tenant_id, workspace_id=str(row[0]), **kw)


async def teardown_unit(session, *, tenant_id: str, workspace_id: str) -> Optional[dict]:
    """Archiving a unit: revoke everyone, stop ingestion, hide its projects. Never raises.

    See `provisioning.teardown_org` for why this is not a `DELETE` — traces live in
    ClickHouse, which a Postgres delete would orphan rather than clean up.

    Bindings are deactivated too, so a restored unit re-provisions fresh API keys instead
    of reusing the ones deleted here.
    """
    if not ENABLE_LANGFUSE:
        return None
    try:
        row = (
            await session.execute(
                text(
                    "select langfuse_org_id from workspaces "
                    "where id = cast(:w as uuid) and organization_id = cast(:t as uuid)"
                ),
                {"w": str(workspace_id), "t": str(tenant_id)},
            )
        ).first()
    except Exception:
        logger.warning("langfuse teardown: unit lookup failed for %s", workspace_id, exc_info=True)
        return None
    if row is None or not row[0]:
        return None
    org_id = str(row[0])

    from shared.observability.bindings import deactivate_binding  # noqa: PLC0415
    from shared.observability.provisioning import LangfuseProvisioner  # noqa: PLC0415

    counts = await LangfuseProvisioner().teardown_org(org_id=org_id)

    try:
        projects = (
            await session.execute(
                text(
                    "select id from projects where workspace_id = cast(:w as uuid) "
                    "and tenant_id = cast(:t as uuid)"
                ),
                {"w": str(workspace_id), "t": str(tenant_id)},
            )
        ).all()
        for (pid,) in projects:
            await deactivate_binding(session, tenant_id=str(tenant_id), project_id=str(pid))
    except Exception:
        logger.warning(
            "langfuse: bindings not deactivated for unit=%s", workspace_id, exc_info=True
        )
    return counts


async def restore_unit(session, *, tenant_id: str, workspace_id: str, **kw) -> Optional[dict]:
    """Un-archiving a unit: undo the soft-delete, then converge access again.

    Keys are NOT restored — `teardown_unit` deleted them and the bindings it deactivated
    make the next traced run mint fresh ones.
    """
    if not ENABLE_LANGFUSE:
        return None
    try:
        row = (
            await session.execute(
                text(
                    "select langfuse_org_id from workspaces "
                    "where id = cast(:w as uuid) and organization_id = cast(:t as uuid)"
                ),
                {"w": str(workspace_id), "t": str(tenant_id)},
            )
        ).first()
    except Exception:
        return None
    if row is not None and row[0]:
        from shared.observability.provisioning import LangfuseProvisioner  # noqa: PLC0415

        await LangfuseProvisioner().restore_org(org_id=str(row[0]))
    return await sync_unit(session, tenant_id=tenant_id, workspace_id=workspace_id, **kw)


# Strong references to in-flight background syncs. Without them the event loop can
# garbage-collect a task mid-flight, which is the classic way a fire-and-forget coroutine
# silently never runs.
_BACKGROUND: set = set()

_ACTIONS = {
    "sync": sync_unit,
    "teardown": teardown_unit,
    "restore": restore_unit,
    "sync_project": sync_project,
}


def schedule(
    action: str,
    *,
    tenant_id: str,
    workspace_id: str = "",
    project_id: str = "",
    actor_email=None,
) -> bool:
    """Run a unit sync in the BACKGROUND, on its own session. Returns whether it started.

    WHY NOT INLINE. Provisioning opens a connection to a Langfuse database in another
    region; `_connect` allows it 30 seconds. Awaiting that inside the request would make
    creating a business unit hang for half a minute whenever Langfuse is unwell — trading
    a working product feature for an observability one, which is backwards.

    WHY ITS OWN SESSION. The caller's session is closed when its request ends, well before
    this runs. Using it would produce a detached-session error under exactly the conditions
    nobody tests, so the task opens a tenant-scoped session of its own.

    Failures are logged and dropped; `scripts/sync_langfuse_orgs.py` is the backstop that
    converges anything lost this way.
    """
    import asyncio  # noqa: PLC0415

    if action != "teardown" and not _enabled():
        return False
    if action == "teardown" and not ENABLE_LANGFUSE:
        return False

    fn = _ACTIONS[action]

    async def _runner():
        try:
            from shared.db import get_db_session_for_tenant  # noqa: PLC0415

            async with get_db_session_for_tenant(str(tenant_id)) as own:
                kwargs: dict = {"tenant_id": tenant_id}
                if action == "sync_project":
                    kwargs["project_id"] = project_id
                else:
                    kwargs["workspace_id"] = workspace_id
                if action != "teardown":
                    kwargs["actor_email"] = actor_email
                await fn(own, **kwargs)
        except Exception:
            logger.warning(
                "langfuse %s failed in background for unit=%s project=%s", action,
                workspace_id or "-", project_id or "-", exc_info=True,
            )

    try:
        task = asyncio.create_task(_runner())
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
        return True
    except RuntimeError:
        # No running loop — a sync context or a script. The reconciler covers it.
        logger.debug("langfuse %s not scheduled (unit=%s project=%s) — no running event loop",
                     action, workspace_id or "-", project_id or "-")
        return False
