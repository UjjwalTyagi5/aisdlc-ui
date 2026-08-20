# Agent-Access Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared foundation every Portfolio-1 agent (Requirements, Design,
Development, Code Review, Security, Testing, Deployment, Documentation) needs before
its owner can safely build against it: a real `projects.track` column, a corrected
role×agent default-access table, a working `require_agent_access` enforcement layer,
and one fully rebuilt reference agent (Security) proving the whole chain end-to-end.

**Architecture:** Backend gains a `TRACK_PORTFOLIOS`/`AGENT_DEFAULT_REACH` config pair
in `agent_registry.py`, a `check_agent_access`/`require_agent_access` module mirroring
the existing `require_permission`/`require_project_access` dependency-factory pattern,
and a `projects.track` column. The already-existing (but currently unread)
`agent_access_overrides` table becomes the live override layer, extended to support
both role-level and person-level grants. The Security Agent — chosen because it scans a
repository directly and needs no fixture upstream artifacts — is rewired through this
layer on both its REST and WebSocket routes, closing a real pre-existing bug along the
way (its REST route currently trusts a client-supplied `user_id` for identity). Frontend
gets the same default-access table corrected to match, plus a real 4-state tile model
(owner / use-only / locked / coming-soon) replacing today's 2-state (locked/unlocked)
one.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + Postgres (backend), Next.js +
Zod (frontend), pytest + `TestClient` (backend tests).

**Spec:** `docs/superpowers/specs/multi-track-agent-access-design.md` — this plan
implements Part 4 (data model, enforcement, frontend mirror) and Part 5 (build pattern),
using Security as the Part-5-checklist's proof case. Read both docs together; this plan
assumes the spec's framing (existing agent/orchestrator code is unverified and must be
properly rebuilt and tested, not assumed working) throughout.

## Global Constraints

- Every new FastAPI dependency that gates a route must be recognized by the D-05
  boot-scan (`assert_all_routes_protected`) — either by setting
  `_dep.__rbac_require_permission__ = True` on the closure (the pattern
  `require_permission`/`require_project_access` already use) or the app fails to boot.
- Role names throughout the backend are lowercase snake_case strings (`security_engineer`,
  `project_admin`, ...) — never PascalCase or spaced.
- `admin:*` / org-wide permissions must **never** bypass an agent-access check.
  Organization Admin and Business Unit Admin hold zero agent access by design (spec
  §1.4) — this is enforced by never consulting `request.state.permissions` inside the
  agent-access resolution path, only the caller's resolved delivery role.
- New migrations chain off the current head, `0023_merge_heads`, with plain (untyped)
  `revision`/`down_revision` string assignments — the repo's existing style, required by
  `tests/test_enterprise_rbac_catalog.py`'s literal-text check on every migration file.
- Enum-like columns in this codebase are `String` + a migration-level
  `CheckConstraint`, never a native Postgres `ENUM` type (no existing column uses one —
  see `Workspace.status`/`data_classification` for the established pattern).
- Backend agent id `code_review` corresponds to frontend phase name `review` — the two
  strings differ; nothing in this plan needs to translate between them (Security's id is
  `security` on both sides, with no mismatch), but note it if a future task touches the
  override-setting UI, which is out of scope here.

---

### Task 1: `projects.track` — column, migration, and wiring through create/read

The frontend already sends `track` on project creation (`ProjectCreateInput.track`,
defaulting to `"greenfield"`) and nothing on the backend stores it — `ProjectCreateIn`
has no `track` field, `Project` has no `track` column, `ProjectOut` never returns one.
Every project created today silently loses its track. This task closes that gap
completely: column, write path, read path.

**Files:**
- Create: `backend/migrations/versions/0024_project_track.py`
- Modify: `backend/shared/models/orm.py` (`Project` class, currently lines 63–87)
- Modify: `backend/shared/routers/projects.py` (`ProjectCreateIn` at line 60, `create_project` at line 218)
- Modify: `backend/shared/routers/_schemas.py` (`ProjectOut` at line 55, `from_orm_project` at line 100)
- Test: `backend/tests/test_project_track.py`

**Interfaces:**
- Produces: `Project.track: str | None` (ORM column), `ProjectCreateIn.track: Literal[...]`
  (request field), `ProjectOut.track: str` (response field) — later tasks (2, 4) read
  `project.track` / a resolved `Project.track` value to select a portfolio.

- [ ] **Step 1: Write the failing migration-shape test**

```python
# backend/tests/test_project_track.py
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import process_api
from config.auth.jwt import create_access_token
from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_and_unit():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Track Test')"
        ), {"i": org, "s": f"track-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    yield {"org": org, "unit": unit}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


def test_creating_a_project_with_a_track_persists_and_returns_it(org_and_unit):
    t = org_and_unit
    user = f"admin-{_uuid.uuid4()}"
    resp = _client().post(
        "/projects",
        json={
            "name": "Warehouse Migration",
            "workspaceId": t["unit"],
            "track": "data_engineering",
        },
        headers=_hdr(user, t["org"], ["project:create", "admin:*"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["track"] == "data_engineering"

    project_id = body["id"]
    reread = _client().get(
        f"/projects/{project_id}", headers=_hdr(user, t["org"], ["admin:*"])
    )
    assert reread.status_code == 200
    assert reread.json()["track"] == "data_engineering"


def test_creating_a_project_without_a_track_defaults_to_greenfield(org_and_unit):
    t = org_and_unit
    user = f"admin-{_uuid.uuid4()}"
    resp = _client().post(
        "/projects",
        json={"name": "No Track Given", "workspaceId": t["unit"]},
        headers=_hdr(user, t["org"], ["project:create", "admin:*"]),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["track"] == "greenfield"


def test_an_invalid_track_is_rejected(org_and_unit):
    t = org_and_unit
    user = f"admin-{_uuid.uuid4()}"
    resp = _client().post(
        "/projects",
        json={"name": "Bad Track", "workspaceId": t["unit"], "track": "not_a_real_track"},
        headers=_hdr(user, t["org"], ["project:create", "admin:*"]),
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && python -m pytest tests/test_project_track.py -v`
Expected: FAIL — `track` is not a recognized field on `ProjectCreateIn`/`ProjectOut`
(Pydantic will silently drop it on create, and the response will have no `track` key,
so the assertions on `body["track"]` raise `KeyError`).

- [ ] **Step 3: Add the migration**

```python
# backend/migrations/versions/0024_project_track.py
"""Add projects.track — the delivery track chosen once at project creation.

Revision ID: 0024_project_track
Revises: 0023_merge_heads
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_project_track"
down_revision = "0023_merge_heads"
branch_labels = None
depends_on = None

_TRACKS = "('greenfield','enhancement','modernization','rpa_infra','data_engineering')"


def upgrade() -> None:
    op.add_column("projects", sa.Column("track", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "ck_project_track", "projects", f"track IN {_TRACKS}"
    )


def downgrade() -> None:
    op.drop_constraint("ck_project_track", "projects", type_="check")
    op.drop_column("projects", "track")
```

Run: `cd backend && python -m alembic upgrade head`
Expected: succeeds, `alembic current` shows `0024_project_track (head)`.

- [ ] **Step 4: Add the ORM column**

In `backend/shared/models/orm.py`, inside `class Project(Base):` (right after
`provider_kind`, matching its `String` + `server_default` style):

```python
    track: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

- [ ] **Step 5: Accept and validate `track` on create**

In `backend/shared/routers/projects.py`, add the import and field:

```python
from typing import Literal, Optional
```

```python
class ProjectCreateIn(BaseModel):
    name: str
    workspaceId: str
    template: str = "blank"
    description: Optional[str] = None
    track: Literal[
        "greenfield", "enhancement", "modernization", "rpa_infra", "data_engineering"
    ] = "greenfield"
    mcp_servers: Optional[dict[str, list[str]]] = None
    connectors: Optional[dict[str, list[str]]] = None
    monthlyBudgetUsd: Optional[float] = None
    ownerId: Optional[str] = None
```

In `create_project`, pass it through to the ORM insert:

```python
    project = Project(
        workspace_id=ws_id,
        tenant_id=tenant_uuid,
        display_name=body.name,
        archived=False,
        track=body.track,
        mcp_servers=body.mcp_servers or None,
        connectors=body.connectors or None,
        monthly_budget_usd=_proj_budget,
    )
```

- [ ] **Step 6: Return `track` on read**

In `backend/shared/routers/_schemas.py`, add the field to `ProjectOut` (after
`template: str`):

```python
    track: str
```

And in `from_orm_project`, pass it through, defaulting legacy (pre-migration) rows to
`"greenfield"` so nothing renders `null`:

```python
            template="blank",
            track=getattr(project, "track", None) or "greenfield",
```

- [ ] **Step 7: Run the tests to confirm they pass**

Run: `cd backend && python -m pytest tests/test_project_track.py -v`
Expected: PASS, all three tests.

- [ ] **Step 8: Run the full backend test suite to check for regressions**

Run: `cd backend && python -m pytest tests/ -x -q`
Expected: no new failures relative to the pre-existing baseline.

- [ ] **Step 9: Commit**

```bash
git add backend/migrations/versions/0024_project_track.py backend/shared/models/orm.py backend/shared/routers/projects.py backend/shared/routers/_schemas.py backend/tests/test_project_track.py
git commit -m "feat: persist a project's delivery track through create and read"
```

---

### Task 2: `TRACK_PORTFOLIOS` and a corrected `AGENT_DEFAULT_REACH`

The design doc's Appendix table (transcribed directly from PRD §14.7) is the
authoritative role×agent chart. This task adds it to the backend as real, importable
data — the single source of truth `require_agent_access` (Task 4) reads from.

**Files:**
- Modify: `backend/config/agent_registry.py`
- Test: `backend/tests/test_agent_registry_portfolios.py`

**Interfaces:**
- Produces: `TRACK_PORTFOLIOS: dict[str, list[str]]`,
  `AGENT_DEFAULT_REACH: dict[str, dict[str, str]]` — Task 4's `check_agent_access`
  imports both by name.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_agent_registry_portfolios.py
from config.agent_registry import AGENT_DEFAULT_REACH, AGENT_REGISTRY, TRACK_PORTFOLIOS

_PORTFOLIO_1 = [
    "requirements", "design", "development", "code_review",
    "security", "testing", "deployment", "documentation",
]


def test_greenfield_and_enhancement_share_the_same_eight_agent_portfolio():
    assert TRACK_PORTFOLIOS["greenfield"] == _PORTFOLIO_1
    assert TRACK_PORTFOLIOS["enhancement"] == _PORTFOLIO_1


def test_portfolios_2_through_4_are_empty_until_their_agents_are_built():
    assert TRACK_PORTFOLIOS["modernization"] == []
    assert TRACK_PORTFOLIOS["rpa_infra"] == []
    assert TRACK_PORTFOLIOS["data_engineering"] == []


def test_every_portfolio_1_agent_has_a_default_reach_row():
    for agent_id in _PORTFOLIO_1:
        assert agent_id in AGENT_DEFAULT_REACH
        assert agent_id in AGENT_REGISTRY


def test_ba_owns_requirements_and_uses_everything_else_in_portfolio_1():
    row = {a: AGENT_DEFAULT_REACH[a]["ba"] for a in _PORTFOLIO_1}
    assert row == {
        "requirements": "owner", "design": "use", "development": "use",
        "code_review": "use", "security": "use", "testing": "use",
        "deployment": "use", "documentation": "use",
    }


def test_security_engineer_owns_only_security_and_reaches_six_others():
    row = {a: AGENT_DEFAULT_REACH[a]["security_engineer"] for a in _PORTFOLIO_1}
    assert row == {
        "requirements": "use", "design": "use", "development": "use",
        "code_review": "use", "security": "owner", "testing": "none",
        "deployment": "use", "documentation": "none",
    }


def test_devops_engineer_has_no_default_reach_to_requirements_design_or_code_review():
    for agent_id in ("requirements", "design", "code_review"):
        assert AGENT_DEFAULT_REACH[agent_id]["devops_engineer"] == "none"


def test_project_admin_owns_every_portfolio_1_agent():
    for agent_id in _PORTFOLIO_1:
        assert AGENT_DEFAULT_REACH[agent_id]["project_admin"] == "owner"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && python -m pytest tests/test_agent_registry_portfolios.py -v`
Expected: FAIL with `ImportError: cannot import name 'TRACK_PORTFOLIOS'`.

- [ ] **Step 3: Add both tables to `agent_registry.py`**

Append at the end of `backend/config/agent_registry.py`, after `AGENT_REGISTRY` and
`get_pipeline_order()`:

```python
# ── Track portfolios (multi-track-agent-access-design.md §1.4) ─────────────────
#
# Each track owns its own agent list. Only Greenfield and Enhancement genuinely
# share one — both point at the same literal list below, not by convention but by
# construction, so they can never silently drift apart. Modernization, RPA/Infra
# Migration, and Data Engineering are independent portfolios; each starts empty
# because none of their agents exist as AGENT_REGISTRY entries yet (spec Part 5 —
# an agent id is added here only once it's actually built and mounted).
_PORTFOLIO_1: list[str] = [
    "requirements", "design", "development", "code_review",
    "security", "testing", "deployment", "documentation",
]

TRACK_PORTFOLIOS: dict[str, list[str]] = {
    "greenfield": _PORTFOLIO_1,
    "enhancement": _PORTFOLIO_1,
    "modernization": [],
    "rpa_infra": [],
    "data_engineering": [],
}

# ── Default role -> agent reach (multi-track-agent-access-design.md Appendix) ──
#
# Transcribed directly from PRD §14.7. "owner" = approves this agent's Consequential
# actions and Sign-offs. "build"/"requests" = does the hands-on work or asks for a
# specific action; a separate owner approves it. "use" = may chat/run Safe
# capabilities only. "none" = no default reach (an override can still grant it,
# per Task 3/4). project_admin is "owner" everywhere by design — the universal
# fallback approver — expressed as real data here, not a code special-case, so
# there is exactly one source of truth for who reaches what.
AGENT_DEFAULT_REACH: dict[str, dict[str, str]] = {
    "requirements": {
        "project_admin": "owner", "ba": "owner", "architect": "use",
        "developer": "use", "qa": "use", "security_engineer": "use",
        "devops_engineer": "none", "data_engineer": "use",
    },
    "design": {
        "project_admin": "owner", "ba": "use", "architect": "owner",
        "developer": "none", "qa": "none", "security_engineer": "use",
        "devops_engineer": "none", "data_engineer": "use",
    },
    "development": {
        "project_admin": "owner", "ba": "use", "architect": "owner",
        "developer": "build", "qa": "use", "security_engineer": "use",
        "devops_engineer": "requests", "data_engineer": "none",
    },
    "code_review": {
        "project_admin": "owner", "ba": "use", "architect": "owner",
        "developer": "requests", "qa": "none", "security_engineer": "use",
        "devops_engineer": "none", "data_engineer": "none",
    },
    "security": {
        "project_admin": "owner", "ba": "use", "architect": "use",
        "developer": "use", "qa": "use", "security_engineer": "owner",
        "devops_engineer": "use", "data_engineer": "use",
    },
    "testing": {
        "project_admin": "owner", "ba": "use", "architect": "use",
        "developer": "use", "qa": "owner", "security_engineer": "none",
        "devops_engineer": "use", "data_engineer": "use",
    },
    "deployment": {
        "project_admin": "owner", "ba": "use", "architect": "use",
        "developer": "none", "qa": "none", "security_engineer": "use",
        "devops_engineer": "owner", "data_engineer": "none",
    },
    "documentation": {
        "project_admin": "owner", "ba": "use", "architect": "use",
        "developer": "use", "qa": "use", "security_engineer": "none",
        "devops_engineer": "use", "data_engineer": "use",
    },
}
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd backend && python -m pytest tests/test_agent_registry_portfolios.py -v`
Expected: PASS, all seven tests.

- [ ] **Step 5: Commit**

```bash
git add backend/config/agent_registry.py backend/tests/test_agent_registry_portfolios.py
git commit -m "feat: add TRACK_PORTFOLIOS and a PRD-accurate AGENT_DEFAULT_REACH table"
```

---

### Task 3: `agent_access_overrides` — dual-grain migration

The table already exists (migration `0016_project_scoped_tables.py`) and is already
read-only-write dead code, keyed `(project_id, role, phase)`. This task adds the
`user_id` grain decided in the design doc §1.6 — a row is either role-scoped
(`role` set, `user_id` null) or person-scoped (`user_id` set, `role` null), never both.

**Files:**
- Create: `backend/migrations/versions/0025_agent_access_override_grain.py`
- Test: `backend/tests/test_agent_access_override_grain.py`

**Interfaces:**
- Produces: `agent_access_overrides.user_id` column, two partial-unique indexes, and a
  check constraint enforcing exactly one of (role, user_id) — Task 4's
  `check_agent_access` queries both grains against this table.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_agent_access_override_grain.py
import uuid as _uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from shared.db import get_db_session_for_tenant, get_db_session_superuser

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_project_user():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    user = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Grain Test')"
        ), {"i": org, "s": f"grain-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
        await s.execute(text(
            "INSERT INTO users (id, tenant_id, email) VALUES (:i, :t, 'grain@example.com')"
        ), {"i": user, "t": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Grain Project')"
        ), {"i": project, "w": unit, "t": org})
    yield {"org": org, "project": project, "user": user}


@pytest.mark.asyncio
async def test_a_role_level_override_row_is_accepted(org_project_user):
    t = org_project_user
    async with get_db_session_for_tenant(t["org"]) as db:
        await db.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, role, phase, involvement) "
            "VALUES (:i, :t, :p, 'developer', 'security', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"]})
        row = (await db.execute(text(
            "SELECT involvement FROM agent_access_overrides "
            "WHERE project_id = :p AND role = 'developer' AND phase = 'security'"
        ), {"p": t["project"]})).first()
        assert row.involvement == "use"


@pytest.mark.asyncio
async def test_a_person_level_override_row_is_accepted(org_project_user):
    t = org_project_user
    async with get_db_session_for_tenant(t["org"]) as db:
        await db.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, user_id, phase, involvement) "
            "VALUES (:i, :t, :p, :u, 'security', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"], "u": t["user"]})
        row = (await db.execute(text(
            "SELECT involvement FROM agent_access_overrides "
            "WHERE project_id = :p AND user_id = :u AND phase = 'security'"
        ), {"p": t["project"], "u": t["user"]})).first()
        assert row.involvement == "use"


@pytest.mark.asyncio
async def test_a_row_with_both_role_and_user_id_is_rejected(org_project_user):
    t = org_project_user
    async with get_db_session_for_tenant(t["org"]) as db:
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "INSERT INTO agent_access_overrides "
                "(id, tenant_id, project_id, role, user_id, phase, involvement) "
                "VALUES (:i, :t, :p, 'developer', :u, 'security', 'use')"
            ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"], "u": t["user"]})


@pytest.mark.asyncio
async def test_a_row_with_neither_role_nor_user_id_is_rejected(org_project_user):
    t = org_project_user
    async with get_db_session_for_tenant(t["org"]) as db:
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "INSERT INTO agent_access_overrides "
                "(id, tenant_id, project_id, phase, involvement) "
                "VALUES (:i, :t, :p, 'security', 'use')"
            ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"]})
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && python -m pytest tests/test_agent_access_override_grain.py -v`
Expected: FAIL on the person-level test — no `user_id` column exists yet.

- [ ] **Step 3: Add the migration**

```python
# backend/migrations/versions/0025_agent_access_override_grain.py
"""agent_access_overrides: add the user_id grain alongside the existing role grain.

A row is either role-scoped (role set, user_id null) or person-scoped (user_id set,
role null), never both, never neither — enforced by ck_agent_access_override_grain.
The old single UNIQUE(project_id, role, phase) is replaced by two partial unique
indexes, one per grain, since a plain UNIQUE constraint can't express "unique only
when user_id IS NULL".

Revision ID: 0025_agent_access_override_grain
Revises: 0024_project_track
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0025_agent_access_override_grain"
down_revision = "0024_project_track"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("agent_access_overrides", "role", nullable=True)
    op.add_column(
        "agent_access_overrides",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_access_override_user", "agent_access_overrides",
        "users", ["user_id"], ["id"], ondelete="CASCADE",
    )
    op.drop_constraint("uq_agent_access_override", "agent_access_overrides", type_="unique")
    op.create_check_constraint(
        "ck_agent_access_override_grain",
        "agent_access_overrides",
        "(role IS NOT NULL AND user_id IS NULL) OR (role IS NULL AND user_id IS NOT NULL)",
    )
    op.create_index(
        "uq_agent_access_override_role", "agent_access_overrides",
        ["project_id", "role", "phase"], unique=True,
        postgresql_where=sa.text("role IS NOT NULL"),
    )
    op.create_index(
        "uq_agent_access_override_user", "agent_access_overrides",
        ["project_id", "user_id", "phase"], unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_agent_access_override_user", table_name="agent_access_overrides")
    op.drop_index("uq_agent_access_override_role", table_name="agent_access_overrides")
    op.drop_constraint("ck_agent_access_override_grain", "agent_access_overrides", type_="check")
    op.create_unique_constraint(
        "uq_agent_access_override", "agent_access_overrides", ["project_id", "role", "phase"]
    )
    op.drop_constraint("fk_agent_access_override_user", "agent_access_overrides", type_="foreignkey")
    op.drop_column("agent_access_overrides", "user_id")
    op.alter_column("agent_access_overrides", "role", nullable=False)
```

Run: `cd backend && python -m alembic upgrade head`
Expected: succeeds, `alembic current` shows `0025_agent_access_override_grain (head)`.

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd backend && python -m pytest tests/test_agent_access_override_grain.py -v`
Expected: PASS, all four tests.

- [ ] **Step 5: Run the full backend test suite to check for regressions**

Run: `cd backend && python -m pytest tests/ -x -q`
Expected: no new failures — `project_scoped.py`'s existing override CRUD only ever
wrote `role`-shaped rows, so relaxing `role` to nullable and replacing the unique
constraint with an equivalent (for existing role-only data) partial index changes no
existing behavior.

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/versions/0025_agent_access_override_grain.py backend/tests/test_agent_access_override_grain.py
git commit -m "feat: let agent_access_overrides grant access by person, not just by role"
```

---

### Task 4: `check_agent_access` / `require_agent_access`

The enforcement layer itself — mirrors `require_permission` (`shared/authz/dependency.py`)
and `require_project_access` (`shared/authz/project_scope.py`) exactly: same
`Depends(get_db_session)` shape, same D-05 sentinel, same "resolve fresh from the
database every request" discipline `effective_role.py` already established.

**Files:**
- Create: `backend/shared/authz/agent_access.py`
- Test: `backend/tests/test_agent_access.py`

**Interfaces:**
- Consumes: `AGENT_DEFAULT_REACH`, `TRACK_PORTFOLIOS` (Task 2, `config.agent_registry`);
  `effective_platform_role(db, request) -> str | None` (`shared.authz.effective_role`,
  already exists); `agent_access_overrides` table (Task 3).
- Produces: `async def check_agent_access(db, *, tenant_id, project_id, role, user_id, agent_id) -> bool`,
  `async def assert_agent_access(db, *, tenant_id, project_id, role, user_id, agent_id) -> None`
  (raises `HTTPException(403)`), `def require_agent_access(agent_id, project_id_param="project_id")`
  (FastAPI dependency factory) — Tasks 5–7 call `assert_agent_access` directly (routes
  with no `{project_id}` path param) or use `require_agent_access(...)` as a
  router-level `Depends` (routes that do have one).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_agent_access.py
import uuid as _uuid

import pytest
from sqlalchemy import text

from shared.authz.agent_access import assert_agent_access, check_agent_access
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from fastapi import HTTPException

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def org_project():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Access Test')"
        ), {"i": org, "s": f"access-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Access Project')"
        ), {"i": project, "w": unit, "t": org})
    yield {"org": org, "project": project}


@pytest.mark.asyncio
async def test_security_engineer_reaches_security_by_default(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="security_engineer", user_id=str(_uuid.uuid4()), agent_id="security",
        )
    assert allowed is True


@pytest.mark.asyncio
async def test_developer_does_not_reach_security_by_default(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=str(_uuid.uuid4()), agent_id="security",
        )
    assert allowed is False


@pytest.mark.asyncio
async def test_project_admin_reaches_every_portfolio_1_agent(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        for agent_id in (
            "requirements", "design", "development", "code_review",
            "security", "testing", "deployment", "documentation",
        ):
            allowed = await check_agent_access(
                db, tenant_id=t["org"], project_id=t["project"],
                role="project_admin", user_id=str(_uuid.uuid4()), agent_id=agent_id,
            )
            assert allowed is True, agent_id


@pytest.mark.asyncio
async def test_org_admin_permissions_do_not_grant_agent_access(org_project):
    """admin:* is never consulted here — org_admin holds zero agent access by design."""
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="org_admin", user_id=str(_uuid.uuid4()), agent_id="security",
        )
    assert allowed is False


@pytest.mark.asyncio
async def test_a_role_level_override_grants_access_the_default_table_denies(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        await db.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, role, phase, involvement) "
            "VALUES (:i, :t, :p, 'developer', 'security', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"]})
        allowed = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=str(_uuid.uuid4()), agent_id="security",
        )
    assert allowed is True


@pytest.mark.asyncio
async def test_a_person_level_override_grants_access_without_touching_the_role(org_project):
    t = org_project
    other_developer = str(_uuid.uuid4())
    named_developer = str(_uuid.uuid4())
    async with get_db_session_for_tenant(t["org"]) as db:
        await db.execute(text(
            "INSERT INTO agent_access_overrides "
            "(id, tenant_id, project_id, user_id, phase, involvement) "
            "VALUES (:i, :t, :p, :u, 'security', 'use')"
        ), {"i": str(_uuid.uuid4()), "t": t["org"], "p": t["project"], "u": named_developer})

        allowed_named = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=named_developer, agent_id="security",
        )
        allowed_other = await check_agent_access(
            db, tenant_id=t["org"], project_id=t["project"],
            role="developer", user_id=other_developer, agent_id="security",
        )
    assert allowed_named is True
    assert allowed_other is False


@pytest.mark.asyncio
async def test_assert_agent_access_raises_403_on_denial(org_project):
    t = org_project
    async with get_db_session_for_tenant(t["org"]) as db:
        with pytest.raises(HTTPException) as exc:
            await assert_agent_access(
                db, tenant_id=t["org"], project_id=t["project"],
                role="developer", user_id=str(_uuid.uuid4()), agent_id="security",
            )
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && python -m pytest tests/test_agent_access.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.authz.agent_access'`.

- [ ] **Step 3: Implement the module**

```python
# backend/shared/authz/agent_access.py
"""Which agents may this caller actually use, on this project.

`agent:invoke` (permissions.py) is a single blanket "may invoke agents at all" flag
every delivery role holds — it was never meant to distinguish Security from
Documentation, and nothing enforces it today (see multi-track-agent-access-design.md
§4.1). This module is that missing distinction: does THIS role reach THIS agent,
by default (AGENT_DEFAULT_REACH) or by an explicit project-scoped override
(agent_access_overrides, checked person-level first, then role-level)?

Deliberately never consults request.state.permissions or admin:* — Organization
Admin and Business Unit Admin hold zero agent access by design (spec §1.4). Resolving
the caller's role is delegated to effective_platform_role, which is itself
deliberately DB-backed (role_bindings), not JWT-trusted, for the same reason
grant_guard.py resolves permissions fresh rather than off the token.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.agent_registry import AGENT_DEFAULT_REACH
from shared.authz.effective_role import effective_platform_role
from shared.db import get_db_session


async def check_agent_access(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    role: str | None,
    user_id: str,
    agent_id: str,
) -> bool:
    """True if `role`/`user_id` may chat with and use `agent_id`'s Safe capabilities
    on `project_id`. Resolution order: person-level override -> role-level override ->
    the built-in default reach table -> deny."""
    if not project_id or not user_id:
        return False

    person_row = (
        await db.execute(
            text(
                "SELECT involvement FROM agent_access_overrides "
                "WHERE tenant_id = CAST(:t AS uuid) AND project_id = CAST(:p AS uuid) "
                "  AND user_id = CAST(:u AS uuid) AND phase = :a"
            ),
            {"t": tenant_id, "p": project_id, "u": user_id, "a": agent_id},
        )
    ).first()
    if person_row is not None:
        return person_row.involvement != "none"

    if role:
        role_row = (
            await db.execute(
                text(
                    "SELECT involvement FROM agent_access_overrides "
                    "WHERE tenant_id = CAST(:t AS uuid) AND project_id = CAST(:p AS uuid) "
                    "  AND role = :r AND phase = :a"
                ),
                {"t": tenant_id, "p": project_id, "r": role, "a": agent_id},
            )
        ).first()
        if role_row is not None:
            return role_row.involvement != "none"

    default = AGENT_DEFAULT_REACH.get(agent_id, {}).get(role or "", "none")
    return default != "none"


async def assert_agent_access(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    role: str | None,
    user_id: str,
    agent_id: str,
) -> None:
    """`check_agent_access`, raising 403 on denial. The direct call site for routes
    (like Security's REST/WS routes — see Tasks 6-7) that have no `{project_id}` path
    parameter for `require_agent_access` to read."""
    allowed = await check_agent_access(
        db, tenant_id=tenant_id, project_id=project_id,
        role=role, user_id=user_id, agent_id=agent_id,
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail=f"You don't have access to the {agent_id} agent on this project.",
        )


def require_agent_access(agent_id: str, project_id_param: str = "project_id"):
    """Router-level dependency enforcing `check_agent_access` on `{project_id_param}`.

    Mirrors `require_project_access`'s exact shape (Request + Depends(get_db_session),
    project id read from the path). A route with no `{project_id_param}` path
    parameter passes through untouched, matching `require_project_access`'s own
    no-project-in-path behavior — there is nothing to scope to.
    """

    async def _dep(
        request: Request, db: AsyncSession = Depends(get_db_session)
    ) -> None:
        project_id = request.path_params.get(project_id_param)
        if not project_id:
            return
        tenant_id = getattr(request.state, "tenant_id", "") or ""
        user_id = getattr(request.state, "user_id", "") or ""
        role = await effective_platform_role(db, request)
        await assert_agent_access(
            db, tenant_id=str(tenant_id), project_id=str(project_id),
            role=role, user_id=str(user_id), agent_id=agent_id,
        )

    _dep.__rbac_require_permission__ = True  # D-05 boot-scan sentinel
    return _dep
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd backend && python -m pytest tests/test_agent_access.py -v`
Expected: PASS, all seven tests.

- [ ] **Step 5: Run the full backend test suite and the D-05 boot scan**

Run: `cd backend && python -m pytest tests/ -x -q`
Expected: no new failures. The app already boots successfully in every prior task's
test run (`TestClient(process_api.app)` triggers the D-05 scan at import time) — this
step exists to catch it explicitly if it hasn't yet, since `require_agent_access` isn't
mounted anywhere until Tasks 5–7.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/authz/agent_access.py backend/tests/test_agent_access.py
git commit -m "feat: add check_agent_access/require_agent_access, the per-agent RBAC gate"
```

---

### Task 5: Wire `security_workspace_router` through `require_agent_access`

`backend/shared/routers/security_workspace.py` already has `{project_id}` in every
route's path and already carries `Depends(require_project_access())` at router level —
the easy case for Task 4's dependency factory: add `require_agent_access("security")`
alongside it.

**Files:**
- Modify: `backend/shared/routers/security_workspace.py` (router declaration, line 33)
- Test: `backend/tests/test_security_workspace_agent_access.py`

**Interfaces:**
- Consumes: `require_agent_access` (Task 4).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_security_workspace_agent_access.py
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def project_with_two_contributors():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    developer = f"dev-{_uuid.uuid4()}"
    security_eng = f"sec-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'SW Test')"
        ), {"i": org, "s": f"sw-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'SW Project')"
        ), {"i": project, "w": unit, "t": org})
    await grant_role(developer, project, "developer", tenant_id=org, scope_kind="project", granted_by="test")
    await grant_role(security_eng, project, "security_engineer", tenant_id=org, scope_kind="project", granted_by="test")
    yield {"org": org, "project": project, "developer": developer, "security_eng": security_eng}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


def test_a_developer_with_no_security_access_gets_403_on_security_scans(project_with_two_contributors):
    t = project_with_two_contributors
    resp = _client().get(
        f"/security/{t['project']}/scans",
        headers=_hdr(t["developer"], t["org"], ["artifact:view"]),
    )
    assert resp.status_code == 403


def test_the_security_engineer_reaches_the_same_route(project_with_two_contributors):
    t = project_with_two_contributors
    resp = _client().get(
        f"/security/{t['project']}/scans",
        headers=_hdr(t["security_eng"], t["org"], ["artifact:view"]),
    )
    assert resp.status_code == 200
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && python -m pytest tests/test_security_workspace_agent_access.py -v`
Expected: FAIL — the developer's request currently returns 200 (or whatever the route
returns today with no per-agent gate), not 403.

- [ ] **Step 3: Add the dependency**

In `backend/shared/routers/security_workspace.py`, near the top where
`security_workspace_router` is declared (line 33):

```python
from shared.authz.agent_access import require_agent_access

security_workspace_router = APIRouter(
    dependencies=[
        Depends(require_project_access()),
        Depends(require_agent_access("security")),
    ]
)
```

(Keep whatever else was already on that line — this only adds the second dependency.)

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd backend && python -m pytest tests/test_security_workspace_agent_access.py -v`
Expected: PASS, both tests.

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && python -m pytest tests/ -x -q`
Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/security_workspace.py backend/tests/test_security_workspace_agent_access.py
git commit -m "feat: gate the security workspace routes on require_agent_access(security)"
```

---

### Task 6: Rebuild Security's REST `/chat/` route — real identity, real access check

`agents_orchestrator/security_agent/security_agent_api.py`'s `/chat/` route currently
trusts a client-supplied `user_id: str = Form(...)` for identity and has no per-agent
(or even per-project) access check at all — only the router-mount-level `artifact:view`
floor. This task fixes both: identity comes from the authenticated
`request.state.user_id`, and every request is checked against `assert_agent_access`
before any scan work happens. `project_id` becomes a real field on the route (it has to
be — there's no `{project_id}` path segment on this router to read it from).

**Files:**
- Modify: `backend/agents_orchestrator/security_agent/security_agent_api.py` (`chat`, line 321)
- Test: `backend/tests/test_security_agent_chat_access.py`

**Interfaces:**
- Consumes: `assert_agent_access` (Task 4), `effective_platform_role` (existing).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_security_agent_chat_access.py
import uuid as _uuid

import pytest
from fastapi.testclient import TestClient

import process_api
from config.auth.jwt import create_access_token
from shared.authz.grant import grant_role
from shared.db import get_db_session_for_tenant, get_db_session_superuser
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("purge_created_orgs")


@pytest.fixture(autouse=True)
async def _dispose_shared_engine():
    yield
    from shared.db import engine
    await engine.dispose()


@pytest.fixture
async def project_with_developer():
    org = str(_uuid.uuid4())
    unit = str(_uuid.uuid4())
    project = str(_uuid.uuid4())
    developer = f"dev-{_uuid.uuid4()}"
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO organizations (id, slug, display_name) VALUES (:i, :s, 'Chat Test')"
        ), {"i": org, "s": f"chat-{org[:8]}"})
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'unit', 'Unit')"
        ), {"i": unit, "o": org})
    async with get_db_session_for_tenant(org) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name) "
            "VALUES (:i, :w, :t, 'Chat Project')"
        ), {"i": project, "w": unit, "t": org})
    await grant_role(developer, project, "developer", tenant_id=org, scope_kind="project", granted_by="test")
    yield {"org": org, "project": project, "developer": developer}


def _client() -> TestClient:
    return TestClient(process_api.app)


def _hdr(user_id: str, org: str, perms: list[str]) -> dict:
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, tenant_id=org, permissions=perms)
    }


def test_a_developer_without_security_access_is_refused_before_any_scan_runs(project_with_developer):
    t = project_with_developer
    resp = _client().post(
        "/sdlc/agent/security/chat/",
        data={
            "session_id": str(_uuid.uuid4()),
            "user_id": t["developer"],
            "text": "scan this",
            "project_id": t["project"],
        },
        headers=_hdr(t["developer"], t["org"], ["artifact:view"]),
    )
    assert resp.status_code == 403


def test_the_form_user_id_can_no_longer_impersonate_someone_else(project_with_developer):
    """The real, authenticated caller is the developer (no security access) even though
    the Form field claims to be someone else entirely — proving identity now comes from
    the verified session, not the request body."""
    t = project_with_developer
    resp = _client().post(
        "/sdlc/agent/security/chat/",
        data={
            "session_id": str(_uuid.uuid4()),
            "user_id": "someone-else-entirely",
            "text": "scan this",
            "project_id": t["project"],
        },
        headers=_hdr(t["developer"], t["org"], ["artifact:view"]),
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && python -m pytest tests/test_security_agent_chat_access.py -v`
Expected: FAIL — today's route has no `project_id` Form field at all (422 on the
current signature) and no access check even once added.

- [ ] **Step 3: Rebuild the `chat` handler**

In `backend/agents_orchestrator/security_agent/security_agent_api.py`, add the import:

```python
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from shared.authz.agent_access import assert_agent_access
from shared.authz.effective_role import effective_platform_role
from shared.db import get_db_session
```

Replace the `chat` handler:

```python
@security_router.post("/chat/")
async def chat(
    request: Request,
    project_id: str = Form(...),
    session_id: str = Form(...),
    user_id: str = Form(...),  # kept for wire compatibility; NOT trusted for identity
    text: str = Form(None),
    pipeline_context: str = Form(None),
    uploaded_files: List[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db_session),
):
    # Identity comes from the verified session, never from the form body — the field
    # above used to be trusted directly, which let any authenticated caller claim to
    # be anyone (see multi-track-agent-access-design.md's "assume broken" framing).
    real_user_id = getattr(request.state, "user_id", "") or ""
    real_tenant_id = getattr(request.state, "tenant_id", "") or ""
    role = await effective_platform_role(db, request)
    await assert_agent_access(
        db, tenant_id=str(real_tenant_id), project_id=project_id,
        role=role, user_id=str(real_user_id), agent_id="security",
    )

    set_websocket_context(manager, session_id)
    set_session_id(session_id)
    set_user_id(real_user_id)
    set_agent_folder("orchestrator")

    s = get_session(session_id)
    s.project_id = s.project_id or project_id
    s.tenant_id = s.tenant_id or real_tenant_id
    first = not s.system_injected
    user_text = text or "Please run the security scan and submit your review."
    if first:
        ctx = _scan_context_block(s)
        content = (ctx + "\n" + user_text) if ctx else user_text
        state = {"messages": [HumanMessage(content=content)]}
        s.system_injected = True
    else:
        state = {"messages": [HumanMessage(content=user_text)]}

    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 140}
    _injected, _skills = await resolve_agent_turn(
        "security", SECURITY_SYSTEM_PROMPT, s.tenant_id, s.project_id
    )
    final = ""
    async with prompt_override_scope("security", _injected), \
            skill_context_scope("security", _skills):
        async for chunk in scan_app.astream(state, stream_mode="messages", config=config):
            msg = chunk[0] if isinstance(chunk, tuple) else chunk
            if isinstance(msg, ToolMessage):
                continue
            if hasattr(msg, "content") and msg.content and not (hasattr(msg, "tool_calls") and msg.tool_calls):
                final += _extract_text(msg.content)
    await _persist_scan_to_run(session_id, s.project_id, s.tenant_id)
    return {"conversation_id": session_id, "responses": final or "No response generated."}
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd backend && python -m pytest tests/test_security_agent_chat_access.py -v`
Expected: PASS, both tests.

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && python -m pytest tests/ -x -q`
Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add backend/agents_orchestrator/security_agent/security_agent_api.py backend/tests/test_security_agent_chat_access.py
git commit -m "fix: stop trusting client-supplied identity on the security chat route; gate it on require_agent_access"
```

---

### Task 7: Rebuild Security's WebSocket `/ws` route — same check, no `Depends`

FastAPI dependency injection doesn't gate WebSocket message handling the way it gates
REST routes here — this router redeems its own ticket and processes messages manually.
`assert_agent_access` still applies; it's just called directly instead of via `Depends`.

**Files:**
- Modify: `backend/agents_orchestrator/security_agent/security_agent_api.py`
  (`_process_ws_message`, line 240)
- Test: manual/integration only (see Task 9) — a WS route is impractical to unit-test
  with `TestClient` in this codebase's existing patterns; correctness here is proven by
  Task 4's `check_agent_access` tests (already covering the exact same function this
  step calls) plus the live end-to-end check in Task 9.

**Interfaces:**
- Consumes: `assert_agent_access` (Task 4), `platform_role_for` (`shared.authz.effective_role`,
  the `Request`-less variant, since a raw WebSocket handler has no `Request` object).

- [ ] **Step 1: Add the check to `_process_ws_message`**

In `backend/agents_orchestrator/security_agent/security_agent_api.py`, add the import:

```python
from shared.authz.agent_access import assert_agent_access
from shared.authz.effective_role import platform_role_for
```

At the top of `_process_ws_message`, right after `project_id` is resolved (after the
existing `project_id = _project_id_from_message(message_data)` line):

```python
async def _process_ws_message(message_data: dict, websocket: WebSocket, user_id, tenant_id: str = ""):
    session_id = message_data.get("session_id", str(uuid4()))
    try:
        project_id = _project_id_from_message(message_data)
        s = get_session(session_id)
        if tenant_id and not s.tenant_id:
            s.tenant_id = tenant_id
        if project_id and not s.project_id:
            s.project_id = project_id

        # Gate every message, not just the first — a session can be reused across
        # projects on the client side, and the ticket only proves who the caller is,
        # not which project they may act on. permissions=[] is deliberate: it forces
        # platform_role_for's DB-backed role_bindings lookup rather than its org-wide
        # permission shortcut, since admin:* must never grant agent access (spec §1.4).
        _effective_project = project_id or s.project_id
        _effective_tenant = tenant_id or s.tenant_id
        async with get_db_session_for_tenant(_effective_tenant) as _access_db:
            _role = await platform_role_for(_access_db, user_id=user_id, permissions=[])
            await assert_agent_access(
                _access_db, tenant_id=_effective_tenant, project_id=_effective_project,
                role=_role, user_id=user_id, agent_id="security",
            )

        if not s.target_bound:
```

(The rest of the function body is unchanged — this inserts the check between the
existing `project_id`/session-binding lines and the existing `if not s.target_bound:`
block. `assert_agent_access` raising `HTTPException` inside this `try` is caught by the
function's existing `except Exception as e:` handler, which already reports the error
back over the socket via `manager.send_agent_response(...)` — no new error-handling
path is needed.)

- [ ] **Step 2: Confirm the app still boots and the REST-side test suite is unaffected**

Run: `cd backend && python -m pytest tests/ -x -q`
Expected: no new failures (this route has no existing automated test coverage to
regress, per the Interfaces note above — Task 9 covers it live).

- [ ] **Step 3: Commit**

```bash
git add backend/agents_orchestrator/security_agent/security_agent_api.py
git commit -m "feat: gate the security agent's websocket route on the same agent-access check as its REST route"
```

---

### Task 8: Frontend — correct `AGENT_OWNERSHIP` for Portfolio 1

`frontend/lib/roles.ts`'s `AGENT_OWNERSHIP` (line 337) currently disagrees with the
PRD's actual §14.7 table on several roles — e.g. today's `ba` entry omits
`development`/`review`/`security`/`testing`/`deployment` entirely (defaulting them to
`"none"`) when the PRD grants BA `"use"` on all of them; today's `security_engineer`
grants `documentation: "use"`, which the PRD does not. This task replaces the 8
Portfolio-1 phase entries with the corrected table from the design doc's Appendix —
the same literal data Task 2 just added to the backend, so both sides agree by
construction.

**Files:**
- Modify: `frontend/lib/roles.ts` (`AGENT_OWNERSHIP`, lines 337–454)
- Test: `frontend/__tests__/lib/agent-ownership.test.ts`

**Interfaces:**
- Produces: corrected `AGENT_OWNERSHIP` entries for `requirements`, `design`,
  `development`, `review`, `security`, `testing`, `deployment`, `documentation` across
  all `PlatformRole` keys — Task 10's tile-state work reads this table.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/__tests__/lib/agent-ownership.test.ts
import { describe, expect, it } from "vitest";
import { AGENT_OWNERSHIP } from "@/lib/roles";

const PORTFOLIO_1 = [
  "requirements", "design", "development", "review",
  "security", "testing", "deployment", "documentation",
] as const;

describe("AGENT_OWNERSHIP matches the PRD's §14.7 table for Portfolio 1", () => {
  it("ba owns requirements and uses every other Portfolio-1 agent", () => {
    const row = Object.fromEntries(PORTFOLIO_1.map((p) => [p, AGENT_OWNERSHIP.ba[p]]));
    expect(row).toEqual({
      requirements: "primary", design: "use", development: "use", review: "use",
      security: "use", testing: "use", deployment: "use", documentation: "use",
    });
  });

  it("architect owns design, development, and review; uses the rest", () => {
    const row = Object.fromEntries(PORTFOLIO_1.map((p) => [p, AGENT_OWNERSHIP.architect[p]]));
    expect(row).toEqual({
      requirements: "use", design: "primary", development: "primary", review: "primary",
      security: "use", testing: "use", deployment: "use", documentation: "use",
    });
  });

  it("developer reaches requirements, security, and testing at use tier", () => {
    expect(AGENT_OWNERSHIP.developer.requirements).toBe("use");
    expect(AGENT_OWNERSHIP.developer.security).toBe("use");
    expect(AGENT_OWNERSHIP.developer.testing).toBe("use");
    expect(AGENT_OWNERSHIP.developer.deployment).toBe("none");
  });

  it("security_engineer no longer has default reach to documentation", () => {
    expect(AGENT_OWNERSHIP.security_engineer.documentation).toBe("none");
  });

  it("security_engineer reaches requirements, design, and deployment at use tier", () => {
    expect(AGENT_OWNERSHIP.security_engineer.requirements).toBe("use");
    expect(AGENT_OWNERSHIP.security_engineer.design).toBe("use");
    expect(AGENT_OWNERSHIP.security_engineer.deployment).toBe("use");
  });

  it("data_engineer no longer has default reach to development", () => {
    expect(AGENT_OWNERSHIP.data_engineer.development).toBe("none");
  });

  it("data_engineer reaches requirements, design, security, and testing at use tier", () => {
    expect(AGENT_OWNERSHIP.data_engineer.requirements).toBe("use");
    expect(AGENT_OWNERSHIP.data_engineer.design).toBe("use");
    expect(AGENT_OWNERSHIP.data_engineer.security).toBe("use");
    expect(AGENT_OWNERSHIP.data_engineer.testing).toBe("use");
  });

  it("devops_engineer has no default reach to requirements, design, or review", () => {
    expect(AGENT_OWNERSHIP.devops_engineer.requirements).toBe("none");
    expect(AGENT_OWNERSHIP.devops_engineer.design).toBe("none");
    expect(AGENT_OWNERSHIP.devops_engineer.review).toBe("none");
  });

  it("devops_engineer reaches security and testing at use tier", () => {
    expect(AGENT_OWNERSHIP.devops_engineer.security).toBe("use");
    expect(AGENT_OWNERSHIP.devops_engineer.testing).toBe("use");
  });

  it("qa reaches requirements and security at use tier", () => {
    expect(AGENT_OWNERSHIP.qa.requirements).toBe("use");
    expect(AGENT_OWNERSHIP.qa.security).toBe("use");
  });

  it("project_admin owns every Portfolio-1 agent", () => {
    for (const phase of PORTFOLIO_1) {
      expect(AGENT_OWNERSHIP.project_admin[phase]).toBe("owner");
    }
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd frontend && npx vitest run __tests__/lib/agent-ownership.test.ts`
Expected: FAIL on most of the reach assertions (`development`/`security`/`testing` on
`ba`, `documentation` on `security_engineer`, `development` on `data_engineer`, etc.).

- [ ] **Step 3: Replace the 8 Portfolio-1 entries**

In `frontend/lib/roles.ts`, replace each role's block with the corrected version below.
Only the 8 Portfolio-1 phase keys (`requirements`/`design`/`development`/`review`/
`security`/`testing`/`deployment`/`documentation`) change; leave every role's
`discovery`/`strategy`/`migration_mapping`/`validation`/`data_engineering` entries
exactly as they are today — those belong to Portfolios 2–4, out of scope here.

```typescript
export const AGENT_OWNERSHIP: Record<PlatformRole, Record<Phase, Involvement>> = {
  org_admin: { ...ALL_NONE },
  bu_admin: { ...ALL_NONE },
  contributor: { ...ALL_NONE },
  project_admin: { ...ALL_OWNER },

  ba: {
    ...ALL_NONE,
    requirements: "primary",
    design: "use",
    development: "use",
    review: "use",
    security: "use",
    testing: "use",
    deployment: "use",
    documentation: "use",
  },
  architect: {
    ...ALL_NONE,
    requirements: "use",
    design: "primary",
    development: "primary",
    review: "primary",
    security: "use",
    testing: "use",
    deployment: "use",
    documentation: "use",
    discovery: "primary",
    strategy: "primary",
    migration_mapping: "primary",
  },
  developer: {
    ...ALL_NONE,
    requirements: "use",
    development: "build",
    review: "requests",
    security: "use",
    testing: "use",
    documentation: "use",
  },
  qa: {
    ...ALL_NONE,
    requirements: "use",
    development: "use",
    security: "use",
    testing: "primary",
    documentation: "use",
    validation: "primary",
  },
  security_engineer: {
    ...ALL_NONE,
    requirements: "use",
    design: "use",
    development: "use",
    review: "use",
    security: "primary",
    deployment: "use",
  },
  devops_engineer: {
    ...ALL_NONE,
    development: "requests",
    security: "use",
    testing: "use",
    deployment: "primary",
    documentation: "use",
  },
  data_engineer: {
    ...ALL_NONE,
    requirements: "use",
    design: "use",
    security: "use",
    testing: "use",
    documentation: "use",
    data_engineering: "primary",
  },
  scrum_master: {
    ...ALL_NONE,
    requirements: "use",
    documentation: "use",
  },
  custom: { ...ALL_NONE },
};
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd frontend && npx vitest run __tests__/lib/agent-ownership.test.ts`
Expected: PASS, all ten assertions.

- [ ] **Step 5: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: check for regressions in any existing test that asserted the *old* (now
corrected) matrix values — if one exists and fails, that test encoded the bug this task
fixes; update its expectation to match the corrected table rather than reverting this
change.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/roles.ts frontend/__tests__/lib/agent-ownership.test.ts
git commit -m "fix: correct AGENT_OWNERSHIP's Portfolio-1 entries to match the PRD's actual role x agent table"
```

---

### Task 9: Frontend — correct the Data Engineering portfolio roster

`frontend/lib/tracks.ts`'s `TRACK_AGENTS.data_engineering` currently lists 9 agents,
including `design`, `development`, and `review` — the design doc's Portfolio 4 (sourced
from PRD §25) is unambiguous that Data Engineering has none of those three, since it's
a pipeline being built, not application code: 6 agents only.

**Files:**
- Modify: `frontend/lib/tracks.ts` (`TRACK_AGENTS`)
- Test: `frontend/__tests__/lib/tracks.test.ts`

**Interfaces:**
- Produces: corrected `TRACK_AGENTS.data_engineering` — Task 10's `roleAgentSplit`
  (already track-aware via `agentsForTrack`) picks this up with no further changes.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/__tests__/lib/tracks.test.ts
import { describe, expect, it } from "vitest";
import { agentsForTrack } from "@/lib/tracks";

describe("data_engineering's portfolio matches the design doc's Portfolio 4", () => {
  it("has exactly 6 agents, in hand-off order", () => {
    expect(agentsForTrack("data_engineering")).toEqual([
      "requirements", "data_engineering", "security", "testing", "deployment", "documentation",
    ]);
  });

  it("has no design, development, or code-review stage", () => {
    const roster = agentsForTrack("data_engineering");
    expect(roster).not.toContain("design");
    expect(roster).not.toContain("development");
    expect(roster).not.toContain("review");
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd frontend && npx vitest run __tests__/lib/tracks.test.ts`
Expected: FAIL — `agentsForTrack("data_engineering")` currently returns 9 entries
including `design`/`development`/`review`.

- [ ] **Step 3: Correct the roster**

In `frontend/lib/tracks.ts`, find the `TRACK_AGENTS` object's `data_engineering` entry
and replace it:

```typescript
  data_engineering: [
    "requirements", "data_engineering", "security", "testing", "deployment", "documentation",
  ],
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd frontend && npx vitest run __tests__/lib/tracks.test.ts`
Expected: PASS, both tests.

- [ ] **Step 5: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: no new failures — check specifically anything asserting
`agentCountForTrack("data_engineering") === 9` or similar, and correct it to `6` if
found, for the same reason as Task 8 Step 5.

- [ ] **Step 6: Commit**

```bash
git add frontend/lib/tracks.ts frontend/__tests__/lib/tracks.test.ts
git commit -m "fix: correct the data_engineering portfolio to 6 agents, matching PRD Track 5"
```

---

### Task 10: Frontend — owner vs. use-only, and a real "coming soon" state

Today's `roleAgentSplit()` collapses every non-`"none"` involvement (`owner`, `primary`,
`build`, `requests`, `use`) into one boolean "reachable" bucket, and `PhasePipeline`
only ever renders 2 states (locked/unlocked). The design doc's tile model (§2.2) needs
4: full access (owner), use-only, locked (request access), and coming soon (agent not
yet properly built — Task 2's `TRACK_PORTFOLIOS` empty lists for Portfolios 2–4 are
exactly this state; Portfolio 1 agents also render this way until their own rebuild is
verified, per the design doc's framing that "built" and "working" are not the same
claim).

**Files:**
- Modify: `frontend/lib/agent-access.ts` (`roleAgentSplit`)
- Modify: `frontend/components/app/phase-pipeline.tsx` (tile rendering, `renderPhaseRow`)
- Test: `frontend/__tests__/lib/agent-access.test.ts`

**Interfaces:**
- Consumes: `AGENT_OWNERSHIP` (Task 8), `agentsForTrack` (Task 9).
- Produces: `TileState = "owner" | "use" | "locked" | "coming_soon"`,
  `tileStateFor(role, phase, track, builtAgents) -> TileState` — replaces
  `roleAgentSplit`'s boolean split as what `PhasePipeline` consumes.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/__tests__/lib/agent-access.test.ts
import { describe, expect, it } from "vitest";
import { tileStateFor } from "@/lib/agent-access";

describe("tileStateFor", () => {
  it("is 'owner' when the role owns the agent and it's built", () => {
    expect(tileStateFor("security_engineer", "security", "greenfield", ["security"])).toBe("owner");
  });

  it("is 'use' when the role reaches but doesn't own the agent, and it's built", () => {
    expect(tileStateFor("developer", "security", "greenfield", ["security"])).toBe("use");
  });

  it("is 'locked' when the role has no reach, and the agent is built", () => {
    expect(tileStateFor("devops_engineer", "requirements", "greenfield", ["requirements"])).toBe("locked");
  });

  it("is 'coming_soon' when the agent isn't in the built list, regardless of role", () => {
    expect(tileStateFor("security_engineer", "security", "greenfield", [])).toBe("coming_soon");
  });

  it("is 'coming_soon' for every agent on a portfolio with nothing built yet", () => {
    expect(tileStateFor("architect", "discovery", "modernization", [])).toBe("coming_soon");
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd frontend && npx vitest run __tests__/lib/agent-access.test.ts`
Expected: FAIL — `tileStateFor` does not exist yet.

- [ ] **Step 3: Add `tileStateFor` alongside the existing `roleAgentSplit`**

In `frontend/lib/agent-access.ts`, add (keep `roleAgentSplit` — other call sites may
still use it; this is additive):

```typescript
export type TileState = "owner" | "use" | "locked" | "coming_soon";

/** Which of the 4 tile states a given (role, phase, track) resolves to.
 *
 * `builtAgents` is the caller-supplied list of phases that have actually been
 * properly built and verified (Task 5-7's Security, once done; nothing else yet) —
 * NOT the same thing as "is in this track's roster". A phase can be in the roster
 * and still render coming_soon, per multi-track-agent-access-design.md §2.2: track
 * membership and "actually works" are two different, both-required conditions.
 */
export function tileStateFor(
  role: PlatformRole,
  phase: Phase,
  track: DeliveryTrack,
  builtAgents: readonly Phase[],
): TileState {
  if (!agentsForTrack(track).includes(phase)) return "coming_soon";
  if (!builtAgents.includes(phase)) return "coming_soon";

  const involvement = involvementFor(role, phase);
  if (involvement === "none") return "locked";
  if (involvement === "owner" || involvement === "primary") return "owner";
  return "use";
}
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `cd frontend && npx vitest run __tests__/lib/agent-access.test.ts`
Expected: PASS, all five tests.

- [ ] **Step 5: Wire `PhasePipeline` to render all 4 states**

In `frontend/components/app/phase-pipeline.tsx`, extend `PhasePipelineProps` with a
`tileState?: (phase: Phase) => TileState` prop (additive, alongside the existing
`lockedPhases`/`renderLockedAction`), and in `renderPhaseRow`, branch on it: `"owner"`
and `"use"` render the existing unlocked row (an "owner" badge can be added to the
approve-affordance area for `"owner"` specifically — the existing per-phase approve UI
already knows how to hide itself for non-owners via the gate-policy/permission checks
elsewhere in this file, so this task only needs to stop suppressing the row); `"locked"`
renders exactly what today's `locked: true` path already renders (lock icon +
`renderLockedAction`); `"coming_soon"` renders a new, non-interactive badge row (no lock
icon, no request-access button — those exist to fix a *permission* problem, not a
*doesn't-exist-yet* one). Since this is a targeted extension of an existing, working
render function rather than a rewrite, read the current `renderPhaseRow` (lines
252–418) before making changes and match its existing JSX/styling conventions for the
new `"coming_soon"` branch rather than introducing a new visual language.

- [ ] **Step 6: Update the call site**

In `frontend/app/(app)/projects/[id]/page.tsx`, replace the `lockedPhases` computation
(lines 98–117) with one that passes `tileState` instead:

```typescript
const tileState = React.useMemo(() => {
  const track = projectQ.data?.track;
  if (!viewerRole || !track) return undefined;
  // TODO(next task): read the project's actually-verified agent list once that
  // signal exists server-side; until then, nothing renders as built.
  const builtAgents: Phase[] = [];
  return (phase: Phase) => tileStateFor(viewerRole, phase, track, builtAgents);
}, [viewerRole, projectQ.data?.track]);
```

Pass `tileState={tileState}` to `<PhasePipeline>` alongside the existing props (leave
`lockedPhases`/`renderLockedAction` in place until `PhasePipeline` itself is confirmed
switched over in Step 5, then remove them in favor of `tileState` doing the same job
plus the two new states).

- [ ] **Step 7: Run the full frontend test suite**

Run: `cd frontend && npx vitest run`
Expected: no new failures.

- [ ] **Step 8: Commit**

```bash
git add frontend/lib/agent-access.ts frontend/components/app/phase-pipeline.tsx frontend/app/\(app\)/projects/\[id\]/page.tsx frontend/__tests__/lib/agent-access.test.ts
git commit -m "feat: add owner/use/locked/coming-soon tile states, replacing the 2-state locked model"
```

---

### Task 11: End-to-end verification

Everything up to here is unit/integration-tested in isolation. This task proves the
whole chain works together, live — the actual bar this plan exists to meet (spec: "must
be properly built and tested," not just built).

**Files:** none (verification only).

- [ ] **Step 1: Confirm the backend boots clean with every new check wired in**

Run: `cd backend && python -m pytest tests/ -q` (full suite) — expect the same pass
count as the pre-existing baseline plus every test added in Tasks 1–7, zero failures.

- [ ] **Step 2: Confirm the frontend type-checks and builds**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: no new type errors, build succeeds.

- [ ] **Step 3: Start both servers**

Run backend: `cd backend && python -m uvicorn process_api:app --host 0.0.0.0 --port 8001 --reload`
Run frontend: `cd frontend && npm run dev`

- [ ] **Step 4: Live-verify the access boundary with two real users**

Using the same mint-token technique established earlier this session (direct
`create_access_token`/`grant_role` calls, never a password field): create a project on
the Greenfield track, bind one user as `developer` and one as `security_engineer`.
Confirm via the browser (or `curl`) that:
- The developer's project page shows the Security tile as locked (no default reach).
- The security engineer's project page shows the Security tile as their own (owner).
- A direct `POST /sdlc/agent/security/chat/` from the developer's session returns 403.
- The same request from the security engineer's session succeeds.
- Granting the developer a role-level override (`agent_access_overrides` row,
  `role='developer', phase='security', involvement='use'`) flips their tile to
  use-only and the 403 to a 200 — proving Task 3/4's override layer, not just the
  default table, actually works end-to-end.

- [ ] **Step 5: Confirm the D-05 boot-scan line in the backend log**

Check the backend startup log for `D-05 route-coverage boot scan: ... no offenders` —
confirms every new dependency (Task 5's router-level `require_agent_access`, Task 4's
sentinel) is recognized, not silently exempted.

- [ ] **Step 6: Report back**

Summarize what was verified and any discrepancy found against this plan's assumptions —
there is no code to commit for this task; it is the plan's acceptance gate.
