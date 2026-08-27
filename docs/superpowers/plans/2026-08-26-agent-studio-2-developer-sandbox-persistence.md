# Agent Studio 2: Developer sandbox (personal tier) persistence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scope="user"` (the personal/developer-sandbox tier) actually persist for
both Behavior and Skills, safely — every role except `org_admin`/`bu_admin` can save
their own personal default, and only their own.

**Architecture:** No schema change (no DB-level CHECK constraint blocks `"user"` today).
The blocker is authorization: `create_draft`/`preview`/`publish`/`unpublish`
(`agent_profiles.py`) and `create`/`update`/`delete`/`toggle`/`activate`
(`agent_skills.py`) are each gated by ONE blanket permission string
(`skill:edit`/`workspace:manage`) checked at the FastAPI `Depends()` level, before
`scope` is even parsed — held only by `developer`+`org_admin` (writes) or
`bu_admin`+`org_admin` (publish), regardless of scope. This plan replaces those
route-level gates with the router's existing `artifact:view` floor plus a new in-body,
scope-aware check, `assert_can_write_agent_scope`, that reproduces today's exact
permission outcome for org/workspace/project (zero behavior change there) and adds a
new self-service rule for `user`: any role except `org_admin`/`bu_admin`, writing only
their own `scope_id`. `ancestor_chain` gains a `user` branch (project → workspace →
org) so a personal draft still resolves inherited content, exactly like every other
tier. The frontend needs no code change — verified during spec research that it
already fully supports `scope="user"` (full personal-tier UI, `chain.userId` already
sent everywhere) — this plan only adds a smoke test confirming that.

**Tech Stack:** FastAPI + SQLAlchemy (async) backend, Next.js + React Query + Zod
frontend, pytest (backend, incl. live-DB tests via `get_db_session_for_tenant`),
vitest + React Testing Library (frontend).

**Spec:** `docs/superpowers/specs/2026-08-26-agent-studio-2-developer-sandbox-persistence-design.md`

## Global Constraints

- No new tables, no migration.
- `assert_can_write_agent_scope` must reproduce TODAY'S exact allow/deny outcome for
  org/workspace/project scopes — same permission string, same actors pass/fail as
  before this plan. Any test that would have passed/failed under the OLD
  `Depends(require_permission(...))` gate must still pass/fail identically under the
  new in-body check.
- `assert_can_write_agent_scope` deliberately does NOT replicate
  `require_permission()`'s `RBAC_DENIALS` metric increment or `record_access_denied`
  audit-trail call — an accepted, disclosed simplification (9 call sites; replicating
  the full metrics/audit dance in every one is a materially bigger change than this
  sub-project needs). It still raises the identical `HTTPException(403, "Forbidden")`
  shape `require_permission` already raises, so callers see no behavior difference.
- Every route keeps a `require_permission`-sentinel dependency (the router-level
  `artifact:view` floor, already present on both routers) so the D-05 boot scan
  (`assert_all_routes_protected`) stays green — no route loses its sentinel, some
  just drop from a route-level override back to the floor.
- Read paths (`GET /agent-profiles/summary`, `GET /agent-skills`, etc.) are
  DELIBERATELY left scope-blind, matching today's existing behavior for
  org/workspace/project reads (nobody scopes those to "members of that tier" either)
  — this plan closes the WRITE-side gap only, per the spec's "Out of scope" section.
- New optional params (`project_id` alongside the existing `workspace_id`) always
  default to `None` — omitting them must degrade gracefully (partial or empty
  ancestor chain), never error. Mirrors sub-project 1's identical constraint for
  `workspace_id`.

---

### Task 1: Backend — `SCOPE_VALUES` + `_validate_scope` accept `"user"`

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py:50` (`SCOPE_VALUES`), `:300-304` (`_validate_scope`)
- Modify: `backend/shared/routers/agent_skills.py:170-174` (`_validate_scope`, separate copy)
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py`, `backend/tests/agent_skills/test_agent_skills_router.py`

**Interfaces:**
- Produces: `SCOPE_VALUES = ("org", "workspace", "project", "user")` (imported by `agent_skills.py` already, no import change needed). `_validate_scope(scope, scope_id)` now requires `scope_id` for `"user"` too.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_profiles/test_agent_profiles_router.py`, in the
`_validate_scope` section (create one if none exists — search the file for
`_validate_scope` to find where similar tests, if any, already live; otherwise add
near the `ancestor_chain` tests):

```python
def test_validate_scope_accepts_user_with_scope_id():
    ap._validate_scope("user", "11111111-1111-1111-1111-111111111111")  # no raise


def test_validate_scope_rejects_user_without_scope_id():
    with pytest.raises(HTTPException) as exc:
        ap._validate_scope("user", None)
    assert exc.value.status_code == 422
```

Add the identical pair to `backend/tests/agent_skills/test_agent_skills_router.py`
(against `sk._validate_scope`, since it's a separate function in that module):

```python
def test_validate_scope_accepts_user_with_scope_id():
    sk._validate_scope("user", "11111111-1111-1111-1111-111111111111")  # no raise


def test_validate_scope_rejects_user_without_scope_id():
    with pytest.raises(HTTPException) as exc:
        sk._validate_scope("user", None)
    assert exc.value.status_code == 422
```

(Both test files already import their respective router module under a short alias —
`ap`/`sk` — and already import `HTTPException`/`pytest`; if either import is missing,
add it at the top matching the file's existing import style.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py tests/agent_skills/test_agent_skills_router.py -k validate_scope_accepts_user -v`
Expected: FAIL — `scope must be one of ('org', 'workspace', 'project')` (422 raised for
the "accepts" test too, since `"user"` isn't in `SCOPE_VALUES` yet).

- [ ] **Step 3: Implement**

In `backend/shared/routers/agent_profiles.py`, change line 50:

```python
SCOPE_VALUES: tuple[str, ...] = ("org", "workspace", "project", "user")
```

And `_validate_scope` (currently lines 300-304):

```python
def _validate_scope(scope: str, scope_id: str | None) -> None:
    if scope not in SCOPE_VALUES:
        raise HTTPException(status_code=422, detail=f"scope must be one of {SCOPE_VALUES}")
    if scope in ("workspace", "project", "user") and not scope_id:
        raise HTTPException(status_code=422, detail=f"scope_id is required for {scope} scope")
```

In `backend/shared/routers/agent_skills.py`, its own separate `_validate_scope`
(currently lines 170-174) gets the identical change (it already imports
`SCOPE_VALUES` from `agent_profiles`, so only the `scope_id`-required tuple needs
updating):

```python
def _validate_scope(scope: str, scope_id: str | None) -> None:
    if scope not in SCOPE_VALUES:
        raise HTTPException(status_code=422, detail=f"scope must be one of {SCOPE_VALUES}")
    if scope in ("workspace", "project", "user") and not scope_id:
        raise HTTPException(status_code=422, detail=f"scope_id is required for {scope} scope")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py tests/agent_skills/test_agent_skills_router.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/shared/routers/agent_skills.py backend/tests/agent_profiles/test_agent_profiles_router.py backend/tests/agent_skills/test_agent_skills_router.py
git commit -m "feat: SCOPE_VALUES accepts the personal (user) tier"
```

---

### Task 2: Backend — `assert_can_write_agent_scope` pure helper

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py` (add near `_validate_scope`, and add a `has_permission` import)
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py`

**Interfaces:**
- Consumes: `SCOPE_VALUES` (unchanged).
- Produces: `assert_can_write_agent_scope(perms: list[str], role: str | None, scope: str, scope_id: str | None, actor_user_id: str, *, action: str) -> None` — raises `HTTPException(403)` on denial, returns `None` on allow. `action` is `"draft"` or `"publish"`. Used by Task 3 (this file), Task 4 (this file), and Task 5 (`agent_skills.py`, imported).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_profiles/test_agent_profiles_router.py`, in a new section:

```python
# ── assert_can_write_agent_scope ────────────────────────────────────────────────────

def test_write_check_shared_tier_draft_matches_todays_skill_edit_gate():
    ap.assert_can_write_agent_scope(["skill:edit"], "developer", "org", None, "u1", action="draft")
    ap.assert_can_write_agent_scope(["skill:edit"], "developer", "workspace", "ws-1", "u1", action="draft")
    ap.assert_can_write_agent_scope(["skill:edit"], "developer", "project", "proj-1", "u1", action="draft")


def test_write_check_shared_tier_draft_denies_without_skill_edit():
    for scope, sid in (("org", None), ("workspace", "ws-1"), ("project", "proj-1")):
        with pytest.raises(HTTPException) as exc:
            ap.assert_can_write_agent_scope([], "bu_admin", scope, sid, "u1", action="draft")
        assert exc.value.status_code == 403


def test_write_check_shared_tier_publish_matches_todays_workspace_manage_gate():
    ap.assert_can_write_agent_scope(["workspace:manage"], "bu_admin", "org", None, "u1", action="publish")
    ap.assert_can_write_agent_scope(["workspace:manage"], "bu_admin", "workspace", "ws-1", "u1", action="publish")
    ap.assert_can_write_agent_scope(["workspace:manage"], "bu_admin", "project", "proj-1", "u1", action="publish")


def test_write_check_shared_tier_publish_denies_developer_without_workspace_manage():
    with pytest.raises(HTTPException) as exc:
        ap.assert_can_write_agent_scope(["skill:edit"], "developer", "workspace", "ws-1", "u1", action="publish")
    assert exc.value.status_code == 403


def test_write_check_user_scope_allows_own_id_for_non_governance_role():
    # No perms needed at all for the personal tier — it's role + self-ownership only,
    # matching frontend canPublishAtTier's rule exactly (role !== org_admin && !== bu_admin).
    ap.assert_can_write_agent_scope([], "developer", "user", "u1", "u1", action="draft")
    ap.assert_can_write_agent_scope([], "contributor", "user", "u1", "u1", action="publish")
    ap.assert_can_write_agent_scope([], "project_admin", "user", "u1", "u1", action="draft")


def test_write_check_user_scope_denies_someone_elses_id():
    with pytest.raises(HTTPException) as exc:
        ap.assert_can_write_agent_scope([], "developer", "user", "someone-else", "u1", action="draft")
    assert exc.value.status_code == 403


def test_write_check_user_scope_denies_org_admin():
    with pytest.raises(HTTPException) as exc:
        ap.assert_can_write_agent_scope(["admin:*"], "org_admin", "user", "u1", "u1", action="draft")
    assert exc.value.status_code == 403


def test_write_check_user_scope_denies_bu_admin():
    with pytest.raises(HTTPException) as exc:
        ap.assert_can_write_agent_scope(["workspace:manage"], "bu_admin", "user", "u1", "u1", action="publish")
    assert exc.value.status_code == 403


def test_write_check_user_scope_denies_missing_scope_id():
    with pytest.raises(HTTPException) as exc:
        ap.assert_can_write_agent_scope([], "developer", "user", None, "u1", action="draft")
    assert exc.value.status_code == 403


def test_write_check_user_scope_denies_role_none():
    with pytest.raises(HTTPException) as exc:
        ap.assert_can_write_agent_scope([], None, "user", "u1", "u1", action="draft")
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -k write_check -v`
Expected: FAIL with `AttributeError: module 'shared.routers.agent_profiles' has no attribute 'assert_can_write_agent_scope'`.

- [ ] **Step 3: Implement**

In `backend/shared/routers/agent_profiles.py`, add the import (alongside the existing
`from shared.authz.dependency import require_permission`, near the top):

```python
from shared.authz.permissions import has_permission
```

Add the helper right after `_validate_scope` (currently lines 300-304):

```python
def assert_can_write_agent_scope(
    perms: list[str],
    role: str | None,
    scope: str,
    scope_id: str | None,
    actor_user_id: str,
    *,
    action: str,
) -> None:
    """Scope-aware authorization for an Agent Studio write (Behavior draft/publish;
    Skills create/update/delete/toggle/activate). Raises HTTPException(403) on denial.

    org/workspace/project: UNCHANGED from before this function existed — the exact
    same permission string that used to gate the route via Depends(require_permission
    (...)) is checked here instead, one line later, after `scope` is known. "draft"
    needs "skill:edit" (create_draft/preview, Skills' create/update/delete/toggle);
    "publish" needs "workspace:manage" (publish/unpublish, Skills' activate). Same
    permission, same actors pass/fail, zero behavior change for these three tiers.

    user: self-service, mirrors propose()'s existing "a personal default is nobody
    else's to approve" reasoning for the SAME tier (see propose()'s NOT_A_SHARED_TIER
    guard below). Allowed only when `role` is neither "org_admin" nor "bu_admin" (PRD
    §14.8 — governance-only roles never run an agent, so a personal default they set
    could never take effect) AND `scope_id` equals the caller's own user id — writing
    anyone else's personal scope is denied regardless of role. This is the
    server-authoritative twin of `canPublishAtTier` in frontend/lib/governance.ts,
    whose own docstring already says it's meant to be "shared by the client gate and
    BOTH server runtimes" — this closes that gap for the personal tier specifically.
    """
    if scope == "user":
        if role is None or role in ("org_admin", "bu_admin"):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not actor_user_id or not scope_id or str(scope_id) != str(actor_user_id):
            raise HTTPException(status_code=403, detail="Forbidden")
        return
    required = "skill:edit" if action == "draft" else "workspace:manage"
    if not has_permission(perms, required):
        raise HTTPException(status_code=403, detail="Forbidden")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/tests/agent_profiles/test_agent_profiles_router.py
git commit -m "feat: assert_can_write_agent_scope — scope-aware write authorization"
```

---

### Task 3: Backend — wire the check into Behavior's `create_draft`/`preview`

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py:393-433` (`create_draft`), `:608-` (`preview`)
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py`

**Interfaces:**
- Consumes: `assert_can_write_agent_scope` (Task 2), `effective_platform_role` (existing, `shared.authz.effective_role`, already used by `propose()` in this same file via a local import).
- Produces: `POST /agent-profiles/draft` and `POST /agent-profiles/preview` no longer carry a route-level `Depends(require_permission("skill:edit"))` — the router's existing `artifact:view` floor plus an in-body `assert_can_write_agent_scope(..., action="draft")` call replace it.

This task needs a real HTTP round trip (the check depends on `request.state.permissions`
and a DB-backed role lookup, both populated by the JWT middleware — not reachable by
calling the route function directly). Follow the `httpx.AsyncClient` + `mint_token`
convention already used elsewhere in this test file's sibling
(`test_agent_skills_router.py`, see Task 5 below for that fixture's exact shape) — if
`test_agent_profiles_router.py` doesn't yet import `httpx`/`mint_token`, add them
matching `test_agent_skills_router.py`'s existing import block exactly.

A caller resolving to `role="developer"`/`"bu_admin"`/etc. (anything besides
`org_admin`, which shortcuts via the `admin:*` permission alone) needs a REAL
`role_bindings` row in the DB — `effective_platform_role` reads bindings, not the
token's `permissions` list (see `platform_role_for`'s docstring: "Deliberately NOT
derived from `request.state.permissions`"). Mirror the existing live-DB role-binding
setup convention from `backend/tests/test_governance_requests.py` (insert into
`users` then `role_bindings` via raw `text()`, random UUIDs, `get_db_session_for_tenant`).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_profiles/test_agent_profiles_router.py`:

```python
# ── create_draft / preview: scope-aware write authorization (route-level) ──────────

async def _bind_role(tenant_id: str, user_id: str, role: str, scope_kind: str, scope_id: str | None) -> None:
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', CAST(:t AS uuid), true) ON CONFLICT (id) DO NOTHING"
        ), {"i": user_id, "e": f"{user_id}@example.com", "t": tenant_id})
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, tenant_id) "
            "VALUES (gen_random_uuid(), :u, :sk, CAST(:si AS uuid), :r, CAST(:t AS uuid))"
        ), {"u": user_id, "sk": scope_kind, "si": scope_id, "r": role, "t": tenant_id})


@pytest.mark.asyncio
async def test_create_draft_user_scope_own_id_succeeds(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", project_id)

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-profiles/draft",
            json={"agent_id": "requirements", "scope": "user", "scope_id": user_id, "prompt_prepend": "hi"},
            headers=headers,
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_draft_user_scope_someone_elses_id_403s(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", project_id)

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-profiles/draft",
            json={"agent_id": "requirements", "scope": "user", "scope_id": str(uuid.uuid4()), "prompt_prepend": "hi"},
            headers=headers,
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_draft_bu_admin_denied_user_scope(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "bu_admin", "business_unit", ws_id)

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view", "workspace:manage"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-profiles/draft",
            json={"agent_id": "requirements", "scope": "user", "scope_id": user_id, "prompt_prepend": "hi"},
            headers=headers,
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_draft_project_scope_unchanged_developer_allowed(mint_token):
    # Regression guard: today's exact behavior for a non-user scope must survive
    # the route-level -> in-body move unchanged.
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", project_id)

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view", "skill:edit"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-profiles/draft",
            json={"agent_id": "requirements", "scope": "project", "scope_id": project_id, "prompt_prepend": "hi"},
            headers=headers,
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_draft_project_scope_unchanged_contributor_denied(mint_token):
    # contributor never held skill:edit before this plan; must still be denied.
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "contributor", "business_unit", str(uuid.uuid4()))

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-profiles/draft",
            json={"agent_id": "requirements", "scope": "project", "scope_id": project_id, "prompt_prepend": "hi"},
            headers=headers,
        )
    assert resp.status_code == 403
```

If this test file does not already import `app` (from `process_api`), `httpx`, `uuid`,
`text`, or `get_db_session_for_tenant`, add them matching
`backend/tests/agent_skills/test_agent_skills_router.py`'s existing import block
(same repo, same conventions — copy from there rather than inventing a new style).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -k create_draft_user_scope -v`
Expected: FAIL — `scope must be one of ('org', 'workspace', 'project')` is gone (Task 1
fixed that), but the route still 403s via the OLD `skill:edit` gate before the body is
even read (a `developer`'s token here only carries `artifact:view` — deliberately, to
prove the NEW self-service rule is what's expected to let them through, not the old
gate). Confirms the old gate is still blocking `scope="user"` writes at this point.

- [ ] **Step 3: Implement — `create_draft`**

In `backend/shared/routers/agent_profiles.py`, replace the `create_draft` route
decorator + signature (currently lines 393-401):

```python
@agent_profiles_router.post("/draft")
async def create_draft(
    body: DraftIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    from shared.authz.effective_role import effective_platform_role  # noqa: PLC0415 - avoids an import cycle, matches propose()'s existing pattern

    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    role = await effective_platform_role(db, request)
    assert_can_write_agent_scope(
        getattr(request.state, "permissions", []) or [], role,
        body.scope, body.scope_id, _user_id(request), action="draft",
    )
```

(Everything after `_validate_scope(body.scope, body.scope_id)` in the existing body —
the lint check, the version-number query, the row insert — is unchanged; only the
decorator lost its `dependencies=[...]` and these five new lines were inserted right
after the existing validation calls.)

- [ ] **Step 4: Implement — `preview`**

Replace the `preview` route decorator + the start of its body (currently around lines
608-622, through the `_validate_scope` call):

```python
@agent_profiles_router.post("/preview")
async def preview(
    body: DraftIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    from shared.authz.effective_role import effective_platform_role  # noqa: PLC0415

    _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    role = await effective_platform_role(db, request)
    assert_can_write_agent_scope(
        getattr(request.state, "permissions", []) or [], role,
        body.scope, body.scope_id, _user_id(request), action="draft",
    )
```

(The rest of `preview`'s existing body — reading `raw = await request.json()` for
`workspace_id`, walking the ancestor chain, building layers — is unchanged here; Task
6 below extends it further to also read `project_id`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/tests/agent_profiles/test_agent_profiles_router.py
git commit -m "feat: create_draft/preview use scope-aware write authorization"
```

---

### Task 4: Backend — wire the check into Behavior's `publish`/`unpublish`

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py:436-507` (`publish`, `unpublish`)
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py`

**Interfaces:**
- Consumes: `assert_can_write_agent_scope` (Task 2). Reads `scope`/`scope_id` from the
  already-loaded `target` row (loaded via `_load_or_404`) rather than the request body
  — `publish`/`unpublish` take only `profile_id` as a path param, mirroring how
  `propose()` already reads `target.scope` post-load in this same file.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_profiles/test_agent_profiles_router.py`:

```python
# ── publish / unpublish: scope-aware write authorization (route-level) ─────────────

async def _create_draft_row(tenant_id: str, scope: str, scope_id: str | None) -> str:
    """Insert a draft AgentProfile row directly (bypassing the route) and return its id."""
    async with get_db_session_for_tenant(tenant_id) as s:
        row_id = str(uuid.uuid4())
        await s.execute(text(
            "INSERT INTO agent_profiles "
            "(id, tenant_id, agent_id, scope, scope_id, version, is_active, created_by) "
            "VALUES (CAST(:i AS uuid), CAST(:t AS uuid), 'requirements', :sc, "
            " CAST(:sid AS uuid), 1, false, 'tester')"
        ), {"i": row_id, "t": tenant_id, "sc": scope, "sid": scope_id})
        return row_id


@pytest.mark.asyncio
async def test_publish_user_scope_own_id_succeeds(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    draft_id = await _create_draft_row(tenant, "user", user_id)
    await _bind_role(tenant, user_id, "developer", "project", str(uuid.uuid4()))

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/agent-profiles/{draft_id}/publish", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_publish_user_scope_someone_elses_id_403s(mint_token):
    tenant = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    draft_id = await _create_draft_row(tenant, "user", owner_id)
    await _bind_role(tenant, other_id, "developer", "project", str(uuid.uuid4()))

    token = mint_token(user_id=other_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/agent-profiles/{draft_id}/publish", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_publish_workspace_scope_unchanged_bu_admin_allowed(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    draft_id = await _create_draft_row(tenant, "workspace", ws_id)
    await _bind_role(tenant, user_id, "bu_admin", "business_unit", ws_id)

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view", "workspace:manage"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/agent-profiles/{draft_id}/publish", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_publish_workspace_scope_unchanged_developer_denied(mint_token):
    # developer never held workspace:manage before this plan; must still be denied.
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    draft_id = await _create_draft_row(tenant, "workspace", ws_id)
    await _bind_role(tenant, user_id, "developer", "project", str(uuid.uuid4()))

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view", "skill:edit"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/agent-profiles/{draft_id}/publish", headers=headers)
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -k "publish_user_scope or publish_workspace_scope" -v`
Expected: `test_publish_user_scope_own_id_succeeds` FAILS (403 from the old blanket
`workspace:manage` gate, which a plain `developer` never holds); the two
`_unchanged_` tests PASS already (today's exact gate, unmodified) — run anyway to
confirm the baseline before editing.

- [ ] **Step 3: Implement — `publish`**

In `backend/shared/routers/agent_profiles.py`, replace the `publish` route decorator +
the start of its body (currently lines 436-448, through the `target = await
_load_or_404(...)` line):

```python
@agent_profiles_router.post("/{profile_id}/publish")
async def publish(
    profile_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    from shared.authz.effective_role import effective_platform_role  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    target = await _load_or_404(db, profile_id)
    role = await effective_platform_role(db, request)
    assert_can_write_agent_scope(
        getattr(request.state, "permissions", []) or [], role,
        target.scope, str(target.scope_id) if target.scope_id else None,
        _user_id(request), action="publish",
    )
```

(The rest of `publish`'s body — the siblings query, `apply_publish_flip`, cache
invalidation, audit emit, return — is unchanged.)

- [ ] **Step 4: Implement — `unpublish`**

Replace the `unpublish` route decorator + the start of its body (currently lines
477-489, through `target = await _load_or_404(db, profile_id)`):

```python
@agent_profiles_router.post("/{profile_id}/unpublish")
async def unpublish(
    profile_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    from shared.authz.effective_role import effective_platform_role  # noqa: PLC0415

    tenant_id = _tenant_id(request)
    target = await _load_or_404(db, profile_id)
    role = await effective_platform_role(db, request)
    assert_can_write_agent_scope(
        getattr(request.state, "permissions", []) or [], role,
        target.scope, str(target.scope_id) if target.scope_id else None,
        _user_id(request), action="publish",
    )
```

(The rest of `unpublish`'s body — flipping `is_active`, cache invalidation, audit
emit, return — is unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/tests/agent_profiles/test_agent_profiles_router.py
git commit -m "feat: publish/unpublish use scope-aware write authorization"
```

---

### Task 5: Backend — wire the check into Skills' write routes

**Files:**
- Modify: `backend/shared/routers/agent_skills.py` (`create_skill`, `update_skill`, `delete_skill`, `toggle_skill`, `activate_version`)
- Test: `backend/tests/agent_skills/test_agent_skills_router.py`

**Interfaces:**
- Consumes: `assert_can_write_agent_scope` (Task 2, imported from `agent_profiles`),
  `resolve_platform_role_for_user(user_id, tenant_id, permissions) -> str | None`
  (existing, `shared.authz.effective_role` — opens its own tenant-scoped DB session
  internally, so none of these five routes need a new `db: AsyncSession` dependency
  added; `agent_profiles.py`'s routes already had `db` for other reasons, `agent_skills.py`'s
  do not).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_skills/test_agent_skills_router.py` (reusing the
`_bind_role`-shaped helper — if `test_agent_profiles_router.py`'s `_bind_role` isn't
importable across test modules in this repo's pytest layout, duplicate the same 6-line
helper here rather than adding a shared conftest fixture, matching how this repo's
tests generally favor small local duplication over new shared fixtures for one-off
setup — check `backend/tests/conftest.py` first in case a suitable helper already
exists there under a different name, and reuse it if so):

```python
# ── create_skill / toggle_skill / update_skill / delete_skill / activate_version:
#    scope-aware write authorization (route-level) ──────────────────────────────────

@pytest.mark.asyncio
async def test_create_skill_user_scope_own_id_succeeds(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", str(uuid.uuid4()))

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "user", "scope_id": user_id,
                "skill_key": "my-skill", "display_name": "My Skill", "body": "do the thing",
            },
            headers=headers,
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_skill_user_scope_someone_elses_id_403s(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", str(uuid.uuid4()))

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "user", "scope_id": str(uuid.uuid4()),
                "skill_key": "my-skill", "display_name": "My Skill", "body": "do the thing",
            },
            headers=headers,
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_skill_project_scope_unchanged_contributor_denied(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "contributor", "business_unit", str(uuid.uuid4()))

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "project", "scope_id": project_id,
                "skill_key": "my-skill", "display_name": "My Skill", "body": "do the thing",
            },
            headers=headers,
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_activate_version_workspace_scope_unchanged_bu_admin_allowed(monkeypatch, mint_token):
    async def fake_activate(*args, **kwargs):
        return {"skill_key": "k", "version": 2}

    class FakeStore:
        activate_custom_version = staticmethod(fake_activate)

    monkeypatch.setattr(sk, "_store", lambda: FakeStore)

    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "bu_admin", "business_unit", ws_id)

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view", "workspace:manage"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/k/activate/2",
            params={"agent_id": "requirements", "scope": "workspace", "scope_id": ws_id},
            headers=headers,
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_activate_version_workspace_scope_unchanged_developer_denied(monkeypatch, mint_token):
    async def fake_activate(*args, **kwargs):
        return {"skill_key": "k", "version": 2}

    class FakeStore:
        activate_custom_version = staticmethod(fake_activate)

    monkeypatch.setattr(sk, "_store", lambda: FakeStore)

    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", str(uuid.uuid4()))

    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view", "skill:edit"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills/k/activate/2",
            params={"agent_id": "requirements", "scope": "workspace", "scope_id": ws_id},
            headers=headers,
        )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/test_agent_skills_router.py -k "create_skill_user_scope or activate_version_workspace" -v`
Expected: `test_create_skill_user_scope_own_id_succeeds` and
`test_activate_version_workspace_scope_unchanged_bu_admin_allowed` FAIL (403 from the
old blanket gates — a plain `developer`/`bu_admin` token here deliberately carries
only `artifact:view` plus, for the "unchanged" test, the SAME permission the old gate
already required, to isolate what's actually being tested); the two
`_unchanged_..._denied` tests PASS already.

- [ ] **Step 3: Implement**

In `backend/shared/routers/agent_skills.py`, update the import block (currently lines
46-50):

```python
from shared.routers.agent_profiles import (
    FORBIDDEN_PATTERNS,
    SCOPE_VALUES,
    ancestor_chain,
    assert_can_write_agent_scope,
)
from shared.authz.effective_role import resolve_platform_role_for_user
```

Replace `create_skill`'s decorator + the start of its body (currently lines 250-256):

```python
@agent_skills_router.post("")
async def create_skill(body: CreateSkillIn, request: Request):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    assert_can_write_agent_scope(perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
```

Replace `toggle_skill`'s decorator + the start of its body (currently lines 297-304):

```python
@agent_skills_router.post("/toggle")
async def toggle_skill(body: ToggleIn, request: Request):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    assert_can_write_agent_scope(perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
    if body.origin not in (Origin.vendor.value, Origin.custom.value):
        raise HTTPException(status_code=422, detail="origin must be one of ('vendor', 'custom')")
```

Replace `activate_version`'s decorator + the start of its body (currently lines
356-370):

```python
@agent_skills_router.post("/{skill_key}/activate/{version}")
async def activate_version(
    request: Request,
    skill_key: str,
    version: int,
    agent_id: str,
    scope: str,
    scope_id: Optional[str] = None,
):
    tenant_id = _tenant_id(request)
    _validate_agent(agent_id)
    _validate_scope(scope, scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    assert_can_write_agent_scope(perms, role, scope, scope_id, _user_id(request), action="publish")
```

Replace `update_skill`'s decorator + the start of its body (currently lines 386-393):

```python
@agent_skills_router.put("/{skill_key}")
async def update_skill(skill_key: str, body: UpdateSkillIn, request: Request):
    tenant_id = _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    assert_can_write_agent_scope(perms, role, body.scope, body.scope_id, _user_id(request), action="draft")
```

Replace `delete_skill`'s decorator + the start of its body (currently lines 416-429):

```python
@agent_skills_router.delete("/{skill_key}")
async def delete_skill(
    request: Request,
    skill_key: str,
    agent_id: str,
    scope: str,
    scope_id: Optional[str] = None,
):
    tenant_id = _tenant_id(request)
    _validate_agent(agent_id)
    _validate_scope(scope, scope_id)
    perms = getattr(request.state, "permissions", []) or []
    role = await resolve_platform_role_for_user(_user_id(request), tenant_id, perms)
    assert_can_write_agent_scope(perms, role, scope, scope_id, _user_id(request), action="draft")
```

Each of these five routes loses its old `dependencies=[Depends(require_permission(...))]`
line entirely — the router-level `Depends(require_permission("artifact:view"))` floor
(construction site, unchanged) keeps the D-05 boot-scan sentinel present. The rest of
each function body (lint checks, the store call, cache invalidation, audit emit,
return) is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/test_agent_skills_router.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full backend suite for both routers to confirm no regressions**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/ tests/agent_skills/ -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/agent_skills.py backend/tests/agent_skills/test_agent_skills_router.py
git commit -m "feat: Skills create/update/delete/toggle/activate use scope-aware write authorization"
```

---

### Task 6: Backend — `ancestor_chain` gains a `user` branch; thread `project_id` through

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py` (`ancestor_chain`, `get_summary`, `preview`)
- Modify: `backend/shared/routers/agent_skills.py` (`list_skills`)
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py`

**Interfaces:**
- Produces: `ancestor_chain(scope, scope_id, workspace_id, project_id=None)` — new 4th
  optional param, defaults to `None` so every existing 3-positional-arg call site is
  unaffected. `scope="user"` now returns `[("project", project_id), ("workspace",
  workspace_id), ("org", None)]`, each id independently optional (omitted entries are
  simply skipped, `("org", None)` always present).
- `_scope_filters` needs **no change** — it already handles any non-`"org"` scope
  generically (`AgentProfile.scope_id == uuid.UUID(str(scope_id))`), which already
  covers `"user"` correctly the moment `SCOPE_VALUES` accepts it (Task 1). Verify this
  with a quick read before writing any test for it — there is nothing to fix here.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_profiles/test_agent_profiles_router.py`, in the
`ancestor_chain` section:

```python
def test_ancestor_chain_user_scope_full_chain():
    assert ap.ancestor_chain("user", "u1", "ws-1", "proj-1") == [
        ("project", "proj-1"), ("workspace", "ws-1"), ("org", None),
    ]


def test_ancestor_chain_user_scope_no_project_id():
    assert ap.ancestor_chain("user", "u1", "ws-1", None) == [
        ("workspace", "ws-1"), ("org", None),
    ]


def test_ancestor_chain_user_scope_no_workspace_id():
    assert ap.ancestor_chain("user", "u1", None, "proj-1") == [
        ("project", "proj-1"), ("org", None),
    ]


def test_ancestor_chain_user_scope_no_ids_at_all():
    assert ap.ancestor_chain("user", "u1", None, None) == [("org", None)]


def test_ancestor_chain_existing_calls_unaffected_by_new_param():
    # 3-positional-arg call sites (every one that predates this task) keep working.
    assert ap.ancestor_chain("project", "proj-1", "ws-1") == [("workspace", "ws-1"), ("org", None)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -k ancestor_chain_user_scope -v`
Expected: FAIL — `ancestor_chain` doesn't accept a 4th positional arg yet / returns
`[]` for `scope="user"` (falls through to the final `return []`).

- [ ] **Step 3: Implement**

In `backend/shared/routers/agent_profiles.py`, replace `ancestor_chain` (currently
lines 54-69):

```python
def ancestor_chain(
    scope: str, scope_id: str | None, workspace_id: str | None, project_id: str | None = None,
) -> list[tuple[str, str | None]]:
    """Nearest-first ancestor (scope, scope_id) pairs above `scope`, for inheritance
    resolution. `workspace_id` is the project's own parent BU — required to resolve a
    project's WORKSPACE ancestor specifically; omitted, a project-scope request still
    resolves its org ancestor, just not its workspace ancestor. `project_id` is
    additionally needed to resolve a PERSONAL (user) scope's project ancestor — the
    only scope whose full chain is longer than one hop. Never errors on a missing id.
    Shared with skill_store.py's list_skills_merged, which needs the identical chain
    shape.
    """
    if scope == "org":
        return []
    if scope == "workspace":
        return [("org", None)]
    if scope == "project":
        return [("workspace", workspace_id), ("org", None)] if workspace_id else [("org", None)]
    if scope == "user":
        chain: list[tuple[str, str | None]] = []
        if project_id:
            chain.append(("project", project_id))
        if workspace_id:
            chain.append(("workspace", workspace_id))
        chain.append(("org", None))
        return chain
    return []
```

- [ ] **Step 4: Thread `project_id` through `get_summary`**

Replace `get_summary`'s signature + `ancestor_chain` call (currently lines 336-356):

```python
@agent_profiles_router.get("/summary")
async def get_summary(
    request: Request,
    scope: str,
    scope_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    project_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    _tenant_id(request)
    _validate_scope(scope, scope_id)
    stmt = select(AgentProfile).where(
        AgentProfile.agent_id.in_(PIPELINE_ORDER),
        *_scope_filters(scope, scope_id),
    )
    rows = list((await db.execute(stmt)).scalars().all())
    by_agent: dict[str, list] = {a: [] for a in PIPELINE_ORDER}
    for r in rows:
        by_agent.setdefault(r.agent_id, []).append(r)

    ancestor_by_agent: dict[str, list[tuple[str, object]]] = {a: [] for a in PIPELINE_ORDER}
    for anc_scope, anc_scope_id in ancestor_chain(scope, scope_id, workspace_id, project_id):
```

(Only the signature gains `project_id` and the `ancestor_chain(...)` call passes it
through — the rest of the function, from the `anc_rows = list(...)` line onward, is
unchanged.)

- [ ] **Step 5: Thread `project_id` through `preview`**

In `preview` (edited in Task 3), extend the raw-body read and the `ancestor_chain`
call:

```python
    raw = await request.json()
    workspace_id = raw.get("workspace_id")
    project_id = raw.get("project_id")
    lower_rows: list = []
    for anc_scope, anc_scope_id in ancestor_chain(body.scope, body.scope_id, workspace_id, project_id):
```

(Same reasoning as the existing `workspace_id` read, documented in Task 3's version of
this function: `DraftIn` doesn't declare `project_id` either, for the same "a draft
belongs to exactly one tier" reason — read it off the raw body instead.)

- [ ] **Step 6: Thread `project_id` through Skills' `list_skills`**

In `backend/shared/routers/agent_skills.py`, replace `list_skills`'s signature +
`ancestor_chain` call (currently lines 234-246):

```python
@agent_skills_router.get("")
async def list_skills(
    request: Request,
    agent_id: str,
    scope: str,
    scope_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    project_id: Optional[str] = None,
):
    tenant_id = _tenant_id(request)
    _validate_agent(agent_id)
    _validate_scope(scope, scope_id)
    ancestor = ancestor_chain(scope, scope_id, workspace_id, project_id)
    skills = await _store().list_skills_merged(tenant_id, agent_id, scope, scope_id, ancestor=ancestor)
    return {"skills": skills}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/ tests/agent_skills/ -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/shared/routers/agent_skills.py backend/tests/agent_profiles/test_agent_profiles_router.py
git commit -m "feat: ancestor_chain resolves the personal tier's project/workspace/org ancestors"
```

---

### Task 7: Backend — live-DB end-to-end round trip (Behavior + Skills)

**Files:**
- Create: `backend/tests/agent_profiles/test_personal_tier_persistence.py`
- Create: `backend/tests/agent_skills/test_personal_tier_persistence.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6, exercised through the real HTTP surface + a
  real Postgres tenant, not mocks — this is the plan's proof that the pieces work
  together, not just individually.

- [ ] **Step 1: Write the round-trip test — Behavior**

Create `backend/tests/agent_profiles/test_personal_tier_persistence.py`:

```python
"""Live-DB end-to-end: a personal (user-scope) Behavior default can be drafted,
published, and read back — and only by its own owner. Agent Studio sub-project 2."""
import uuid

import httpx
import pytest
from sqlalchemy import text

from process_api import app
from shared.db import get_db_session_for_tenant


async def _bind_role(tenant_id: str, user_id: str, role: str, scope_kind: str, scope_id: str | None) -> None:
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', CAST(:t AS uuid), true) ON CONFLICT (id) DO NOTHING"
        ), {"i": user_id, "e": f"{user_id}@example.com", "t": tenant_id})
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, tenant_id) "
            "VALUES (gen_random_uuid(), :u, :sk, CAST(:si AS uuid), :r, CAST(:t AS uuid))"
        ), {"u": user_id, "sk": scope_kind, "si": scope_id, "r": role, "t": tenant_id})


@pytest.mark.asyncio
async def test_personal_behavior_default_round_trips(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", str(uuid.uuid4()))
    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        draft = await client.post(
            "/agent-profiles/draft",
            json={
                "agent_id": "requirements", "scope": "user", "scope_id": user_id,
                "prompt_prepend": "Always ask about compliance constraints first.",
            },
            headers=headers,
        )
        assert draft.status_code == 200
        draft_id = draft.json()["id"]

        published = await client.post(f"/agent-profiles/{draft_id}/publish", headers=headers)
        assert published.status_code == 200

        summary = await client.get(
            "/agent-profiles/summary", params={"scope": "user", "scope_id": user_id}, headers=headers,
        )
        assert summary.status_code == 200
        entry = next(a for a in summary.json()["agents"] if a["agent_id"] == "requirements")
        assert entry["inherited_from"] is None
        assert entry["active"]["prompt_prepend"] == "Always ask about compliance constraints first."


@pytest.mark.asyncio
async def test_personal_behavior_default_inherits_from_project(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "developer", "project", project_id)
    dev_token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])

    project_admin_id = str(uuid.uuid4())
    await _bind_role(tenant, project_admin_id, "project_admin", "project", project_id)
    pa_token = mint_token(
        user_id=project_admin_id, tenant_id=tenant, permissions=["artifact:view", "workspace:manage"],
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        draft = await client.post(
            "/agent-profiles/draft",
            json={
                "agent_id": "requirements", "scope": "project", "scope_id": project_id,
                "prompt_prepend": "Project-wide default.",
            },
            headers={"Authorization": f"Bearer {pa_token}"},
        )
        assert draft.status_code == 200
        publish = await client.post(
            f"/agent-profiles/{draft.json()['id']}/publish",
            headers={"Authorization": f"Bearer {pa_token}"},
        )
        assert publish.status_code == 200

        summary = await client.get(
            "/agent-profiles/summary",
            params={"scope": "user", "scope_id": user_id, "project_id": project_id},
            headers={"Authorization": f"Bearer {dev_token}"},
        )
        assert summary.status_code == 200
        entry = next(a for a in summary.json()["agents"] if a["agent_id"] == "requirements")
        assert entry["inherited_from"] == "project"
        assert entry["active"]["prompt_prepend"] == "Project-wide default."
```

- [ ] **Step 2: Write the round-trip test — Skills**

Create `backend/tests/agent_skills/test_personal_tier_persistence.py`:

```python
"""Live-DB end-to-end: a personal (user-scope) custom skill can be created, toggled,
and read back — and only by its own owner. Agent Studio sub-project 2."""
import uuid

import httpx
import pytest
from sqlalchemy import text

from process_api import app
from shared.db import get_db_session_for_tenant


async def _bind_role(tenant_id: str, user_id: str, role: str, scope_kind: str, scope_id: str | None) -> None:
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(text(
            "INSERT INTO users (id, email, password_hash, tenant_id, active) "
            "VALUES (:i, :e, 'x', CAST(:t AS uuid), true) ON CONFLICT (id) DO NOTHING"
        ), {"i": user_id, "e": f"{user_id}@example.com", "t": tenant_id})
        await s.execute(text(
            "INSERT INTO role_bindings (id, user_id, scope_kind, scope_id, role_name, tenant_id) "
            "VALUES (gen_random_uuid(), :u, :sk, CAST(:si AS uuid), :r, CAST(:t AS uuid))"
        ), {"u": user_id, "sk": scope_kind, "si": scope_id, "r": role, "t": tenant_id})


@pytest.mark.asyncio
async def test_personal_skill_round_trips(mint_token):
    tenant = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    await _bind_role(tenant, user_id, "contributor", "business_unit", str(uuid.uuid4()))
    token = mint_token(user_id=user_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "user", "scope_id": user_id,
                "skill_key": "my-checklist", "display_name": "My Checklist",
                "body": "Always double check acceptance criteria.",
            },
            headers=headers,
        )
        assert created.status_code == 200

        listed = await client.get(
            "/agent-skills", params={"agent_id": "requirements", "scope": "user", "scope_id": user_id},
            headers=headers,
        )
        assert listed.status_code == 200
        hit = next(s for s in listed.json()["skills"] if s["skill_key"] == "my-checklist")
        assert hit["origin_scope"] == "user"
        assert hit["editable"] is True


@pytest.mark.asyncio
async def test_someone_elses_personal_skill_write_is_denied(mint_token):
    tenant = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    attacker_id = str(uuid.uuid4())
    await _bind_role(tenant, attacker_id, "developer", "project", str(uuid.uuid4()))
    token = mint_token(user_id=attacker_id, tenant_id=tenant, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agent-skills",
            json={
                "agent_id": "requirements", "scope": "user", "scope_id": owner_id,
                "skill_key": "sneaky", "display_name": "Sneaky", "body": "x",
            },
            headers=headers,
        )
    assert resp.status_code == 403
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_personal_tier_persistence.py tests/agent_skills/test_personal_tier_persistence.py -v`
Expected: all PASS.

- [ ] **Step 4: Run the full backend suite to confirm no regressions anywhere**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all PASS (this is the widest net — confirms nothing outside
`agent_profiles`/`agent_skills` broke, e.g. any other router that happened to import
`SCOPE_VALUES` or `ancestor_chain`).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/agent_profiles/test_personal_tier_persistence.py backend/tests/agent_skills/test_personal_tier_persistence.py
git commit -m "test: live-DB round trip for personal-tier Behavior and Skills persistence"
```

---

### Task 8: Backend — update module docstrings; Frontend smoke test

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py:1-21` (module docstring)
- Modify: `backend/shared/routers/agent_skills.py:1-28` (module docstring)
- Create: `frontend/components/agent-studio/__tests__/personal-tier-smoke.test.tsx`

**Interfaces:**
- No new production interfaces — documentation + a frontend confirmation test.

- [ ] **Step 1: Update `agent_profiles.py`'s module docstring**

Replace the `RBAC` paragraph (currently lines 17-20):

```
RBAC (design §3.5, extended by sub-project 2): reads gate on the "artifact:view" floor
(router-level, matching the capabilities router). draft/preview/publish/unpublish all
now use the in-body, scope-aware `assert_can_write_agent_scope` check instead of a
route-level Depends(): for org/workspace/project scope it requires "skill:edit"
(draft/preview) or "workspace:manage" (publish/unpublish) exactly as before; for the
personal ("user") scope, any role except org_admin/bu_admin may write ONLY their own
scope_id. Every route still carries a require_permission sentinel (the router-level
floor) so the process_api D-05 boot scan stays green.
```

- [ ] **Step 2: Update `agent_skills.py`'s module docstring**

Replace the `RBAC` paragraph (currently lines 19-22):

```
RBAC (mirrors agent_profiles, extended by sub-project 2): reads gate on the
"artifact:view" floor (router-level). create/update/toggle/delete and activate all now
use the in-body, scope-aware `assert_can_write_agent_scope` check (imported from
agent_profiles) instead of a route-level Depends(): for org/workspace/project scope it
requires "skill:edit" (create/update/toggle/delete) or "workspace:manage" (activate)
exactly as before; for the personal ("user") scope, any role except org_admin/bu_admin
may write ONLY their own scope_id. Every route still carries a require_permission
sentinel (the router-level floor) so the process_api D-05 boot scan stays green.
```

- [ ] **Step 3: Write the frontend smoke test**

Create `frontend/components/agent-studio/__tests__/personal-tier-smoke.test.tsx`,
following the exact mocking convention already established in
`frontend/components/agent-studio/__tests__/skills-tab.test.tsx` (`vi.mock` the API
modules directly, wrap in a fresh `QueryClientProvider` — this repo has no wired MSW
server for component tests):

```tsx
// @vitest-environment jsdom
/**
 * Smoke test for Agent Studio sub-project 2: confirms BehaviorTab and SkillsTab
 * render without error at the personal ("user") tier and that a save round-trips
 * against the (now scope="user"-accepting) API client — catching any accidental
 * frontend assumption that the personal tier never persists. Full backend
 * authorization coverage lives in the live-DB tests (Task 7); this test only proves
 * the frontend itself has no "user" special-casing left to break.
 */
import "@testing-library/jest-dom/vitest";

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/agent-skills", () => ({
  listAgentSkills: vi.fn().mockResolvedValue({ skills: [] }),
  getAgentSkill: vi.fn(),
  createAgentSkill: vi.fn(),
  updateAgentSkill: vi.fn(),
  toggleAgentSkill: vi.fn(),
  deleteAgentSkill: vi.fn(),
  listAgentSkillVersions: vi.fn(),
}));
vi.mock("@/lib/api/agent-profiles", () => ({
  getAgentProfilesSummary: vi.fn().mockResolvedValue({
    agents: [{
      agent_id: "requirements", active_version: null, latest_version: null,
      draft_count: 0, updated_at: null, active: null, inherited_from: null,
    }],
  }),
  listAgentProfileVersions: vi.fn().mockResolvedValue({ versions: [] }),
  createAgentProfileDraft: vi.fn().mockResolvedValue({
    id: "draft-1", agent_id: "requirements", scope: "user", scope_id: "u1",
    version: 1, is_active: false, prompt_prepend: "", prompt_append: "",
    output_contract_extra: "", created_by: "u1", created_at: null, updated_at: null,
  }),
  publishAgentProfile: vi.fn(),
  unpublishAgentProfile: vi.fn(),
  previewAgentProfile: vi.fn().mockResolvedValue({ layers: [], warnings: [] }),
  proposeAgentProfilePublish: vi.fn(),
  getLintViolations: vi.fn().mockReturnValue(null),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

import { SkillsTab } from "../skills-tab";
import { BehaviorTab } from "../behavior-tab";
import type { ScopeContext } from "../agent-editor";
import type { AgentProfileSummaryEntry } from "@/lib/schemas/agent-profiles";

afterEach(cleanup);

function personalScopeContext(): ScopeContext {
  return {
    scope: "user",
    scopeId: "u1",
    scopeLabel: "You",
    chain: { workspaceId: "ws-1", projectId: "proj-1", userId: "u1" },
    isOwner: true,
    canPropose: false,
    ownerRoleLabel: null,
  };
}

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("Agent Studio personal-tier smoke test", () => {
  it("SkillsTab renders at the personal tier without error", async () => {
    renderWithClient(
      <SkillsTab agentId="requirements" agentLabel="Requirements" scopeContext={personalScopeContext()} />,
    );
    await waitFor(() => expect(screen.getByText(/personal skills/i)).toBeInTheDocument());
  });

  it("BehaviorTab renders at the personal tier and can save a draft", async () => {
    const summary: AgentProfileSummaryEntry = {
      agent_id: "requirements", active_version: null, latest_version: null,
      draft_count: 0, updated_at: null, active: null, inherited_from: null,
    };
    const { createAgentProfileDraft } = await import("@/lib/api/agent-profiles");
    const user = userEvent.setup();

    renderWithClient(
      <BehaviorTab
        agentId="requirements"
        agentLabel="Requirements"
        summary={summary}
        scopeContext={personalScopeContext()}
      />,
    );

    const saveButton = await screen.findByRole("button", { name: /save draft/i });
    await user.click(saveButton);
    await waitFor(() => expect(createAgentProfileDraft).toHaveBeenCalled());
    const [[callArg]] = (createAgentProfileDraft as ReturnType<typeof vi.fn>).mock.calls;
    expect(callArg.scope).toBe("user");
    expect(callArg.scope_id).toBe("u1");
  });
});
```

If `BehaviorTab`'s real save button label, empty-state copy, or prop shape differs
from what's assumed above, adjust the selectors to match the actual rendered output —
read `frontend/components/agent-studio/behavior-tab.tsx` first to confirm the exact
button text and required props before finalizing this test (this file was read in
full during spec research; its "Save draft" action and `BehaviorTabProps` shape were
confirmed to exist, but re-verify the exact button accessible name before relying on
it in a selector).

- [ ] **Step 4: Run the new test**

Run: `cd frontend && npm test -- personal-tier-smoke --run`
Expected: PASS.

- [ ] **Step 5: Run the full frontend suite + typecheck**

Run: `cd frontend && npm run typecheck && npm test -- --run`
Expected: all PASS, 0 typecheck errors.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/shared/routers/agent_skills.py frontend/components/agent-studio/__tests__/personal-tier-smoke.test.tsx
git commit -m "docs: describe scope-aware RBAC in module docstrings; add personal-tier frontend smoke test"
```
