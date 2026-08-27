# Agent Studio 3: Skills promotion parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blanket, scope-blind `skill:edit`/`workspace:manage` gates on
every org/workspace/project write (both Behavior and Skills) with real tier-ownership
+ "propose exactly one tier up, properly scoped to real membership" enforcement, and
give Skills a `propose()` mirroring Behavior's — so the RBAC mechanism the whole
Agent Studio effort is building actually functions for every role, not just
`developer`/`org_admin`.

**Architecture:** A new async, self-contained (opens its own tenant session, like
`resolve_platform_role_for_user`) helper, `resolve_actor_tier_access`, answers
`(owns, may_propose)` for one specific `(scope, scope_id)` via real `role_bindings`
raw-SQL lookups — never the existing "highest standing wins" global role resolver,
which is provably wrong for this purpose (a `bu_admin` on Workspace X must not pass
an ownership check for Workspace Y). `assert_can_write_agent_scope` becomes `async`
and calls it for the shared-tier branch; its 9 existing call sites gain `await` +
`tenant_id`. Behavior's `propose()` gains the same check in place of its old blanket
gate. Skills gains a mirrored `propose()` route, a new `activate: bool` parameter on
its store's create/update functions (default `True`, zero behavior change for
owners — only a newly-reachable non-owner's write goes in inactive), and a fallback
branch in the existing governance effect handler (`_apply_agent_default`) that also
knows how to flip an `AgentSkill` row, reusing the exact same pure `apply_publish_flip`
function Behavior's own publish already uses. No new governance request type.

**Tech Stack:** FastAPI + SQLAlchemy (async) backend, Next.js + React Query + Zod
frontend, pytest (backend, incl. live-DB tests), vitest + React Testing Library
(frontend).

**Spec:** `docs/superpowers/specs/2026-08-26-agent-studio-3-skills-promotion-parity-design.md`

## Global Constraints

- No new tables, no migration, no new governance request type — Skills proposals
  reuse `agent_default_org`/`_workspace`/`_project` exactly as Behavior's do.
- `resolve_actor_tier_access` must NEVER use `effective_platform_role`/
  `resolve_platform_role_for_user`'s output as an ownership signal for the
  org/workspace/project branch — that resolver is global "highest standing wins,"
  not scoped to the resource being acted on (see spec's "Existing state"). Every
  `owns`/`may_propose` determination for workspace/project scope must be a direct,
  scope_id-filtered `role_bindings` query.
- An OWNER's Skills create/update must activate immediately, with ZERO behavior
  change from today — the new `activate: bool = True` default exists specifically to
  guarantee this. Only a non-owner's write (unreachable before this plan) inserts
  inactive.
- Every route keeps a `require_permission`-sentinel dependency (the router-level
  `artifact:view` floor, already present on both routers) so the D-05 boot scan
  stays green — this plan removes no route-level dependency that isn't already gone
  (sub-project 2 already removed the per-route `skill:edit`/`workspace:manage`
  dependencies from every route this plan touches except `propose()`, which still
  has one to remove here).
- Self-approval blocking, approver routing, and audit-trail emission for the reused
  `agent_default_*` request types are ALREADY generic (`governance_requests.py`'s
  `decide()`) — nothing here should duplicate or special-case that logic for Skills.

---

### Task 1: Backend — `resolve_actor_tier_access`

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py` (add near `assert_can_write_agent_scope`)
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py` (or a new file if that one is getting large — check its current line count first; if over ~600 lines, create `backend/tests/agent_profiles/test_tier_access.py` instead, matching this repo's precedent of splitting when a file grows unwieldy)

**Interfaces:**
- Produces: `async def resolve_actor_tier_access(tenant_id: str, actor_user_id: str, perms: list[str], scope: str, scope_id: str | None) -> tuple[bool, bool]` — `(owns, may_propose)`. Used by Task 2 (this file) and Task 6 (`agent_skills.py`'s new `propose()` route, imported).

- [ ] **Step 1: Write the failing tests**

Add to the chosen test file:

```python
import uuid

from shared.db import get_db_session_for_tenant
from sqlalchemy import text


async def _bind(tenant_id, user_id, role, scope_kind, scope_id):
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', CAST(:t AS uuid), true) ON CONFLICT (id) DO NOTHING"
        ), {"i": user_id, "e": f"{user_id}@example.com", "t": tenant_id})
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, tenant_id) "
            "VALUES (gen_random_uuid(), :u, :sk, CAST(:si AS uuid), :r, CAST(:t AS uuid))"
        ), {"u": user_id, "sk": scope_kind, "si": scope_id, "r": role, "t": tenant_id})


async def _make_project(tenant_id, workspace_id):
    project_id = str(uuid.uuid4())
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(text(
            "INSERT INTO projects (id, tenant_id, workspace_id, display_name, status, created_by) "
            "VALUES (CAST(:p AS uuid), CAST(:t AS uuid), CAST(:w AS uuid), 'Test Project', 'active', 'tester')"
        ), {"p": project_id, "t": tenant_id, "w": workspace_id})
    return project_id


@pytest.mark.asyncio
async def test_org_owns_via_admin_wildcard_only():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    owns, may_propose = await ap.resolve_actor_tier_access(tenant, user_id, ["admin:*"], "org", None)
    assert owns is True
    owns, _ = await ap.resolve_actor_tier_access(tenant, user_id, [], "org", None)
    assert owns is False


@pytest.mark.asyncio
async def test_org_may_propose_for_bu_admin_anywhere():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    await _bind(tenant, user_id, "bu_admin", "business_unit", str(uuid.uuid4()))
    owns, may_propose = await ap.resolve_actor_tier_access(tenant, user_id, [], "org", None)
    assert owns is False
    assert may_propose is True


@pytest.mark.asyncio
async def test_workspace_owns_requires_binding_on_this_exact_workspace():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    other_ws_id = str(uuid.uuid4())
    await _bind(tenant, user_id, "bu_admin", "business_unit", ws_id)

    owns, _ = await ap.resolve_actor_tier_access(tenant, user_id, [], "workspace", ws_id)
    assert owns is True
    owns, _ = await ap.resolve_actor_tier_access(tenant, user_id, [], "workspace", other_ws_id)
    assert owns is False


@pytest.mark.asyncio
async def test_workspace_may_propose_for_project_admin_on_a_project_in_this_ws():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    other_ws_id = str(uuid.uuid4())
    project_id = await _make_project(tenant, ws_id)
    await _bind(tenant, user_id, "project_admin", "project", project_id)

    _, may_propose = await ap.resolve_actor_tier_access(tenant, user_id, [], "workspace", ws_id)
    assert may_propose is True
    _, may_propose = await ap.resolve_actor_tier_access(tenant, user_id, [], "workspace", other_ws_id)
    assert may_propose is False


@pytest.mark.asyncio
async def test_project_owns_requires_binding_on_this_exact_project():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    project_id = await _make_project(tenant, ws_id)
    other_project_id = await _make_project(tenant, ws_id)
    await _bind(tenant, user_id, "project_admin", "project", project_id)

    owns, _ = await ap.resolve_actor_tier_access(tenant, user_id, [], "project", project_id)
    assert owns is True
    owns, _ = await ap.resolve_actor_tier_access(tenant, user_id, [], "project", other_project_id)
    assert owns is False


@pytest.mark.asyncio
async def test_project_may_propose_for_any_member_except_contributor():
    tenant = str(uuid.uuid4())
    dev_id = str(uuid.uuid4())
    contributor_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    project_id = await _make_project(tenant, ws_id)
    await _bind(tenant, dev_id, "developer", "project", project_id)
    await _bind(tenant, contributor_id, "contributor", "project", project_id)

    _, may_propose = await ap.resolve_actor_tier_access(tenant, dev_id, [], "project", project_id)
    assert may_propose is True
    _, may_propose = await ap.resolve_actor_tier_access(tenant, contributor_id, [], "project", project_id)
    assert may_propose is False


@pytest.mark.asyncio
async def test_project_may_propose_false_for_unrelated_project():
    tenant = str(uuid.uuid4())
    dev_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    project_a = await _make_project(tenant, ws_id)
    project_b = await _make_project(tenant, ws_id)
    await _bind(tenant, dev_id, "developer", "project", project_a)

    _, may_propose = await ap.resolve_actor_tier_access(tenant, dev_id, [], "project", project_b)
    assert may_propose is False


@pytest.mark.asyncio
async def test_owns_implies_no_need_for_may_propose_but_both_are_reported_independently():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    project_id = await _make_project(tenant, ws_id)
    await _bind(tenant, user_id, "project_admin", "project", project_id)

    owns, may_propose = await ap.resolve_actor_tier_access(tenant, user_id, [], "project", project_id)
    assert owns is True
    # project_admin's own project binding also matches the "any member" propose
    # query — both booleans are independently correct, callers decide precedence.
    assert may_propose is True
```

(If the test file doesn't already import `pytest`/`uuid`/`text`/
`get_db_session_for_tenant`/an `ap` alias for `shared.routers.agent_profiles`, add
them matching this repo's established convention in the sibling
`test_personal_tier_persistence.py` files.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/ -k tier_access -v`
Expected: FAIL with `AttributeError: module 'shared.routers.agent_profiles' has no attribute 'resolve_actor_tier_access'`.

- [ ] **Step 3: Implement**

In `backend/shared/routers/agent_profiles.py`, add the import (alongside the existing
`from shared.authz.permissions import has_permission`):

```python
from shared.authz.read_scope import live_binding
```

Add the function right after `assert_own_user_scope` (currently ends around line 407):

```python
async def resolve_actor_tier_access(
    tenant_id: str, actor_user_id: str, perms: list[str], scope: str, scope_id: str | None,
) -> tuple[bool, bool]:
    """(owns, may_propose) for `actor_user_id` on this EXACT (scope, scope_id) — a
    real per-resource lookup, never the global "highest standing" role
    (`effective_platform_role`/`resolve_platform_role_for_user` are scope-blind and
    must not be reused here — a bu_admin on Workspace X must not pass an ownership
    check for Workspace Y just because they're "a bu_admin" tenant-wide).

    owns: may publish/unpublish/activate this tier directly.
    may_propose: may draft-and-file-for-approval at this tier. Irrelevant once
    `owns` is True, but reported independently — callers decide precedence.

    org: owns via the admin:* wildcard alone (org_admin always carries it; no
    role_bindings lookup needed for a role that IS the wildcard). may_propose via
    a live bu_admin binding ANYWHERE in the tenant — org is the tenant's one
    instance, so "one tier up from workspace" needs no specific workspace id.

    workspace: owns via a live bu_admin binding scoped to this exact workspace.
    may_propose via a live project_admin binding on ANY project whose
    workspace_id is this workspace (one tier up from "some project in this BU").

    project: owns via a live project_admin binding scoped to this exact project.
    may_propose via ANY live role_binding scoped to this exact project, excluding
    role_name='contributor' — contributor is documented elsewhere as "not enough
    to open an agent"; membership alone earns propose access for every other role.
    """
    from shared.authz.permissions import has_permission as _has_perm  # noqa: PLC0415 - already imported at module scope, kept local for symmetry with other lazy imports here
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    if scope == "org":
        owns = _has_perm(perms, "admin:*")
        async with get_db_session_for_tenant(tenant_id) as session:
            hit = (await session.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    f"AND rb.scope_kind = 'business_unit' AND rb.role_name = 'bu_admin' LIMIT 1"
                ),
                {"u": actor_user_id, "now": datetime.now(tz=timezone.utc)},
            )).first()
        return owns, hit is not None

    if scope == "workspace":
        async with get_db_session_for_tenant(tenant_id) as session:
            owns_hit = (await session.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    f"AND rb.scope_kind = 'business_unit' AND rb.scope_id = :w "
                    f"AND rb.role_name = 'bu_admin' LIMIT 1"
                ),
                {"u": actor_user_id, "w": scope_id, "now": datetime.now(tz=timezone.utc)},
            )).first()
            propose_hit = (await session.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    f"AND rb.scope_kind = 'project' AND rb.role_name = 'project_admin' "
                    f"AND rb.scope_id IN (SELECT id FROM projects WHERE workspace_id = CAST(:w AS uuid)) "
                    f"LIMIT 1"
                ),
                {"u": actor_user_id, "w": scope_id, "now": datetime.now(tz=timezone.utc)},
            )).first()
        return owns_hit is not None, propose_hit is not None

    if scope == "project":
        async with get_db_session_for_tenant(tenant_id) as session:
            owns_hit = (await session.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    f"AND rb.scope_kind = 'project' AND rb.scope_id = :p "
                    f"AND rb.role_name = 'project_admin' LIMIT 1"
                ),
                {"u": actor_user_id, "p": scope_id, "now": datetime.now(tz=timezone.utc)},
            )).first()
            propose_hit = (await session.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    f"AND rb.scope_kind = 'project' AND rb.scope_id = :p "
                    f"AND rb.role_name != 'contributor' LIMIT 1"
                ),
                {"u": actor_user_id, "p": scope_id, "now": datetime.now(tz=timezone.utc)},
            )).first()
        return owns_hit is not None, propose_hit is not None

    return False, False
```

Add the needed imports at the top of the file if not already present: `from datetime
import datetime, timezone` and `from sqlalchemy import text` (check first — `select`
is already imported from `sqlalchemy`; `text` may not be).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/tests/agent_profiles/
git commit -m "feat: resolve_actor_tier_access — real per-resource tier ownership/propose lookup"
```

---

### Task 2: Backend — `assert_can_write_agent_scope` becomes tier-ownership-aware; wire into `agent_profiles.py`'s 4 call sites

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py` (`assert_can_write_agent_scope`, `create_draft`, `preview`, `publish`, `unpublish`)
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py` (or wherever Task 1's tests landed)

**Interfaces:**
- Consumes: `resolve_actor_tier_access` (Task 1).
- Produces: `assert_can_write_agent_scope(tenant_id: str, perms: list[str], role: str | None, scope: str, scope_id: str | None, actor_user_id: str, *, action: Literal["draft", "publish"]) -> None` — now `async`, gains `tenant_id` as its new first parameter. Every existing caller must be updated in this same task (a partially-updated call graph would break the running app).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_profiles/test_agent_profiles_router.py`:

```python
@pytest.mark.asyncio
async def test_write_check_project_admin_owns_own_project():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    # Table-driven test targets the pure-ish async function directly, bypassing
    # HTTP — role_bindings/projects setup mirrors Task 1's helpers.
    await _bind(tenant, user_id, "project_admin", "project", project_id)
    await ap.assert_can_write_agent_scope(
        tenant, [], "project_admin", "project", project_id, user_id, action="draft",
    )
    await ap.assert_can_write_agent_scope(
        tenant, [], "project_admin", "project", project_id, user_id, action="publish",
    )  # no raise


@pytest.mark.asyncio
async def test_write_check_project_admin_denied_on_unrelated_project():
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    other_project_id = str(uuid.uuid4())
    await _bind(tenant, user_id, "project_admin", "project", project_id)
    with pytest.raises(HTTPException) as exc:
        await ap.assert_can_write_agent_scope(
            tenant, [], "project_admin", "project", other_project_id, user_id, action="draft",
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_write_check_user_scope_unaffected_by_tier_ownership_change():
    # Regression guard: the personal-tier branch (sub-project 2) must be completely
    # untouched by this task's changes to the shared-tier branch.
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    await ap.assert_can_write_agent_scope(
        tenant, [], "developer", "user", user_id, user_id, action="draft",
    )  # no raise
    with pytest.raises(HTTPException) as exc:
        await ap.assert_can_write_agent_scope(
            tenant, [], "org_admin", "user", user_id, user_id, action="draft",
        )
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/ -k "write_check_project_admin or write_check_user_scope_unaffected" -v`
Expected: `test_write_check_project_admin_owns_own_project` FAILS (the OLD function still
checks `has_permission(perms, "skill:edit")`, which `project_admin` never held); the
`unaffected` test passes already since it exercises only the `scope=="user"` branch
which this task will leave semantically identical (still fails/passes today only
because the function signature itself hasn't changed yet — this confirms the
personal-tier baseline before editing).

- [ ] **Step 3: Implement — `assert_can_write_agent_scope`**

Replace the function (currently lines 347-389) with:

```python
async def assert_can_write_agent_scope(
    tenant_id: str,
    perms: list[str],
    role: str | None,
    scope: str,
    scope_id: str | None,
    actor_user_id: str,
    *,
    action: Literal["draft", "publish"],
) -> None:
    """Scope-aware authorization for an Agent Studio write (Behavior draft/publish/
    propose; Skills create/update/delete/toggle/activate/propose). Raises
    HTTPException(403) on denial.

    user: self-service, unchanged from sub-project 2 — allowed only when `role` is
    neither "org_admin" nor "bu_admin" AND `scope_id` equals the caller's own user id.

    org/workspace/project: real tier ownership + "propose one tier up," via
    `resolve_actor_tier_access` — NOT the old blanket permission-string check
    (sub-project 3 replaces it deliberately; see the sub-project 3 spec's
    "Existing state" section for why the old check was a real bug, not just
    incomplete). "publish" requires ownership. "draft" requires ownership OR
    propose-eligibility — a non-owner may still draft, to have something to
    propose.
    """
    if scope == "user":
        if role is None or role in ("org_admin", "bu_admin"):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not _same_actor(scope_id, actor_user_id):
            raise HTTPException(status_code=403, detail="Forbidden")
        return
    owns, may_propose = await resolve_actor_tier_access(
        tenant_id, actor_user_id, perms, scope, scope_id,
    )
    if action == "publish":
        if not owns:
            raise HTTPException(status_code=403, detail="Forbidden")
        return
    if not (owns or may_propose):
        raise HTTPException(status_code=403, detail="Forbidden")
```

- [ ] **Step 4: Update `create_draft`, `preview`, `publish`, `unpublish`**

In `backend/shared/routers/agent_profiles.py`, each of these four functions' existing
`assert_can_write_agent_scope(...)` call (not `await`ed today) becomes:

`create_draft` (currently lines 511-514):
```python
    await assert_can_write_agent_scope(
        tenant_id, getattr(request.state, "permissions", []) or [], role,
        body.scope, body.scope_id, _user_id(request), action="draft",
    )
```

`publish` (currently lines 557-561):
```python
    await assert_can_write_agent_scope(
        tenant_id, getattr(request.state, "permissions", []) or [], role,
        target.scope, str(target.scope_id) if target.scope_id else None,
        _user_id(request), action="publish",
    )
```

`unpublish`: identical shape to `publish` (same call, `action="publish"`) — find its
matching block (same pattern, a few lines after `publish`'s) and apply the same edit.

`preview`: same shape as `create_draft` (`action="draft"`) — find its matching block
and apply the same edit.

In every case, the ONLY change is: add `await`, and insert `tenant_id` as the new
first positional argument (already a local variable at that point in every one of
these four functions — confirm before editing, don't add a new `_tenant_id(request)`
call if one already ran earlier in the function).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/ -v`
Expected: all PASS. Note: this WILL break some of sub-project 2's own "unchanged"
regression tests that asserted the OLD permission-string behavior for org/workspace/
project (e.g. a test proving `contributor` was denied at project scope because they
lacked `skill:edit` — under the NEW rule, `contributor` is STILL denied, but for a
different reason: they hold no non-contributor role_binding on that project, or none
at all). Read any failure carefully: if it's a test that hard-codes the OLD
permission-list-based setup (mints a token with `skill:edit`/`workspace:manage`
instead of a real `role_bindings` row), it needs its setup updated to the new
`role_bindings`-based convention (Task 1's `_bind` helper), not deleted — the
BEHAVIOR it was checking (e.g. "an unrelated role is denied") is still a real
requirement, just enforced a different way now.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/tests/agent_profiles/
git commit -m "feat: assert_can_write_agent_scope enforces real tier ownership for org/workspace/project"
```

---

### Task 3: Backend — wire the updated check into `agent_skills.py`'s 5 call sites

**Files:**
- Modify: `backend/shared/routers/agent_skills.py` (`create_skill`, `update_skill`, `delete_skill`, `toggle_skill`, `activate_version`)
- Test: `backend/tests/agent_skills/test_agent_skills_router.py`

**Interfaces:**
- Consumes: the now-`async` `assert_can_write_agent_scope` (Task 2, imported from `agent_profiles`, already imported by name — only the call sites change, not the import statement).

This task is mechanical: at each of the 5 existing call sites, add `await` and insert
`tenant_id` as the new first positional argument. `tenant_id` is already a local
variable at every one of these call sites (confirmed: `tenant_id = _tenant_id(request)`
runs as literally the first line of every one of these 5 functions).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_skills/test_agent_skills_router.py` (reusing whatever
`_bind_role`/`_bind`-shaped helper this file already has from sub-project 2's work —
check first, don't duplicate if one already exists under a slightly different name):

```python
@pytest.mark.asyncio
async def test_create_skill_project_admin_owns_own_project(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "project_admin", "project", project_id)
    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "project", "scope_id": project_id,
                "skill_key": "own-skill", "display_name": "Own Skill", "body": "do it",
            },
            headers=headers,
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_skill_project_admin_denied_on_unrelated_project(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    other_project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "project_admin", "project", project_id)
    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "project", "scope_id": other_project_id,
                "skill_key": "not-yours", "display_name": "Not Yours", "body": "do it",
            },
            headers=headers,
        )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/ -k project_admin_owns -v`
Expected: FAIL — `TypeError: object bool can't be used in 'await' expression` or
similar, since `assert_can_write_agent_scope` is now a coroutine and the call sites
don't `await` it yet.

- [ ] **Step 3: Implement**

In `backend/shared/routers/agent_skills.py`, at each of the 5 call sites (`create_skill`
line ~286, `toggle_skill` line ~333, `activate_version` line ~402, `update_skill` line
~425, and `delete_skill`'s — find it, same shape), change:

```python
    assert_can_write_agent_scope(perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
```
to:
```python
    await assert_can_write_agent_scope(tenant_id, perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
```

(and the analogous `action="publish"` one for `activate_version`; `delete_skill`/
`activate_version` use bare `scope`/`scope_id` params, not `body.scope`/
`body.scope_id` — keep each call site's existing argument source, only add `await`
and `tenant_id`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/ -v`
Expected: all PASS (same caveat as Task 2 Step 5 — some sub-project 2 "unchanged"
tests may need their setup updated from token-permissions to real `role_bindings`
rows; update setup, don't delete the behavioral assertion).

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_skills.py backend/tests/agent_skills/
git commit -m "feat: Skills write routes use tier-ownership-aware authorization"
```

---

### Task 4: Backend — Behavior's `propose()` gains a real ownership check

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py` (`propose`)
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py`

**Interfaces:**
- Consumes: `resolve_actor_tier_access` (Task 1).
- Produces: `POST /agent-profiles/{profile_id}/propose` no longer carries a route-level `Depends(require_permission("skill:edit"))` — replaced by the router's `artifact:view` floor plus an in-body ownership/eligibility check.

**Verified fact, load-bearing for this task:** `propose()` (current code, lines
635-639) takes **no request body at all** — signature is
`propose(profile_id: str, request: Request, db: AsyncSession = Depends(get_db_session)) -> dict`.
It derives everything (`agent_id`, `scope`, `scope_id`, `version`) from the `target`
row it loads via `profile_id` — never from client input. This is deliberate (see
the function's own docstring: a client-suppliable target would let a proposal be
pointed at any row in the tenant). The tests below POST with no JSON body, matching
this exactly — do not add one.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_profiles/test_agent_profiles_router.py`:

```python
@pytest.mark.asyncio
async def test_propose_allowed_for_project_member_with_no_permission_string(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "qa", "project", project_id)  # QA held no permission before this plan
    draft_id = await _create_draft_row(tenant, "project", project_id)  # reuse sub-project 2's helper

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/agent-profiles/{draft_id}/propose", headers=headers)
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_propose_denied_for_non_member(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    draft_id = await _create_draft_row(tenant, "project", project_id)
    # user_id has NO binding anywhere in this tenant.
    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/agent-profiles/{draft_id}/propose", headers=headers)
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/ -k "propose_allowed_for_project_member or propose_denied_for_non_member" -v`
Expected: `test_propose_allowed_for_project_member_with_no_permission_string` FAILS
(403 from the OLD blanket `skill:edit` gate, which `qa` never held).

- [ ] **Step 3: Implement**

In `backend/shared/routers/agent_profiles.py`, find `propose`'s decorator (currently
around line 630, carrying `dependencies=[Depends(require_permission("skill:edit"))]`)
and remove that `dependencies=[...]` entirely (router-level `artifact:view` floor
remains, satisfying D-05). In the function body, after the existing
`target = await _load_or_404(db, profile_id)` and the existing `scope == "user"`
`NOT_A_SHARED_TIER` guard (both stay exactly as-is, unchanged), insert:

```python
    tenant_id = _tenant_id(request)
    perms = getattr(request.state, "permissions", []) or []
    owns, may_propose = await resolve_actor_tier_access(
        tenant_id, _user_id(request), perms, target.scope,
        str(target.scope_id) if target.scope_id else None,
    )
    if not (owns or may_propose):
        raise HTTPException(status_code=403, detail="Forbidden")
```

(Check whether `tenant_id`/`perms` are already computed earlier in this function
before adding — `propose()` may already have a `tenant_id = _tenant_id(request)`
line near its top; reuse it rather than declaring it twice.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/tests/agent_profiles/
git commit -m "feat: propose() uses real tier-ownership/membership check instead of skill:edit"
```

---

### Task 5: Backend — `skill_store.py` gains conditional activation

**Files:**
- Modify: `backend/shared/services/skill_store.py` (`create_custom_skill`, `update_custom_skill`)
- Test: `backend/tests/agent_skills/test_skill_store_inheritance.py` (or a new file — check size first)

**Interfaces:**
- Produces: `create_custom_skill(..., created_by, activate: bool = True) -> dict` and `update_custom_skill(..., created_by, activate: bool = True) -> Optional[dict]` — new trailing optional param, defaults preserve every existing caller's exact behavior.

- [ ] **Step 1: Write the failing tests**

Add to the chosen test file:

```python
@pytest.mark.asyncio
async def test_create_custom_skill_activate_false_inserts_inactive():
    tenant = str(uuid.uuid4())
    detail = await store.create_custom_skill(
        tenant, "requirements", "project", str(uuid.uuid4()), "draft-skill",
        "Draft Skill", "d", "w", "body", "tester", activate=False,
    )
    assert detail["enabled"] is False or detail.get("active_version") is None
    # Confirm via a live-DB read that the row itself is inactive, not just the
    # merged-list "enabled" flag (which reflects toggles, not is_active).
    row = await store.get_skill_detail(tenant, "requirements", "project", detail["scope_id"] if "scope_id" in detail else None, "custom", "draft-skill")


@pytest.mark.asyncio
async def test_update_custom_skill_activate_false_leaves_prior_version_active():
    tenant = str(uuid.uuid4())
    scope_id = str(uuid.uuid4())
    await store.create_custom_skill(
        tenant, "requirements", "project", scope_id, "k", "V1", "d", "w", "body v1", "tester",
    )  # default activate=True, matches every existing caller
    updated = await store.update_custom_skill(
        tenant, "requirements", "project", scope_id, "k", "V2 (proposed)", "d", "w", "body v2",
        "tester", activate=False,
    )
    assert updated is not None
    # The ACTIVE version the runtime/list would surface is still v1's content —
    # v2 exists as an inactive row, not yet live.
    active = await store.get_skill_detail(tenant, "requirements", "project", scope_id, "custom", "k")
    assert active["display_name"] == "V1"
```

Adjust the exact assertions once you've read `get_skill_detail`'s actual return
shape (`display_name` field access above assumes it returns the ACTIVE version's
content when asked for a skill_key without a version — verify this against the
function's real implementation before finalizing).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/ -k "activate_false" -v`
Expected: FAIL — `TypeError: create_custom_skill() got an unexpected keyword argument 'activate'`.

- [ ] **Step 3: Implement**

In `backend/shared/services/skill_store.py`, replace `create_custom_skill`'s
signature and the `is_active=True` line (currently lines 465-490):

```python
async def create_custom_skill(
    tenant_id, agent_id, scope, scope_id, skill_key, display_name,
    description, when_to_use, body, created_by, activate: bool = True,
) -> dict:
    """Insert a v1 custom skill. Active immediately unless `activate=False` (a
    non-owner's proposed draft, per sub-project 3 — stays inactive until a
    governance approval flips it). Raises ValueError if one already exists."""
    sid = _as_uuid(scope_id) if scope != "org" else None
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        existing = (await session.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == str(agent_id),
                AgentSkill.scope == scope,
                AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                AgentSkill.skill_key == skill_key,
                AgentSkill.deleted_at.is_(None),
            )
        )).scalars().first()
        if existing is not None:
            raise ValueError(f"skill '{skill_key}' already exists at {scope} scope")
        row = AgentSkill(
            tenant_id=_as_uuid(tenant_id),
            agent_id=str(agent_id),
            scope=scope,
            scope_id=sid,
            skill_key=skill_key,
            version=1,
            is_active=activate,
            display_name=display_name or skill_key,
            description=description,
            when_to_use=when_to_use,
            body=body,
            runtime="llm",
            origin="custom",
            created_by=created_by or "system",
        )
        session.add(row)
        await session.flush()
        version = row.version
    detail = await get_skill_detail(tenant_id, agent_id, scope, scope_id, "custom", skill_key)
    return detail or {"skill_key": skill_key, "version": version, "origin": "custom"}
```

Replace `update_custom_skill` (currently lines 506-546):

```python
async def update_custom_skill(
    tenant_id, agent_id, scope, scope_id, skill_key, display_name,
    description, when_to_use, body, created_by, activate: bool = True,
) -> Optional[dict]:
    """Insert v(n+1). Activates it (and deactivates prior versions) immediately
    unless `activate=False`, in which case the new row is inserted inactive and
    every existing version — including the currently active one — is left
    untouched (a non-owner's proposed draft; publish/governance-approval flips
    it later). None when no existing skill."""
    sid = _as_uuid(scope_id) if scope != "org" else None
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        rows = list((await session.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == str(agent_id),
                AgentSkill.scope == scope,
                AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                AgentSkill.skill_key == skill_key,
                AgentSkill.deleted_at.is_(None),
            ).order_by(AgentSkill.version.desc())
        )).scalars().all())
        if not rows:
            return None
        next_version = rows[0].version + 1
        if activate:
            for r in rows:
                if r.is_active:
                    r.is_active = False
        new_row = AgentSkill(
            tenant_id=_as_uuid(tenant_id),
            agent_id=str(agent_id),
            scope=scope,
            scope_id=sid,
            skill_key=skill_key,
            version=next_version,
            is_active=activate,
            display_name=display_name or skill_key,
            description=description,
            when_to_use=when_to_use,
            body=body,
            runtime="llm",
            origin="custom",
            created_by=created_by or "system",
        )
        session.add(new_row)
        await session.flush()
    return await get_skill_detail(tenant_id, agent_id, scope, scope_id, "custom", skill_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/ -v`
Expected: all PASS — including every PRE-EXISTING test of `create_custom_skill`/
`update_custom_skill`, which never pass `activate` and must therefore see zero
behavior change (default `True`).

- [ ] **Step 5: Commit**

```bash
git add backend/shared/services/skill_store.py backend/tests/agent_skills/
git commit -m "feat: skill_store create/update gain an activate flag for proposed (non-owner) drafts"
```

---

### Task 6: Backend — `create_skill`/`update_skill` pass `activate=owns`; new Skills `propose()` route

**Files:**
- Modify: `backend/shared/routers/agent_skills.py` (`create_skill`, `update_skill`, new `propose_skill`)
- Modify: `backend/shared/services/skill_store.py` (new `get_latest_draft_version`)
- Test: `backend/tests/agent_skills/test_agent_skills_router.py`

**Interfaces:**
- Consumes: `resolve_actor_tier_access` (Task 1, imported), `activate` param (Task 5).
- Produces: `POST /agent-skills/{skill_key}/propose`, new `ProposeSkillIn` Pydantic model, new `skill_store.get_latest_draft_version(tenant_id, agent_id, scope, scope_id, skill_key) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_skills/test_agent_skills_router.py`:

```python
@pytest.mark.asyncio
async def test_create_skill_by_non_owner_inserts_inactive(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", project_id)  # member, not owner
    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "project", "scope_id": project_id,
                "skill_key": "proposed-skill", "display_name": "Proposed Skill", "body": "x",
            },
            headers=headers,
        )
        assert created.status_code == 200
        detail = created.json()

        listed = await client.get(
            "/agent-skills",
            params={"agent_id": "requirements", "scope": "project", "scope_id": project_id},
            headers=headers,
        )
        # An inactive skill has no active version to surface in the merged list —
        # confirms the write went in inactive, not immediately live.
        assert not any(s["skill_key"] == "proposed-skill" for s in listed.json()["skills"])


@pytest.mark.asyncio
async def test_propose_skill_then_approve_activates_it(mint_token):
    tenant = str(uuid.uuid4())
    dev_id = str(uuid.uuid4())
    pa_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, dev_id, "developer", "project", project_id)
    await _bind_role(tenant, pa_id, "project_admin", "project", project_id)
    dev_token = mint_token(user_id=dev_id, tenant_id=tenant, permissions=["artifact:view"])
    pa_token = mint_token(user_id=pa_id, tenant_id=tenant, permissions=["artifact:view", "governance:decide"])

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "project", "scope_id": project_id,
                "skill_key": "team-checklist", "display_name": "Team Checklist", "body": "check it",
            },
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        assert created.status_code == 200
        version = created.json()["version"]

        proposed = await client.post(
            "/agent-skills/team-checklist/propose",
            json={"agent_id": "requirements", "scope": "project", "scope_id": project_id},
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        assert proposed.status_code == 201
        request_id = proposed.json()["id"]

        decided = await client.post(
            f"/governance-approvals/{request_id}/decide",
            json={"decision": "approved"},
            headers={"Authorization": f"Bearer {pa_token}"},
        )
        assert decided.status_code == 200

        listed = await client.get(
            "/agent-skills",
            params={"agent_id": "requirements", "scope": "project", "scope_id": project_id},
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        hit = next(s for s in listed.json()["skills"] if s["skill_key"] == "team-checklist")
        assert hit["enabled"] is True
```

**Verified fact, load-bearing for this task:** `propose_skill` (below) does NOT
accept a client-supplied `target_ref` or `version` — mirroring Behavior's
`propose()` exactly (verified in Task 4: it takes no body at all, deriving
everything from a server-loaded row, specifically so a client can never point a
proposal at an arbitrary row). Skills has no single-row-UUID path param anywhere
today, so `propose_skill` instead resolves its own target server-side via a new
store function, `get_latest_draft_version` (implemented in Step 4 below) — "the
newest INACTIVE version of this skill_key at this scope," which is exactly the row
a preceding non-owner `create`/`update` call just inserted. The test above reflects
this: no `target_ref`/`version` in the `/propose` request body.

Before finalizing, verify the actual governance decide endpoint's path/body shape
(`/governance-approvals/{id}/decide` and `{"decision": "approved"}` are assumptions
in the test above — check `shared/routers/approvals.py` or wherever governance
decisions are actually exposed over HTTP, and correct the test to match).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/ -k "non_owner_inserts_inactive or propose_skill_then_approve" -v`
Expected: FAIL — `test_create_skill_by_non_owner_inserts_inactive` fails because
`create_skill` doesn't yet compute/pass `activate`; `test_propose_skill_then_approve_activates_it`
fails with 404 (no `/propose` route exists yet).

- [ ] **Step 3: Implement — `create_skill`/`update_skill`**

In `backend/shared/routers/agent_skills.py`, `create_skill` (currently lines 280-286):

```python
@agent_skills_router.post("")
async def create_skill(body: CreateSkillIn, request: Request):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    await assert_can_write_agent_scope(tenant_id, perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
    owns, _ = await resolve_actor_tier_access(tenant_id, _user_id(request), perms, body.scope, body.scope_id)
```

(Everything after this in the existing function body is unchanged EXCEPT the final
`store.create_custom_skill(...)` call, which gains `activate=owns` as its final
keyword argument.)

`update_skill` (currently lines 419-425): identical shape of edit — add the
`owns, _ = await resolve_actor_tier_access(...)` line after the existing
`assert_can_write_agent_scope` call, and add `activate=owns` to the
`store.update_custom_skill(...)` call.

- [ ] **Step 4: Implement — `get_latest_draft_version`, `ProposeSkillIn`, `propose_skill`**

In `backend/shared/services/skill_store.py`, add a new function near
`activate_custom_version` (same query shape, filtered the other direction):

```python
async def get_latest_draft_version(
    tenant_id, agent_id, scope, scope_id, skill_key,
) -> Optional[dict]:
    """The newest INACTIVE version of this skill_key at this scope, if any — the
    row a non-owner's create/update (activate=False) just inserted. Used by
    propose_skill to resolve its target server-side, never from client input
    (mirrors AgentProfile's propose(), which resolves target_ref the same way)."""
    sid = _as_uuid(scope_id) if scope != "org" else None
    async with get_db_session_for_tenant(str(tenant_id)) as session:
        rows = list((await session.execute(
            select(AgentSkill).where(
                AgentSkill.agent_id == str(agent_id),
                AgentSkill.scope == scope,
                AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                AgentSkill.skill_key == skill_key,
                AgentSkill.deleted_at.is_(None),
                AgentSkill.is_active.is_(False),
            ).order_by(AgentSkill.version.desc())
        )).scalars().all())
        if not rows:
            return None
        return {"id": str(rows[0].id), "version": rows[0].version}
```

In `backend/shared/routers/agent_skills.py`, add near the other request-body
models (alongside `ToggleIn`):

```python
class ProposeSkillIn(BaseModel):
    agent_id: str
    scope: str
    scope_id: Optional[str] = None
```

Add the route (placed among the other literal-suffix routes, e.g. right after
`toggle_skill`, before the two-segment detail route per this file's existing
ROUTE ORDER convention documented in its module docstring):

```python
@agent_skills_router.post("/{skill_key}/propose", status_code=201)
async def propose_skill(skill_key: str, body: ProposeSkillIn, request: Request):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    if body.scope == "user":
        raise HTTPException(status_code=422, detail={
            "code": "NOT_A_SHARED_TIER",
            "message": "A personal default is yours alone; there is nobody to propose it to.",
        })
    perms = getattr(request.state, "permissions", []) or []
    owns, may_propose = await resolve_actor_tier_access(
        tenant_id, _user_id(request), perms, body.scope, body.scope_id,
    )
    if not (owns or may_propose):
        raise HTTPException(status_code=403, detail="Forbidden")

    draft = await _store().get_latest_draft_version(
        tenant_id, body.agent_id, body.scope, body.scope_id, skill_key,
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="Nothing to propose")

    from shared.authz.effective_role import effective_platform_role, actor_display_name  # noqa: PLC0415
    from shared.authz.workspace import active_workspace_for_request  # noqa: PLC0415
    from shared.services import governance_requests as governance_service  # noqa: PLC0415
    from shared.services.governance_requests import GovernanceError  # noqa: PLC0415
    from shared.db import get_db_session_for_tenant  # noqa: PLC0415

    scope_label = {"org": "organization", "workspace": "business unit", "project": "project"}[body.scope]
    request_type = f"agent_default_{body.scope}"
    async with get_db_session_for_tenant(tenant_id) as db:
        role = await effective_platform_role(db, request)
        name = await actor_display_name(db, request)
        workspace_id = body.scope_id if body.scope_id else await active_workspace_for_request(db, request)
        if not workspace_id:
            raise HTTPException(status_code=422, detail={
                "code": "NO_WORKSPACE",
                "message": "Choose a business unit before proposing an organization default.",
            })
        try:
            return await governance_service.create_request(
                db, tenant_id=tenant_id, initiator_id=_user_id(request), initiator_name=name,
                initiator_role=role, request_type=request_type,
                title=f"{body.agent_id} skill '{skill_key}' change ({scope_label})",
                description=f"{name} proposed a change to the '{skill_key}' skill for the {body.agent_id} agent ({scope_label} default), version {draft['version']}.",
                workspace_id=workspace_id, project_id=body.scope_id if body.scope == "project" else None,
                target_ref=draft["id"], payload={
                    "agentId": body.agent_id, "skillKey": skill_key, "scope": body.scope,
                    "version": draft["version"],
                },
                system_raised=True,
            )
        except GovernanceError as exc:
            raise HTTPException(status_code=exc.http_status, detail={"code": exc.code, "message": str(exc)})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/ -v`
Expected: `test_create_skill_by_non_owner_inserts_inactive` PASSES.
`test_propose_skill_then_approve_activates_it` still FAILS at this point — its final
assertion depends on Task 7's governance-effect change, which doesn't exist yet. This
is expected; do not treat it as a regression at this step. Confirm the failure is
specifically at the LAST assertion (the skill still being enabled/false after
"approval"), not an earlier step — if it fails earlier (e.g. the `/propose` call
itself 4xxs), that IS a bug in this task and must be fixed before committing.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/agent_skills.py backend/shared/services/skill_store.py backend/tests/agent_skills/
git commit -m "feat: Skills gains propose(); create/update activate conditionally on tier ownership"
```

---

### Task 7: Backend — `_apply_agent_default` gains an `AgentSkill` fallback

**Files:**
- Modify: `backend/shared/governance/effects.py` (`_apply_agent_default`)
- Test: `backend/tests/agent_skills/test_agent_skills_router.py` (completes Task 6's `test_propose_skill_then_approve_activates_it`) plus a focused unit test here

**Interfaces:**
- Consumes: `apply_publish_flip` (existing, `agent_profiles.py`, already imported by this file for the `AgentProfile` path).
- Produces: no signature change to `_apply_agent_default` — same `(db, request) -> str`. Internal behavior gains a second lookup path.

- [ ] **Step 1: Run Task 6's pending test to confirm today's exact failure point**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/test_agent_skills_router.py::test_propose_skill_then_approve_activates_it -v`
Expected: FAILS at the final assertion (`hit["enabled"] is True`) — the governance
decision itself succeeds (200), but the skill never actually activates, because
`_apply_agent_default` only knows how to look up `AgentProfile` rows and silently
finds nothing for an `AgentSkill`'s `target_ref` (or raises `EffectNotAvailable`,
surfacing as a 4xx on the decide call instead — check which, and note it precisely
in this task's report either way, since it changes what "the test now fails at the
approve step" vs. "fails at the final list check" means for verifying Step 3 fixed it).

- [ ] **Step 2: Implement**

In `backend/shared/governance/effects.py`, inside `_apply_agent_default` (currently
lines 383-441), replace the block that raises `EffectNotAvailable` when the
`AgentProfile` row is missing:

```python
    row = (
        await db.execute(select(AgentProfile).where(AgentProfile.id == target_uuid))
    ).scalar_one_or_none()
    if row is None:
        return await _apply_agent_default_skill(db, request, target_uuid)
```

(Replaces the existing `if row is None: raise EffectNotAvailable(...)` line — the
rest of the `AgentProfile` path, from `siblings = list(...)` onward, is unchanged.)

Add a new helper function right after `_apply_agent_default`:

```python
async def _apply_agent_default_skill(db: AsyncSession, request: dict[str, Any], target_uuid) -> str:
    """AgentSkill counterpart to the AgentProfile path above — same target_ref
    convention, same apply_publish_flip reuse, different ORM model. A proposal's
    target_ref may name either kind of row; this is the fallback once the
    AgentProfile lookup comes up empty."""
    from shared.models.orm import AgentSkill  # noqa: PLC0415

    row = (
        await db.execute(select(AgentSkill).where(AgentSkill.id == target_uuid))
    ).scalar_one_or_none()
    if row is None:
        raise EffectNotAvailable(request["type"], "That draft version no longer exists.")

    siblings = list(
        (
            await db.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == row.agent_id,
                    AgentSkill.scope == row.scope,
                    AgentSkill.scope_id == row.scope_id,
                    AgentSkill.skill_key == row.skill_key,
                )
            )
        )
        .scalars()
        .all()
    )
    apply_publish_flip(siblings, row.id)
    await db.flush()

    try:
        from shared.services.skill_runtime import invalidate_skills_cache  # noqa: PLC0415

        invalidate_skills_cache(str(request["tenantId"]), row.agent_id)
    except Exception:  # pragma: no cover - cache is best-effort, the write is not
        logger.warning("governance: skill cache invalidation failed for %s", row.agent_id)

    logger.info(
        "governance: skill published request=%s skill=%s agent=%s key=%s v%s",
        request["id"], row.id, row.agent_id, row.skill_key, row.version,
    )
    return f"Published skill '{row.skill_key}' v{row.version} at {row.scope} scope."
```

(`apply_publish_flip` is already imported at the top of this file for the
`AgentProfile` path — reused verbatim, unchanged, no new import needed for it. Check
`EffectNotAvailable`/`logger`/`select`/`AsyncSession`/`Any` are already imported at
module scope — they should be, since `_apply_agent_default` already uses them all.)

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/ tests/agent_profiles/ -v`
Expected: all PASS, including `test_propose_skill_then_approve_activates_it` now
fully green end-to-end. Also confirm no regression in ANY existing Behavior
governance test (`test_governance_requests.py` and similar) — the `AgentProfile`
path's behavior must be byte-identical to before this task; only a NEW fallback
branch was added.

- [ ] **Step 4: Run the governance test suite specifically**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -v`
Expected: all PASS — this file was untouched by this task but exercises the exact
`decide()`/`apply_on_approve` path this task modified; a regression here would mean
the `AgentProfile` branch's early-return / control flow was accidentally altered.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/governance/effects.py backend/tests/agent_skills/
git commit -m "feat: governance approval effect flips an AgentSkill row when target_ref names one"
```

---

### Task 8: Backend — update module docstrings

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py` (module docstring)
- Modify: `backend/shared/routers/agent_skills.py` (module docstring)
- Modify: `backend/shared/governance/effects.py` (`_apply_agent_default`'s docstring, or a module-level note)

**Interfaces:** No new production interfaces — documentation only, describing the
RBAC scheme this plan just replaced.

- [ ] **Step 1: Update `agent_profiles.py`'s RBAC docstring paragraph**

Replace the paragraph describing draft/preview/publish/unpublish's authorization
(the one sub-project 2 wrote, now stale — it still says "for org/workspace/project
scope it requires skill:edit... or workspace:manage... exactly as before") with an
accurate description of the tier-ownership + propose-one-tier-up scheme this plan
implements — reference `resolve_actor_tier_access` by name and summarize its three
scope branches at a high level (don't repeat the full docstring already on that
function — point to it).

- [ ] **Step 2: Update `agent_skills.py`'s RBAC docstring paragraph**

Same update, plus a note that `create`/`update` now activate conditionally
(`activate=owns`) and that `propose_skill` exists, reusing the `agent_default_*`
governance types Behavior's `propose()` already used — no new type family.

- [ ] **Step 3: Update `effects.py`'s `_apply_agent_default` docstring**

Note that `target_ref` may now name either an `AgentProfile` or an `AgentSkill` row,
and that the dispatch is a plain "try one, then the other" fallback, not a payload
discriminator — mention why (considered and rejected a `skill_default_*` type family;
see the sub-project 3 spec for the reasoning) so a future reader doesn't wonder why
Skills proposals aren't a distinct type.

- [ ] **Step 4: Run the full backend suite for the touched packages once more**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles tests/agent_skills tests/test_governance_requests.py -q`
Expected: all PASS (docstring-only change, but confirms nothing else drifted since
Task 7).

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/shared/routers/agent_skills.py backend/shared/governance/effects.py
git commit -m "docs: describe tier-ownership RBAC scheme and Skills promotion parity"
```

---

### Task 9: Frontend — Skills "Propose" action

**Files:**
- Modify: `frontend/lib/api/agent-skills.ts` (new `proposeAgentSkill`)
- Modify: `frontend/lib/schemas/agent-skills.ts` (new `SkillProposeInput`)
- Modify: `frontend/components/agent-studio/skills-tab.tsx` (new "Propose" action)
- Test: `frontend/components/agent-studio/__tests__/skills-tab.test.tsx`

**Interfaces:**
- Produces: `proposeAgentSkill(skillKey: string, input: SkillProposeInput) -> Promise<GovernanceApproval>`.

- [ ] **Step 1: Read the current non-owner UX first**

Before writing any code, read `skills-tab.tsx`'s current rendering for a non-owner,
non-inherited custom skill (the `!canManage` branch — currently likely just a "View"
action, no propose path at all, since Skills never had one). Also read
`behavior-tab.tsx`'s existing "Propose" button (the one Behavior already has,
sub-project-1-and-earlier code, untouched by this plan) to match its visual/
interaction pattern exactly — same button placement logic, same toast-on-success
copy style, same error handling via `getLintViolations`-adjacent patterns if
applicable.

- [ ] **Step 2: Write the failing test**

Add to `frontend/components/agent-studio/__tests__/skills-tab.test.tsx`, following
this file's existing `vi.mock` convention:

```tsx
it("shows a Propose action for a non-owner viewing their own (non-inherited) custom skill, and calls the API on click", async () => {
  mockedListAgentSkills.mockResolvedValue({
    skills: [{
      origin: "custom", skill_key: "team-skill", agent_id: "requirements",
      display_name: "Team Skill", description: null, when_to_use: null,
      runtime: "llm", enabled: true, editable: false, deletable: false,
      version: 1, active_version: null, origin_scope: "project",
    }],
  } satisfies SkillList);
  const mockedPropose = vi.mocked(proposeAgentSkill);
  mockedPropose.mockResolvedValue({ id: "req-1" } as any);

  const user = userEvent.setup();
  renderSkillsTab(projectScopeContext(false));  // add this helper if it doesn't exist, mirroring workspaceScopeContext(isOwner)

  await screen.findByText("Team Skill");
  await user.click(screen.getByRole("button", { name: /propose/i }));

  await waitFor(() => expect(mockedPropose).toHaveBeenCalled());
});
```

(`editable: false, deletable: false, active_version: null` models an INACTIVE
proposed-draft skill a non-owner just created — check this matches what the real
list endpoint would actually return for such a row given Task 6's `activate=owns`
change, and adjust field values if your reading of the store/list code disagrees.)

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npm test -- skills-tab --run`
Expected: FAIL — no "Propose" button exists yet, `proposeAgentSkill` doesn't exist.

- [ ] **Step 4: Implement the API client + schema**

`frontend/lib/schemas/agent-skills.ts`, add near `SkillToggleInput`:

```typescript
// No target_ref/version here — the backend resolves its own target server-side
// (the newest inactive version at this scope), mirroring Behavior's propose(),
// which likewise never accepts a client-suppliable target. See the sub-project 3
// spec for why: a client-suppliable target could be pointed at an arbitrary row.
export const SkillProposeInput = z.object({
  agent_id: z.string(),
  scope: SkillScope,
  scope_id: z.string().nullish(),
});
export type SkillProposeInput = z.infer<typeof SkillProposeInput>;
```

`frontend/lib/api/agent-skills.ts`, add near `toggleAgentSkill` (importing
`GovernanceApproval` the same way `lib/api/agent-profiles.ts` already does for
`proposeAgentProfilePublish`):

```typescript
export const proposeAgentSkill = (skillKey: string, input: SkillProposeInput) =>
  api(`/agent-skills/${encodeURIComponent(skillKey)}/propose`, {
    method: "POST",
    body: input,
    schema: GovernanceApproval,
  });
```

- [ ] **Step 5: Implement the UI action**

In `skills-tab.tsx`, add a "Propose" action for a non-owner (`!canManage`) viewing a
custom skill that is their own scope's content (not inherited — inherited items
already show a different affordance per sub-project 1). Match the exact rendering
condition and button styling of the existing "Override" action's sibling code path
(same file, already read in Task 9 Step 1) — a `useMutation` calling
`proposeAgentSkill`, success toast, no optimistic update needed (proposing doesn't
change the visible skill state, only files a request).

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npm test -- skills-tab --run`
Expected: all PASS.

- [ ] **Step 7: Run the full frontend suite + typecheck**

Run: `cd frontend && npm run typecheck && npm test -- --run`
Expected: all PASS, 0 typecheck errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/lib/api/agent-skills.ts frontend/lib/schemas/agent-skills.ts frontend/components/agent-studio/skills-tab.tsx frontend/components/agent-studio/__tests__/skills-tab.test.tsx
git commit -m "feat: Skills tab gains a Propose action for non-owner project/workspace/org members"
```
