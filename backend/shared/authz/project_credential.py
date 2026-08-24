"""The personal credential a project member saved for themselves, if any.

`project_integration_credentials` (migration 0016) was, until this change,
write-and-display only: the secret a user typed on a project's Integrations
page was read and discarded (see shared/routers/project_scoped.py's history),
and no connector call ever consulted the table. This module is the missing
read path — the one place `secret_ref` is resolved back into a usable token,
called from `BaseConnector._resolve_credential_override` (config/connectors/base.py)
so every connector's `auth_adapter()` checks it the same way.

WHY A REF, NOT THE VALUE, LIVES ON THE ROW. Same discipline as every other
credential in this codebase (`workspace_connectors`, `model_providers`,
`mcp_servers`): the plaintext never sits in a queryable column. `secret_ref`
points into `shared.services.secret_store`, keyed distinctly per
(project, owner, kind, target) so two members' credentials for the same
connector on the same project — or the same member's credentials on two
different projects — never collide or overwrite each other.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services import secret_store


def project_credential_ref(project_id: str, owner_id: str, kind: str, target_id: str) -> str:
    """The secret_store ref a project member's credential for one connector lives
    under. Stable and reconstructable from the four identifiers alone — the PUT
    handler and this read path must agree on it without sharing state."""
    return f"project-cred/{project_id}/{owner_id}/{kind}/{target_id}"


async def resolve_project_secret(
    db: Optional[AsyncSession] = None,
    *,
    tenant_id: str,
    project_id: str,
    owner_id: str,
    kind: str,
    target_id: str,
) -> Optional[str]:
    """This project member's own credential for (kind, target_id), or None.

    None means "no override" — not "denied". `auth_adapter()` callers fall back
    to the tenant-wide credential exactly as before this existed; this function
    never raises and never returns a level it is not sure about, matching the
    fail-closed discipline the rest of this codebase's authz layer uses.

    Opens its own tenant-scoped session when `db` is omitted — connector auth
    resolution runs deep inside agent tool calls and worker tasks with no
    request-scoped session in hand, the same shape as
    `connector_grants.py::resolve_effective_access`.
    """
    if not (tenant_id and project_id and owner_id and kind and target_id):
        return None

    async def _lookup(session: AsyncSession) -> Optional[str]:
        row = (
            await session.execute(
                text(
                    "SELECT secret_ref FROM project_integration_credentials "
                    "WHERE project_id = CAST(:p AS uuid) AND owner_id = :o "
                    "  AND kind = :k AND target_id = :r"
                ),
                {"p": project_id, "o": owner_id, "k": kind, "r": target_id},
            )
        ).first()
        return row.secret_ref if row is not None else None

    try:
        if db is not None:
            ref = await _lookup(db)
        else:
            from shared.db import get_db_session_for_tenant  # noqa: PLC0415 — import cycle

            async with get_db_session_for_tenant(tenant_id) as session:
                ref = await _lookup(session)
    except Exception:  # noqa: BLE001 — fail closed, never fail open on a credential path
        return None

    if not ref:
        return None
    return await secret_store.get_secret(tenant_id, ref)
