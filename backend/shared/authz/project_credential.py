"""The personal credential a project member saved for themselves, if any.

`project_integration_credentials` (migration 0016) was, until this change,
write-and-display only: the secret a user typed on a project's Integrations
page was read and discarded (see shared/routers/project_scoped.py's history),
and no connector call ever consulted the table. This module is the missing
read path — the one place `secret_ref` is resolved back into a usable token,
called from `BaseConnector._resolve_credential_override` (config/connectors/base.py)
so every connector's `auth_adapter()` checks it the same way.

WHAT IS A SECRET AND WHAT IS NOT. `base_url` and `account` are plain columns on
the row: a site URL and an account name are configuration, and the person who
typed them has to be able to see and correct them. Only the token goes through
the secret store. `resolve_project_credential` returns all three together
because a connector needs all three to authenticate and resolving them from two
different places is how they drift apart.

WHY A REF, NOT THE VALUE, LIVES ON THE ROW. Same discipline as every other
credential in this codebase (`workspace_connectors`, `model_providers`,
`mcp_servers`): the plaintext never sits in a queryable column. `secret_ref`
points into `shared.services.secret_store`, keyed distinctly per
(project, owner, kind, target) so two members' credentials for the same
connector on the same project — or the same member's credentials on two
different projects — never collide or overwrite each other.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services import secret_store


@dataclass(frozen=True)
class ProjectCredentialFields:
    """One project member's saved credential for one integration, in full.

    `base_url` and `account` are read straight off the row (plaintext config);
    `token` comes from the secret store. Any of the three may be None — a row
    predating the base_url column, or a connector that needs no URL, is normal
    and each consumer falls back to its tenant-wide chain field by field.
    """

    base_url: Optional[str] = None
    account: Optional[str] = None
    token: Optional[str] = None


def project_credential_ref(project_id: str, owner_id: str, kind: str, target_id: str) -> str:
    """The secret_store ref a project member's credential for one connector lives
    under. Stable and reconstructable from the four identifiers alone — the PUT
    handler and this read path must agree on it without sharing state."""
    return f"project-cred/{project_id}/{owner_id}/{kind}/{target_id}"


async def resolve_project_credential(
    db: Optional[AsyncSession] = None,
    *,
    tenant_id: str,
    project_id: str,
    owner_id: str,
    kind: str,
    target_id: str,
) -> Optional[ProjectCredentialFields]:
    """This project member's own credential for (kind, target_id), or None.

    None means "no override" — not "denied". `auth_adapter()` callers fall back
    to the tenant-wide credential exactly as before this existed; this function
    never raises and never returns a level it is not sure about, matching the
    fail-closed discipline the rest of this codebase's authz layer uses.

    TWO SOURCES, ONE RECORD. `account` and the token are the caller's own, off
    `project_integration_credentials`; `base_url` is the PROJECT's, off
    `project_integration_config`, which only someone who administers the project
    may write (migration 0032). A contributor therefore supplies who they are
    and cannot change where that identity gets sent.

    A returned record may still have None fields. A project with no configured
    instance, or a connector that needs no URL, is a perfectly good credential —
    consumers fall back per field rather than discarding the whole record, so a
    stored token keeps working whether or not a URL was ever set.

    Opens its own tenant-scoped session when `db` is omitted — connector auth
    resolution runs deep inside agent tool calls and worker tasks with no
    request-scoped session in hand, the same shape as
    `connector_grants.py::resolve_effective_access`.
    """
    if not (tenant_id and project_id and owner_id and kind and target_id):
        return None

    async def _lookup(session: AsyncSession):
        # base_url comes from the PROJECT's config, account/secret from the
        # caller's own credential — a LEFT JOIN so a project with no configured
        # instance still yields the member's identity, and the connector falls
        # back to its tenant-wide URL rather than losing the token as well.
        return (
            await session.execute(
                text(
                    "SELECT f.base_url AS base_url, c.account AS account, "
                    "       c.secret_ref AS secret_ref "
                    "FROM project_integration_credentials c "
                    "LEFT JOIN project_integration_config f "
                    "  ON f.project_id = c.project_id AND f.kind = c.kind "
                    " AND f.target_id = c.target_id "
                    "WHERE c.project_id = CAST(:p AS uuid) AND c.owner_id = :o "
                    "  AND c.kind = :k AND c.target_id = :r"
                ),
                {"p": project_id, "o": owner_id, "k": kind, "r": target_id},
            )
        ).first()

    try:
        if db is not None:
            row = await _lookup(db)
        else:
            from shared.db import get_db_session_for_tenant  # noqa: PLC0415 — import cycle

            async with get_db_session_for_tenant(tenant_id) as session:
                row = await _lookup(session)
    except Exception:  # noqa: BLE001 — fail closed, never fail open on a credential path
        return None

    if row is None:
        return None

    token = (
        await secret_store.get_secret(tenant_id, row.secret_ref)
        if row.secret_ref
        else None
    )
    return ProjectCredentialFields(
        base_url=row.base_url or None,
        account=row.account or None,
        token=token or None,
    )


async def resolve_project_secret(
    db: Optional[AsyncSession] = None,
    *,
    tenant_id: str,
    project_id: str,
    owner_id: str,
    kind: str,
    target_id: str,
) -> Optional[str]:
    """Just the token from `resolve_project_credential`, for callers that
    authenticate with a bare string and have nowhere to put a URL.

    `shared/services/mcp_registry.py` is the one such caller: an MCP server's
    address comes from its org registration, so a personal credential there is
    only ever the bearer token folded into the headers.
    """
    creds = await resolve_project_credential(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        owner_id=owner_id,
        kind=kind,
        target_id=target_id,
    )
    return creds.token if creds else None
