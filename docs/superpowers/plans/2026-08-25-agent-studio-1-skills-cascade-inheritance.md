# Agent Studio 1: Skills cascade + inheritance visibility — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Skills participate in the same Org → Business Unit → Project cascade
Behavior already has, then make both Behavior and Skills show inherited (not just
own-tier) configuration, so an admin drilling into any tier can see what an ancestor
tier already configured instead of silently inheriting it unlabeled.

**Architecture:** Backend: extend `build_agent_summary` (Behavior) and
`list_skills_merged` (Skills) to accept an ancestor chain and report which scope an
inherited config actually came from; thread new optional query params
(`workspace_id`/`project_id`) through both routers to supply that chain — the frontend
already sends them for Behavior's summary and preview calls today, they're just
ignored. Frontend: `SkillsTab` stops hardcoding `workspace` scope and instead follows
`scopeContext` exactly like `BehaviorTab` already does; `agent-rail.tsx` gets a badge
for inherited configs; `skills-tab.tsx` gets an origin badge + "Override" action.
Behavior's own frontend needs zero changes — it already renders `inherited_from`
correctly, just never receives a populated value today.

**Tech Stack:** FastAPI + SQLAlchemy (async) backend, Next.js + React Query + Zod
frontend, pytest (backend), vitest + React Testing Library + MSW (frontend).

**Spec:** `docs/superpowers/specs/2026-08-25-agent-studio-1-inheritance-visibility-design.md`

## Global Constraints

- No new tables, no migration — every change here is a query/response-shape change.
- `SCOPE_VALUES`/`_validate_scope` still only accept org/workspace/project — `user`
  (personal) scope support is sub-project 2, not touched here.
- New query params (`workspace_id`, `project_id`) are always optional, defaulting to
  `None` — omitting them must degrade to exactly today's behavior (no inheritance
  resolution attempted), never error. This keeps every existing caller/test working
  unchanged.
- Snake_case on the wire everywhere (matches this router pair's established
  convention) — new fields are `inherited_from` (Behavior, already declared in the
  frontend schema) and `origin_scope` (Skills, new).
- Reuse `SCOPE_ORDER`/`SCOPE_VALUES` from `shared/routers/agent_profiles.py` as the
  single source of truth for scope vocabulary — `agent_skills.py` and `skill_store.py`
  already import from there; don't duplicate.

---

### Task 1: Backend — `ancestor_chain()` helper + `build_agent_summary` inheritance

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py`
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py`

**Interfaces:**
- Produces: `ancestor_chain(scope: str, scope_id: str | None, workspace_id: str | None) -> list[tuple[str, str | None]]` — nearest-first ancestor `(scope, scope_id)` pairs. Used by Task 2 (this file) and Task 4 (`skill_store.py`).
- Produces: `build_agent_summary(agent_id: str, rows: Iterable, ancestor_active: list[tuple[str, object]] | None = None) -> dict` — signature changes (new optional 3rd param), return dict gains `inherited_from`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/agent_profiles/test_agent_profiles_router.py`, right after the
existing `# ── build_agent_summary` section (after `test_summary_no_active_pointer`):

```python
def test_summary_inherits_from_nearest_ancestor():
    # Own tier has nothing; workspace has an active row; org also has one — nearest wins.
    ws_row = _profile(1, True, prepend="ws-prepend")
    org_row = _profile(1, True, prepend="org-prepend")
    s = ap.build_agent_summary("design", [], ancestor_active=[("workspace", ws_row), ("org", org_row)])
    assert s["inherited_from"] == "workspace"
    assert s["active"] == {
        "prompt_prepend": "ws-prepend", "prompt_append": "", "output_contract_extra": "",
    }
    assert s["active_version"] is None  # still no OWN version — inheriting, not authored here
    assert s["latest_version"] is None
    assert s["draft_count"] == 0


def test_summary_falls_through_to_farther_ancestor():
    # Nearest ancestor (workspace) has nothing active; org does.
    org_row = _profile(2, True, prepend="org-prepend")
    s = ap.build_agent_summary("design", [], ancestor_active=[("workspace", None), ("org", org_row)])
    assert s["inherited_from"] == "org"
    assert s["active"]["prompt_prepend"] == "org-prepend"


def test_summary_own_tier_active_wins_over_ancestors():
    own_rows = [_profile(1, True, prepend="own-prepend")]
    ancestor = [("workspace", _profile(5, True, prepend="ws-prepend"))]
    s = ap.build_agent_summary("design", own_rows, ancestor_active=ancestor)
    assert s["inherited_from"] is None
    assert s["active"]["prompt_prepend"] == "own-prepend"


def test_summary_no_ancestors_and_no_own_active_stays_null():
    # No ancestor_active passed at all — must match today's exact behavior.
    s = ap.build_agent_summary("design", [])
    assert s == {
        "agent_id": "design", "active_version": None, "latest_version": None,
        "draft_count": 0, "updated_at": None, "active": None, "inherited_from": None,
    }


# ── ancestor_chain ──────────────────────────────────────────────────────────────────

def test_ancestor_chain_org_has_none():
    assert ap.ancestor_chain("org", None, None) == []


def test_ancestor_chain_workspace_is_org():
    assert ap.ancestor_chain("workspace", "ws-1", None) == [("org", None)]


def test_ancestor_chain_project_is_workspace_then_org():
    assert ap.ancestor_chain("project", "proj-1", "ws-1") == [("workspace", "ws-1"), ("org", None)]


def test_ancestor_chain_project_without_workspace_id_degrades_to_none():
    # No workspace_id supplied -> no inheritance resolution attempted, never an error.
    assert ap.ancestor_chain("project", "proj-1", None) == []
```

Also update the TWO pre-existing tests that assert exact dict equality, since the
return shape now always includes `inherited_from`:

```python
def test_summary_empty():
    s = ap.build_agent_summary("design", [])
    assert s == {
        "agent_id": "design", "active_version": None, "latest_version": None,
        "draft_count": 0, "updated_at": None, "active": None, "inherited_from": None,
    }
```

(`test_summary_no_active_pointer` only checks individual keys via `s["active_version"]`
etc — no change needed there.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -v`
Expected: the four new tests FAIL with `AttributeError: module 'shared.routers.agent_profiles' has no attribute 'ancestor_chain'` (or a `TypeError` for the `ancestor_active` kwarg on the pre-existing signature), and `test_summary_empty` FAILS on the dict-equality mismatch (missing `inherited_from` key).

- [ ] **Step 3: Implement `ancestor_chain` and extend `build_agent_summary`**

In `backend/shared/routers/agent_profiles.py`, add `ancestor_chain` right after
`SCOPE_ORDER` (around line 51-52):

```python
def ancestor_chain(scope: str, scope_id: str | None, workspace_id: str | None) -> list[tuple[str, str | None]]:
    """Nearest-first ancestor (scope, scope_id) pairs above `scope`, for inheritance
    resolution. `workspace_id` is the project's own parent BU — required to resolve a
    project's ancestors; omitted, a project-scope request simply gets no ancestors
    back (degrades to no-inheritance behavior, never errors). Shared with
    skill_store.py's list_skills_merged, which needs the identical chain shape.
    """
    if scope == "org":
        return []
    if scope == "workspace":
        return [("org", None)]
    if scope == "project":
        return [("workspace", workspace_id), ("org", None)] if workspace_id else []
    return []
```

Replace `build_agent_summary` (currently lines 143-169) with:

```python
def _active_content(row) -> dict:
    return {
        "prompt_prepend": row.prompt_prepend or "",
        "prompt_append": row.prompt_append or "",
        "output_contract_extra": row.output_contract_extra or "",
    }


def _nearest_ancestor_active(ancestor_active: list[tuple[str, object]]) -> tuple[str | None, object | None]:
    for anc_scope, row in ancestor_active:
        if row is not None:
            return anc_scope, row
    return None, None


def build_agent_summary(
    agent_id: str, rows: Iterable, ancestor_active: list[tuple[str, object]] | None = None,
) -> dict:
    """Summarize all version rows for one agent+scope into the summary[] shape.

    `ancestor_active` (nearest-first) is consulted ONLY when this tier has no active
    row of its own — draft_count/latest_version always describe THIS tier's own
    history, never an ancestor's; only `active`/`inherited_from` fall through.
    """
    rows = list(rows)
    ancestor_active = ancestor_active or []
    if not rows:
        inherited_from, inherited_row = _nearest_ancestor_active(ancestor_active)
        return {
            "agent_id": agent_id, "active_version": None, "latest_version": None,
            "draft_count": 0, "updated_at": None,
            "active": _active_content(inherited_row) if inherited_row is not None else None,
            "inherited_from": inherited_from,
        }
    active_rows = [r for r in rows if r.is_active]
    active = max(active_rows, key=lambda r: r.version) if active_rows else None
    updated_candidates = [r.updated_at for r in rows if r.updated_at is not None]

    inherited_from = None
    active_content = None
    if active is not None:
        active_content = _active_content(active)
    else:
        inherited_from, inherited_row = _nearest_ancestor_active(ancestor_active)
        if inherited_row is not None:
            active_content = _active_content(inherited_row)

    return {
        "agent_id": agent_id,
        "active_version": active.version if active else None,
        "latest_version": max(r.version for r in rows),
        "draft_count": sum(1 for r in rows if not r.is_active),
        "updated_at": _iso(max(updated_candidates)) if updated_candidates else None,
        "active": active_content,
        "inherited_from": inherited_from,
    }
```

Delete the old inline `"active": {...} if active else None,` construction — it's now
`_active_content(active)`, reused for both the own-tier and inherited paths.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -v`
Expected: all PASS, including the pre-existing tests in this file.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/tests/agent_profiles/test_agent_profiles_router.py
git commit -m "feat: resolve inherited_from in build_agent_summary via ancestor_chain"
```

---

### Task 2: Backend — wire `GET /agent-profiles/summary` to resolve inheritance

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py:288-305` (the `get_summary` route)
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py`

**Interfaces:**
- Consumes: `ancestor_chain` and `build_agent_summary` from Task 1.
- Produces: `GET /agent-profiles/summary` now accepts optional `workspace_id` query param (already sent by the frontend for every scope; `project_id` accepted too for symmetry but unused by this route — a project-scope summary's own scope_id IS the project id, so only its ancestor, the workspace, needs a separate id).

Route tests in this file assert structure only (see the file's own docstring — "Route
wiring is asserted structurally... rather than driving HTTP"); this task's route-level
behavior is instead covered by the pure-function tests in Task 1, since `get_summary`'s
new logic is a thin composition of `ancestor_chain` + `build_agent_summary` + one extra
query per ancestor scope, matching the existing thin-route/pure-helper split this
router already established. No new test needed here beyond confirming the route still
imports and the D-05 boot scan (existing structural test in this file) stays green.

- [ ] **Step 1: Run the existing structural route test to confirm current baseline**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -k route -v`
Expected: PASS (baseline, before this task's edit — confirms nothing is broken yet to compare against).

- [ ] **Step 2: Implement the route change**

In `backend/shared/routers/agent_profiles.py`, replace `get_summary` (currently lines
288-305) with:

```python
@agent_profiles_router.get("/summary")
async def get_summary(
    request: Request,
    scope: str,
    scope_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
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
    for anc_scope, anc_scope_id in ancestor_chain(scope, scope_id, workspace_id):
        anc_rows = list((await db.execute(
            select(AgentProfile).where(
                AgentProfile.agent_id.in_(PIPELINE_ORDER),
                AgentProfile.is_active.is_(True),
                *_scope_filters(anc_scope, anc_scope_id),
            )
        )).scalars().all())
        for r in anc_rows:
            ancestor_by_agent.setdefault(r.agent_id, []).append((anc_scope, r))

    return {"agents": [
        build_agent_summary(a, by_agent.get(a, []), ancestor_by_agent.get(a))
        for a in PIPELINE_ORDER
    ]}
```

- [ ] **Step 3: Run the structural route test again + full file**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -v`
Expected: all PASS.

- [ ] **Step 4: Manual live-DB smoke check**

This route has no live-DB test in this repo (matches the file's own documented
philosophy — pure-helper coverage instead). Confirm manually against the running dev
backend: start it if not already running, then:

```bash
curl -s "http://localhost:8001/agent-profiles/summary?scope=workspace&scope_id=<a-real-workspace-uuid>&workspace_id=" -H "Authorization: Bearer <a-valid-token>" | head -c 500
```

Expected: 200 with an `agents` array where every entry now has an `inherited_from` key
(null is fine if nothing's set up yet in that tenant) — confirms the route doesn't 500
on the new code path even with no ancestor data.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_profiles.py
git commit -m "feat: GET /agent-profiles/summary resolves inheritance across the ancestor chain"
```

---

### Task 3: Backend — `POST /agent-profiles/preview` resolves the full chain

**Files:**
- Modify: `backend/shared/routers/agent_profiles.py:543-579` (the `preview` route)
- Test: `backend/tests/agent_profiles/test_agent_profiles_router.py`

**Interfaces:**
- Consumes: `ancestor_chain` from Task 1, `build_preview_layers` (existing, unchanged signature).
- Produces: no change to `POST /agent-profiles/preview`'s request/response shape — `workspace_id`/`project_id` are already in `DraftIn`/`AgentProfileDraftInput` today (both backend and frontend), just unused by the route body. This task makes the route actually use them.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/agent_profiles/test_agent_profiles_router.py`, in the
`build_preview_layers` section:

```python
def test_preview_layers_include_workspace_between_org_and_draft():
    # Org layer, then workspace layer, then draft — nearest-to-farthest is OUTERMOST
    # first in the returned list (mirrors build_preview_layers' existing SCOPE_ORDER
    # sort: org(0) before workspace(1) before project(2), draft always innermost).
    org_row = _scope_row("org", prepend="org-says-hi")
    ws_row = _scope_row("workspace", prepend="ws-says-hi")
    layers = ap.build_preview_layers([org_row, ws_row], "draft-prepend", "", "")
    prepend_sources = [l["source"] for l in layers if "prepend" in l["name"].lower()]
    assert prepend_sources == ["org", "workspace", "draft"]
```

(This test targets the existing, unchanged `build_preview_layers` — it already handles
multiple `lower_rows` correctly by scope order; it just proves that claim explicitly,
since `preview()`'s route body is the only thing that needs to change to actually SUPPLY
a workspace row alongside the org row.)

- [ ] **Step 2: Run test to verify it passes already**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -k test_preview_layers_include_workspace -v`
Expected: PASS already — `build_preview_layers` needs no change; this step just proves
it before touching the route, isolating the coming route-body change as the only real
edit in this task.

- [ ] **Step 3: Implement the route change**

In `backend/shared/routers/agent_profiles.py`, replace the `preview` route body
(currently lines 547-579) — specifically the `lower_rows` resolution block (currently
lines 556-568) — with:

```python
@agent_profiles_router.post(
    "/preview",
    dependencies=[Depends(require_permission("skill:edit"))],
)
async def preview(
    body: DraftIn,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    _tenant_id(request)
    _validate_agent(body.agent_id)
    _validate_scope(body.scope, body.scope_id)

    # Active layers from every ancestor tier the draft would stack on. `DraftIn`
    # doesn't carry workspace_id/project_id (draft-create genuinely doesn't need
    # them — a draft only ever belongs to its own tier), but preview's frontend
    # caller (behavior-tab.tsx) already sends them via a superset body; FastAPI
    # ignores fields DraftIn doesn't declare, so read them off the raw request
    # body instead of widening DraftIn's contract for every other caller.
    raw = await request.json()
    workspace_id = raw.get("workspace_id")
    lower_rows: list = []
    for anc_scope, anc_scope_id in ancestor_chain(body.scope, body.scope_id, workspace_id):
        anc_rows = list((await db.execute(
            select(AgentProfile).where(
                AgentProfile.agent_id == body.agent_id,
                AgentProfile.is_active.is_(True),
                *_scope_filters(anc_scope, anc_scope_id),
            )
        )).scalars().all())
        lower_rows.extend(anc_rows)

    layers = build_preview_layers(
        lower_rows,
        body.prompt_prepend or "",
        body.prompt_append or "",
        body.output_contract_extra or "",
    )
    warnings = lint_profile_fields(
        body.prompt_prepend, body.prompt_append, body.output_contract_extra
    )
    return {"layers": layers, "warnings": warnings}
```

Note: reading `workspace_id` via `await request.json()` rather than adding it to
`DraftIn` is deliberate — `DraftIn` is also `create_draft`'s body model, and a draft
never needs ancestor ids (a draft belongs to exactly one tier, full stop); widening it
would let a caller send meaningless workspace_id/project_id into a draft-create call
that ignores them, which is more confusing than reading the one field `preview`
actually needs directly off the request body it already receives.

- [ ] **Step 4: Run the full test file**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/test_agent_profiles_router.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/shared/routers/agent_profiles.py backend/tests/agent_profiles/test_agent_profiles_router.py
git commit -m "fix: preview resolves the full ancestor chain, not just org"
```

---

### Task 4: Backend — Skills ancestor merge in `list_skills_merged`

**Files:**
- Modify: `backend/shared/services/skill_store.py`
- Test: `backend/tests/agent_skills/test_skill_store_inheritance.py` (new file, live DB)

**Interfaces:**
- Consumes: `shared.routers.agent_profiles.ancestor_chain` (Task 1).
- Produces: `list_skills_merged(tenant_id, agent_id, scope, scope_id, ancestor=None) -> list[dict]` — signature gains optional 5th param; each returned item gains `origin_scope: str` (the tier the item's content actually lives at — for vendor items, always `None`).

This is a live-DB test (unlike Tasks 1-3's pure-function tests) — `list_skills_merged`
does async queries inline, not through an extractable pure helper, and this repo's
established pattern for that shape is a real Postgres test using
`get_db_session_for_tenant` directly (see `backend/tests/test_model_grants.py` for the
established convention: random `uuid.uuid4()` tenant per test, raw `INSERT`s via
`text()` for setup, real service-function calls for the behavior under test).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/agent_skills/test_skill_store_inheritance.py`:

```python
"""Live-DB tests for list_skills_merged's ancestor-chain merge (Agent Studio
sub-project 1). Mirrors the live-DB convention in tests/test_model_grants.py —
random per-test tenant, raw INSERTs for setup, real service calls under test."""
import uuid

import pytest
from sqlalchemy import text

from shared.db import get_db_session_for_tenant
from shared.services import skill_store as store


async def _insert_skill(tenant_id: str, agent_id: str, scope: str, scope_id: str | None,
                         skill_key: str, display_name: str) -> None:
    async with get_db_session_for_tenant(tenant_id) as s:
        await s.execute(
            text(
                "INSERT INTO agent_skills "
                "(id, tenant_id, agent_id, scope, scope_id, skill_key, version, is_active, "
                " display_name, description, when_to_use, body, runtime, origin, created_by) "
                "VALUES (gen_random_uuid(), CAST(:t AS uuid), :a, :sc, "
                " CAST(:sid AS uuid), :k, 1, true, :dn, 'd', 'w', 'body text', 'llm', 'custom', 'tester')"
            ),
            {"t": tenant_id, "a": agent_id, "sc": scope, "sid": scope_id, "k": skill_key, "dn": display_name},
        )


@pytest.mark.asyncio
async def test_own_scope_wins_over_ancestors():
    tenant = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "org", None, "shared-key", "Org Version")
    await _insert_skill(tenant, "requirements", "workspace", ws_id, "shared-key", "BU Version")

    items = await store.list_skills_merged(
        tenant, "requirements", "workspace", ws_id, ancestor=[("org", None)],
    )
    hit = next(i for i in items if i["skill_key"] == "shared-key")
    assert hit["display_name"] == "BU Version"
    assert hit["origin_scope"] == "workspace"


@pytest.mark.asyncio
async def test_falls_through_to_ancestor_when_own_scope_empty():
    tenant = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "org", None, "org-only", "Org Only Skill")

    items = await store.list_skills_merged(
        tenant, "requirements", "workspace", ws_id, ancestor=[("org", None)],
    )
    hit = next(i for i in items if i["skill_key"] == "org-only")
    assert hit["origin_scope"] == "org"


@pytest.mark.asyncio
async def test_no_ancestor_arg_matches_todays_behavior():
    tenant = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "org", None, "org-only", "Org Only Skill")
    ws_id = str(uuid.uuid4())

    items = await store.list_skills_merged(tenant, "requirements", "workspace", ws_id)
    assert not any(i["skill_key"] == "org-only" for i in items)


@pytest.mark.asyncio
async def test_toggle_precedence_nearest_wins_across_scopes():
    tenant = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    await _insert_skill(tenant, "requirements", "org", None, "shared-key", "Org Version")
    # Org-level toggle turns it OFF; this workspace's own toggle turns it back ON —
    # nearest (own scope) should win.
    async with get_db_session_for_tenant(tenant) as s:
        await s.execute(text(
            "INSERT INTO agent_skill_toggles "
            "(id, tenant_id, agent_id, scope, scope_id, origin, skill_key, enabled, updated_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), 'requirements', 'org', NULL, "
            " 'custom', 'shared-key', false, 'tester')"
        ), {"t": tenant})
        await s.execute(text(
            "INSERT INTO agent_skill_toggles "
            "(id, tenant_id, agent_id, scope, scope_id, origin, skill_key, enabled, updated_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), 'requirements', 'workspace', "
            " CAST(:sid AS uuid), 'custom', 'shared-key', true, 'tester')"
        ), {"t": tenant, "sid": ws_id})

    items = await store.list_skills_merged(
        tenant, "requirements", "workspace", ws_id, ancestor=[("org", None)],
    )
    hit = next(i for i in items if i["skill_key"] == "shared-key")
    assert hit["enabled"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/test_skill_store_inheritance.py -v`
Expected: FAIL — `list_skills_merged() got an unexpected keyword argument 'ancestor'`.

- [ ] **Step 3: Implement the ancestor merge + toggle precedence**

In `backend/shared/services/skill_store.py`, add near the top (after the existing
`_SCOPE_RANK` constant, around line 36):

```python
def _toggle_precedence(
    toggle_rows_with_rank: list[tuple[int, "AgentSkillToggle"]],
) -> dict[tuple[str, str], bool]:
    """(origin, skill_key) -> effective enabled, nearest scope (highest rank) wins.
    Shared shape with resolve_active_skills' own nearest-wins toggle logic, but kept
    as a separate small helper here rather than refactored to share code with that
    function — resolve_active_skills is on the RUNTIME path (every agent turn) and
    has no test coverage of its own yet; extending its signature to serve this
    management-list use case is a bigger, riskier change than this feature needs.
    """
    best: dict[tuple[str, str], tuple[int, bool]] = {}
    for rank, t in toggle_rows_with_rank:
        k = (t.origin, t.skill_key)
        cur = best.get(k)
        if cur is None or rank > cur[0]:
            best[k] = (rank, bool(t.enabled))
    return {k: v[1] for k, v in best.items()}
```

Replace `list_skills_merged` (currently lines 220-274) with:

```python
async def list_skills_merged(tenant_id, agent_id, scope, scope_id, ancestor=None) -> list[dict]:
    """Management list for one scope: vendor skills + custom skills authored here OR
    inherited from an ancestor tier (nearest wins per skill_key), each with its
    effective enabled flag (nearest applicable toggle wins, own scope included).
    Fail-soft to [].

    `ancestor`: nearest-first [(scope, scope_id), ...] above `scope` — see
    shared.routers.agent_profiles.ancestor_chain. None/[] (default) matches today's
    exact behavior: no ancestor tiers are consulted at all.
    """
    if not tenant_id:
        return []
    ancestor = ancestor or []
    sid = _as_uuid(scope_id) if scope != "org" else None
    try:
        async with get_db_session_for_tenant(str(tenant_id)) as session:
            custom_rows = list((await session.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == str(agent_id),
                    AgentSkill.scope == scope,
                    AgentSkill.scope_id.is_(None) if sid is None else AgentSkill.scope_id == sid,
                    AgentSkill.deleted_at.is_(None),
                )
            )).scalars().all())
            toggle_rows = list((await session.execute(
                select(AgentSkillToggle).where(
                    AgentSkillToggle.agent_id == str(agent_id),
                    AgentSkillToggle.scope == scope,
                    AgentSkillToggle.scope_id.is_(None) if sid is None else AgentSkillToggle.scope_id == sid,
                )
            )).scalars().all())

            ancestor_custom: list[tuple[str, AgentSkill]] = []
            ancestor_toggle_rows: list[tuple[str, AgentSkillToggle]] = []
            for anc_scope, anc_scope_id in ancestor:
                anc_sid = _as_uuid(anc_scope_id) if anc_scope != "org" else None
                anc_custom_rows = list((await session.execute(
                    select(AgentSkill).where(
                        AgentSkill.agent_id == str(agent_id),
                        AgentSkill.scope == anc_scope,
                        AgentSkill.scope_id.is_(None) if anc_sid is None else AgentSkill.scope_id == anc_sid,
                        AgentSkill.deleted_at.is_(None),
                        AgentSkill.is_active.is_(True),
                    )
                )).scalars().all())
                ancestor_custom.extend((anc_scope, r) for r in anc_custom_rows)
                anc_toggle_rows = list((await session.execute(
                    select(AgentSkillToggle).where(
                        AgentSkillToggle.agent_id == str(agent_id),
                        AgentSkillToggle.scope == anc_scope,
                        AgentSkillToggle.scope_id.is_(None) if anc_sid is None else AgentSkillToggle.scope_id == anc_sid,
                    )
                )).scalars().all())
                ancestor_toggle_rows.extend((anc_scope, r) for r in anc_toggle_rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_skills_merged(%s/%s) failed: %s", tenant_id, agent_id, exc)
        return []

    own_rank = _SCOPE_RANK.get(scope, 0)
    toggle_ranked = [(own_rank, t) for t in toggle_rows] + [
        (_SCOPE_RANK.get(anc_scope, -1), t) for anc_scope, t in ancestor_toggle_rows
    ]
    enabled_by_key = _toggle_precedence(toggle_ranked)

    items: list[dict] = []

    for v in vendor_skills_for(str(agent_id)):
        items.append({
            **_list_item(
                origin="vendor", skill_key=v.skill_key, agent_id=v.agent_id,
                display_name=v.display_name, description=v.description,
                when_to_use=v.when_to_use, runtime=v.runtime,
                enabled=enabled_by_key.get(("vendor", v.skill_key), True),
                version=None, active_version=None,
            ),
            "origin_scope": None,
        })

    # Own scope's active custom rows, tagged with this scope; then ancestor rows for
    # any skill_key not already claimed by the own scope (nearest-first order already
    # guaranteed by the caller's `ancestor` argument, so first-inserted-per-key wins).
    active_by_key: dict[str, tuple[str, AgentSkill]] = {}
    for r in custom_rows:
        if r.is_active:
            active_by_key[r.skill_key] = (scope, r)
    for anc_scope, r in ancestor_custom:
        if r.skill_key not in active_by_key:
            active_by_key[r.skill_key] = (anc_scope, r)

    for skill_key, (origin_scope, r) in active_by_key.items():
        items.append({
            **_list_item(
                origin="custom", skill_key=r.skill_key, agent_id=r.agent_id,
                display_name=r.display_name or r.skill_key, description=r.description or "",
                when_to_use=r.when_to_use or "", runtime=r.runtime or "llm",
                enabled=enabled_by_key.get(("custom", r.skill_key), True),
                version=r.version, active_version=r.version,
            ),
            "origin_scope": origin_scope,
        })

    items.sort(key=lambda i: (i["origin"] != "custom", i["display_name"].lower()))
    return items
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/test_skill_store_inheritance.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full agent_skills test suite to confirm nothing else broke**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/ -v`
Expected: all PASS (the router tests patch `_store()`, so they're unaffected by this
signature change since they never call the real `list_skills_merged`).

- [ ] **Step 6: Commit**

```bash
git add backend/shared/services/skill_store.py backend/tests/agent_skills/test_skill_store_inheritance.py
git commit -m "feat: list_skills_merged merges ancestor-tier custom skills, nearest wins"
```

---

### Task 5: Backend — wire `GET /agent-skills` to pass the ancestor chain

**Files:**
- Modify: `backend/shared/routers/agent_skills.py:228-239` (the `list_skills` route)
- Test: `backend/tests/agent_skills/test_agent_skills_router.py`

**Interfaces:**
- Consumes: `list_skills_merged(..., ancestor=...)` (Task 4), `ancestor_chain` (Task 1, imported from `agent_profiles`).
- Produces: `GET /agent-skills` accepts new optional `workspace_id` query param (`project_id` too, for the same project-ancestor-needs-its-BU reason as Task 2).

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/agent_skills/test_agent_skills_router.py`, in the endpoint-tests
section (alongside this file's other `httpx.AsyncClient` + `sk._store()` monkeypatch
tests — same conventions, reusing this repo's standard `mint_token` fixture already
used the same way throughout `backend/tests/`, e.g.
`mint_token(user_id=..., tenant_id=..., permissions=[...])`):

```python
# ── list_skills threads the ancestor chain through ────────────────────────────────

@pytest.mark.asyncio
async def test_list_skills_passes_ancestor_chain_to_store(monkeypatch, mint_token):
    captured = {}

    async def fake_list_skills_merged(tenant_id, agent_id, scope, scope_id, ancestor=None):
        captured["ancestor"] = ancestor
        return []

    class FakeStore:
        list_skills_merged = staticmethod(fake_list_skills_merged)

    monkeypatch.setattr(sk, "_store", lambda: FakeStore)

    from process_api import app
    token = mint_token(user_id="u1", tenant_id=TENANT_A, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/agent-skills",
            params={"agent_id": "requirements", "scope": "project", "scope_id": "proj-1", "workspace_id": "ws-1"},
            headers=headers,
        )
    assert resp.status_code == 200
    assert captured["ancestor"] == [("workspace", "ws-1"), ("org", None)]


@pytest.mark.asyncio
async def test_list_skills_no_workspace_id_means_no_ancestors(monkeypatch, mint_token):
    captured = {}

    async def fake_list_skills_merged(tenant_id, agent_id, scope, scope_id, ancestor=None):
        captured["ancestor"] = ancestor
        return []

    class FakeStore:
        list_skills_merged = staticmethod(fake_list_skills_merged)

    monkeypatch.setattr(sk, "_store", lambda: FakeStore)

    from process_api import app
    token = mint_token(user_id="u1", tenant_id=TENANT_A, permissions=["artifact:view"])
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/agent-skills",
            params={"agent_id": "requirements", "scope": "workspace", "scope_id": "ws-1"},
            headers=headers,
        )
    assert resp.status_code == 200
    assert captured["ancestor"] == [("org", None)]
```

If `mint_token`'s real signature (check `backend/tests/conftest.py`) differs from
`mint_token(user_id=..., tenant_id=..., permissions=[...])` — e.g. a different keyword
name — adjust the two calls above to match it exactly; every other router test in this
plan (Tasks 1-3) already established this exact fixture is available project-wide, so
this is a signature check, not a new fixture to build.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/test_agent_skills_router.py -k ancestor_chain -v`
Expected: FAIL — route doesn't accept `workspace_id` yet / doesn't pass `ancestor` to the store.

- [ ] **Step 3: Implement the route change**

In `backend/shared/routers/agent_skills.py`, add the import (near the top, alongside
the existing `from shared.routers.agent_profiles import FORBIDDEN_PATTERNS, SCOPE_VALUES`):

```python
from shared.routers.agent_profiles import FORBIDDEN_PATTERNS, SCOPE_VALUES, ancestor_chain
```

Replace `list_skills` (currently lines 228-239) with:

```python
@agent_skills_router.get("")
async def list_skills(
    request: Request,
    agent_id: str,
    scope: str,
    scope_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
):
    tenant_id = _tenant_id(request)
    _validate_agent(agent_id)
    _validate_scope(scope, scope_id)
    ancestor = ancestor_chain(scope, scope_id, workspace_id)
    skills = await _store().list_skills_merged(tenant_id, agent_id, scope, scope_id, ancestor=ancestor)
    return {"skills": skills}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_skills/test_agent_skills_router.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full backend suite for both routers to confirm no regressions**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/agent_profiles/ tests/agent_skills/ -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/routers/agent_skills.py backend/tests/agent_skills/test_agent_skills_router.py
git commit -m "feat: GET /agent-skills threads the ancestor chain to list_skills_merged"
```

---

### Task 6: Frontend — `origin_scope` schema field + chain params in the API client

**Files:**
- Modify: `frontend/lib/schemas/agent-skills.ts`
- Modify: `frontend/lib/api/agent-skills.ts`

**Interfaces:**
- Produces: `SkillListItem.origin_scope: ProfileScope | null`. `listAgentSkills(agentId, scope, scopeId?, chain?)` gains a 4th param, `chain?: { workspaceId?: string | null; projectId?: string | null }`.

- [ ] **Step 1: Update the schema**

In `frontend/lib/schemas/agent-skills.ts`, add `origin_scope` to `SkillListItem`
(after the `active_version` line):

```typescript
export const SkillListItem = z.object({
  origin: SkillOrigin,
  skill_key: z.string(),
  agent_id: z.string(),
  display_name: z.string(),
  description: z.string().nullable(),
  when_to_use: z.string().nullable(),
  runtime: SkillRuntime,
  enabled: z.boolean(),
  editable: z.boolean(),
  deletable: z.boolean(),
  version: z.number().int().nullable(),
  active_version: z.number().int().nullable(),
  /** Which tier this item's content actually lives at — null for vendor skills
   *  (no scope of their own). Differs from the requested scope when the item is
   *  inherited from an ancestor tier rather than authored at this one. */
  origin_scope: SkillScope.nullable(),
});
```

(`SkillScope` is already defined above this in the same file — no new import needed.)

- [ ] **Step 2: Update the API client**

In `frontend/lib/api/agent-skills.ts`, replace `listAgentSkills`:

```typescript
/** Chain ids so the cascade can resolve inheritance past the requested tier —
 *  mirrors ProfileChainIds in lib/api/agent-profiles.ts. */
export interface SkillChainIds {
  workspaceId?: string | null;
  projectId?: string | null;
}

/**
 * Merged vendor + custom skills for an agent at a scope, including any inherited
 * from an ancestor tier (see origin_scope on each item). `scopeId` is required for
 * "workspace"/"project" scope, omit (or pass null) for "org". `chain` supplies the
 * ancestor ids needed to resolve inheritance — omit to get today's exact-scope-only
 * behavior.
 */
export const listAgentSkills = (
  agentId: string,
  scope: SkillScope,
  scopeId?: string | null,
  chain?: SkillChainIds,
) =>
  api("/agent-skills", {
    query: {
      agent_id: agentId,
      scope,
      scope_id: scopeId,
      workspace_id: chain?.workspaceId,
    },
    schema: SkillList,
  });
```

- [ ] **Step 3: Run frontend typecheck**

Run: `cd frontend && npm run typecheck`
Expected: fails at this point — `skills-tab.tsx` still calls `listAgentSkills(agentId, SCOPE, workspaceId)` with the old 3-arg shape, which still typechecks (chain is optional), so actually expect PASS here; the real breakage (if any) will show up once Task 9 changes `SCOPE` to a variable. Run it anyway to confirm a clean baseline before Task 9's larger edit.

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/schemas/agent-skills.ts frontend/lib/api/agent-skills.ts
git commit -m "feat: origin_scope field + ancestor chain param for agent-skills client"
```

---

### Task 7: Frontend — `agent-rail.tsx` shows an inherited badge

**Files:**
- Modify: `frontend/components/agent-studio/agent-rail.tsx`
- Test: new file `frontend/components/agent-studio/__tests__/agent-rail.test.tsx`

**Interfaces:**
- Consumes: `AgentProfileSummaryEntry.inherited_from` (already in the schema, now populated by Task 2).

- [ ] **Step 1: Write the failing test**

Create `frontend/components/agent-studio/__tests__/agent-rail.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { AgentRail } from "../agent-rail";
import type { AgentProfileSummaryEntry } from "@/lib/schemas/agent-profiles";

function entry(overrides: Partial<AgentProfileSummaryEntry>): AgentProfileSummaryEntry {
  return {
    agent_id: "design",
    active_version: null,
    latest_version: null,
    draft_count: 0,
    updated_at: null,
    active: null,
    inherited_from: null,
    ...overrides,
  };
}

describe("AgentRail inheritance badge", () => {
  it("shows an own-tier version badge when active_version is set, no inherited badge", () => {
    render(
      <AgentRail
        agents={[entry({ active_version: 3, latest_version: 3 })]}
        selectedId="design"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(screen.queryByText(/Inherited/)).not.toBeInTheDocument();
  });

  it("shows 'Inherited from Org' when inherited_from is org and nothing is active locally", () => {
    render(
      <AgentRail
        agents={[entry({ inherited_from: "org" })]}
        selectedId="design"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Inherited from Org")).toBeInTheDocument();
  });

  it("shows 'Inherited from Business Unit' for workspace", () => {
    render(
      <AgentRail
        agents={[entry({ inherited_from: "workspace" })]}
        selectedId="design"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Inherited from Business Unit")).toBeInTheDocument();
  });

  it("falls back to a plain 'default' label when there's no active version and nothing inherited", () => {
    render(
      <AgentRail
        agents={[entry({})]}
        selectedId="design"
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("default")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- agent-rail --run`
Expected: FAIL — "Inherited from Org" text not found (current code renders bare "default").

- [ ] **Step 3: Implement the badge**

In `frontend/components/agent-studio/agent-rail.tsx`, add an import and a label map at
the top (after the existing imports):

```tsx
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
```

```tsx
const INHERITED_LABEL: Record<string, string> = {
  org: "Inherited from Org",
  workspace: `Inherited from ${BUSINESS_UNIT_LABEL}`,
  project: "Inherited from Project",
};
```

Replace the badge-rendering block inside the `.map` (currently lines 47-58):

```tsx
{hasActive ? (
  <Badge
    variant={selected ? "info" : "outline"}
    className="shrink-0 font-mono text-[10px]"
  >
    v{agent.active_version}
  </Badge>
) : agent.inherited_from ? (
  <span className="text-muted-foreground/70 shrink-0 text-[11px]">
    {INHERITED_LABEL[agent.inherited_from] ?? "Inherited"}
  </span>
) : hasDraft ? null : (
  <span className="text-muted-foreground/70 shrink-0 text-[11px]">
    default
  </span>
)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- agent-rail --run`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/agent-studio/agent-rail.tsx frontend/components/agent-studio/__tests__/agent-rail.test.tsx
git commit -m "feat: agent rail shows which tier an inherited default came from"
```

---

### Task 8: Frontend — thread chain ids into the Behavior summary/preview calls

**Files:**
- Modify: `frontend/components/agent-studio/agent-studio.tsx`

**Interfaces:**
- Consumes: `chain.workspaceId` (already computed in `agent-studio.tsx`'s existing `chain` object).

Behavior's `previewAgentProfile` call in `behavior-tab.tsx` already sends
`workspace_id`/`project_id` (confirmed while researching this spec — no change needed
there). The one remaining gap: `getAgentProfilesSummary` in `agent-studio.tsx` (line
190) already passes `chain` too — re-verify this is genuinely already correct with a
quick read rather than assuming; if the call site already reads
`getAgentProfilesSummary(tier, scopeId, chain)` exactly as `lib/api/agent-profiles.ts`
expects, this task is a no-op confirmation, not an edit.

- [ ] **Step 1: Verify the existing call site**

Read `frontend/components/agent-studio/agent-studio.tsx` around line 183-191 (the
`summaryQ` `useQuery`) and confirm `getAgentProfilesSummary(tier, scopeId, chain)` is
called with the 3-arg shape `lib/api/agent-profiles.ts`'s `getAgentProfilesSummary`
declares (`scope, scopeId?, chain?`). If it matches exactly (it does, per the file
already read during spec research — `chain.workspaceId`/`chain.projectId`/
`chain.userId` are all threaded through as of the current code), this task needs no
code change.

- [ ] **Step 2: Confirm end-to-end with a manual smoke test**

With the dev backend and frontend both running (Task 2/3's backend changes must be
live), open `/agent-studio` as an Org Admin, publish a Behavior default for any agent
at Org tier, then switch to a Business Unit that has never set its own default for
that agent. Expected: the rail shows "Inherited from Org" for that agent, and opening
it shows the message from `behavior-tab.tsx` lines 315-320 with the inherited content
already in the fields.

- [ ] **Step 3: No commit needed for this task** — it's a verification-only task
confirming Task 2's backend change reaches the UI through code that already existed.

---

### Task 9: Frontend — make `SkillsTab` cascade-aware

**Files:**
- Modify: `frontend/components/agent-studio/agent-editor.tsx`
- Modify: `frontend/components/agent-studio/skills-tab.tsx`
- Test: new file `frontend/components/agent-studio/__tests__/skills-tab.test.tsx`

**Interfaces:**
- Consumes: `ScopeContext` (already exported from `agent-editor.tsx`, already fully populated for every tier).
- Produces: `SkillsTabProps` changes from `{ agentId, agentLabel, workspaceId, workspaceName, canManage }` to `{ agentId, agentLabel, scopeContext }` — callers read `scope`/`scopeId`/`isOwner`/`scopeLabel` off `scopeContext` directly, matching `BehaviorTabProps`'s existing shape.

- [ ] **Step 1: Write the failing tests**

Create `frontend/components/agent-studio/__tests__/skills-tab.test.tsx`. Follow the
MSW-mock + React Testing Library convention already established in
`frontend/components/app/__tests__/add-model-dialog.test.tsx` (read that file first for
the exact `server.use(http.get(...))` / `render` / `userEvent` setup this repo uses) —
concretely:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import { server } from "@/mocks/server"; // match the actual MSW setup import path used by add-model-dialog.test.tsx
import { SkillsTab } from "../skills-tab";
import type { ScopeContext } from "../agent-editor";
import { renderWithProviders } from "@/test/render-with-providers"; // match whatever test-utils wrapper add-model-dialog.test.tsx actually uses for QueryClientProvider

function orgScopeContext(): ScopeContext {
  return {
    scope: "org",
    scopeId: null,
    scopeLabel: "Organization",
    chain: { workspaceId: null, projectId: null, userId: null },
    isOwner: true,
    canPropose: false,
    ownerRoleLabel: "Organization Admin",
  };
}

function workspaceScopeContext(isOwner: boolean): ScopeContext {
  return {
    scope: "workspace",
    scopeId: "ws-1",
    scopeLabel: "Acme BU",
    chain: { workspaceId: "ws-1", projectId: null, userId: null },
    isOwner,
    canPropose: !isOwner,
    ownerRoleLabel: "Business Unit Admin",
    workspaceId: "ws-1",
    workspaceName: "Acme BU",
  };
}

describe("SkillsTab cascade awareness", () => {
  it("requests skills at the org tier when scopeContext.scope is org (was hardcoded to workspace before)", async () => {
    let capturedScope = "";
    let capturedScopeId: string | null = null;
    server.use(
      http.get("*/agent-skills", ({ request }) => {
        const url = new URL(request.url);
        capturedScope = url.searchParams.get("scope") ?? "";
        capturedScopeId = url.searchParams.get("scope_id");
        return HttpResponse.json({ skills: [] });
      }),
    );

    render(
      <SkillsTab agentId="requirements" agentLabel="Requirements" scopeContext={orgScopeContext()} />,
    );

    await waitFor(() => expect(capturedScope).toBe("org"));
    expect(capturedScopeId).toBeNull();
  });

  it("read-only when scopeContext.isOwner is false, not the old flat permission check", async () => {
    server.use(
      http.get("*/agent-skills", () =>
        HttpResponse.json({
          skills: [{
            origin: "custom", skill_key: "k", agent_id: "requirements",
            display_name: "A Skill", description: null, when_to_use: null,
            runtime: "llm", enabled: true, editable: true, deletable: true,
            version: 1, active_version: 1, origin_scope: "workspace",
          }],
        }),
      ),
    );

    render(
      <SkillsTab agentId="requirements" agentLabel="Requirements" scopeContext={workspaceScopeContext(false)} />,
    );

    await screen.findByText("A Skill");
    expect(screen.getByText(/read-only access/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new skill/i })).not.toBeInTheDocument();
  });

  it("shows an origin badge and Override action for an inherited skill", async () => {
    server.use(
      http.get("*/agent-skills", () =>
        HttpResponse.json({
          skills: [{
            origin: "custom", skill_key: "shared-key", agent_id: "requirements",
            display_name: "Org Skill", description: null, when_to_use: null,
            runtime: "llm", enabled: true, editable: true, deletable: true,
            version: 1, active_version: 1, origin_scope: "org",
          }],
        }),
      ),
    );

    render(
      <SkillsTab agentId="requirements" agentLabel="Requirements" scopeContext={workspaceScopeContext(true)} />,
    );

    await screen.findByText("Org Skill");
    expect(screen.getByText(/From Organization/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /override/i })).toBeInTheDocument();
  });
});
```

Before finalizing this file, actually open `add-model-dialog.test.tsx` and correct the
two import paths marked above (`@/mocks/server`, `renderWithProviders`) to match
whatever this repo's real MSW/test-provider setup is called — don't guess; the file
already read during this spec's research shows the real convention, use it verbatim.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- skills-tab --run`
Expected: FAIL — `SkillsTab` doesn't accept a `scopeContext` prop yet (TypeScript error on `workspaceId`/`workspaceName`/`canManage` being required-but-missing), and no "Override"/"From Organization" text exists yet.

- [ ] **Step 3: Update `agent-editor.tsx`**

Replace the `SkillsTab` usage (currently lines 80-90) with:

```tsx
<TabsContent value="skills" className="mt-4">
  {/* key=agentId remounts the tab so dialog/toggle state resets per agent */}
  <SkillsTab
    key={summary.agent_id}
    agentId={summary.agent_id}
    agentLabel={label}
    scopeContext={scopeContext}
  />
</TabsContent>
```

Remove the now-unused `skillsCanManage`/`can`/`useRawSession`/`session` lines (lines
48, 52) and their imports (`useRawSession` from `@/components/auth/session-provider`,
`can` from `@/lib/auth/capabilities`) if nothing else in this file still uses them —
check before removing.

- [ ] **Step 4: Update `skills-tab.tsx`**

Replace the `SkillsTabProps` interface and the top of the `SkillsTab` function
(currently lines 84-121) with:

```tsx
import type { ScopeContext } from "./agent-editor";

const SCOPE_TIER_LABEL: Record<string, string> = {
  org: "Organization",
  workspace: BUSINESS_UNIT_LABEL,
  project: "Project",
  user: "you",
};

export interface SkillsTabProps {
  agentId: string;
  agentLabel: string;
  scopeContext: ScopeContext;
}

/** `"create"`'s `seed` is set only for an Override (a create-at-this-tier copying
 *  an inherited item) — `fromScope` names the ancestor tier it was copied from, for
 *  the dialog's explanatory copy. A plain "New skill" click sets `mode: "create"`
 *  with no `seed`. */
type EditorState =
  | {
      mode: "create";
      seed?: {
        skill_key: string;
        display_name: string;
        description: string;
        when_to_use: string;
        fromScope: string;
      };
    }
  | { mode: "edit"; skill: SkillListItem }
  | null;

/**
 * Cascade-tier skills manager for one agent (Org/Business Unit/Project — see
 * ScopeContext; Personal tier is sub-project 2). Two groups: locked vendor
 * "Built-in" skills (view + toggle) and authored custom skills for the current
 * tier OR inherited from an ancestor tier (origin_scope on each item — see
 * list_skills_merged). Parent remounts this per agent (key=agentId), so
 * dialog/toggle state resets cleanly when switching agents.
 */
export function SkillsTab({ agentId, agentLabel, scopeContext }: SkillsTabProps) {
  const { scope, scopeId, chain, isOwner, scopeLabel } = scopeContext;
  const queryClient = useQueryClient();
  const listKey = qk.agentSkills.list(agentId, scope, scopeId);
  const authoringDisabled = CUSTOM_SKILLS_UNSUPPORTED.has(agentId);
  const canManage = isOwner;
  const tierNoun = SCOPE_TIER_LABEL[scope] ?? scopeLabel;

  const [editor, setEditor] = React.useState<EditorState>(null);
  const [viewing, setViewing] = React.useState<SkillListItem | null>(null);
  const [deleting, setDeleting] = React.useState<SkillListItem | null>(null);

  const skillsQ = useQuery({
    queryKey: listKey,
    queryFn: () => listAgentSkills(agentId, scope, scopeId, { workspaceId: chain.workspaceId }),
    enabled: scopeId !== null || scope === "org",
  });
```

Every other reference to `workspaceId`/`workspaceName` inside the rest of the function
body (the `toggleAgentSkill`/`deleteAgentSkill`/`SkillEditorDialog`/`SkillViewDialog`
call sites, currently using `SCOPE`/`workspaceId` as literal/prop values) becomes
`scope`/`scopeId`; every place the group-header text reads "This Business Unit" becomes
`` `This ${tierNoun}` `` (or, for the `user` tier specifically, "Your personal skills"
— not reachable yet since this task doesn't add `user` to `_validate_scope`, but write
the mapping so sub-project 2 doesn't have to revisit this file for wording). Concretely,
in the JSX:

```tsx
<GroupHeader
  id="custom-heading"
  title={scope === "user" ? "Your personal skills" : `This ${tierNoun}`}
  count={customSkills.length}
/>
```

And `SkillEditorDialog`/`SkillViewDialog` (currently taking `workspaceId`/
`workspaceName` props) become `scope`/`scopeId`/`scopeLabel` props threaded the same
way — update their prop interfaces and every internal `agentId, SCOPE, workspaceId`
call (in `createAgentSkill`/`updateAgentSkill`/`toggleAgentSkill`/`deleteAgentSkill`/
`getAgentSkill`/`listAgentSkillVersions`) to `agentId, scope, scopeId`.

Now add the origin badge + Override action. In `SkillRow`'s call sites (both the vendor
`.map` and the custom `.map`), pass a new `originBadge` prop and render it in `SkillRow`:

```tsx
function SkillRow({
  skill,
  originBadge,
  control,
  actions,
}: {
  skill: SkillListItem;
  originBadge?: React.ReactNode;
  control: React.ReactNode;
  actions: React.ReactNode;
}) {
  return (
    <li className="flex items-center gap-3 px-3 py-2.5">
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{skill.display_name}</span>
          {skill.origin === "vendor" && (
            <Badge variant="secondary" className="shrink-0 text-[10px]">
              Built-in
            </Badge>
          )}
          {skill.runtime === "shell" && (
            <Badge variant="outline" className="border-line-soft shrink-0 text-[10px]">
              shell
            </Badge>
          )}
          {originBadge}
        </div>
        {skill.description && (
          <p className="text-muted-foreground truncate text-xs">{skill.description}</p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">{actions}</div>
      <div className="shrink-0 pl-1">{control}</div>
    </li>
  );
}
```

In the custom-skills `.map` inside `SkillsTab`, compute the badge and an Override
action per item:

```tsx
{customSkills.map((skill) => {
  const inherited = skill.origin_scope !== null && skill.origin_scope !== scope;
  return (
    <SkillRow
      key={skill.skill_key}
      skill={skill}
      originBadge={
        inherited ? (
          <Badge variant="outline" className="border-line-soft shrink-0 text-[10px]">
            From {SCOPE_TIER_LABEL[skill.origin_scope!] ?? skill.origin_scope}
          </Badge>
        ) : undefined
      }
      control={renderSwitch(skill)}
      actions={
        canManage ? (
          inherited ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                setEditor({
                  mode: "create",
                  seed: {
                    skill_key: skill.skill_key,
                    display_name: skill.display_name,
                    description: skill.description ?? "",
                    when_to_use: skill.when_to_use ?? "",
                    fromScope: skill.origin_scope!,
                  },
                })
              }
              aria-label={`Override ${skill.display_name} at ${scopeLabel}`}
            >
              <Pencil className="size-3.5" aria-hidden />
              Override
            </Button>
          ) : (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setEditor({ mode: "edit", skill })}
                aria-label={`Edit ${skill.display_name}`}
              >
                <Pencil className="size-3.5" aria-hidden />
                Edit
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground hover:text-destructive"
                onClick={() => setDeleting(skill)}
                aria-label={`Delete ${skill.display_name}`}
              >
                <Trash2 className="size-3.5" aria-hidden />
              </Button>
            </>
          )
        ) : (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setViewing(skill)}
            aria-label={`View ${skill.display_name}`}
          >
            <Eye className="size-3.5" aria-hidden />
            View
          </Button>
        )
      }
    />
  );
})}
```

`EditorState`'s `"create"` variant (defined earlier in this task's Step 4) already
carries the optional `seed` this call site populates.

In `SkillEditorDialog`'s initial-fields computation (currently `EMPTY_FIELDS` for
create mode), use the seed when present:

```tsx
const [fields, setFields] = React.useState<EditorFields>(() =>
  isEdit
    ? {
        skill_key: state.skill.skill_key,
        display_name: state.skill.display_name,
        description: state.skill.description ?? "",
        when_to_use: state.skill.when_to_use ?? "",
        body: "",
      }
    : state.mode === "create" && state.seed
      ? { ...state.seed, body: "" }
      : EMPTY_FIELDS,
);
```

An override's `body` still starts empty rather than fetching the ancestor's full body
via `getAgentSkill` — deliberate for this task: the list row doesn't carry `body` (only
`get_skill_detail`/the detail endpoint does), and fetching it just to pre-fill one more
field is a reasonable follow-up, not blocking for "visibility" to work. Surface this to
the user explicitly rather than silently shipping half of it unlabeled — add a
one-line note in the dialog, using `state.seed.fromScope` (already populated at the
call site above) to name the ancestor tier:

```tsx
{state.mode === "create" && state.seed && (
  <p className="text-muted-foreground text-xs">
    Overriding &quot;{state.seed.display_name}&quot; from{" "}
    {SCOPE_TIER_LABEL[state.seed.fromScope] ?? "an ancestor tier"}. Its name and
    description are copied below — re-enter the instructions body for your{" "}
    {tierNoun.toLowerCase()}.
  </p>
)}
```

`SkillsTabProps` doesn't expose `tierNoun` to `SkillEditorDialog` today — pass it down
as a new `tierNoun: string` prop from `SkillsTab`'s render of `<SkillEditorDialog
... tierNoun={tierNoun} />`, alongside the existing `agentId`/`agentLabel`/`scope`/
`scopeId`/`scopeLabel` props this step already threads through.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npm test -- skills-tab --run`
Expected: all PASS.

- [ ] **Step 6: Run typecheck and the full frontend suite**

Run: `cd frontend && npm run typecheck && npm test -- --run`
Expected: both clean — this task touches `agent-editor.tsx` and `skills-tab.tsx`, both
without their own pre-existing test files, so no other test file should be affected;
confirm this is actually true by reading the full suite's summary line.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/agent-studio/agent-editor.tsx frontend/components/agent-studio/skills-tab.tsx frontend/components/agent-studio/__tests__/skills-tab.test.tsx
git commit -m "feat: SkillsTab follows the cascade tier instead of hardcoded workspace scope"
```

---

### Task 10: Final verification pass

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: only the pre-existing, already-documented environment/RLS failures (see
`.superpowers/sdd/2026-08-25-agent-studio/progress.md` if that baseline needs
re-establishing — cross-check against a fresh count on `main` if unsure whether a
failure is new or pre-existing, same discipline as this session's earlier merge work).

- [ ] **Step 2: Full frontend suite + typecheck + lint**

Run: `cd frontend && npm run typecheck && npm run lint && npm test -- --run`
Expected: all clean.

- [ ] **Step 3: Manual smoke test in the browser**

With both dev servers running: as an Org Admin, set an org-wide Behavior default and a
custom Skill for one agent. Switch to a Business Unit that has never touched that
agent. Confirm: rail shows "Inherited from Org"; Behavior tab shows the inheritance
message with the org content pre-filled; Skills tab shows the org skill with a "From
Organization" badge and an "Override" button. Click Override, confirm the dialog
pre-fills name/description and lets you type a new body, save it, confirm it now shows
as this BU's own (no badge, "Edit"/"Delete" actions instead of "Override").

- [ ] **Step 4: Update the ledger**

Mark sub-project 1 complete in `.superpowers/sdd/2026-08-25-agent-studio/progress.md`
(spec doc + plan + implementation all checked off), and note anything discovered during
implementation that sub-project 2/3 should know about (e.g. the deferred "fetch full
body on Override" follow-up from Task 9).
