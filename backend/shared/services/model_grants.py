"""Org -> Business Unit -> Project model-grant cascade.

Implements docs/superpowers/specs/2026-08-11-model-gateway-bu-cascade-design.md §3.

Kept separate from model_config.py (provider CRUD/verify) and model_resolver.py (run-time
resolution) — this module owns the GOVERNANCE POLICY layer: which models exist for the
tenant's catalogue and how far each reaches, and what one project actually selected from
what it was allowed. It reads model_providers/model_offerings (owned by model_config.py)
but never writes them.

RBAC note: every endpoint that calls into this module is gated by model:manage or
run:create at the router (see shared/routers/model.py), AND — since scoped RBAC
(shared/authz/can_perform.py) landed — by a resource-scoped can_perform check at the
router for the BU/project routes (_require_scoped in model.py), closing the gap the
design spec §1/§8 originally flagged. This module itself still performs no scope
check; that responsibility stays at the router, one layer up.
"""
from __future__ import annotations

import uuid as _uuid
from typing import Optional

from sqlalchemy import text

from shared.db import get_db_session_for_tenant
from shared.services import model_config as mc


class NotAllowedForUnitError(Exception):
    """A project selection names an (provider, model_id, credential_id) the project's
    Business Unit was not granted."""


class ProjectKeyNotAllowedError(Exception):
    """A project tried to onboard its own key for a (provider, model_id) its Business
    Unit hasn't opted into project-level keys for (PRD §371/§1640 — 'only if the
    Business Unit allows it'), or for a model the BU never made reachable at all."""


class ProjectOutsideUnitError(Exception):
    """assign_provider_to_project's target project belongs to a different Business
    Unit than the provider connection being pushed onto it (or the provider isn't
    BU-scoped at all — an org-wide connection has no single unit to push from)."""


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


async def get_bu_key_policy(tenant_id: str, workspace_id: str) -> dict[tuple[str, str], bool]:
    """(provider, model_id) -> whether this BU's projects may bring their own key for
    it. Missing entries default to False (PRD §371: off unless the BU explicitly
    allows it) — this table only ever stores the models someone has actually toggled.

    workspace_id is compared against a UUID column: a malformed value (mirrors
    get_availability's own local_rows guard) must not crash callers like
    get_bu_allowed that tolerate a bad id by returning "nothing BU-specific" rather
    than raising."""
    if not _is_valid_uuid(workspace_id):
        return {}
    async with get_db_session_for_tenant(tenant_id) as s:
        rows = (await s.execute(
            text("SELECT provider, model_id, allow_project_key FROM bu_model_key_policy "
                 "WHERE tenant_id = :t AND workspace_id = :w"),
            {"t": tenant_id, "w": workspace_id},
        )).fetchall()
    return {(r.provider, r.model_id): bool(r.allow_project_key) for r in rows}


async def set_bu_key_policy(
    tenant_id: str, workspace_id: str, entries: list[dict], updated_by: str,
) -> dict[tuple[str, str], bool]:
    """Replace this BU's (provider, model_id) -> allow_project_key policy.
    `entries`: [{provider, model_id, allow_project_key}, ...] — only entries with
    allow_project_key=True need to be sent; anything omitted defaults back to False
    (full-replace, matching set_bu_grants' contract)."""
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text("DELETE FROM bu_model_key_policy WHERE tenant_id = :t AND workspace_id = :w"),
            {"t": tenant_id, "w": workspace_id},
        )
        for e in entries:
            if not e.get("allow_project_key"):
                continue
            await s.execute(
                text(
                    "INSERT INTO bu_model_key_policy "
                    "(id, tenant_id, workspace_id, provider, model_id, allow_project_key, updated_by) "
                    "VALUES (:id, :t, :w, :p, :m, true, :by)"
                ),
                {"id": str(_uuid.uuid4()), "t": tenant_id, "w": workspace_id,
                 "p": e["provider"], "m": e["model_id"], "by": updated_by},
            )
    return await get_bu_key_policy(tenant_id, workspace_id)


async def get_bu_allowed(tenant_id: str, workspace_id: str) -> list[dict]:
    grants = await get_org_grants(tenant_id)
    policy = await get_bu_key_policy(tenant_id, workspace_id)
    return [
        {
            "provider": g["provider"], "model_id": g["model_id"],
            "credential_id": g["credential_id"], "credential_name": g["credential_name"],
            "visibility": g["visibility"],
            "allow_project_key": policy.get((g["provider"], g["model_id"]), False),
        }
        for g in grants
        if _grant_reaches(g["visibility"], g["business_unit_ids"], workspace_id)
    ]


async def set_bu_grants(
    tenant_id: str, workspace_id: str, entries: list[dict], updated_by: str = "system",
) -> list[dict]:
    """Org Admin's per-unit control (spec §4): only moves `specific`-visibility grants for
    this unit. Implemented as: for each entry, ensure a `specific` grant naming this
    workspace exists; any EXISTING specific grant naming this workspace that is not in
    `entries` has this workspace removed from its business_unit_ids. Global grants are
    untouched — they already reach every unit and cannot be edited per-unit.
    The caller actually administering `workspace_id` is verified one layer up, at the
    router (shared/routers/model.py's _require_scoped, via can_perform).
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
    await set_bu_key_policy(tenant_id, workspace_id, entries, updated_by)
    return await get_bu_allowed(tenant_id, workspace_id)


def _is_valid_uuid(value: str) -> bool:
    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


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
        # workspace_id is compared against a UUID column below; a malformed value (e.g. a
        # BU identity not yet migrated to a real backend id) must not crash the request —
        # treat it as "nothing locally credentialed" rather than raising.
        if _is_valid_uuid(workspace_id):
            local_rows = (await s.execute(
                text(
                    "SELECT o.provider_id, mp.provider, o.model_id FROM model_offerings o "
                    "JOIN model_providers mp ON mp.id = o.provider_id "
                    "WHERE o.tenant_id = :t AND mp.tenant_id = :t AND mp.workspace_id = :w "
                    "AND mp.status = 'valid' AND o.enabled = true"
                ), {"t": tenant_id, "w": workspace_id},
            )).fetchall()
        else:
            local_rows = []
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


async def assert_project_key_allowed(tenant_id: str, project_id: str, provider: str, model_id: str) -> str:
    """Raise ProjectKeyNotAllowedError unless this project's BU (a) has the model
    reachable at all, AND (b) has explicitly opted into project-level keys for it.
    Returns the project's workspace_id (the caller needs it too — resolved once).
    """
    workspace_id = await _project_workspace_id(tenant_id, project_id)
    bu_allowed = await get_bu_allowed(tenant_id, workspace_id)
    entry = next((e for e in bu_allowed if e["provider"] == provider and e["model_id"] == model_id), None)
    if entry is None:
        raise ProjectKeyNotAllowedError(
            f"{provider}/{model_id} is not reachable by this project's business unit at all"
        )
    if not entry.get("allow_project_key"):
        raise ProjectKeyNotAllowedError(
            f"this project's business unit has not allowed project-level keys for {provider}/{model_id}"
        )
    return workspace_id


async def _project_owned_offering_keys(tenant_id: str, project_id: str, selected: list[dict]) -> set[tuple]:
    """(provider, model_id, credential_id) keys within `selected` whose credential_id
    is a model_providers row this exact project owns — the second acceptance path
    set_project_selection allows alongside the BU-shared allowed set."""
    cred_ids = {e.get("credential_id") for e in selected if e.get("credential_id")}
    if not cred_ids:
        return set()
    async with get_db_session_for_tenant(tenant_id) as s:
        rows = (await s.execute(
            text("SELECT mp.id AS provider_id, mp.provider, mo.model_id FROM model_providers mp "
                 "JOIN model_offerings mo ON mo.provider_id = mp.id "
                 "WHERE mp.tenant_id = :t AND mp.project_id = :p AND mp.id = ANY(:ids)"),
            {"t": tenant_id, "p": project_id, "ids": list(cred_ids)},
        )).fetchall()
    return {(r.provider, r.model_id, str(r.provider_id)) for r in rows}


async def set_project_selection(
    tenant_id: str, project_id: str, selected: list[dict], default_key: Optional[str],
) -> dict:
    workspace_id = await _project_workspace_id(tenant_id, project_id)
    bu_allowed = await get_bu_allowed(tenant_id, workspace_id)
    allowed_keys = {_entry_key(e) for e in bu_allowed}
    # A project may also select its OWN key (assert_project_key_allowed already gated
    # creating it), for a model its BU made reachable under any credential — checked by
    # model_id alone here since the BU's shared credential_id differs from the project's own.
    reachable_models = {(e["provider"], e["model_id"]) for e in bu_allowed}
    own_keys = await _project_owned_offering_keys(tenant_id, project_id, selected)
    for e in selected:
        key = _entry_key(e)
        if key in allowed_keys:
            continue
        if key in own_keys and (e["provider"], e["model_id"]) in reachable_models:
            continue
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


async def assign_provider_to_project(
    tenant_id: str, provider_id: str, project_id: str, actor_id: str,
) -> dict:
    """A BU Admin pushes a `model_providers` row they already created (mc.create_provider's
    BU-scoped, key-required flow — Task 4) onto one of their own projects: appends one
    ModelAllowEntry-shaped dict per ENABLED offering to that project's stored `selected`
    list, in the exact on-disk shape set_project_selection already writes/get_project_selection
    already reads (provider/model_id/credential_id/credential_name keys, JSONB column,
    ON CONFLICT (project_id) upsert) — so the two never disagree about what "selected"
    contains. `default_key` is read back and re-written unchanged; the ON CONFLICT clause
    only ever assigns `selected`, so an existing default_key can never be clobbered by
    this path even under a concurrent write.

    Ownership — does the caller actually administer the target project's Business Unit —
    is checked one layer up at the router (_require_scoped in model.py), matching every
    other BU/project route in this module (see module docstring). This function only
    checks the one thing that check can't: that the PROVIDER named is actually scoped to
    that SAME Business Unit, not just that the caller administers some unit or other.
    """
    if not mc._is_valid_uuid(provider_id):
        raise mc.ProviderNotFoundError(provider_id)

    async with get_db_session_for_tenant(tenant_id) as s:
        row = await mc._provider_row(s, provider_id)
        if row is None:
            raise mc.ProviderNotFoundError(provider_id)
        offerings = await mc._offerings_for(s, provider_id)

    provider_workspace_id = str(row.workspace_id) if row.workspace_id else None
    project_workspace_id = await _project_workspace_id(tenant_id, project_id)
    if provider_workspace_id is None or provider_workspace_id != str(project_workspace_id):
        raise ProjectOutsideUnitError(
            f"provider {provider_id!r} is not scoped to project {project_id!r}'s business unit"
        )

    new_entries = [
        {"provider": row.provider, "model_id": o["model_id"],
         "credential_id": provider_id, "credential_name": row.display_name}
        for o in offerings if o["enabled"]
    ]

    async with get_db_session_for_tenant(tenant_id) as s:
        sel_row = (await s.execute(
            text("SELECT selected, default_key FROM project_model_selections WHERE project_id = :p"),
            {"p": project_id},
        )).first()
        selected = list(sel_row.selected) if sel_row and sel_row.selected else []
        existing_keys = {_entry_key(e) for e in selected}
        for e in new_entries:
            key = _entry_key(e)
            if key not in existing_keys:
                selected.append(e)
                existing_keys.add(key)
        default_key = sel_row.default_key if sel_row else None
        await s.execute(
            text(
                "INSERT INTO project_model_selections (id, tenant_id, project_id, selected, default_key, updated_at) "
                "VALUES (:id, :t, :p, :sel, :dk, now()) "
                # Unlike set_project_selection's upsert, default_key is deliberately NOT in
                # the UPDATE SET clause — this path only ever appends to `selected` and must
                # never touch an already-set defaultKey (the brief's stated contract).
                "ON CONFLICT (project_id) DO UPDATE SET selected = :sel, updated_at = now()"
            ),
            {"id": str(_uuid.uuid4()), "t": tenant_id, "p": project_id,
             "sel": _json_dumps(selected), "dk": default_key},
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
