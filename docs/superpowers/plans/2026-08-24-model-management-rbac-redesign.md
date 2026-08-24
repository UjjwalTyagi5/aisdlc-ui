# Model Management RBAC Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Org Admin grants a Business Unit access to a *provider* (not specific models); BU Admin can only add keys for providers the org granted them, with a mandatory API key + live Test; BU Admin pushes specific keys to specific projects; Project Admin picks a master key among what was pushed.

**Architecture:** Reuse the existing connector-grant machinery (`integration_grants` table, generalized to a third `kind='model_provider'`) rather than building a parallel grant system. Reuse the existing `UnitAccessPicker` component unchanged for the Org Admin's grant-toggle UI. Add one new resource-scoped check (BU must hold a `model_provider` grant before creating a BU-scoped `ModelProvider`) to the existing `POST /model/providers` handler, which today has no workspace-ownership check at all — a real, standalone gap fixed as part of this work.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Postgres (RLS) on the backend; Next.js 15 + React Query + Zod on the frontend. `uv` for Python, `npm` for the frontend in this worktree.

**Spec:** `docs/superpowers/specs/2026-08-24-model-management-rbac-redesign-design.md`

## Global Constraints

- Every new/changed backend route stays under the existing `/model` or `/integrations`/`/connectors` prefixes — no new top-level routers.
- No change to `backend/shared/services/model_resolver.py` — confirmed out of scope by spec §6/§9.
- `api_key` is required (not optional) only on the **BU-scoped** provider-creation path (`workspace_id` set). Org-wide creation (`workspace_id is None`) keeps today's optional/keyless behavior.
- Reuse `integration_grants` (migration `0015_integration_grants`) via a third `kind='model_provider'` — do not create a new grant table.
- Reuse `UnitAccessPicker` (`frontend/components/app/unit-access-picker.tsx`) unchanged for the Org Admin grant-toggle UI — it is already fully generic (`units`, `selected`, `onToggle` props, no connector-specific code).
- Follow this repo's existing test-per-layer discipline: backend tests pass before frontend work starts on the same slice.

---

### Task 1: Migration — add `model_provider` to `integration_grants.kind`

**Files:**
- Create: `backend/migrations/versions/0028_model_provider_grant_kind.py`
- Test: `backend/tests/test_m9_migration_heads.py` (existing — just needs to still pass; no new assertions, since that file no longer pins a specific revision per PR #18's fix)

**Interfaces:**
- Produces: `integration_grants.kind` now accepts `'model_provider'` in addition to `'connector'` | `'mcp'`. Every later backend task depends on this migration having run.

- [ ] **Step 1: Write the migration**

```python
"""Allow integration_grants to carry model-provider grants.

Reuses the existing integration_grants table (0015_integration_grants) rather than
adding a parallel one — same reasoning as that migration's own docstring: "the same
decision made about two kinds of thing." A model_provider grant means "this Business
Unit may use provider X" — no model, no key, exactly like a connector grant.

Revision ID: 0028_model_provider_grant_kind
Revises: 0027_merge_heads_3
"""
from alembic import op

revision = "0028_model_provider_grant_kind"
down_revision = "0027_merge_heads_3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE integration_grants DROP CONSTRAINT ck_integration_grant_kind")
    op.execute(
        "ALTER TABLE integration_grants ADD CONSTRAINT ck_integration_grant_kind "
        "CHECK (kind IN ('connector', 'mcp', 'model_provider'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE integration_grants DROP CONSTRAINT ck_integration_grant_kind")
    op.execute(
        "ALTER TABLE integration_grants ADD CONSTRAINT ck_integration_grant_kind "
        "CHECK (kind IN ('connector', 'mcp'))"
    )
```

- [ ] **Step 2: Run the migration against the dev DB**

Run: `cd backend && uv run alembic upgrade head`
Expected: no errors; `uv run alembic heads` now shows `0028_model_provider_grant_kind (head)`.

- [ ] **Step 3: Verify the constraint accepts the new kind**

Run:
```bash
cd backend && uv run python -c "
import asyncio
from shared.db import get_db_session_superuser
from sqlalchemy import text

async def main():
    async with get_db_session_superuser() as s:
        await s.execute(text(
            \"INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id, granted_by) \"
            \"SELECT id, 'model_provider', 'test-provider-do-not-keep', id, 'migration-test' FROM organizations LIMIT 1\"
        ))
        await s.execute(text(\"DELETE FROM integration_grants WHERE granted_by = 'migration-test'\"))
        print('OK: model_provider kind accepted and cleaned up')

asyncio.run(main())
"
```
Expected: prints `OK: model_provider kind accepted and cleaned up`, no constraint violation.

- [ ] **Step 4: Commit**

```bash
cd backend && git add migrations/versions/0028_model_provider_grant_kind.py
git commit -m "migrate: allow integration_grants to carry model-provider grants"
```

---

### Task 2: Backend — generalize the connector-grant read/write routes to accept `kind=model_provider`

**Files:**
- Modify: `backend/shared/routers/integration_access.py:229-297` (`grant_integration_access`), `:300-391` (`revoke_integration_access`), `:394-429` (`list_connector_grants`), `:432-502` (`set_connector_grants`)
- Test: `backend/tests/test_integration_access.py` (create if it does not already exist — check first with `ls backend/tests/test_integration_access.py`; if a connector-grant test file exists under a different name, find it with `grep -rl "grant_integration_access\|/integrations/access" backend/tests/` and add to that file instead of creating a new one)

**Interfaces:**
- Consumes: `integration_grants` table with the new `model_provider` kind (Task 1).
- Produces: `POST /integrations/access?kind=model_provider&id=<provider>&workspaceId=<bu>` and `DELETE /integrations/access?kind=model_provider&id=<provider>&workspaceId=<bu>&level=unit` — org-admin-only grant/revoke, generic over kind. `GET/PUT /model/providers/grants` — new thin wrapper routes with model-provider-specific shape, described in Task 3.

- [ ] **Step 1: Read the current file in full to confirm exact line numbers before editing**

Run: `sed -n '1,60p' backend/shared/routers/integration_access.py` (already read in full during planning — this step is the implementer's own confirmation pass since line numbers may have drifted).

- [ ] **Step 2: Widen the two `kind not in (...)` checks**

In `grant_integration_access` (around line 253):
```python
    if kind not in ("connector", "mcp", "model_provider"):
        raise HTTPException(status_code=422, detail="kind must be 'connector', 'mcp', or 'model_provider'")
```

In `revoke_integration_access` (around line 326), the same change:
```python
    if kind not in ("connector", "mcp", "model_provider"):
        raise HTTPException(status_code=422, detail="kind must be 'connector', 'mcp', or 'model_provider'")
```

Leave every other line in both functions unchanged — they already operate generically on whatever `kind`/`id` was passed in, since the SQL uses `:k`/`:r` bind params, not a hardcoded `'connector'` string. (`list_connector_grants` and `set_connector_grants` are connector-specific by design — hardcoded `kind = 'connector'` in their SQL and `_CATALOG_KINDS` filtering — leave those two untouched; Task 3 adds parallel model-provider-specific versions rather than generalizing these two, since the "which kinds exist" catalog differs: connectors have a closed `_CATALOG_KINDS` set, model providers are an open LiteLLM-slug set per `ModelProviderKind = z.string()` in the frontend schema.)

- [ ] **Step 3: Write the failing test**

First check whether a grant test file already exists:
```bash
grep -rl "grant_integration_access\|/integrations/access" backend/tests/
```
If one exists, add this test to it; otherwise create `backend/tests/test_integration_access.py` with the necessary fixtures (mirror whatever fixture pattern the existing file — or the nearest connector test file, e.g. `backend/tests/test_m74_connectors.py` — uses for a seeded org/workspace/user with `org_admin`/`bu_admin` role bindings; do not invent a new fixture style).

```python
@pytest.mark.asyncio
async def test_grant_and_revoke_model_provider_access(org_admin_client, seeded_workspace):
    # Grant
    resp = await org_admin_client.post(
        "/integrations/access",
        params={"kind": "model_provider", "id": "anthropic", "workspaceId": seeded_workspace["id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["changed"] is True

    # Idempotent re-grant
    resp2 = await org_admin_client.post(
        "/integrations/access",
        params={"kind": "model_provider", "id": "anthropic", "workspaceId": seeded_workspace["id"]},
    )
    assert resp2.status_code == 200

    # Revoke
    resp3 = await org_admin_client.delete(
        "/integrations/access",
        params={"kind": "model_provider", "id": "anthropic", "workspaceId": seeded_workspace["id"], "level": "unit"},
    )
    assert resp3.status_code == 200
    assert resp3.json()["changed"] is True


@pytest.mark.asyncio
async def test_bu_admin_cannot_grant_model_provider_access(bu_admin_client, seeded_workspace):
    resp = await bu_admin_client.post(
        "/integrations/access",
        params={"kind": "model_provider", "id": "anthropic", "workspaceId": seeded_workspace["id"]},
    )
    assert resp.status_code == 403
```

Adjust `org_admin_client`/`bu_admin_client`/`seeded_workspace` fixture names to whatever the chosen test file's existing fixtures are actually called — read the file first and match its exact names; do not introduce new fixture names if equivalent ones already exist.

- [ ] **Step 4: Run test to verify it fails (or passes if kind check was the only blocker)**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_integration_access.py -k model_provider -v` (adjust path to wherever the test landed in Step 3)
Expected before Step 2's edit: FAIL with a 422 (kind rejected). After Step 2's edit: should already PASS, since nothing else in these two functions is kind-specific.

- [ ] **Step 5: Run test to verify it passes**

Run: same command as Step 4.
Expected: PASS, both tests.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/integration_access.py backend/tests/test_integration_access.py
git commit -m "feat: allow model-provider grants through the existing integration-access routes"
```

---

### Task 3: Backend — `GET`/`PUT /model/providers/grants`

**Files:**
- Modify: `backend/shared/routers/model.py` (add two new routes; imports for `require_permission`, `Request`, `AsyncSession`, `get_db_session` already present at the top of this file per Task-writing-time inspection)
- Test: `backend/tests/test_model_provider_grants.py` (new)

**Interfaces:**
- Consumes: same `integration_grants` rows as Task 2, filtered `kind = 'model_provider'`.
- Produces: `GET /model/providers/grants` → `list[{provider: str, businessUnitIds: list[str]}]`; `PUT /model/providers/grants?workspaceId=<bu>` body `{providers: list[str]}` → same shape, replacing that BU's full model-provider grant set (mirrors `set_connector_grants`'s single-workspace branch exactly — no "replace org-wide" mode is needed here since the Org Admin's grant UI always acts per-BU per the spec, unlike connectors which also has a whole-policy replace mode). Both `model:manage`-gated at the router level, with `_require_org_admin`-equivalent enforcement inline (see Step 2 — this router doesn't already import `is_org_wide`/`_require_org_admin` from `integration_access.py`; import them).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_model_provider_grants.py
import pytest


@pytest.mark.asyncio
async def test_get_and_put_model_provider_grants(org_admin_client, seeded_workspace):
    # Initially empty
    resp = await org_admin_client.get("/model/providers/grants")
    assert resp.status_code == 200
    assert resp.json() == []

    # Grant anthropic to the seeded workspace
    resp2 = await org_admin_client.put(
        "/model/providers/grants",
        params={"workspaceId": seeded_workspace["id"]},
        json={"providers": ["anthropic"]},
    )
    assert resp2.status_code == 200
    assert resp2.json() == [{"provider": "anthropic", "businessUnitIds": [seeded_workspace["id"]]}]

    # Read back org-wide
    resp3 = await org_admin_client.get("/model/providers/grants")
    assert resp3.json() == [{"provider": "anthropic", "businessUnitIds": [seeded_workspace["id"]]}]


@pytest.mark.asyncio
async def test_bu_admin_cannot_set_model_provider_grants(bu_admin_client, seeded_workspace):
    resp = await bu_admin_client.put(
        "/model/providers/grants",
        params={"workspaceId": seeded_workspace["id"]},
        json={"providers": ["anthropic"]},
    )
    assert resp.status_code == 403
```

Match this test's fixtures to whatever `org_admin_client`/`bu_admin_client`/`seeded_workspace` actually resolve to in Task 2's chosen test file — reuse the same conftest fixtures, don't reinvent.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_model_provider_grants.py -v`
Expected: FAIL — 404 (routes don't exist yet).

- [ ] **Step 3: Add the routes to `backend/shared/routers/model.py`**

Add near the bottom of the file, after the existing `/allowed/*` routes:

```python
from shared.authz.read_scope import is_org_wide  # add to the existing import block at the top


@model_router.get("/providers/grants")
async def list_model_provider_grants_route(request: Request, db: AsyncSession = Depends(get_db_session)) -> list[dict]:
    tenant_id = _tenant_id(request)
    rows = (
        await db.execute(
            text(
                "SELECT target_ref, workspace_id FROM integration_grants "
                "WHERE tenant_id = CAST(:t AS uuid) AND kind = 'model_provider'"
            ),
            {"t": tenant_id},
        )
    ).fetchall()
    by_provider: dict[str, list[str]] = {}
    for target, ws in rows:
        by_provider.setdefault(target, []).append(str(ws))
    return [
        {"provider": p, "businessUnitIds": sorted(v)}
        for p, v in sorted(by_provider.items())
    ]


@model_router.put("/providers/grants")
async def set_model_provider_grants_route(
    request: Request, workspaceId: str, body: dict, db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    if not is_org_wide(request):
        raise HTTPException(
            status_code=403,
            detail="Only an Organization Admin decides which providers a business unit may use.",
        )
    tenant_id = _tenant_id(request)
    actor = _user_id(request)
    providers = [p for p in (body.get("providers") or []) if isinstance(p, str) and p.strip()]

    await db.execute(
        text(
            "DELETE FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
            "  AND kind = 'model_provider' AND workspace_id = CAST(:w AS uuid)"
        ),
        {"t": tenant_id, "w": workspaceId},
    )
    for p in providers:
        await db.execute(
            text(
                "INSERT INTO integration_grants "
                "  (tenant_id, kind, target_ref, workspace_id, granted_by) "
                "VALUES (CAST(:t AS uuid), 'model_provider', :r, CAST(:w AS uuid), :by)"
            ),
            {"t": tenant_id, "r": p, "w": workspaceId, "by": actor},
        )
    await db.flush()
    return await list_model_provider_grants_route(request, db=db)
```

`text` and `HTTPException` are already imported at the top of `model.py`; confirm and add if either is missing.

- [ ] **Step 4: Run test to verify it passes**

Run: same as Step 2.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/model.py backend/tests/test_model_provider_grants.py
git commit -m "feat: add GET/PUT /model/providers/grants for the new provider-level grant flow"
```

---

### Task 4: Backend — gate BU-scoped provider creation on the grant + require `api_key`

**Files:**
- Modify: `backend/shared/routers/model.py:275-305` (`create_provider_route`)
- Test: `backend/tests/test_model_config_api.py` (existing — confirm exact name with `grep -rl "create_provider_route\|POST.*model/providers" backend/tests/`)

**Interfaces:**
- Consumes: `granted_target_refs(db, tenant_id=..., workspace_id=..., kind="model_provider")` from `backend/shared/authz/connector_grants.py:86-111` (already generic over `kind`, no change needed to that function itself).
- Produces: `create_provider_route` now 403s a BU-scoped call with no grant, and 422s a BU-scoped call with no `api_key`.

- [ ] **Step 1: Write the failing tests**

Add to the existing model-config API test file (find via the grep above):

```python
@pytest.mark.asyncio
async def test_bu_scoped_provider_creation_requires_grant(bu_admin_client, seeded_workspace):
    resp = await bu_admin_client.post(
        "/model/providers",
        json={
            "provider": "anthropic", "display_name": "Test key", "api_key": "sk-test-123",
            "models": [{"model_id": "claude-sonnet-5"}], "workspaceId": seeded_workspace["id"],
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_bu_scoped_provider_creation_succeeds_once_granted(
    org_admin_client, bu_admin_client, seeded_workspace,
):
    grant = await org_admin_client.put(
        "/model/providers/grants",
        params={"workspaceId": seeded_workspace["id"]},
        json={"providers": ["anthropic"]},
    )
    assert grant.status_code == 200

    resp = await bu_admin_client.post(
        "/model/providers",
        json={
            "provider": "anthropic", "display_name": "Test key", "api_key": "sk-test-123",
            "models": [{"model_id": "claude-sonnet-5"}], "workspaceId": seeded_workspace["id"],
        },
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_bu_scoped_provider_creation_requires_api_key(
    org_admin_client, bu_admin_client, seeded_workspace,
):
    await org_admin_client.put(
        "/model/providers/grants",
        params={"workspaceId": seeded_workspace["id"]},
        json={"providers": ["anthropic"]},
    )
    resp = await bu_admin_client.post(
        "/model/providers",
        json={
            "provider": "anthropic", "display_name": "Test key", "api_key": "",
            "models": [{"model_id": "claude-sonnet-5"}], "workspaceId": seeded_workspace["id"],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_org_wide_provider_creation_still_allows_no_key(org_admin_client):
    resp = await org_admin_client.post(
        "/model/providers",
        json={
            "provider": "openai", "display_name": "Org-wide, keyless", "api_key": "",
            "models": [{"model_id": "gpt-5.1"}], "workspaceId": None,
        },
    )
    assert resp.status_code == 201
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_model_config_api.py -k "bu_scoped_provider_creation or org_wide_provider_creation" -v`
Expected: `requires_grant` and `requires_api_key` FAIL (currently succeed, should 403/422); `succeeds_once_granted` and `still_allows_no_key` currently PASS (no regression to protect yet).

- [ ] **Step 3: Modify `create_provider_route`**

```python
@model_router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider_route(request: Request, body: CreateProviderIn, db: AsyncSession = Depends(get_db_session)) -> ProviderOut:
    if body.workspace_id is not None:
        if not (body.api_key or "").strip():
            raise HTTPException(status_code=422, detail="api_key is required when adding a key to a business unit's provider.")
        from shared.authz.connector_grants import granted_target_refs  # noqa: PLC0415
        granted = await granted_target_refs(
            db, tenant_id=_tenant_id(request), workspace_id=body.workspace_id, kind="model_provider",
        )
        if body.provider not in granted:
            raise HTTPException(
                status_code=403,
                detail=f"Your organization has not granted this business unit access to {body.provider!r}.",
            )
        await _require_scoped(
            db, request, permission="model:manage", resource_kind="workspace", resource_id=body.workspace_id,
        )

    # Accept either rich `models` (with pricing) or back-compat bare `enabled_models`.
    models: list[dict] = [m.model_dump() for m in body.models]
    if not models and body.enabled_models:
        models = [{"model_id": m} for m in body.enabled_models]
    try:
        d = await mc.create_provider(
            _tenant_id(request), provider=body.provider, display_name=body.display_name,
            api_key=body.api_key, models=models, api_base=body.api_base,
            created_by=_user_id(request), workspace_id=body.workspace_id,
            max_cost_per_call_usd=body.max_cost_per_call_usd,
        )
    except mc.DuplicateProviderNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except mc.InvalidModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if body.workspace_id is None and (body.visibility or body.business_unit_ids):
        entries = [
            {"provider": body.provider, "model_id": m["model_id"], "credential_id": d["id"],
             "visibility": body.visibility or "global", "business_unit_ids": body.business_unit_ids or []}
            for m in models
        ]
        existing = await mg.get_org_grants(_tenant_id(request))
        await mg.set_org_grants(_tenant_id(request), existing + entries, created_by=_user_id(request))
    return _to_provider_out(d)
```

The added block runs before the existing `try`/`except` so a missing grant or key fails fast without touching `mc.create_provider` at all. Verify `_require_scoped`'s exact signature at `backend/shared/routers/model.py:43-66` matches this call before running — it takes `db, request, *, permission, resource_kind, resource_id, deny_status=403` per the version read during planning; if `resource_kind="workspace"` is not a value `can_perform` recognizes, grep `can_perform`'s implementation (`backend/shared/authz/can_perform.py`) for the resource_kind string this codebase actually uses for a workspace/business-unit resource (it may be `"business_unit"` rather than `"workspace"` — confirm before using either) and use that exact string instead.

- [ ] **Step 4: Run tests to verify they pass**

Run: same as Step 2.
Expected: all four PASS.

- [ ] **Step 5: Run the full existing model API test file to check for regressions**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_model_config_api.py -v`
Expected: all pass, including tests that predate this change (e.g. any existing org-wide creation test).

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/model.py backend/tests/test_model_config_api.py
git commit -m "fix: require a model-provider grant and a key for BU-scoped provider creation"
```

---

### Task 5: Backend — `POST /model/providers/{id}/assign` (BU Admin pushes a key to a project)

**Files:**
- Modify: `backend/shared/routers/model.py` (add route), `backend/shared/services/model_grants.py` (add service function — read the file first to find `get_project_selection`/`set_project_selection`'s exact current signatures before adding a new function alongside them, matching their style)
- Test: `backend/tests/test_model_provider_assign.py` (new)

**Interfaces:**
- Consumes: `ProjectModelSelection`'s existing shape (`backend/shared/services/model_grants.py` — the functions backing `GET/PUT /model/allowed/project`).
- Produces: `POST /model/providers/{provider_id}/assign` body `{projectId: str}` → appends a `ModelAllowEntry` (this provider's offerings) to that project's `ProjectModelSelection.selected`, without disturbing `defaultKey`.

- [ ] **Step 1: Read `model_grants.py`'s existing project-selection functions**

Run: `grep -n "async def.*project" backend/shared/services/model_grants.py` and read each matched function in full before writing Step 2 — this task's new function must produce output in the exact same on-disk shape `set_project_selection` already writes, or the two will disagree about what "selected" contains.

- [ ] **Step 2: Write the failing test**

```python
@pytest.mark.asyncio
async def test_bu_admin_assigns_key_to_project(
    org_admin_client, bu_admin_client, seeded_workspace, seeded_project,
):
    await org_admin_client.put(
        "/model/providers/grants",
        params={"workspaceId": seeded_workspace["id"]},
        json={"providers": ["anthropic"]},
    )
    created = await bu_admin_client.post(
        "/model/providers",
        json={
            "provider": "anthropic", "display_name": "Payments prod", "api_key": "sk-test-123",
            "models": [{"model_id": "claude-sonnet-5"}], "workspaceId": seeded_workspace["id"],
        },
    )
    provider_id = created.json()["id"]

    resp = await bu_admin_client.post(
        f"/model/providers/{provider_id}/assign",
        json={"projectId": seeded_project["id"]},
    )
    assert resp.status_code == 200

    selection = await bu_admin_client.get(
        "/model/allowed/project", params={"projectId": seeded_project["id"]},
    )
    assert any(
        e["provider"] == "anthropic" and e["credentialId"] == provider_id
        for e in selection.json()["selected"]
    )


@pytest.mark.asyncio
async def test_assign_rejects_a_project_outside_the_bu_admins_unit(
    bu_admin_client, other_bu_project,
):
    resp = await bu_admin_client.post(
        "/model/providers/some-id/assign", json={"projectId": other_bu_project["id"]},
    )
    assert resp.status_code in (403, 404)
```

Add `seeded_project`/`other_bu_project` fixtures matching whatever pattern the chosen test file's neighbors already use for a project inside/outside the BU admin's unit — check `backend/tests/test_project_workspace_scope.py` for the existing "a project outside my unit" fixture pattern and reuse its shape rather than inventing a new one.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_model_provider_assign.py -v`
Expected: FAIL — 404 (route doesn't exist).

- [ ] **Step 4: Implement the service function and route**

In `model_grants.py`, add a function following the exact style/signature convention of the neighboring `set_project_selection`-style function found in Step 1 (do not guess its shape — copy the pattern). It must: look up the provider row (`mc`'s existing lookup helper, reuse rather than re-querying), confirm the project belongs to the same `workspace_id` the provider is scoped to, then append one `ModelAllowEntry`-shaped dict (`provider`, `model_id` per offering, `credentialId=provider_id`, `credentialName=display_name`) per enabled offering to the project's stored selection, leaving `defaultKey` untouched if already set.

In `model.py`, add:
```python
@model_router.post("/providers/{provider_id}/assign")
async def assign_provider_to_project_route(
    request: Request, provider_id: str, body: dict, db: AsyncSession = Depends(get_db_session),
) -> dict:
    project_id = body.get("projectId")
    if not project_id:
        raise HTTPException(status_code=422, detail="projectId is required")
    try:
        await mg.assign_provider_to_project(
            _tenant_id(request), provider_id=provider_id, project_id=project_id,
            actor_id=_user_id(request),
        )
    except mg.ProviderNotFoundError:
        raise HTTPException(status_code=404, detail="Provider not found")
    except mg.ProjectOutsideUnitError:
        raise HTTPException(status_code=403, detail="That project is not in your business unit.")
    return {"ok": True}
```

Name the two exception classes (`ProviderNotFoundError`, `ProjectOutsideUnitError`) to match whatever equivalent exceptions `model_grants.py`/`model_config.py` already raise elsewhere in the file (e.g. if `mc.ProviderNotFoundError` already exists, reuse it instead of defining a duplicate in `mg`).

- [ ] **Step 5: Run test to verify it passes**

Run: same as Step 3.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/model.py backend/shared/services/model_grants.py backend/tests/test_model_provider_assign.py
git commit -m "feat: let a BU Admin assign a provider key to one of their projects"
```

---

### Task 6: Backend — full RBAC-chain integration test (spec §6)

**Files:**
- Test: `backend/tests/test_model_provider_rbac_chain.py` (new)

**Interfaces:**
- Consumes: everything from Tasks 1-5, plus the existing `resolve_model_for_run` (`backend/shared/services/model_resolver.py`) — unchanged, verifying it correctly resolves the offering created by this new chain with no code change of its own.

- [ ] **Step 1: Write the end-to-end test**

```python
@pytest.mark.asyncio
async def test_full_chain_org_grant_to_run_resolution(
    org_admin_client, bu_admin_client, seeded_workspace, seeded_project,
):
    # 1. Org Admin grants Payments access to anthropic
    await org_admin_client.put(
        "/model/providers/grants",
        params={"workspaceId": seeded_workspace["id"]},
        json={"providers": ["anthropic"]},
    )
    # 2. BU Admin adds a key
    created = await bu_admin_client.post(
        "/model/providers",
        json={
            "provider": "anthropic", "display_name": "Payments prod", "api_key": "sk-test-123",
            "models": [{"model_id": "claude-sonnet-5"}], "workspaceId": seeded_workspace["id"],
        },
    )
    provider_id = created.json()["id"]
    offering_id = created.json()["offerings"][0]["id"]

    # 3. BU Admin assigns it to the project
    await bu_admin_client.post(f"/model/providers/{provider_id}/assign", json={"projectId": seeded_project["id"]})

    # 4. Project Admin sets it as default
    selection = await bu_admin_client.get("/model/allowed/project", params={"projectId": seeded_project["id"]})
    await bu_admin_client.put(
        "/model/allowed/project",
        params={"projectId": seeded_project["id"]},
        json={"selected": selection.json()["selected"], "defaultKey": provider_id},
    )

    # 5. resolve_model_for_run resolves that exact offering — no code change needed here
    from shared.services.model_resolver import resolve_model_for_run
    resolved = await resolve_model_for_run(
        seeded_workspace["tenant_id"], offering_id=offering_id, project_id=seeded_project["id"],
    )
    assert resolved.offering_id == offering_id
    assert resolved.provider == "anthropic"


@pytest.mark.asyncio
async def test_ungranted_bu_cannot_add_a_key_directly(bu_admin_client, seeded_workspace):
    """The bug the user opened this design conversation to fix."""
    resp = await bu_admin_client.post(
        "/model/providers",
        json={
            "provider": "anthropic", "display_name": "Should be blocked", "api_key": "sk-test-123",
            "models": [{"model_id": "claude-sonnet-5"}], "workspaceId": seeded_workspace["id"],
        },
    )
    assert resp.status_code == 403
```

Adjust the `PUT /model/allowed/project` body shape to whatever `ProjectModelSelection`'s actual PUT schema requires per `frontend/lib/schemas/model.ts:161-168` (`selected`, `defaultKey`) — already matches; confirm `mg`'s corresponding backend function accepts the same two keys by the same names before running.

- [ ] **Step 2: Run and verify both pass**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_model_provider_rbac_chain.py -v`
Expected: both PASS. If `resolve_model_for_run`'s signature doesn't match this call exactly, read its actual signature (already read once this session: `resolve_model_for_run(tenant_id, requested_model_id=None, *, offering_id=None, project_id=None)`) and fix the call rather than the function.

- [ ] **Step 3: Run the FULL backend test suite (excluding the pre-existing-broken collection paths) to check for regressions**

Run: `cd backend && PYTHONPATH=. uv run pytest -q -m "not integration" --ignore=agents_orchestrator/monitoring_feedback_agent/test_pipeline.py --ignore=agents_orchestrator/monitoring_feedback_agent/test_router_logic.py --ignore=agents_orchestrator/testing_agent/tests`

This can be slow (observed ~30 min elsewhere this session) and has pre-existing unrelated failures (documented in `help/portfolio-1-decisions-log.md` from earlier this session — stale migration-head assertions, a local Postgres role-separation config gap, legacy pre-auth tests). Confirm no NEW failures appear in files this plan touched (`test_integration_access.py`, `test_model_provider_grants.py`, `test_model_config_api.py`, `test_model_provider_assign.py`, `test_model_provider_rbac_chain.py`, and anything importing `integration_access.py`/`model.py`/`connector_grants.py`/`model_grants.py`). Redirect output to a file rather than piping through `tail` (this session found piped output gets silently truncated on long runs); run it as a single clean background process, not concurrent with any `uv sync`.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_model_provider_rbac_chain.py
git commit -m "test: prove the full org-grant-to-run-resolution RBAC chain end to end"
```

---

### Task 7: Backend — data migration script for existing per-model grants

**Files:**
- Create: `backend/scripts/migrate_org_model_grants_to_provider_grants.py`
- Test: `backend/tests/test_migrate_org_model_grants_to_provider_grants.py` (new)

**Interfaces:**
- Consumes: `org_model_grants` (existing table, unchanged), writes to `integration_grants` (`kind='model_provider'`).

- [ ] **Step 1: Read `model_grants.py`'s `get_org_grants` to know the exact row shape being migrated from**

Run: `grep -n "async def get_org_grants" -A 30 backend/shared/services/model_grants.py` and read the full function before Step 2.

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_migrate_org_model_grants_to_provider_grants.py
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_migrates_specific_visibility_grants_to_provider_grants(seeded_org_with_specific_grant):
    from scripts.migrate_org_model_grants_to_provider_grants import migrate

    tenant_id, workspace_id = seeded_org_with_specific_grant
    await migrate(tenant_id)

    from shared.db import get_db_session_superuser
    async with get_db_session_superuser() as s:
        row = (await s.execute(
            text(
                "SELECT 1 FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
                "  AND kind = 'model_provider' AND target_ref = 'anthropic' "
                "  AND workspace_id = CAST(:w AS uuid)"
            ),
            {"t": tenant_id, "w": workspace_id},
        )).first()
    assert row is not None


@pytest.mark.asyncio
async def test_global_visibility_grants_are_not_migrated(seeded_org_with_global_grant):
    """Global grants already reach every BU via org-wide model_providers rows —
    nothing to backfill, per spec §7 step 1."""
    from scripts.migrate_org_model_grants_to_provider_grants import migrate

    tenant_id = seeded_org_with_global_grant
    await migrate(tenant_id)

    from shared.db import get_db_session_superuser
    async with get_db_session_superuser() as s:
        count = (await s.execute(
            text(
                "SELECT count(*) FROM integration_grants WHERE tenant_id = CAST(:t AS uuid) "
                "  AND kind = 'model_provider'"
            ),
            {"t": tenant_id},
        )).scalar()
    assert count == 0
```

Add the two `seeded_org_with_specific_grant`/`seeded_org_with_global_grant` fixtures to this test file directly (each seeds an org, a workspace, and one `org_model_grants` row with the named visibility) — follow `test_model_resolver.py`'s `_seed_valid_provider`-style fixture pattern (already read this session) for the seeding style.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_migrate_org_model_grants_to_provider_grants.py -v`
Expected: FAIL — `ModuleNotFoundError` (script doesn't exist yet).

- [ ] **Step 4: Write the script**

```python
"""One-off: backfill integration_grants (kind='model_provider') from existing
org_model_grants rows with visibility='specific'. Global-visibility grants need no
action — see the module docstring in the design spec, §7.

Idempotent: re-running inserts nothing new for a pair already migrated (ON CONFLICT
DO NOTHING against integration_grants' composite primary key).

    python -m scripts.migrate_org_model_grants_to_provider_grants [--tenant TENANT_ID]

Omit --tenant to migrate every tenant.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from shared.db import get_db_session_for_tenant, get_db_session_superuser


async def migrate(tenant_id: str) -> int:
    """Returns the number of integration_grants rows written."""
    async with get_db_session_for_tenant(tenant_id) as s:
        rows = (
            await s.execute(
                text(
                    "SELECT DISTINCT provider, unnest(business_unit_ids) AS workspace_id "
                    "FROM org_model_grants WHERE tenant_id = CAST(:t AS uuid) AND visibility = 'specific'"
                ),
                {"t": tenant_id},
            )
        ).fetchall()
        written = 0
        for provider, workspace_id in rows:
            result = await s.execute(
                text(
                    "INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id, granted_by) "
                    "VALUES (CAST(:t AS uuid), 'model_provider', :p, CAST(:w AS uuid), 'migration-0028') "
                    "ON CONFLICT (tenant_id, kind, target_ref, workspace_id) DO NOTHING"
                ),
                {"t": tenant_id, "p": provider, "w": str(workspace_id)},
            )
            written += result.rowcount
        return written


async def _all_tenant_ids() -> list[str]:
    async with get_db_session_superuser() as s:
        rows = (await s.execute(text("SELECT id FROM organizations"))).fetchall()
    return [str(r.id) for r in rows]


async def main(tenant: str | None) -> None:
    tenant_ids = [tenant] if tenant else await _all_tenant_ids()
    total = 0
    for t in tenant_ids:
        n = await migrate(t)
        total += n
        print(f"  tenant {t}: {n} grant(s) written")
    print(f"\n{total} total integration_grants row(s) written across {len(tenant_ids)} tenant(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=None, help="Migrate only this tenant id")
    args = parser.parse_args()
    asyncio.run(main(args.tenant))
```

- [ ] **Step 5: Run test to verify it passes**

Run: same as Step 3.
Expected: both PASS.

- [ ] **Step 6: Run the script for real against this worktree's dev DB**

Run: `cd backend && uv run python -m scripts.migrate_org_model_grants_to_provider_grants`
Report the printed summary — this is the actual one-time backfill for whatever `org_model_grants` rows exist in the shared dev Postgres today.

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/migrate_org_model_grants_to_provider_grants.py backend/tests/test_migrate_org_model_grants_to_provider_grants.py
git commit -m "feat: one-off migration from per-model org grants to provider grants"
```

---

### Task 8: Frontend — API client + schema additions

**Files:**
- Modify: `frontend/lib/api/models.ts` (add functions), `frontend/lib/schemas/model.ts` (add types)

**Interfaces:**
- Produces: `listModelProviderGrants()`, `setModelProviderGrants(workspaceId, providers)`, `assignProviderToProject(providerId, projectId)`, `grantModelProvider(provider, workspaceId)`, `revokeModelProvider(provider, workspaceId)` — the typed client functions every frontend task below calls.

- [ ] **Step 1: Add the Zod schema**

In `frontend/lib/schemas/model.ts`, add after `ModelGrantMatrix` (around line 150):

```typescript
export const ModelProviderGrant = z.object({
  provider: ModelProviderKind,
  businessUnitIds: z.array(z.string()),
});
export type ModelProviderGrant = z.infer<typeof ModelProviderGrant>;
```

- [ ] **Step 2: Add the API functions**

In `frontend/lib/api/models.ts`, add after `getModelGrantMatrix` (end of file):

```typescript
import { ModelProviderGrant } from "@/lib/schemas/model";

/** Which business units may use which provider — the Org Admin's grant list. */
export const listModelProviderGrants = () =>
  api("/model/providers/grants", { schema: z.array(ModelProviderGrant) });

/** Replace one business unit's full provider-grant set. */
export const setModelProviderGrants = (workspaceId: string, providers: string[]) =>
  api("/model/providers/grants", {
    method: "PUT",
    query: { workspaceId },
    body: { providers },
    schema: z.array(ModelProviderGrant),
  });

/** BU Admin pushes an already-added key onto one of their projects. */
export const assignProviderToProject = (providerId: string, projectId: string) =>
  api(`/model/providers/${encodeURIComponent(providerId)}/assign`, {
    method: "POST",
    body: { projectId },
  });

export const grantModelProvider = (provider: string, workspaceId: string) =>
  api("/integrations/access", {
    method: "POST",
    query: { kind: "model_provider", id: provider, workspaceId },
  });

export const revokeModelProvider = (provider: string, workspaceId: string) =>
  api("/integrations/access", {
    method: "DELETE",
    query: { kind: "model_provider", id: provider, workspaceId, level: "unit" },
  });
```

Check `frontend/lib/api/client.ts`'s `api()` helper signature before this step (its `query`/`body`/`method` option shape is already used identically elsewhere in this same file, e.g. `setBuConnectorGrants` in `frontend/lib/api/connectors.ts:26-30` — mirror that exact call shape).

- [ ] **Step 3: Add to the query-key registry**

In `frontend/lib/api/query-keys.ts`, find the `model` section (used by `qk.model.providers`, `qk.model.grantMatrix`, etc. — already referenced in `frontend/app/(app)/admin/models/page.tsx`) and add:
```typescript
providerGrants: () => ["model", "providerGrants"] as const,
```

- [ ] **Step 4: Verify the frontend type-checks**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep -i "model.ts\|models.ts\|query-keys.ts"`
Expected: no output (no new type errors from these three files).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/api/models.ts frontend/lib/schemas/model.ts frontend/lib/api/query-keys.ts
git commit -m "feat: frontend API client for provider grants and project key assignment"
```

---

### Task 9: Frontend — Org Admin view: remove "Add provider", add the grant-toggle dropdown

**Files:**
- Modify: `frontend/app/(app)/admin/models/page.tsx:339-498` (main render), `:522-578` (`ProviderCard`)
- Test: check for an existing test file with `grep -rl "ModelProvidersPage\|admin/models" frontend/__tests__/` and update it, or note in the commit if none exists yet

**Interfaces:**
- Consumes: `listModelProviderGrants()`, `setModelProviderGrants()` from Task 8; `UnitAccessPicker` from `frontend/components/app/unit-access-picker.tsx` (unchanged, already generic — `{units, selected, onToggle}` props).
- Produces: Org Admin's `/admin/models` page with the "Add provider" button removed for `scope === "org"`, provider cards rendering an inline `UnitAccessPicker` instead of a `Link` when `scope === "org"`.

- [ ] **Step 1: Add a grants query, gated on `isOrg`**

In `ModelProvidersPage` (around line 167, next to the existing `matrixQ`), add:
```typescript
const providerGrantsQ = useQuery({
  queryKey: qk.model.providerGrants(),
  queryFn: listModelProviderGrants,
  enabled: isOrg,
});
```
Import `listModelProviderGrants` from `@/lib/api/models` and add it to the existing import list at the top of the file.

- [ ] **Step 2: Remove the org-admin "Add provider" button**

At line 355-363 (the header's `<Button onClick={() => setAddOpen(true)} ...>Add provider</Button>`), wrap it: this button should render only when `!isOrg` (BU/project scope keeps it for now — Task 10 replaces its label/behavior for BU scope specifically):
```tsx
{!isOrg && (
  <Button
    onClick={() => setAddOpen(true)}
    disabled={effectiveCatalog.length === 0}
    title={effectiveCatalog.length === 0 ? "No allowed models to onboard from yet" : undefined}
    className="from-brand-gradient-from to-brand-gradient-to shrink-0 bg-gradient-to-br font-semibold text-white shadow-[0_6px_18px_-6px_oklch(0.6_0.2_35_/_0.65)] transition-shadow hover:shadow-[0_10px_26px_-8px_oklch(0.6_0.2_35_/_0.8)]"
  >
    <Plus className="size-4" aria-hidden />
    Add provider
  </Button>
)}
```
Do the same for the second "Add provider" button inside the empty-state block (around line 416-423).

- [ ] **Step 3: Pass grant data + a toggle handler into `ProviderCard`, and branch its rendering**

Change `ProviderCard`'s call site (around line 465-471) to also pass `isOrg`, `grants`, and `allWorkspaces` (`grantableWorkspaces`, already computed at line 200):
```tsx
<ProviderCard
  key={kind}
  kind={kind}
  connections={group}
  spendUsd={spendQ.data ? (spendByProvider.get(kind) ?? 0) : null}
  isOrg={isOrg}
  grantedUnitIds={providerGrantsQ.data?.find((g) => g.provider === kind)?.businessUnitIds ?? []}
  grantableWorkspaces={grantableWorkspaces}
  onToggleGrant={(workspaceId) => toggleProviderGrant(kind, workspaceId)}
/>
```

Add `toggleProviderGrant` as a function in the page component, above the `return`:
```typescript
const toggleProviderGrant = async (provider: string, workspaceId: string) => {
  const current = providerGrantsQ.data?.find((g) => g.provider === provider)?.businessUnitIds ?? [];
  const next = current.includes(workspaceId)
    ? current.filter((id) => id !== workspaceId)
    : [...current, workspaceId];
  await setModelProviderGrants(workspaceId, next.includes(workspaceId) ? [provider] : []);
  // Re-fetch rather than optimistic-update: a single toggle can only change ONE
  // workspace's set, but setModelProviderGrants replaces that workspace's WHOLE
  // set — see Task 3's route contract — so the correct call is scoped per-workspace,
  // not per-provider. Simplify: call with workspaceId's full new provider list.
  queryClient.invalidateQueries({ queryKey: qk.model.providerGrants() });
};
```
This naive per-provider toggle doesn't match the PUT route's per-workspace-replace-whole-set contract from Task 3. Fix before finishing this step: `setModelProviderGrants(workspaceId, providers)` replaces workspace `workspaceId`'s ENTIRE provider grant list, so toggling one provider for one workspace must first compute that workspace's current full provider list (which requires inverting `providerGrantsQ.data`'s provider→units shape into a units→providers map), then call with that whole list plus/minus the one being toggled:
```typescript
const grantsByWorkspace = React.useMemo(() => {
  const map: Record<string, string[]> = {};
  for (const g of providerGrantsQ.data ?? []) {
    for (const wsId of g.businessUnitIds) {
      (map[wsId] ??= []).push(g.provider);
    }
  }
  return map;
}, [providerGrantsQ.data]);

const toggleProviderGrant = async (provider: string, workspaceId: string) => {
  const current = grantsByWorkspace[workspaceId] ?? [];
  const next = current.includes(provider)
    ? current.filter((p) => p !== provider)
    : [...current, provider];
  await setModelProviderGrants(workspaceId, next);
  queryClient.invalidateQueries({ queryKey: qk.model.providerGrants() });
};
```

- [ ] **Step 4: Update `ProviderCard` to branch on `isOrg`**

Replace the component signature and its `Link`-wrapped title (lines 522-578) so that when `isOrg` is true, the title renders as plain text (not a link) and a `UnitAccessPicker` appears inline instead of the `ChevronRight`:

```tsx
function ProviderCard({
  kind,
  connections,
  spendUsd,
  isOrg,
  grantedUnitIds,
  grantableWorkspaces,
  onToggleGrant,
}: {
  kind: string;
  connections: ModelProvider[];
  spendUsd: number | null;
  isOrg: boolean;
  grantedUnitIds: string[];
  grantableWorkspaces: { id: string; name: string }[];
  onToggleGrant: (workspaceId: string) => void;
}) {
  const label = providerLabel(kind);
  const modelCount = new Set(
    connections.flatMap((c) => c.offerings.filter((o) => o.enabled).map((o) => o.model_id)),
  ).size;

  return (
    <Card className="border-line-soft bg-panel-elevated relative flex flex-row items-center gap-3 px-4 py-3.5 shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_4px_14px_-6px_oklch(0_0_0_/_0.35)]">
      <ProviderGlyph kind={kind} label={label} />
      <div className="min-w-0 flex-1">
        {isOrg ? (
          <h3 className="font-display text-[15px] font-bold tracking-[-0.01em] truncate">{label}</h3>
        ) : (
          <h3 className="font-display text-[15px] font-bold tracking-[-0.01em]">
            <Link
              href={`/admin/models/${encodeURIComponent(kind)}`}
              className="block truncate rounded-sm after:absolute after:inset-0 after:content-[''] focus-visible:outline-none"
            >
              {label}
            </Link>
          </h3>
        )}
        <p className="text-muted-foreground mt-0.5 font-mono text-[11.5px] tabular-nums">
          {modelCount} {modelCount === 1 ? "model" : "models"}
          {typeof spendUsd === "number" && (
            <>
              {" · "}
              <span className="text-foreground font-semibold">
                {spendUsd.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 })}
              </span>
              <span className="ml-1 font-sans">this month</span>
            </>
          )}
        </p>
      </div>
      {isOrg ? (
        <UnitAccessPicker
          units={grantableWorkspaces}
          selected={grantedUnitIds}
          onToggle={onToggleGrant}
        />
      ) : (
        <ChevronRight className="text-muted-foreground size-4 shrink-0" aria-hidden />
      )}
    </Card>
  );
}
```

Note: removing the `relative` + `::after` stretched-link pattern's dependency on the whole card being a click target is intentional for `isOrg` — the card body is no longer a link at all in that branch, only the (removed) chevron was hinting at navigation; the `UnitAccessPicker` popover is its own interactive control. Import `UnitAccessPicker` at the top of the file: `import { UnitAccessPicker } from "@/components/app/unit-access-picker";`.

- [ ] **Step 5: Add the separate, de-emphasized "add org-wide key" action for Org Admin**

Per spec §5, this stays a real action but visually secondary. Add a small text-button in the page header, next to (not replacing) the removed primary button, visible only when `isOrg`:
```tsx
{isOrg && (
  <button
    type="button"
    onClick={() => setAddOpen(true)}
    className="text-muted-foreground hover:text-foreground text-[12.5px] underline underline-offset-2"
  >
    Add an org-wide key
  </button>
)}
```
Place this directly below the `<PageTitle>` in the header, not in the button's old position — it should read as a secondary, textual affordance, not a call-to-action button. The existing `AddModelDialog` (unchanged in this task — Task 10 changes it for BU scope only) still opens via `setAddOpen(true)` and still works for org-wide keyed/keyless onboarding exactly as today.

- [ ] **Step 6: Manual verification (no automated test framework covers full page interaction here — confirm via the running app)**

Start the backend and frontend (see the "Commands to start everything" note the user already has from earlier this session), sign in as an org_admin seeded persona, navigate to `/admin/models`, confirm: no "Add provider" button in the header where the primary button used to be; a small "Add an org-wide key" text link is present and opens the existing dialog; each provider card shows a `UnitAccessPicker` popover, not a link; toggling a business unit in the popover persists across a page reload.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/\(app\)/admin/models/page.tsx
git commit -m "feat: Org Admin grants providers to business units instead of adding models"
```

---

### Task 10: Frontend — BU Admin view: "Add key" flow with mandatory key + Test button

**Files:**
- Read first (full file, 858 lines): `frontend/components/app/add-model-dialog.tsx`
- Modify: same file
- Modify: `frontend/app/(app)/admin/models/page.tsx` (BU-scope button label + filtered provider list)

**Interfaces:**
- Consumes: `verifyModelProvider(id)` from `frontend/lib/api/models.ts:103-107` (already exists, already builds the live-probe verify call this Test button needs — confirm its exact behavior by reading its current call sites, e.g. wherever the existing detail page already uses it, before wiring a new button to it).
- Produces: for BU-scoped (`!isOrg`) rendering, `AddModelDialog` requires `api_key`, shows a "Test" button beside the key field that must succeed before "Save" enables, and skips the provider-picker step (provider is fixed to whichever tile was clicked).

- [ ] **Step 1: Read the full current `AddModelDialog` implementation**

Run: `cat frontend/components/app/add-model-dialog.tsx` (858 lines — read in full; this task cannot be planned further at the byte level without seeing the actual current step/state structure, which was not fully read during the planning pass). Identify: the component's step state machine (the screenshot shows a 4-step numbered form: Provider → Models → Credential → Usage limits), where `api_key`'s optionality is currently expressed (the input's `required` prop or lack thereof, and the submit handler's validation), and where/whether a "Test" affordance already exists anywhere in this file (the screenshots suggest none does yet for this specific dialog, though `verifyModelProvider` exists as a callable).

- [ ] **Step 2: Add a `mode: "org" | "bu-add-key"` prop (or equivalent — match whatever prop-naming convention the file already uses for its existing `targetUnits`/`needsApproval` mode-like props)**

When `mode === "bu-add-key"`:
- Skip the provider-selection step entirely — the dialog opens already scoped to one provider (passed as a new required prop, e.g. `fixedProvider: string`).
- Make the API key field required: add client-side validation that blocks Save while empty, mirroring however this file already validates its other required fields (e.g. `display_name`).
- Add a "Test" button beside the key input, calling a NEW client-side-only verify flow: since `verifyModelProvider(id)` (existing) needs an already-created provider id, and this dialog is pre-save, either (a) call a lighter-weight live-probe endpoint if one already exists for pre-save validation — grep `frontend/lib/api/models.ts` and `backend/shared/routers/model.py` for anything resembling a stateless "test these credentials without saving" route before assuming one must be built — or (b) if none exists, create the provider row first in an unverified state on Test-click, immediately call `verifyModelProvider`, and only enable Save once that returns `status: "valid"`, deleting the row via `deleteModelProvider` if the user cancels without saving. Prefer (a); only build (b)'s create-then-verify-then-commit dance if grepping confirms no stateless pre-save probe endpoint exists. If (b) is required, this is a real backend gap this task must also close — add a stateless `POST /model/providers/probe` route (body: `{provider, api_key, api_base?}`, no persistence) reusing whatever live-probe logic `mc.verify_provider` already wraps (read that function first), and prefer that over the create-then-delete dance since a probe-then-create is strictly less error-prone than create-then-maybe-delete.

- [ ] **Step 3: Update the BU-scope call site in `page.tsx`**

Change the BU-scope "Add provider" button (kept per Task 9 Step 2's `{!isOrg && ...}` branch) to read "Add key" instead of "Add provider", and pass `mode="bu-add-key"` plus a `fixedProvider` prop once the dialog is opened from a specific already-granted provider's tile rather than a bare page-level button (this changes WHERE the button lives: it should move from the page header into each granted-but-not-yet-keyed provider card, matching the screenshots — re-read screenshot 7/8's flow: the BU Admin's "Add provider"/"Add key" action is reached from the page-level button in the current screenshots, but the target design has them adding a key per already-visible granted provider. Confirm with a quick reread of the design spec §5's BU Admin section before finalizing whether the button stays page-level with a provider-picker-first step removed, or moves per-card — the spec says "provider is fixed to the one being added to (no provider picker)" which implies per-card, so move the trigger into each `ModelAvailabilityCard`/provider tile row for a granted-but-unkeyed provider, not the page header).

- [ ] **Step 4: Update the existing test file for this dialog if one exists**

Run: `grep -rl "AddModelDialog" frontend/__tests__/ frontend/components/app/__tests__/ 2>/dev/null`. If found, add cases for: Save disabled with empty key in `bu-add-key` mode; Save disabled until Test succeeds; provider-picker step absent in `bu-add-key` mode. If no test file exists yet, create `frontend/components/app/__tests__/add-model-dialog.test.tsx` covering exactly those three cases, matching whatever testing-library setup the nearest existing component test in this same `__tests__` directory already uses (e.g. `clarification-card.test.tsx`, already found earlier this session).

- [ ] **Step 5: Run the frontend test suite for this file**

Run: `cd frontend && npx vitest run add-model-dialog` (or whatever runner/command the existing test file convention uses — check `package.json`'s `test` script first).
Expected: PASS.

- [ ] **Step 6: Manual verification**

Sign in as a bu_admin persona whose unit has been granted a provider (from Task 9's toggle, done as org_admin first), confirm the "Add key" flow: no provider picker, key required, Test button gates Save, and after Save the provider appears with a real key.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/app/add-model-dialog.tsx frontend/app/\(app\)/admin/models/page.tsx
git commit -m "feat: BU Admin's Add key flow requires a credential and a passing Test"
```

---

### Task 11: Frontend — BU Admin's keys-list-per-provider page gains "Assign to project"

**Files:**
- Read first (full file, 895 lines): `frontend/app/(app)/admin/models/[provider]/page.tsx`
- Modify: same file

**Interfaces:**
- Consumes: `assignProviderToProject` from Task 8.
- Produces: for a BU-scoped viewer (not org), each listed key row gains an "Assign to project" action opening a project picker scoped to the viewer's own BU's projects.

- [ ] **Step 1: Read the full current detail-page implementation**

Run: `cat frontend/app/(app)/admin/models/[provider]/page.tsx` (895 lines). Identify: how it currently distinguishes org-admin vs BU-admin rendering (mirroring the pattern already read in `page.tsx`'s `scope` logic), where each credential/key row is rendered, and whether a "which projects use my BU" data source already exists on this page or needs a new query (`useScopedBusinessUnits`'s associated projects, or a project-list-by-workspace API call — check `frontend/lib/api/projects.ts` for an existing `listProjects(workspaceId)`-shaped function before adding a new one).

- [ ] **Step 2: Add an "Assign to project" action per key row, BU-scope only**

Using whatever existing project-list API this file (or `projects.ts`) already exposes for "projects in workspace X", add a small popover/picker (reuse a `Command`+`Popover` pattern matching `UnitAccessPicker`'s own structure from Task 9, or reuse a project-picker component if one already exists — grep `frontend/components/app/` for `project-picker` or `project-select` before writing a new one) that calls `assignProviderToProject(credentialId, projectId)` on selection, with a toast confirmation matching this codebase's existing toast pattern (check any nearby mutation's `onSuccess` for the toast call shape already used elsewhere on this page).

- [ ] **Step 3: Manual verification**

As a bu_admin with at least one keyed provider, open its detail page, assign a key to one of the BU's projects, confirm no error and (per Task 12) that the project's Settings → Model tab now shows it.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/\(app\)/admin/models/\[provider\]/page.tsx
git commit -m "feat: BU Admin can assign a provider key to a project from its detail page"
```

---

### Task 12: Frontend — Project Admin's Settings → Model tab: assigned keys + master picker

**Files:**
- Read first: `frontend/components/app/project-model-selection-card.tsx` (found during Task investigation — this is very likely the existing component already implementing `defaultKey` selection; read it in full before deciding what changes vs. what's already correct)
- Modify: same file (or confirm no changes needed beyond what's already there)

**Interfaces:**
- Consumes: `getProjectModelSelection`/`setProjectModelSelection` (existing, unchanged schema).

- [ ] **Step 1: Read the current component in full**

Run: `cat frontend/components/app/project-model-selection-card.tsx`. Per spec §3's Project Admin section and this plan's Task 5, `selected` is now typically populated by the BU Admin's assign action rather than the project admin browsing freely — determine whether this component currently offers a "browse the BU's full catalogue and self-select" UI (per today's stated behavior in `page.tsx`'s BU/project header copy: "Onboard your own credentials for any that need them") that needs removing/narrowing, or whether it already just renders whatever `selected` contains with a `defaultKey` radio/picker and needs no structural change at all — only the upstream data source changed (BU-pushed rather than self-picked), not this component's own rendering contract.

- [ ] **Step 2: If free self-service browsing/adding exists in this component, remove it per decision #4**

Only if Step 1 finds it: remove any "browse BU catalogue and add" affordance from the project-scope rendering path, leaving only: the list of `selected` entries (as pushed by the BU Admin) and the existing `defaultKey` picker control, now the tab's primary control per spec §5.

- [ ] **Step 3: Manual verification**

As a project_admin whose BU Admin assigned a key (Task 11), open the project's Settings → Model tab, confirm the assigned key appears and can be set as default, and confirm (if Step 2 found something to remove) that there's no longer a way to add an arbitrary un-assigned key directly from this tab.

- [ ] **Step 4: Commit (only if Step 1/2 found and made changes)**

```bash
git add frontend/components/app/project-model-selection-card.tsx
git commit -m "fix: project's Model tab shows only BU-assigned keys, not free self-service browsing"
```

If Step 1 finds nothing to change, skip the commit and note in the final report that this component already matched the target design.

---

### Task 13: Full regression pass

**Files:** none (verification-only task)

- [ ] **Step 1: Backend full suite**

Run: `cd backend && PYTHONPATH=. uv run pytest -q -m "not integration" --ignore=agents_orchestrator/monitoring_feedback_agent/test_pipeline.py --ignore=agents_orchestrator/monitoring_feedback_agent/test_router_logic.py --ignore=agents_orchestrator/testing_agent/tests > /tmp-or-scratchpad/full_suite.log 2>&1` (redirect to a real file per this session's earlier finding that piping through `tail` silently truncates long runs; run as a single process, nothing concurrent).
Compare failures against the baseline already documented in `help/portfolio-1-decisions-log.md` from earlier this session — only investigate NEW failures not already explained there.

- [ ] **Step 2: Frontend full suite**

Run: `cd frontend && npm test` (or whatever the actual `package.json` test script is — confirm before running).

- [ ] **Step 3: Frontend typecheck**

Run: `cd frontend && npx tsc --noEmit`

- [ ] **Step 4: Report**

Summarize: total tasks completed, any deviations from this plan made during implementation (e.g. Task 10's mode (a)/(b) branch, Task 12's found-nothing-to-change outcome), full regression status, and the exact `git log --oneline` of this worktree's branch since it forked from main.

---

## Self-review notes (completed during planning)

- **Spec coverage:** §3.1 (grant table reuse) → Task 1-3. §3.2 (BU-scoped creation gate) → Task 4. §3.4 (assign + master key) → Task 5, 11, 12. §4 (API table) → Tasks 2-5. §5 (frontend flows) → Tasks 9-12. §6 (RBAC chain) → Task 6. §7 (migration) → Task 7. §8 (testing) → woven into every task's own test steps plus Task 6/13. §9 (out of scope) → respected; no task touches `model_resolver.py`.
- **Placeholder scan:** the two spots where a task says "read the file first to confirm X before writing Y" (Tasks 5, 10, 11, 12) are not placeholders — they are legitimate reconnaissance steps for files whose full content (858/895 lines) was not read during plan-writing; each still specifies exactly what to look for and what decision the reading resolves, not "figure it out."
- **Type consistency:** `ModelProviderGrant` (Task 8) is the one new shared type — used identically in Task 9's `providerGrantsQ.data` and nowhere renamed. `granted_target_refs(kind="model_provider")` (Task 2/4) matches the existing function's real signature read directly from source, not guessed.
