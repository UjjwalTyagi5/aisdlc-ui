"""Org -> Business Unit -> Project model-grant cascade.

Implements docs/superpowers/specs/2026-08-11-model-gateway-bu-cascade-design.md §3.

Kept separate from model_config.py (provider CRUD/verify) and model_resolver.py (run-time
resolution) — this module owns the GOVERNANCE POLICY layer: which models exist for the
tenant's catalogue and how far each reaches, and what one project actually selected from
what it was allowed. It reads model_providers/model_offerings (owned by model_config.py)
but never writes them.

RBAC note: every endpoint that calls into this module is gated by model:manage or
run:create at the router (see shared/routers/model.py) — there is no per-workspace
"is this caller really this BU's admin" check here. That's a known, accepted gap; see
the design spec §1 and §8. Marked inline with # TODO(scoped-rbac).
"""
from __future__ import annotations

import uuid as _uuid
from typing import Optional

from sqlalchemy import text

from shared.db import get_db_session_for_tenant


class NotAllowedForUnitError(Exception):
    """A project selection names an (provider, model_id, credential_id) the project's
    Business Unit was not granted."""


def _grant_reaches(visibility: str, business_unit_ids: list[str], workspace_id: str) -> bool:
    if visibility == "global":
        return True
    return str(workspace_id) in {str(x) for x in business_unit_ids}


def _entry_key(e: dict) -> tuple[str, str, str | None]:
    return (e["provider"], e["model_id"], e.get("credential_id"))


async def get_org_grants(tenant_id: str) -> list[dict]:
    async with get_db_session_for_tenant(tenant_id) as s:
        rows = (await s.execute(
            text(
                "SELECT g.provider, g.model_id, g.credential_id, g.visibility, g.business_unit_ids, "
                "p.display_name AS credential_name "
                "FROM org_model_grants g LEFT JOIN model_providers p ON p.id = g.credential_id "
                "WHERE g.tenant_id = :t ORDER BY g.provider, g.model_id"
            ), {"t": tenant_id},
        )).fetchall()
    return [
        {
            "provider": r.provider, "model_id": r.model_id,
            "credential_id": str(r.credential_id) if r.credential_id else None,
            "credential_name": r.credential_name,
            "visibility": r.visibility, "business_unit_ids": list(r.business_unit_ids or []),
        }
        for r in rows
    ]


async def set_org_grants(tenant_id: str, entries: list[dict], created_by: str) -> list[dict]:
    # Dedupe on the same key the DB uniqueness relies on — a caller sending the same
    # (provider, model_id, credential_id) twice in one PUT must not violate the partial
    # unique index at insert time.
    deduped: dict[tuple, dict] = {}
    for e in entries:
        deduped[_entry_key(e)] = e

    async with get_db_session_for_tenant(tenant_id) as s:
        # Validate every non-null credential_id belongs to a real provider for this tenant.
        cred_ids = {e.get("credential_id") for e in deduped.values() if e.get("credential_id")}
        if cred_ids:
            found = {
                str(r[0]) for r in (await s.execute(
                    text("SELECT id FROM model_providers WHERE tenant_id = :t AND id = ANY(:ids)"),
                    {"t": tenant_id, "ids": list(cred_ids)},
                )).fetchall()
            }
            missing = cred_ids - found
            if missing:
                raise ValueError(f"unknown credential_id(s): {sorted(missing)}")

        # Full-replace semantics, matching PUT /model/allowed/org's contract.
        await s.execute(text("DELETE FROM org_model_grants WHERE tenant_id = :t"), {"t": tenant_id})
        for e in deduped.values():
            await s.execute(
                text(
                    "INSERT INTO org_model_grants "
                    "(id, tenant_id, provider, model_id, credential_id, visibility, business_unit_ids, created_by) "
                    "VALUES (:id, :t, :p, :m, :cred, :vis, :bus, :by)"
                ),
                {
                    "id": str(_uuid.uuid4()), "t": tenant_id, "p": e["provider"], "m": e["model_id"],
                    "cred": e.get("credential_id"), "vis": e.get("visibility", "global"),
                    "bus": _json_dumps(e.get("business_unit_ids", [])), "by": created_by,
                },
            )
    return await get_org_grants(tenant_id)


def _json_dumps(value) -> str:
    import json
    return json.dumps(value)


async def get_bu_allowed(tenant_id: str, workspace_id: str) -> list[dict]:
    grants = await get_org_grants(tenant_id)
    return [
        {
            "provider": g["provider"], "model_id": g["model_id"],
            "credential_id": g["credential_id"], "credential_name": g["credential_name"],
            "visibility": g["visibility"],
        }
        for g in grants
        if _grant_reaches(g["visibility"], g["business_unit_ids"], workspace_id)
    ]


async def set_bu_grants(tenant_id: str, workspace_id: str, entries: list[dict]) -> list[dict]:
    """Org Admin's per-unit control (spec §4): only moves `specific`-visibility grants for
    this unit. Implemented as: for each entry, ensure a `specific` grant naming this
    workspace exists; any EXISTING specific grant naming this workspace that is not in
    `entries` has this workspace removed from its business_unit_ids. Global grants are
    untouched — they already reach every unit and cannot be edited per-unit.
    # TODO(scoped-rbac): should also verify the caller actually administers `workspace_id`.
    """
    wanted = {_entry_key(e) for e in entries}
    async with get_db_session_for_tenant(tenant_id) as s:
        # Validate every non-null credential_id belongs to a real provider for this tenant —
        # the same check set_org_grants performs. Without it, an unknown id falls through
        # to the INSERT below and raises an unmapped FK IntegrityError (bare 500).
        cred_ids = {e.get("credential_id") for e in entries if e.get("credential_id")}
        if cred_ids:
            found = {
                str(r[0]) for r in (await s.execute(
                    text("SELECT id FROM model_providers WHERE tenant_id = :t AND id = ANY(:ids)"),
                    {"t": tenant_id, "ids": list(cred_ids)},
                )).fetchall()
            }
            missing = cred_ids - found
            if missing:
                raise ValueError(f"unknown credential_id(s): {sorted(missing)}")

        all_rows = (await s.execute(
            text(
                "SELECT id, provider, model_id, credential_id, visibility, business_unit_ids "
                "FROM org_model_grants WHERE tenant_id = :t"
            ), {"t": tenant_id},
        )).fetchall()
        specific_rows = [r for r in all_rows if r.visibility == "specific"]
        for r in specific_rows:
            key = (r.provider, r.model_id, str(r.credential_id) if r.credential_id else None)
            bu_ids = set(str(x) for x in (r.business_unit_ids or []))
            if key in wanted:
                bu_ids.add(str(workspace_id))
            else:
                bu_ids.discard(str(workspace_id))
            await s.execute(
                text("UPDATE org_model_grants SET business_unit_ids = :bus WHERE id = :id"),
                {"bus": _json_dumps(sorted(bu_ids)), "id": r.id},
            )
        # An entry needs a brand-new specific grant row only if that (provider, model_id,
        # credential_id) key has NO existing grant of any kind. If a `global` grant already
        # covers it, it already reaches every unit (including this one) — inserting a second,
        # `specific` row for the same key would violate uq_org_grant_cred /
        # uq_org_grant_null_cred, and would be a no-op anyway since it's already allowed. If a
        # `specific` grant already exists for the key, the loop above just added this
        # workspace to it.
        existing_keys = {
            (r.provider, r.model_id, str(r.credential_id) if r.credential_id else None) for r in all_rows
        }
        for e in entries:
            key = _entry_key(e)
            if key not in existing_keys:
                await s.execute(
                    text(
                        "INSERT INTO org_model_grants "
                        "(id, tenant_id, provider, model_id, credential_id, visibility, business_unit_ids, created_by) "
                        "VALUES (:id, :t, :p, :m, :cred, 'specific', :bus, 'system')"
                    ),
                    {
                        "id": str(_uuid.uuid4()), "t": tenant_id, "p": e["provider"], "m": e["model_id"],
                        "cred": e.get("credential_id"), "bus": _json_dumps([str(workspace_id)]),
                    },
                )
    return await get_bu_allowed(tenant_id, workspace_id)


async def get_availability(tenant_id: str, workspace_id: str) -> list[dict]:
    allowed = await get_bu_allowed(tenant_id, workspace_id)
    async with get_db_session_for_tenant(tenant_id) as s:
        central_rows = (await s.execute(
            text(
                "SELECT o.provider_id, mp.provider, o.model_id FROM model_offerings o "
                "JOIN model_providers mp ON mp.id = o.provider_id "
                "WHERE o.tenant_id = :t AND mp.tenant_id = :t AND mp.workspace_id IS NULL "
                "AND mp.status = 'valid' AND o.enabled = true"
            ), {"t": tenant_id},
        )).fetchall()
        local_rows = (await s.execute(
            text(
                "SELECT o.provider_id, mp.provider, o.model_id FROM model_offerings o "
                "JOIN model_providers mp ON mp.id = o.provider_id "
                "WHERE o.tenant_id = :t AND mp.tenant_id = :t AND mp.workspace_id = :w "
                "AND mp.status = 'valid' AND o.enabled = true"
            ), {"t": tenant_id, "w": workspace_id},
        )).fetchall()
    central = {(r.provider, r.model_id) for r in central_rows}
    local = {(r.provider, r.model_id) for r in local_rows}
    out = []
    for e in allowed:
        key = (e["provider"], e["model_id"])
        out.append({
            # e already carries "visibility" from get_bu_allowed — ModelAvailability
            # (frontend/lib/schemas/model.ts) requires it.
            **e,
            "centrallyCredentialed": key in central,
            "locallyCredentialed": key in local,
        })
    return out


async def _project_workspace_id(tenant_id: str, project_id: str) -> str:
    from sqlalchemy.exc import DBAPIError  # noqa: PLC0415

    try:
        async with get_db_session_for_tenant(tenant_id) as s:
            row = (await s.execute(
                text("SELECT workspace_id, tenant_id FROM projects WHERE id = :id"), {"id": project_id},
            )).first()
    except DBAPIError:
        # A malformed (non-UUID) project_id fails the SQL layer's UUID cast with a raw
        # DBAPIError — turn it into the same ValueError the "no such project" case raises,
        # so the router's existing `except ValueError -> 404` handles it uniformly.
        raise ValueError(f"invalid project_id: {project_id!r}")
    if row is None:
        raise ValueError(f"unknown project {project_id!r}")
    return str(row.workspace_id)


async def _workspace_name(tenant_id: str, workspace_id: str) -> str | None:
    async with get_db_session_for_tenant(tenant_id) as s:
        row = (await s.execute(
            text("SELECT display_name FROM workspaces WHERE id = :id"), {"id": workspace_id},
        )).first()
    return row.display_name if row else None


async def get_project_selection(tenant_id: str, project_id: str) -> dict:
    workspace_id = await _project_workspace_id(tenant_id, project_id)
    inherited = await get_bu_allowed(tenant_id, workspace_id)

    async with get_db_session_for_tenant(tenant_id) as s:
        row = (await s.execute(
            text("SELECT selected, default_key FROM project_model_selections WHERE project_id = :p"),
            {"p": project_id},
        )).first()

    selected = list(row.selected) if row and row.selected else []
    using_defaults = not selected
    ws_name = await _workspace_name(tenant_id, workspace_id)
    return {
        "inherited": inherited,
        "inheritedFrom": {"id": workspace_id, "name": ws_name} if ws_name else None,
        "selected": selected if not using_defaults else inherited,
        "usingDefaults": using_defaults,
        "defaultKey": (row.default_key if row else None),
    }


async def set_project_selection(
    tenant_id: str, project_id: str, selected: list[dict], default_key: Optional[str],
) -> dict:
    workspace_id = await _project_workspace_id(tenant_id, project_id)
    allowed_keys = {_entry_key(e) for e in await get_bu_allowed(tenant_id, workspace_id)}
    for e in selected:
        if _entry_key(e) not in allowed_keys:
            raise NotAllowedForUnitError(
                f"{e['provider']}/{e['model_id']} is not in this project's business unit's allowed set"
            )

    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO project_model_selections (id, tenant_id, project_id, selected, default_key, updated_at) "
                "VALUES (:id, :t, :p, :sel, :dk, now()) "
                "ON CONFLICT (project_id) DO UPDATE SET selected = :sel, default_key = :dk, updated_at = now()"
            ),
            {"id": str(_uuid.uuid4()), "t": tenant_id, "p": project_id, "sel": _json_dumps(selected), "dk": default_key},
        )
    return await get_project_selection(tenant_id, project_id)


async def get_grant_matrix(tenant_id: str) -> dict:
    """Rows = every (provider, model_id) currently onboarded anywhere in the tenant
    (org-wide or BU-owned) — not the full global LiteLLM catalog, which would be
    thousands of rows the matrix has no use for. See spec §3 note."""
    async with get_db_session_for_tenant(tenant_id) as s:
        onboarded = (await s.execute(
            text(
                "SELECT DISTINCT o.provider_id, mp.provider, o.model_id, mp.display_name AS credential_name, "
                "mp.workspace_id, (mp.secret_ref IS NOT NULL) AS credential_has_key "
                "FROM model_offerings o JOIN model_providers mp ON mp.id = o.provider_id "
                "WHERE o.tenant_id = :t AND mp.tenant_id = :t"
            ), {"t": tenant_id},
        )).fetchall()
        workspaces = (await s.execute(
            text("SELECT id, display_name FROM workspaces WHERE organization_id = :t"), {"t": tenant_id},
        )).fetchall()

    grants = await get_org_grants(tenant_id)
    grants_by_key = {_entry_key(g): g for g in grants}

    central_providers = {r.provider for r in onboarded if r.workspace_id is None}

    rows = []
    for r in onboarded:
        key = (r.provider, r.model_id, str(r.provider_id))
        grant = grants_by_key.get(key) or grants_by_key.get((r.provider, r.model_id, None))
        units = []
        for ws in workspaces:
            has_access = bool(grant) and _grant_reaches(
                grant["visibility"], grant["business_unit_ids"], str(ws.id)
            ) if grant else False
            units.append({
                "id": str(ws.id), "name": ws.display_name, "hasAccess": has_access,
                "locallyCredentialed": r.workspace_id is not None and str(r.workspace_id) == str(ws.id),
            })
        rows.append({
            "provider": r.provider, "model_id": r.model_id,
            "credential_id": str(r.provider_id), "credential_name": r.credential_name,
            "credentialHasKey": bool(r.credential_has_key),
            "granted": grant is not None,
            "visibility": grant["visibility"] if grant else None,
            "centrallyCredentialed": r.provider in central_providers,
            "units": units,
        })
    return {"rows": rows, "centrallyKeyedProviders": sorted(central_providers)}


async def effective_project_offerings(tenant_id: str, project_id: str | None) -> set[str] | None:
    """Returns None ("stay fully open") when the tenant has zero org_model_grants rows —
    the backward-compatibility rule from spec §5. Otherwise the set of eligible
    offering_ids for the project (its own selection, or its BU's inherited set)."""
    async with get_db_session_for_tenant(tenant_id) as s:
        has_grants = (await s.execute(
            text("SELECT 1 FROM org_model_grants WHERE tenant_id = :t LIMIT 1"), {"t": tenant_id},
        )).first()
    if not has_grants:
        return None
    if not project_id:
        # Grants are configured, but this resolution has no project context to gate by
        # (e.g. a background job). Fail closed to nothing rather than silently opening up.
        return set()

    selection = await get_project_selection(tenant_id, project_id)
    effective_entries = selection["selected"]
    keys = {_entry_key(e) for e in effective_entries}

    async with get_db_session_for_tenant(tenant_id) as s:
        rows = (await s.execute(
            text(
                "SELECT o.id, mp.provider, o.model_id, o.provider_id FROM model_offerings o "
                "JOIN model_providers mp ON mp.id = o.provider_id "
                "WHERE o.tenant_id = :t AND mp.tenant_id = :t"
            ), {"t": tenant_id},
        )).fetchall()
    out = set()
    for r in rows:
        if (r.provider, r.model_id, str(r.provider_id)) in keys or (r.provider, r.model_id, None) in keys:
            out.add(str(r.id))
    return out
