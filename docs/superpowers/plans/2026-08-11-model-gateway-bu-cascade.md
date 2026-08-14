# Model Gateway: Org → BU → Project Cascade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the model-gateway org→BU→project governance cascade real — an Org Admin
grants which models exist and how far they reach, a Business Unit's projects can only
select from what was granted, and every LLM run enforces that. Also replace the frontend's
9 fixture-backed `/api/model/*` BFF routes with real calls to the FastAPI backend.

**Architecture:** New `org_model_grants` + `project_model_selections` Postgres tables, a new
`shared/services/model_grants.py` service, new endpoints on the existing
`shared/routers/model.py`, a small resolver change so `resolve_model_for_run` gates
eligibility by the calling project's effective offering set, and a frontend-only swap of
9 route handlers from in-memory fixtures to the existing `bffProxy` pattern (already used
by every other real BFF domain in this app).

**Tech Stack:** FastAPI + SQLAlchemy (async, raw `text()` queries) + Alembic + Postgres +
Redis, on the backend. Next.js 15 App Router route handlers + Zod, on the frontend.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-11-model-gateway-bu-cascade-design.md` — every task
  below implements a section of that document; read it first if anything here is unclear.
- Backend dev server: `uv run uvicorn process_api:app --port 8001 --host 127.0.0.1` from
  `backend/`. Postgres + Redis run via `docker compose up -d postgres redis` from `backend/`
  (already running in this environment — user `postgres`, password `1234`, db
  `sdlc_product`).
- Migrations: `uv run python -m alembic upgrade head` from `backend/`. Current head is
  `0034_merge_skills_budgets`.
- Tests: `uv run pytest <path> -v` from `backend/`. Tests hit the **real** Postgres
  container (no mocking) — see `backend/tests/test_model_resolver.py` for the exact style:
  each test uses a fresh random `uuid.uuid4()` tenant id for isolation, no rollback/cleanup
  needed.
- A tenant with **zero** `org_model_grants` rows must resolve exactly as it does today
  (fully open, tenant-wide) — this is the backward-compatibility rule from the spec §5. It
  is not optional; several tests below assert it directly.
- RBAC: gate new endpoints with the existing `require_permission("model:manage")` /
  `require_permission("run:create")` dependencies already in `shared/routers/model.py` —
  do not invent a new permission string. See spec §4 for which of the two each endpoint
  uses.
- Known, accepted gap (do not try to fix in this plan): no backend enforcement stops a
  `model:manage` holder from editing a BU they aren't really assigned to. Mark every spot
  this matters with `# TODO(scoped-rbac)`.

---

### Task 1: Migration — grants, selections, approval columns

**Files:**
- Create: `backend/migrations/versions/0035_model_grants_cascade.py`

**Interfaces:**
- Produces tables `org_model_grants`, `project_model_selections` and new columns on
  `model_providers` (`approval_status`, `approval_decided_by`, `approval_decided_at`,
  `approval_reason`) that every later task reads/writes via raw SQL.

- [ ] **Step 1: Write the migration**

```python
"""0035 model grants cascade — org_model_grants, project_model_selections,
model_providers approval columns.

Implements the data model from docs/superpowers/specs/2026-08-11-model-gateway-bu-cascade-design.md §2.

org_model_grants: the only place a model enters the org's catalogue for use beyond its
onboarding provider. A `global` grant reaches every BU automatically; `specific` reaches
only the named units (business_unit_ids). Same model can be granted twice under two
different keys (credential_id), so uniqueness is (tenant_id, provider, model_id,
credential_id) — with a partial index covering the NULL-credential ("any key") case,
since Postgres treats every NULL as distinct in a normal unique constraint.

project_model_selections: what one project actually uses. No row (or an empty `selected`)
means "inherit the BU's full allowed set" — enforced in the service layer, not here.

model_providers gains approval workflow columns (schema only in this plan — see spec §8
known gap 2 for why the workflow itself isn't reachable yet).

Both new tables are tenant-private → full RLS lifecycle (ENABLE + POLICY + FORCE), same
pattern as 0032/usage_monthly and 0015/model_providers.

Revision ID: 0035
Revises: 0034_merge_skills_budgets
"""
import uuid as _uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0035"
down_revision = "0034_merge_skills_budgets"
branch_labels = None
depends_on = None


def _force_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(
        f"CREATE POLICY tenant_isolation_insert ON {table} "
        "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # ── org_model_grants ───────────────────────────────────────────────────
    op.create_table(
        "org_model_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column(
            "credential_id", UUID(as_uuid=True),
            sa.ForeignKey("model_providers.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="global"),
        sa.Column("business_unit_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_org_model_grants_tenant_id", "org_model_grants", ["tenant_id"])
    # Non-null credential_id: normal unique constraint.
    op.create_unique_constraint(
        "uq_org_grant_cred", "org_model_grants", ["tenant_id", "provider", "model_id", "credential_id"],
    )
    # Null credential_id ("any key"): partial unique index, since NULL <> NULL would
    # otherwise let duplicate "any key" grants for the same model slip past the constraint
    # above.
    op.execute(
        "CREATE UNIQUE INDEX uq_org_grant_null_cred ON org_model_grants "
        "(tenant_id, provider, model_id) WHERE credential_id IS NULL"
    )
    _force_rls("org_model_grants")

    # ── project_model_selections ───────────────────────────────────────────
    op.create_table(
        "project_model_selections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=_uuid.uuid4),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False, index=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True,
        ),
        sa.Column("selected", JSONB, nullable=False, server_default="[]"),
        sa.Column("default_key", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_project_model_selections_tenant_id", "project_model_selections", ["tenant_id"])
    _force_rls("project_model_selections")

    # ── model_providers approval workflow columns ──────────────────────────
    op.add_column("model_providers", sa.Column("approval_status", sa.String(20), nullable=False, server_default="active"))
    op.add_column("model_providers", sa.Column("approval_decided_by", sa.String(255), nullable=True))
    op.add_column("model_providers", sa.Column("approval_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("model_providers", sa.Column("approval_reason", sa.Text(), nullable=True))

    print(
        "\n[0035] org_model_grants + project_model_selections created (full RLS); "
        "model_providers.approval_status/decided_by/decided_at/reason added."
    )


def downgrade() -> None:
    op.drop_column("model_providers", "approval_reason")
    op.drop_column("model_providers", "approval_decided_at")
    op.drop_column("model_providers", "approval_decided_by")
    op.drop_column("model_providers", "approval_status")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON project_model_selections")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON project_model_selections")
    op.drop_table("project_model_selections")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_insert ON org_model_grants")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON org_model_grants")
    op.execute("DROP INDEX IF EXISTS uq_org_grant_null_cred")
    op.drop_table("org_model_grants")
```

- [ ] **Step 2: Run the migration**

Run (from `backend/`): `uv run python -m alembic upgrade head`
Expected: prints `[0035] org_model_grants + project_model_selections created...` and exits 0.

- [ ] **Step 3: Verify with psql**

Run: `docker exec sdlc-postgres psql -U postgres -d sdlc_product -c "\d org_model_grants"` and
`docker exec sdlc-postgres psql -U postgres -d sdlc_product -c "\d project_model_selections"`
Expected: both tables listed with the columns above.

- [ ] **Step 4: Commit**

```bash
git add backend/migrations/versions/0035_model_grants_cascade.py
git commit -m "Add org_model_grants + project_model_selections tables and model_providers approval columns"
```

---

### Task 2: `model_grants.py` service

**Files:**
- Create: `backend/shared/services/model_grants.py`
- Test: `backend/tests/test_model_grants.py`

**Interfaces:**
- Consumes: `shared.db.get_db_session_for_tenant(tenant_id)` (async context manager, same as
  `model_config.py`); `model_providers`/`model_offerings` tables from Task 1's migration and
  the pre-existing schema.
- Produces (used by Task 3's router and Task 4's resolver):
  - `class NotAllowedForUnitError(Exception)`
  - `async def get_org_grants(tenant_id: str) -> list[dict]`
  - `async def set_org_grants(tenant_id: str, entries: list[dict], created_by: str) -> list[dict]`
  - `async def get_bu_allowed(tenant_id: str, workspace_id: str) -> list[dict]`
  - `async def set_bu_grants(tenant_id: str, workspace_id: str, entries: list[dict]) -> list[dict]`
  - `async def get_availability(tenant_id: str, workspace_id: str) -> list[dict]`
  - `async def get_project_selection(tenant_id: str, project_id: str) -> dict`
  - `async def set_project_selection(tenant_id: str, project_id: str, selected: list[dict], default_key: str | None) -> dict`
  - `async def get_grant_matrix(tenant_id: str) -> dict`
  - `async def effective_project_offerings(tenant_id: str, project_id: str | None) -> set[str] | None`
    — `None` means "no grants configured, stay fully open" (Task 4 depends on this exact
    contract).

Each `dict` entry for a grant/allow-entry has keys: `provider`, `model_id`,
`credential_id` (nullable str), `credential_name` (nullable str).

- [ ] **Step 1: Write the failing tests**

```python
"""backend/tests/test_model_grants.py — org/BU/project grant cascade (live Postgres)."""
import uuid

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


async def _seed_org_workspace_project(tenant_id: str, ws_name: str = "Retail Banking"):
    """Insert a minimal organizations/workspaces/projects row-set for FK targets.
    Mirrors the pattern in tests/development/test_pr_persistence.py."""
    from shared.db import get_db_session_for_tenant

    ws_id = uuid.uuid4()
    proj_id = uuid.uuid4()
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                "VALUES (:id, :slug, :dn, now(), now()) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": tenant_id, "slug": f"org-{tenant_id[:8]}", "dn": "Test Org"},
        )
        await s.execute(
            text(
                "INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                "VALUES (:id, :org_id, :slug, :dn, now(), now())"
            ),
            {"id": str(ws_id), "org_id": tenant_id, "slug": f"ws-{str(ws_id)[:8]}", "dn": ws_name},
        )
        await s.execute(
            text(
                "INSERT INTO projects (id, workspace_id, tenant_id, display_name, created_at, updated_at) "
                "VALUES (:id, :ws_id, :t, :dn, now(), now())"
            ),
            {"id": str(proj_id), "ws_id": str(ws_id), "t": tenant_id, "dn": "Mobile App"},
        )
    return str(ws_id), str(proj_id)


async def _seed_provider(tenant_id: str, models: list[str], workspace_id: str | None = None):
    from shared.services import model_config as mc
    return await mc.create_provider(
        tenant_id, provider="anthropic", display_name=f"conn-{uuid.uuid4().hex[:8]}",
        api_key="sk-byok-xyz", enabled_models=models, created_by="admin1",
        workspace_id=workspace_id,
    )


@pytest.mark.asyncio
async def test_global_grant_reaches_every_bu():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, _ = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"])
    offering_id = provider["offerings"][0]["id"]

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": offering_id and provider["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    allowed = await mg.get_bu_allowed(tenant, ws_a)
    assert any(e["model_id"] == "claude-sonnet-4-6" for e in allowed)


@pytest.mark.asyncio
async def test_specific_grant_reaches_only_named_bu():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, _ = await _seed_org_workspace_project(tenant, "Unit A")
    ws_b, _ = await _seed_org_workspace_project(tenant, "Unit B")
    provider = await _seed_provider(tenant, ["claude-opus-4-8"])

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-opus-4-8", "credential_id": provider["id"], "visibility": "specific", "business_unit_ids": [ws_a]}],
        created_by="admin1",
    )

    allowed_a = await mg.get_bu_allowed(tenant, ws_a)
    allowed_b = await mg.get_bu_allowed(tenant, ws_b)
    assert any(e["model_id"] == "claude-opus-4-8" for e in allowed_a)
    assert not any(e["model_id"] == "claude-opus-4-8" for e in allowed_b)


@pytest.mark.asyncio
async def test_project_selection_rejects_out_of_grant_entry():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"])
    # NOTE: no grant created at all — the BU's allowed set is empty.

    with pytest.raises(mg.NotAllowedForUnitError):
        await mg.set_project_selection(
            tenant, proj_a,
            selected=[{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"]}],
            default_key=None,
        )


@pytest.mark.asyncio
async def test_project_using_defaults_inherits_bu_set_live():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"])

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    selection = await mg.get_project_selection(tenant, proj_a)
    assert selection["usingDefaults"] is True
    assert any(e["model_id"] == "claude-sonnet-4-6" for e in selection["inherited"])
    assert selection["selected"] == selection["inherited"]

    # Widen the BU's grant with a second model — the project's inherited view must move too.
    await mg.set_org_grants(
        tenant,
        [
            {"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []},
            {"provider": "anthropic", "model_id": "claude-haiku-4-5-20251001", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []},
        ],
        created_by="admin1",
    )
    selection2 = await mg.get_project_selection(tenant, proj_a)
    assert len(selection2["inherited"]) == 2


@pytest.mark.asyncio
async def test_effective_project_offerings_none_when_no_grants_configured():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    _, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    # No org_model_grants rows for this tenant at all.

    result = await mg.effective_project_offerings(tenant, proj_a)
    assert result is None


@pytest.mark.asyncio
async def test_effective_project_offerings_scoped_once_a_grant_exists():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    ws_a, proj_a = await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6", "claude-opus-4-8"])
    offering_ids = {o["model_id"]: o["id"] for o in provider["offerings"]}

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    result = await mg.effective_project_offerings(tenant, proj_a)
    assert result == {offering_ids["claude-sonnet-4-6"]}


@pytest.mark.asyncio
async def test_grant_matrix_one_row_per_model_credential_pair():
    from shared.services import model_grants as mg

    tenant = str(uuid.uuid4())
    await _seed_org_workspace_project(tenant, "Unit A")
    provider = await _seed_provider(tenant, ["claude-sonnet-4-6"])

    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": provider["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    matrix = await mg.get_grant_matrix(tenant)
    rows = [r for r in matrix["rows"] if r["model_id"] == "claude-sonnet-4-6"]
    assert len(rows) == 1
    assert rows[0]["granted"] is True
    assert "anthropic" in matrix["centrallyKeyedProviders"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_model_grants.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.services.model_grants'` (and
`create_provider() got an unexpected keyword argument 'workspace_id'` once the module
exists, until Task 3 lands — that's expected; Task 3 makes that argument real).

- [ ] **Step 3: Write the implementation**

```python
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
        rows = (await s.execute(
            text(
                "SELECT id, provider, model_id, credential_id, visibility, business_unit_ids "
                "FROM org_model_grants WHERE tenant_id = :t AND visibility = 'specific'"
            ), {"t": tenant_id},
        )).fetchall()
        for r in rows:
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
        # An entry with no existing specific grant row at all needs one created —
        # e.g. the unit is being granted a model that has no grant yet.
        existing_keys = {(r.provider, r.model_id, str(r.credential_id) if r.credential_id else None) for r in rows}
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
            **e,
            "centrallyCredentialed": key in central,
            "locallyCredentialed": key in local,
        })
    return out


async def _project_workspace_id(tenant_id: str, project_id: str) -> str:
    async with get_db_session_for_tenant(tenant_id) as s:
        row = (await s.execute(
            text("SELECT workspace_id, tenant_id FROM projects WHERE id = :id"), {"id": project_id},
        )).first()
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
                "mp.workspace_id "
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
            "credentialHasKey": True,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_model_grants.py -v`
Expected: all 7 tests PASS. If `test_project_selection_rejects_out_of_grant_entry` fails
with anything other than `NotAllowedForUnitError`, re-check that no grant was accidentally
seeded for that test's tenant (it must be truly empty).

- [ ] **Step 5: Commit**

```bash
git add backend/shared/services/model_grants.py backend/tests/test_model_grants.py
git commit -m "Add model_grants service: org grants, BU allow-lists, project selection, grant matrix"
```

---

### Task 3: `model_config.py` changes — workspace-scoped, keyless onboarding

**Files:**
- Modify: `backend/shared/services/model_config.py`
- Test: `backend/tests/test_model_config_api.py` (add new tests; do not remove existing ones)

**Interfaces:**
- Consumes: nothing new.
- Produces: `create_provider(..., workspace_id: str | None = None)` now accepts an
  optional `api_key` (`None`/empty → provider created with no secret, `hasKey=false`
  equivalent — surfaced as `"secret_ref": None` and no `secret_store.put_secret` call).
  `list_providers(tenant_id, scope: str | None = None, workspace_id: str | None = None)`
  — `scope="all"` returns every connection (org-wide + every BU's); a bare `workspace_id`
  returns org-wide + that one BU's. Both are consumed by Task 5's router.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_model_config_api.py`:

```python
@pytest.mark.asyncio
async def test_create_provider_without_api_key_has_no_key():
    from shared.services import model_config as mc
    import uuid
    tenant = str(uuid.uuid4())

    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Keyless",
        api_key=None, enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    assert created["secret_ref"] is None


@pytest.mark.asyncio
async def test_create_provider_scoped_to_workspace():
    from shared.services import model_config as mc
    import uuid
    tenant = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())

    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="BU Key",
        api_key="sk-x", enabled_models=["claude-sonnet-4-6"], created_by="admin1",
        workspace_id=ws_id,
    )
    providers = await mc.list_providers(tenant, workspace_id=ws_id)
    assert any(p["id"] == created["id"] for p in providers)

    other_ws_providers = await mc.list_providers(tenant, workspace_id=str(uuid.uuid4()))
    # A different BU sees org-wide connections only — this BU-scoped one is absent.
    assert not any(p["id"] == created["id"] for p in other_ws_providers)


@pytest.mark.asyncio
async def test_list_providers_scope_all_returns_every_connection():
    from shared.services import model_config as mc
    import uuid
    tenant = str(uuid.uuid4())
    org_wide = await mc.create_provider(
        tenant, provider="anthropic", display_name="Org Wide",
        api_key="sk-x", enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    bu_scoped = await mc.create_provider(
        tenant, provider="openai", display_name="BU Scoped",
        api_key="sk-y", enabled_models=["gpt-4o"], created_by="admin1",
        workspace_id=str(uuid.uuid4()),
    )
    providers = await mc.list_providers(tenant, scope="all")
    ids = {p["id"] for p in providers}
    assert org_wide["id"] in ids and bu_scoped["id"] in ids
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_model_config_api.py -v -k "without_api_key or scoped_to_workspace or scope_all"`
Expected: FAIL — `create_provider() got an unexpected keyword argument 'workspace_id'`
(and the api_key-required ValueError firing for the keyless test).

- [ ] **Step 3: Modify `create_provider` and `list_providers`**

In `backend/shared/services/model_config.py`, change the `create_provider` signature and
body (replace the existing function):

```python
async def create_provider(
    tenant_id: str, *, provider: str, display_name: str, api_key: str | None,
    created_by: str, models: list[dict] | None = None,
    enabled_models: list[str] | None = None, api_base: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    """Create a provider connection + its enabled model offerings.

    `api_key` may be None/empty — the connection is registered with no secret (hasKey via
    a null secret_ref) so its models can be granted centrally while a Business Unit or
    project supplies its own key later (spec §2.3). `workspace_id` scopes the connection to
    that BU (NULL = org-wide).

    Pass either `models` (rich specs: {model_id, input_price_per_million?,
    output_price_per_million?}) or `enabled_models` (bare ids, back-compat).
    Providers outside the curated catalog are treated as CUSTOM: catalog
    validation is skipped (any LiteLLM provider/model is allowed), but pricing on
    every model is MANDATORY so Cost/Langfuse can attribute spend. Onboarding is
    gated only by model:manage RBAC at the router.
    """
    display_name = (display_name or "").strip()
    provider = (provider or "").strip()
    api_base = (api_base or "").strip() or None
    api_key = (api_key or "").strip() or None
    if models is None:
        models = [{"model_id": m} for m in (enabled_models or [])]
    if not provider or not display_name:
        raise ValueError("provider and display_name are required")
    if not models:
        raise ValueError("at least one model is required")

    is_custom = not is_known_provider(provider)
    for m in models:
        model_id = (m.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("each model needs a model_id")
        known = is_valid_model(provider, model_id)
        in_p = m.get("input_price_per_million")
        out_p = m.get("output_price_per_million")
        if in_p is None or out_p is None:
            c_in, c_out = price_for(provider, model_id)
            in_p = in_p if in_p is not None else c_in
            out_p = out_p if out_p is not None else c_out
        if not known and (in_p is None or out_p is None):
            raise InvalidModelError(
                f"model {model_id!r} is not in the catalog for provider {provider!r} — "
                f"provide input and output pricing (USD per 1M tokens) to onboard it")
        m["input_price_per_million"] = in_p
        m["output_price_per_million"] = out_p

    async with get_db_session_for_tenant(tenant_id) as s:
        if await _name_exists(s, tenant_id, display_name):
            raise DuplicateProviderNameError(
                f"A provider connection named {display_name!r} already exists")

    provider_id = str(_uuid.uuid4())
    secret_ref = _secret_ref(provider_id) if api_key else None
    if api_key:
        await secret_store.put_secret(tenant_id, secret_ref, api_key)
    try:
        async with get_db_session_for_tenant(tenant_id) as s:
            await s.execute(
                text("INSERT INTO model_providers "
                     "(id, tenant_id, workspace_id, provider, display_name, secret_ref, api_base, is_custom, status, created_by) "
                     "VALUES (:id, :t, :w, :p, :n, :ref, :ab, :cust, 'unverified', :by)"),
                {"id": provider_id, "t": tenant_id, "w": workspace_id, "p": provider, "n": display_name,
                 "ref": secret_ref, "ab": api_base, "cust": is_custom, "by": created_by},
            )
            for m in models:
                await s.execute(
                    text("INSERT INTO model_offerings "
                         "(id, tenant_id, provider_id, model_id, enabled, is_default, "
                         "input_price_per_million, output_price_per_million, "
                         "rpm_limit, tpm_limit, cost_limit_usd) "
                         "VALUES (:id, :t, :pid, :m, true, false, :ip, :op, :rpm, :tpm, :cost)"),
                    {"id": str(_uuid.uuid4()), "t": tenant_id, "pid": provider_id,
                     "m": (m.get("model_id") or "").strip(),
                     "ip": m.get("input_price_per_million"),
                     "op": m.get("output_price_per_million"),
                     "rpm": m.get("rpm_limit"), "tpm": m.get("tpm_limit"),
                     "cost": m.get("cost_limit_usd")},
                )
            row = await _provider_row(s, provider_id)
            offerings = await _offerings_for(s, provider_id)
    except Exception:
        if secret_ref:
            await secret_store.delete_secret(tenant_id, secret_ref)
        raise
    logger.info("model provider created tenant=%s provider=%s id=%s custom=%s workspace=%s",
                tenant_id, provider, provider_id, is_custom, workspace_id)
    return _provider_dict(row, offerings)
```

Change `list_providers` (replace the existing function):

```python
async def list_providers(
    tenant_id: str, workspace_id: str | None = None, scope: str | None = None,
) -> list[dict]:
    """scope="all" -> every connection (org-wide + every BU's) — the Org Admin's view.
    A bare workspace_id -> org-wide connections + that one BU's own — a BU/Project Admin's
    view. Neither -> org-wide only (legacy default, unchanged for existing callers)."""
    async with get_db_session_for_tenant(tenant_id) as s:
        if scope == "all":
            where = "tenant_id = :t"
            params: dict = {"t": tenant_id}
        elif workspace_id:
            where = "tenant_id = :t AND (workspace_id IS NULL OR workspace_id = :w)"
            params = {"t": tenant_id, "w": workspace_id}
        else:
            where = "tenant_id = :t AND workspace_id IS NULL"
            params = {"t": tenant_id}
        prows = (await s.execute(
            text(f"SELECT id, provider, display_name, secret_ref, status, last_verified_at, created_at, "
                 f"api_base, is_custom FROM model_providers WHERE {where} ORDER BY created_at"),
            params,
        )).fetchall()
        out = []
        for r in prows:
            out.append(_provider_dict(r, await _offerings_for(s, str(r.id))))
    return out
```

Note: `_provider_dict` and `_provider_row` already select `workspace_id`/`api_base`/
`is_custom` in some queries but not all — leave `_provider_dict` as-is for this task (it
doesn't need to emit `workspace_id` in the API response yet; Task 5 adds that to the
router's `ProviderOut` schema directly from the row).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_model_config_api.py -v`
Expected: all tests PASS, including the pre-existing ones (the `if not provider or not
display_name or not api_key` check that used to reject a missing key is gone — confirm no
existing test relied on that exact rejection; if one does, it was testing "api_key is
required," which is no longer true by design, so update that assertion to expect success
with `hasKey=False` instead).

- [ ] **Step 5: Commit**

```bash
git add backend/shared/services/model_config.py backend/tests/test_model_config_api.py
git commit -m "model_config: optional api_key (keyless onboarding) and workspace-scoped listing"
```

---

### Task 4: Resolver gating + project-aware options

**Files:**
- Modify: `backend/shared/services/model_resolver.py`
- Modify: `backend/shared/services/model_config.py` (`get_options`)
- Test: `backend/tests/test_model_resolver.py` (add new tests)

**Interfaces:**
- Consumes: `model_grants.effective_project_offerings(tenant_id, project_id)` from Task 2.
- Produces: `resolve_model_for_run` behavior is unchanged in signature; `get_options` gains
  no new required parameter (it already resolves project scope internally — see below).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_model_resolver.py`:

```python
@pytest.mark.asyncio
async def test_resolver_respects_project_grant_scope():
    from shared.services import model_config as mc
    from shared.services import model_grants as mg
    from shared.services.model_resolver import resolve_model_for_run, ModelNotEnabledError
    from shared.db import get_db_session_for_tenant
    from sqlalchemy import text
    import uuid as _uuid

    tenant = str(_uuid.uuid4())
    ws_id = str(_uuid.uuid4())
    proj_id = str(_uuid.uuid4())
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(
            text("INSERT INTO organizations (id, slug, display_name, created_at, updated_at) "
                 "VALUES (:id, :slug, :dn, now(), now()) ON CONFLICT (id) DO NOTHING"),
            {"id": tenant, "slug": f"org-{tenant[:8]}", "dn": "Org"},
        )
        await s.execute(
            text("INSERT INTO workspaces (id, organization_id, slug, display_name, created_at, updated_at) "
                 "VALUES (:id, :org_id, :slug, :dn, now(), now())"),
            {"id": ws_id, "org_id": tenant, "slug": f"ws-{ws_id[:8]}", "dn": "Unit A"},
        )
        await s.execute(
            text("INSERT INTO projects (id, workspace_id, tenant_id, display_name, created_at, updated_at) "
                 "VALUES (:id, :ws_id, :t, :dn, now(), now())"),
            {"id": proj_id, "ws_id": ws_id, "t": tenant, "dn": "Proj"},
        )

    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Acme", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6", "claude-opus-4-8"], created_by="admin1",
    )
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text("UPDATE model_providers SET status='valid' WHERE id=:i"), {"i": created["id"]})

    # Grant only claude-sonnet-4-6 to this project's BU — claude-opus-4-8 is onboarded
    # but never granted, so it must become unresolvable for this project.
    await mg.set_org_grants(
        tenant,
        [{"provider": "anthropic", "model_id": "claude-sonnet-4-6", "credential_id": created["id"], "visibility": "global", "business_unit_ids": []}],
        created_by="admin1",
    )

    resolved = await resolve_model_for_run(tenant, "claude-sonnet-4-6", project_id=proj_id)
    assert resolved.model == "claude-sonnet-4-6"

    with pytest.raises(ModelNotEnabledError):
        await resolve_model_for_run(tenant, "claude-opus-4-8", project_id=proj_id)


@pytest.mark.asyncio
async def test_resolver_stays_open_with_zero_grants_configured():
    """Backward-compat: a tenant with no org_model_grants rows resolves exactly as today."""
    from shared.services import model_config as mc
    from shared.services.model_resolver import resolve_model_for_run
    from shared.db import get_db_session_for_tenant
    from sqlalchemy import text
    import uuid as _uuid

    tenant = str(_uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Acme", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text("UPDATE model_providers SET status='valid' WHERE id=:i"), {"i": created["id"]})

    # No grants at all, no project_id passed — must resolve exactly as before this feature.
    resolved = await resolve_model_for_run(tenant, "claude-sonnet-4-6")
    assert resolved.model == "claude-sonnet-4-6"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_model_resolver.py -v -k "grant_scope or zero_grants"`
Expected: `test_resolver_respects_project_grant_scope` FAILs (currently resolves
`claude-opus-4-8` successfully — no gating yet); `test_resolver_stays_open_with_zero_grants_configured`
PASSes already (nothing to break yet) — that's fine, it's a regression guard for the next
step.

- [ ] **Step 3: Modify `resolve_model_for_run`**

In `backend/shared/services/model_resolver.py`, add the import and the gating call. Change
the `_load_enabled` call site inside `resolve_model_for_run` (the function body from
`offerings = await _load_enabled(tenant_id)` onward):

```python
    offerings = await _load_enabled(tenant_id)
    if not offerings:
        raise NoModelConfiguredError(
            f"tenant {tenant_id} has no valid, enabled model provider configured")

    from shared.services.model_grants import effective_project_offerings  # noqa: PLC0415

    effective_ids = await effective_project_offerings(
        tenant_id, project_id or _RUN_PROJECT.get()
    )
    if effective_ids is not None:
        offerings = [o for o in offerings if o["offering_id"] in effective_ids]
        if not offerings:
            raise NoModelConfiguredError(
                f"tenant {tenant_id} has grants configured but none apply to this project"
            )
```

Insert this block immediately after the existing `if not offerings: raise
NoModelConfiguredError(...)` check and before the `if offering_id:` branch that follows.
Everything after (the `offering_id`/`requested_model_id`/default selection logic) is
unchanged — it now just operates on the narrowed `offerings` list.

- [ ] **Step 4: Modify `get_options` for project-aware filtering**

In `backend/shared/services/model_config.py`, replace `get_options`:

```python
async def get_options(tenant_id: str, workspace_id: str | None = None, project_id: str | None = None) -> dict:
    """Selectable offerings whose provider is verified `valid` — for the model
    picker. When `project_id` is given and the tenant has grants configured, narrows to
    that project's effective offering set (spec §5) — otherwise (no grants yet, or no
    project context) stays tenant-wide, unchanged from before this feature."""
    from shared.services.model_grants import effective_project_offerings  # noqa: PLC0415

    async with get_db_session_for_tenant(tenant_id) as s:
        rows = (await s.execute(text(
            "SELECT o.id AS offering_id, o.model_id, o.is_default, "
            "o.input_price_per_million, o.output_price_per_million, "
            "p.id AS provider_id, p.provider, p.display_name "
            "FROM model_offerings o JOIN model_providers p ON p.id = o.provider_id "
            "WHERE o.enabled = true AND p.status = 'valid' AND p.tenant_id = :t AND o.tenant_id = :t"
            " ORDER BY p.display_name, p.provider, o.model_id"
        ), {"t": tenant_id})).fetchall()

    effective_ids = await effective_project_offerings(tenant_id, project_id)
    if effective_ids is not None:
        rows = [r for r in rows if str(r.offering_id) in effective_ids]

    options = [{
        "offering_id": str(r.offering_id),
        "provider_id": str(r.provider_id),
        "display_name": r.display_name,
        "provider": r.provider,
        "model_id": r.model_id,
        "is_default": bool(r.is_default),
        "input_price_per_million": _price(r.input_price_per_million),
        "output_price_per_million": _price(r.output_price_per_million),
    } for r in rows]
    default = next((o for o in options if o["is_default"]), None)
    return {
        "options": options,
        "default_offering_id": default["offering_id"] if default else None,
        "default_model_id": default["model_id"] if default else None,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_model_resolver.py tests/test_model_config_api.py -v`
Expected: all PASS, including the two new tests and every pre-existing one (the
backward-compat test is the regression guard — if it fails, the gating logic is wrongly
narrowing when it shouldn't).

- [ ] **Step 6: Commit**

```bash
git add backend/shared/services/model_resolver.py backend/shared/services/model_config.py backend/tests/test_model_resolver.py
git commit -m "Gate model resolution and options by the project's effective grant set"
```

---

### Task 5: Router — new cascade endpoints

**Files:**
- Modify: `backend/shared/routers/model.py`
- Test: `backend/tests/test_model_config_api.py` (HTTP-level tests using the `mint_token`
  fixture — check the top of that file for how it builds a test client if unsure; otherwise
  follow the pattern below, which uses `httpx.AsyncClient` against the app directly)

**Interfaces:**
- Consumes: everything from Tasks 2–4.
- Produces: the 6 new HTTP endpoints from spec §4, plus modified `GET /providers` (`scope`
  query param) and `POST /providers` (`workspace_id`/`visibility`/`business_unit_ids` body
  fields).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_model_config_api.py` (adjust the app/client import at the top
of this block to match however the existing tests in that file build their client — if the
file already imports `from process_api import app` and uses
`httpx.AsyncClient(app=app, base_url="http://test")`, reuse that exact pattern instead of
re-declaring it):

```python
@pytest.mark.asyncio
async def test_org_grants_roundtrip_via_router(mint_token):
    import httpx
    from process_api import app
    from shared.services import model_config as mc
    import uuid

    tenant = str(uuid.uuid4())
    created = await mc.create_provider(
        tenant, provider="anthropic", display_name="Acme", api_key="sk-x",
        enabled_models=["claude-sonnet-4-6"], created_by="admin1",
    )
    token = mint_token(tenant_id=tenant, permissions=["model:manage"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        put_resp = await client.put(
            "/model/allowed/org",
            json={"entries": [{
                "provider": "anthropic", "model_id": "claude-sonnet-4-6",
                "credential_id": created["id"], "visibility": "global", "business_unit_ids": [],
            }]},
            headers=headers,
        )
        assert put_resp.status_code == 200

        get_resp = await client.get("/model/allowed/org", headers=headers)
        assert get_resp.status_code == 200
        assert len(get_resp.json()) == 1


@pytest.mark.asyncio
async def test_allowed_project_requires_run_create_not_model_manage(mint_token):
    """A caller with run:create but WITHOUT model:manage can still read/write their
    project's model selection (spec §4 permission split)."""
    import httpx
    from process_api import app
    import uuid

    tenant = str(uuid.uuid4())
    token = mint_token(tenant_id=tenant, permissions=["run:create"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/model/allowed/project", params={"projectId": str(uuid.uuid4())}, headers=headers)
        # 404/422 (unknown project) is fine — the point is it's NOT 403.
        assert resp.status_code != 403
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_model_config_api.py -v -k "org_grants_roundtrip or requires_run_create"`
Expected: FAIL with 404 (routes don't exist yet).

- [ ] **Step 3: Add the new endpoints**

In `backend/shared/routers/model.py`, add these imports near the top (alongside the
existing `from shared.services.model_catalog import list_providers as catalog_providers`):

```python
from shared.services import model_grants as mg
```

Add these schemas after `SetDefaultIn`:

```python
class GrantEntryIn(BaseModel):
    provider: str
    model_id: str
    credential_id: str | None = None
    visibility: str = "global"
    business_unit_ids: list[str] = Field(default_factory=list)


class AllowEntryIn(BaseModel):
    provider: str
    model_id: str
    credential_id: str | None = None


class SetOrgGrantsIn(BaseModel):
    entries: list[GrantEntryIn] = Field(default_factory=list)


class SetBuGrantsIn(BaseModel):
    entries: list[AllowEntryIn] = Field(default_factory=list)


class SetProjectSelectionIn(BaseModel):
    selected: list[AllowEntryIn] = Field(default_factory=list)
    default_key: str | None = None
```

Add these routes at the end of the file (after `get_options_route`):

```python
@model_router.get("/allowed/org")
async def get_org_grants_route(request: Request) -> list[dict]:
    return await mg.get_org_grants(_tenant_id(request))


@model_router.put("/allowed/org")
async def set_org_grants_route(request: Request, body: SetOrgGrantsIn) -> list[dict]:
    try:
        return await mg.set_org_grants(
            _tenant_id(request), [e.model_dump() for e in body.entries], created_by=_user_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@model_router.get("/allowed/bu")
async def get_bu_allowed_route(request: Request, workspaceId: str) -> list[dict]:
    return await mg.get_bu_allowed(_tenant_id(request), workspaceId)


@model_router.put("/allowed/bu")
async def set_bu_grants_route(request: Request, workspaceId: str, body: SetBuGrantsIn) -> list[dict]:
    return await mg.set_bu_grants(
        _tenant_id(request), workspaceId, [e.model_dump() for e in body.entries],
    )


@model_router.get("/availability")
async def get_availability_route(request: Request, workspaceId: str) -> list[dict]:
    return await mg.get_availability(_tenant_id(request), workspaceId)


@model_router.get("/grant-matrix")
async def get_grant_matrix_route(request: Request) -> dict:
    return await mg.get_grant_matrix(_tenant_id(request))


@model_options_router.get("/allowed/project")
async def get_project_selection_route(request: Request, projectId: str) -> dict:
    try:
        return await mg.get_project_selection(_tenant_id(request), projectId)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@model_options_router.put("/allowed/project")
async def set_project_selection_route(request: Request, projectId: str, body: SetProjectSelectionIn) -> dict:
    try:
        return await mg.set_project_selection(
            _tenant_id(request), projectId,
            [e.model_dump() for e in body.selected], body.default_key,
        )
    except mg.NotAllowedForUnitError as exc:
        raise HTTPException(status_code=400, detail={"code": "not_allowed_for_unit", "message": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

Note the split: the six org/BU-level routes are on `model_router` (already
`model:manage`-gated at the router-prefix level); the two project-selection routes are on
`model_options_router` (already `run:create`-gated) — matching spec §4 exactly.

- [ ] **Step 4: Modify `GET /providers` and `POST /providers` for scope/workspace**

Replace `list_providers_route` and `create_provider_route`:

```python
@model_router.get("/providers", response_model=list[ProviderOut])
async def list_providers_route(request: Request, scope: str | None = None, workspaceId: str | None = None) -> list[ProviderOut]:
    ws = workspaceId or await _active_ws(request)
    return [
        _to_provider_out(d)
        for d in await mc.list_providers(_tenant_id(request), workspace_id=ws, scope=scope)
    ]


@model_router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider_route(request: Request, body: CreateProviderIn) -> ProviderOut:
    models: list[dict] = [m.model_dump() for m in body.models]
    if not models and body.enabled_models:
        models = [{"model_id": m} for m in body.enabled_models]
    try:
        d = await mc.create_provider(
            _tenant_id(request), provider=body.provider, display_name=body.display_name,
            api_key=body.api_key, models=models, api_base=body.api_base,
            created_by=_user_id(request), workspace_id=body.workspace_id,
        )
    except mc.DuplicateProviderNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except mc.InvalidModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if body.workspace_id is None and (body.visibility or body.business_unit_ids):
        # Org-wide onboarding writes the matching grant in the same act (spec §2.3) — a
        # key can't land without anyone being able to use what it unlocks.
        entries = [
            {"provider": body.provider, "model_id": m["model_id"], "credential_id": d["id"],
             "visibility": body.visibility or "global", "business_unit_ids": body.business_unit_ids or []}
            for m in models
        ]
        existing = await mg.get_org_grants(_tenant_id(request))
        await mg.set_org_grants(_tenant_id(request), existing + entries, created_by=_user_id(request))
    return _to_provider_out(d)
```

Add `workspace_id`, `visibility`, `business_unit_ids` to `CreateProviderIn` and make
`api_key` optional (edit the existing class):

```python
class CreateProviderIn(BaseModel):
    provider: str = Field(min_length=1, max_length=64, pattern="^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=255)
    api_key: str | None = Field(default=None, max_length=512)
    api_base: str | None = Field(default=None, max_length=512)
    models: list[ModelIn] = Field(default_factory=list)
    enabled_models: list[str] = Field(default_factory=list)
    workspace_id: str | None = None
    visibility: str | None = None
    business_unit_ids: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_model_config_api.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/model.py backend/tests/test_model_config_api.py
git commit -m "Add grant-cascade endpoints to the model router; scope providers by workspace"
```

---

### Task 6: Frontend — rewire the 6 already-real routes to `bffProxy`

**Files:**
- Modify: `frontend/app/api/model/catalog/route.ts`
- Modify: `frontend/app/api/model/providers/route.ts`
- Modify: `frontend/app/api/model/providers/[id]/route.ts`
- Modify: `frontend/app/api/model/providers/[id]/verify/route.ts`
- Modify: `frontend/app/api/model/default/route.ts`
- Modify: `frontend/app/api/model/options/route.ts`

**Interfaces:**
- Consumes: `bffProxy` from `@/lib/bff/proxy` (unchanged, already exists); backend
  endpoints from Tasks 3–5.
- Produces: no more `DUMMY-DATA SEAM` — these 6 files no longer import
  `@/lib/mock/model-fixtures`.

- [ ] **Step 1: Rewrite `catalog/route.ts`**

```typescript
import { z } from "zod";

import { bffProxy } from "@/lib/bff/proxy";
import { CatalogProvider } from "@/lib/schemas/model";

export function GET() {
  return bffProxy("/model/catalog", { schema: z.array(CatalogProvider) });
}
```

- [ ] **Step 2: Rewrite `providers/route.ts`**

```typescript
import { type NextRequest } from "next/server";
import { z } from "zod";

import { bffProxy } from "@/lib/bff/proxy";
import { ModelProvider } from "@/lib/schemas/model";

export function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/model/providers${qs ? `?${qs}` : ""}`, { schema: z.array(ModelProvider) });
}

export async function POST(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/model/providers", { method: "POST", body, schema: ModelProvider });
}
```

- [ ] **Step 3: Rewrite `providers/[id]/route.ts`**

```typescript
import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ModelProvider } from "@/lib/schemas/model";

export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body: unknown = await req.json();
  return bffProxy(`/model/providers/${encodeURIComponent(id)}`, {
    method: "PATCH", body, schema: ModelProvider,
  });
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/model/providers/${encodeURIComponent(id)}`, { method: "DELETE" });
}
```

- [ ] **Step 4: Rewrite `providers/[id]/verify/route.ts`**

```typescript
import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { VerifyResult } from "@/lib/schemas/model";

export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  return bffProxy(`/model/providers/${encodeURIComponent(id)}/verify`, {
    method: "POST", schema: VerifyResult,
  });
}
```

- [ ] **Step 5: Rewrite `default/route.ts`**

```typescript
import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";

export async function PUT(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/model/default", { method: "PUT", body });
}
```

- [ ] **Step 6: Rewrite `options/route.ts`**

```typescript
import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ModelOptions } from "@/lib/schemas/model";

export function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/model/options${qs ? `?${qs}` : ""}`, { schema: ModelOptions });
}
```

- [ ] **Step 7: Manual verification against the running backend**

Ensure the backend (Task 5) and frontend dev servers are both running, then from a
logged-in browser session (or `curl` with a valid session cookie forwarded through
Next's own auth), hit `GET http://localhost:3000/api/model/catalog` and confirm the
response is the LiteLLM-derived catalog (hundreds of providers/models), not the small
3-provider fixture list — that's the tell that it's now hitting the real backend.

- [ ] **Step 8: Commit**

```bash
git add frontend/app/api/model/catalog/route.ts frontend/app/api/model/providers/route.ts frontend/app/api/model/providers/\[id\]/route.ts "frontend/app/api/model/providers/[id]/verify/route.ts" frontend/app/api/model/default/route.ts frontend/app/api/model/options/route.ts
git commit -m "Wire the 6 core model BFF routes to the real backend instead of fixtures"
```

---

### Task 7: Frontend — build the 5 new cascade routes for real

**Files:**
- Modify: `frontend/app/api/model/allowed/org/route.ts`
- Modify: `frontend/app/api/model/allowed/bu/route.ts`
- Modify: `frontend/app/api/model/allowed/project/route.ts`
- Modify: `frontend/app/api/model/availability/route.ts`
- Modify: `frontend/app/api/model/grant-matrix/route.ts`

**Interfaces:**
- Consumes: `bffProxy`; backend endpoints from Task 5.
- Produces: no more `DUMMY-DATA SEAM`, no more frontend-side
  `effectivePlatformRole`/`resolveSessionScope` gating (the backend now enforces via
  `model:manage`/`run:create` — see spec §4's permission split and the known RBAC gap noted
  there).

- [ ] **Step 1: Rewrite `allowed/org/route.ts`**

```typescript
import { type NextRequest } from "next/server";
import { z } from "zod";

import { bffProxy } from "@/lib/bff/proxy";
import { OrgModelGrant } from "@/lib/schemas/model";

export function GET() {
  return bffProxy("/model/allowed/org", { schema: z.array(OrgModelGrant) });
}

export async function PUT(req: NextRequest) {
  const body: unknown = await req.json();
  return bffProxy("/model/allowed/org", { method: "PUT", body, schema: z.array(OrgModelGrant) });
}
```

- [ ] **Step 2: Rewrite `allowed/bu/route.ts`**

```typescript
import { type NextRequest } from "next/server";
import { z } from "zod";

import { bffProxy } from "@/lib/bff/proxy";
import { ModelAllowEntry } from "@/lib/schemas/model";

export function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/model/allowed/bu?${qs}`, { schema: z.array(ModelAllowEntry) });
}

export async function PUT(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  const body: unknown = await req.json();
  return bffProxy(`/model/allowed/bu?${qs}`, { method: "PUT", body, schema: z.array(ModelAllowEntry) });
}
```

- [ ] **Step 3: Rewrite `allowed/project/route.ts`**

```typescript
import { type NextRequest } from "next/server";

import { bffProxy } from "@/lib/bff/proxy";
import { ProjectModelSelection } from "@/lib/schemas/model";

export function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/model/allowed/project?${qs}`, { schema: ProjectModelSelection });
}

export async function PUT(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  const body: unknown = await req.json();
  return bffProxy(`/model/allowed/project?${qs}`, { method: "PUT", body, schema: ProjectModelSelection });
}
```

- [ ] **Step 4: Rewrite `availability/route.ts`**

```typescript
import { type NextRequest } from "next/server";
import { z } from "zod";

import { bffProxy } from "@/lib/bff/proxy";
import { ModelAvailability } from "@/lib/schemas/model";

export function GET(req: NextRequest) {
  const qs = req.nextUrl.searchParams.toString();
  return bffProxy(`/model/availability?${qs}`, { schema: z.array(ModelAvailability) });
}
```

- [ ] **Step 5: Rewrite `grant-matrix/route.ts`**

```typescript
import { bffProxy } from "@/lib/bff/proxy";
import { ModelGrantMatrix } from "@/lib/schemas/model";

export function GET() {
  return bffProxy("/model/grant-matrix", { schema: ModelGrantMatrix });
}
```

- [ ] **Step 6: Manual verification**

With both servers running and at least one provider onboarded (via the admin Models UI or
a direct `POST /api/model/providers` call), exercise the full cascade by hand: `PUT
/api/model/allowed/org` a grant, `GET /api/model/allowed/bu?workspaceId=<a real workspace
id>` and confirm it reflects the grant, then `PUT /api/model/allowed/project?projectId=<a
real project id in that workspace>` with an out-of-grant entry and confirm a 400 with
`{code: "not_allowed_for_unit"}` comes back.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/api/model/allowed/org/route.ts frontend/app/api/model/allowed/bu/route.ts frontend/app/api/model/allowed/project/route.ts frontend/app/api/model/availability/route.ts frontend/app/api/model/grant-matrix/route.ts
git commit -m "Wire the org/BU/project grant-cascade BFF routes to the real backend"
```

---

### Task 8: Full-stack smoke pass and known-consequence note

**Files:**
- None created/modified — verification only, plus one explicit note below.

**Interfaces:**
- Consumes: everything from Tasks 1–7.

- [ ] **Step 1: Run the full backend test suite for this area**

Run: `uv run pytest tests/test_model_grants.py tests/test_model_config_api.py tests/test_model_resolver.py tests/test_model_resolver_failclosed.py -v`
Expected: all PASS. `test_model_resolver_failclosed.py` in particular is a pre-existing
regression guard for the fail-closed BYOK philosophy — it must still pass unmodified.

- [ ] **Step 2: Restart the backend and re-run migrations from clean**

Run (from `backend/`): `uv run python -m alembic upgrade head` (idempotent — confirms 0035
is the head and applies cleanly against the already-migrated dev DB from earlier in this
session), then restart uvicorn and hit `curl http://localhost:8001/health` — expect
`{"status":"ok",...,"postgres":"ok","redis":"ok",...}`.

- [ ] **Step 3: Manually walk the PRD's cascade end to end**

Using the admin Models UI in the browser (org admin session): onboard a provider org-wide
with a global grant, confirm it shows in the grant matrix; create a second, BU-scoped
provider; confirm a project inside that BU sees the right inherited set on its Settings →
Model tab; try selecting a model that was never granted and confirm the UI surfaces the 400
as an error state (via `ApiErrorState`, per `bffProxy`'s error passthrough).

- [ ] **Step 4: Record the known e2e-test consequence**

`frontend/e2e/model-management.spec.ts` asserts against specific states "seeded in
`lib/mock/model-fixtures.ts`" (its own docstring says so) — e.g. a provider with no key,
one model on two subscriptions. Once the BFF routes hit the real backend, that spec will
fail because the real backend has no such state unless it's actually created through the
now-real API first. **Do not silently patch this in this task.** Instead:

```bash
uv run --directory ../frontend true 2>/dev/null; cd ../frontend && npx playwright test e2e/model-management.spec.ts 2>&1 | tail -40
```

Expected: failures, because the seeded fixture states no longer back these routes. Leave
the failure output as evidence and stop — updating this spec to seed the same states
through the real backend (or moving it under the `e2e:real-api` Playwright project) is
follow-up work explicitly out of this plan's scope (see spec §8, item 3's siblings —
task #20/22/23 are separate; this test fix is a small addendum to #21 that didn't fit the
original task list and should be raised with the user rather than done silently).

- [ ] **Step 5: Final commit**

```bash
cd ../backend
git status --short
git add -A
git commit -m "Model gateway BU/project cascade: final verification pass" --allow-empty
```

(The `--allow-empty` is deliberate — this step exists to capture the verification, not
necessarily new file changes; skip the commit entirely if `git status --short` shows
nothing new since Task 7's commit.)
