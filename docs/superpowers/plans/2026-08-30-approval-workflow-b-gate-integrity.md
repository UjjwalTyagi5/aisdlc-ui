# Approval Workflow Sub-Project B: Gate Integrity Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `decide()`'s "is this yours to answer?" check verifies the decider actually
holds their approving role at a binding that covers the SPECIFIC request — not just
that they hold the role name somewhere in the tenant — for every one of the 17
governance request types, with no false negatives introduced for a legitimate decider.

**Architecture:** `decide()`'s only authorization check today is
`decider_role != request["currentApproverRole"]` — a bare role-NAME comparison.
`decider_role` comes from `effective_platform_role()`, which resolves a caller's
single, scope-agnostic "platform role" (highest-standing role held anywhere in the
tenant), discarding exactly the specific-binding information needed to know WHICH
project or business unit that role applies to. This plan adds one new function,
`decider_covers_scope()`, queried directly against `role_bindings` (not derived from
the already-collapsed platform-role string), and calls it alongside the existing
name check in `decide()`. It closes both already-found live bugs
(`cross_bu_assignment` decidable by the wrong unit's BU Admin; `agent_access` stage
two decidable by an unrelated project's delivery-role holder) as a side effect of one
general rule, with no per-type special-casing. One genuine edge case — a
`user_onboarding` request raised by a contributor/developer with no `projectId`,
which still tier-routes to `project_admin` first — needs a documented, narrower-than-
today fallback (any project_admin within the request's own business unit) rather than
an outright refusal, since no single project exists to scope to in that case.

**Tech Stack:** FastAPI + SQLAlchemy (async), pytest (backend, incl. live-DB tests).
This plan is backend-only — no frontend changes; the fix is entirely inside
`decide()`'s existing authorization path, invisible to every client that already calls
it correctly. Live verification against the real running dev stack
(`127.0.0.1:8001` + local Postgres) is a first-class requirement, not optional
polish — see Global Constraints.

**Spec:** `docs/superpowers/specs/2026-08-30-approval-workflow-b-gate-integrity-design.md`

## Global Constraints

- **Every task ends with a LIVE verification**, not just a green `pytest` run: hit the
  real running backend at `127.0.0.1:8001` (restart it cleanly first —
  `cd backend && uv run uvicorn process_api:app --host 127.0.0.1 --port 8001 --reload`,
  backgrounded — rather than trust an already-running instance is current. This exact
  "stale server, edits not taking effect" issue bit multiple tasks during sub-project A;
  do not assume a running server reflects your latest edit without restarting.
- **The new check can only make `decide()` MORE restrictive, never less.** Every task
  that changes behavior must include a test proving the CORRECT decider (the request's
  own project's Project Admin, its own workspace's BU Admin, org_admin) is still able
  to decide — a false negative here is exactly as bad a regression as the bypass this
  plan fixes.
- **`org_admin` needs no scope check** — it is tenant-wide by design. Do not add any
  binding lookup for it; the function returns `True` immediately.
- **No per-request-type special-casing.** The whole point of this design is one
  general rule. If a task's test reveals a type that needs its own branch, that is a
  signal the design is wrong for that type, not a cue to add an `if request_type ==`
  branch — stop and reconsider (or, if implementing under subagent-driven-development,
  report `BLOCKED` with the specific type and shape rather than patching around it).
- Every backend test that touches `governance_requests`/`role_bindings` uses this
  repo's live-DB convention: `get_db_session_for_tenant`/`get_db_session_superuser` +
  raw `text()` INSERTs + a random UUID tenant per test (the `org`/`_bind` fixtures
  already in `backend/tests/test_governance_requests.py` — reuse them, do not
  reinvent). Never mock the database.
- `live_binding()` (`shared/authz/read_scope.py`) is the ONE existing helper for
  "this role_bindings row is still active, not expired" — reuse it verbatim in every
  new query; do not write `status = 'active'` by hand a second time.
- `ROLE_SCOPE` (`shared/authz/permissions.py:43`) already maps each role to its
  natural `scope_kind` (`org_admin` → `"organization"`, `bu_admin` →
  `"business_unit"`, `project_admin` and every delivery role → `"project"`) — key the
  new function off this existing table, not a second hand-rolled mapping.

---

### Task 1: Audit — confirm every request type's scope-field assumptions hold

**Why first:** this changes a single, shared, security-critical function every one of
the 17 request types funnels through. Establishing which types can reach
`project_admin`/`bu_admin` as `currentApproverRole` WITHOUT a `projectId` (the one
case the design needs a fallback for) belongs before the check goes live, not
discovered afterward — the same "ground truth first" discipline sub-project A's
Task 1 used.

**Files:**
- Create: `backend/.superpowers-findings/2026-08-30-gate-scope-audit.md`

**Interfaces:**
- Produces: a findings doc later tasks and the final review reference — no code.

- [ ] **Step 1: Enumerate every (request_type, approver_role) pairing**

Read `backend/shared/governance/routing.py` in full: `GOVERNANCE_APPROVER_ROLE`,
`REQUEST_ESCALATION_CHAIN`, `TYPE_ROUTED`, `AGENT_OWNER_ROLE`,
`_CONTRIBUTOR_RAISABLE`/`_PROJECT_ADMIN_RAISABLE`/`_BU_ADMIN_RAISABLE`. For each of
the 17 types in `REQUEST_TYPES`, write one row: which role(s) can ever be
`currentApproverRole` for it (across every stage, for `agent_access`), and whether
`ROLE_SCOPE` puts that role at `"organization"` (no check needed),
`"business_unit"`, or `"project"`.

- [ ] **Step 2: For every `"business_unit"`/`"project"` row, confirm the scope field is populated**

`create_request()` (`backend/shared/services/governance_requests.py`) requires
`workspace_id: str` unconditionally — every request has one. Confirm this by
reading the function signature directly. For `projectId`: cross-reference against
`backend/.superpowers-findings/2026-08-29-request-type-baseline-audit.md` (sub-
project A's own live-traced findings for all 17 types) plus a direct read of each
raising UI/route for any type not already covered there. Record, per type, whether
a `project_admin`-tier decision for it can ever occur with no `projectId` set.

Expected finding (confirm, do not just assume): `user_onboarding` is the one type
genuinely raisable with no project in view (it is in `_CONTRIBUTOR_RAISABLE`, tier-
routes to `project_admin` first for a contributor/developer raiser, and its own
effect — `_apply_user_onboarding` — only ever reads `workspaceId`, never
`projectId`, confirming the omission is by design, not an oversight). Note any
OTHER type your audit finds in the same shape — the fallback design (Task 3) does
not care which type hits it, so finding more than one does not change the fix, only
this document's completeness.

- [ ] **Step 3: Write the findings doc**

```markdown
# Gate scope audit — 2026-08-30

Every request type's (approver role, scope_kind) pairing, and whether its scope
field is reliably populated when that role is the decider.

| Type | Approver role(s) | ROLE_SCOPE | Scope field always set? |
|---|---|---|---|
| project_creation | bu_admin | business_unit | Yes — raised against a workspace |
| model_credential | project_admin -> bu_admin -> org_admin | project / business_unit / organization | project_id set when project_admin decides (raised FOR a project); workspace_id always set |
| ... (all 17 types) | | | |
| user_onboarding | project_admin (contributor/developer raiser only) -> bu_admin -> org_admin | project / business_unit / organization | project_id ABSENT when a contributor/developer raises it and project_admin is deciding — the one case needing Task 3's fallback |
| agent_access | project_admin (stage 1) -> AGENT_OWNER_ROLE[phase] (stage 2) | project (both stages) | Yes — always project-scoped by definition |

## Conclusion

[State plainly: does the design in the spec (exact-scope match, with the
project-less fallback for project_admin only) correctly handle every row above? If
a row is found that the design does not handle, STOP here and report it in the
task's DONE_WITH_CONCERNS / BLOCKED status rather than silently proceeding to
Task 2.]
```

- [ ] **Step 4: Commit**

```bash
git add backend/.superpowers-findings/2026-08-30-gate-scope-audit.md
git commit -m "docs: audit every request type's decider-scope assumptions before the gate-integrity fix lands"
```

---

### Task 2: `decider_covers_scope` — implemented and unit-tested, not yet wired into `decide()`

**Files:**
- Modify: `backend/shared/services/governance_requests.py`
- Test: `backend/tests/test_governance_requests.py` (extend)

**Interfaces:**
- Consumes: `ROLE_SCOPE` (`shared/authz/permissions.py`, already imported in this
  file at line 59), `live_binding()` (`shared/authz/read_scope.py` — new import
  needed), `datetime`/`timezone` (already imported at line 53).
- Produces: `async def decider_covers_scope(db: AsyncSession, *, decider_id: str,
  role: str, request: dict[str, Any]) -> bool` — Task 3 wires this into `decide()`.

- [ ] **Step 1: Write the failing tests**

Add near the other service-layer tests in `test_governance_requests.py` (the file
already imports `svc` as `shared.services.governance_requests`):

```python
@pytest.mark.asyncio
async def test_decider_covers_scope_org_admin_always_true(org):
    """org_admin is tenant-wide by design — no binding needed at all."""
    assert await svc.decider_covers_scope(
        await _open_session(org), decider_id="anyone", role="org_admin",
        request={"workspaceId": org["bu"], "projectId": org["project"]},
    )


@pytest.mark.asyncio
async def test_decider_covers_scope_bu_admin_matches_own_unit(org):
    admin = f"bu-{_uuid.uuid4()}"
    await _bind(org, admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])
    async with get_db_session_for_tenant(org["org"]) as s:
        assert await svc.decider_covers_scope(
            s, decider_id=admin, role="bu_admin",
            request={"workspaceId": org["bu"]},
        )


@pytest.mark.asyncio
async def test_decider_covers_scope_bu_admin_refuses_other_unit(org):
    """The exact bug this plan fixes for cross_bu_assignment: a bu_admin bound to
    a DIFFERENT business unit must not cover this request."""
    admin = f"bu-{_uuid.uuid4()}"
    other_bu = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'lending', 'Lending')"
        ), {"i": other_bu, "o": org["org"]})
    await _bind(org, admin, "bu_admin", scope_kind="business_unit", scope_id=other_bu)
    async with get_db_session_for_tenant(org["org"]) as s:
        assert not await svc.decider_covers_scope(
            s, decider_id=admin, role="bu_admin",
            request={"workspaceId": org["bu"]},
        )


@pytest.mark.asyncio
async def test_decider_covers_scope_project_admin_matches_own_project(org):
    admin = f"pa-{_uuid.uuid4()}"
    await _bind(org, admin, "project_admin", scope_kind="project", scope_id=org["project"])
    async with get_db_session_for_tenant(org["org"]) as s:
        assert await svc.decider_covers_scope(
            s, decider_id=admin, role="project_admin",
            request={"workspaceId": org["bu"], "projectId": org["project"]},
        )


@pytest.mark.asyncio
async def test_decider_covers_scope_project_admin_refuses_other_project(org):
    """The exact bug this plan fixes for agent_access stage two: a project_admin
    (or delivery role) bound to a DIFFERENT project must not cover this request."""
    admin = f"pa-{_uuid.uuid4()}"
    other_project = str(_uuid.uuid4())
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind) "
            "VALUES (:i, :w, :t, 'Other project', 'github')"
        ), {"i": other_project, "w": org["bu"], "t": org["org"]})
    await _bind(org, admin, "project_admin", scope_kind="project", scope_id=other_project)
    async with get_db_session_for_tenant(org["org"]) as s:
        assert not await svc.decider_covers_scope(
            s, decider_id=admin, role="project_admin",
            request={"workspaceId": org["bu"], "projectId": org["project"]},
        )


@pytest.mark.asyncio
async def test_decider_covers_scope_project_less_falls_back_to_own_business_unit(org):
    """user_onboarding's shape: no projectId, project_admin deciding. Falls back
    to any project_admin binding within the request's own business unit."""
    admin = f"pa-{_uuid.uuid4()}"
    await _bind(org, admin, "project_admin", scope_kind="project", scope_id=org["project"])
    async with get_db_session_for_tenant(org["org"]) as s:
        assert await svc.decider_covers_scope(
            s, decider_id=admin, role="project_admin",
            request={"workspaceId": org["bu"]},  # no projectId
        )


@pytest.mark.asyncio
async def test_decider_covers_scope_project_less_fallback_refuses_other_business_unit(org):
    """The fallback must still be scoped — not a blanket pass for project_admin."""
    admin = f"pa-{_uuid.uuid4()}"
    other_bu, other_project = str(_uuid.uuid4()), str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'lending', 'Lending')"
        ), {"i": other_bu, "o": org["org"]})
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind) "
            "VALUES (:i, :w, :t, 'Other unit project', 'github')"
        ), {"i": other_project, "w": other_bu, "t": org["org"]})
    await _bind(org, admin, "project_admin", scope_kind="project", scope_id=other_project)
    async with get_db_session_for_tenant(org["org"]) as s:
        assert not await svc.decider_covers_scope(
            s, decider_id=admin, role="project_admin",
            request={"workspaceId": org["bu"]},  # no projectId, and admin is in a DIFFERENT unit
        )
```

`_open_session` does not exist yet — the first test needs a bare session for the
`org_admin` no-op path, which touches no tables. Add this tiny helper right above
the test class/functions it is used by (it does not need a fixture — it just opens
one session and lets the caller use it inline).

**Scope this helper NARROWLY to the org_admin no-op case only.** It returns an
ALREADY-CLOSED session (see the note right below) — correct only because
`decider_covers_scope`'s `org_admin` branch never touches the database. Do not
reuse `_open_session` anywhere a real write or read needs to happen (Task 3's
`create_request` calls, for instance, need a genuinely open `async with
get_db_session_for_tenant(...) as s:` block around them, not this helper) — this
was a real mistake caught and fixed during this plan's own self-review, worth
naming explicitly so it is not repeated during implementation.

```python
async def _open_session(org: dict):
    async with get_db_session_for_tenant(org["org"]) as s:
        return s
```

Wait — an `async with` block closes its session on exit, so this returns a
closed session, which will break the `org_admin` test the moment `decider_covers_scope`
touches it. Since `decider_covers_scope` returns `True` immediately for `org_admin`
WITHOUT touching the database at all (per Task 3's implementation below — the
`scope_kind == "organization"` branch returns before any `db.execute` call), passing a
closed session is actually fine for this one test — it proves the function does not
even try to query for `org_admin`. Do not "fix" this by opening a session inside a
`with` block for that test; a closed session that would raise the moment it was
touched is the whole point of this test's shape. If a future edit makes `org_admin`
try to query the database, this test failing with a "connection closed" error IS the
regression signal working correctly.

- [ ] **Step 2: Run, confirm all seven fail (function does not exist yet)**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k decider_covers_scope -v`
Expected: FAIL — `AttributeError: module 'shared.services.governance_requests' has no attribute 'decider_covers_scope'`.

- [ ] **Step 3: Implement `decider_covers_scope`**

Add the new import at the top of `backend/shared/services/governance_requests.py`
(alongside the existing imports at lines 59-62):

```python
from shared.authz.read_scope import live_binding
```

Add the function directly above `decide()` (currently starting at line 577):

```python
async def decider_covers_scope(
    db: AsyncSession, *, decider_id: str, role: str, request: dict[str, Any]
) -> bool:
    """Does `decider_id`'s own `role` binding actually cover THIS request's scope?

    `decide()`'s existing check (`decider_role != request["currentApproverRole"]`)
    only confirms the decider holds the right role NAME somewhere in the tenant —
    `effective_platform_role()` collapses every binding a person holds into one
    "highest standing wins" string with no project/workspace information at all.
    This is the second half: does the decider hold THAT role at a binding that
    actually covers the project or business unit this request names, queried
    directly against role_bindings (never derived from the already-collapsed
    platform-role string, which has thrown that information away by the time it
    reaches here).

    Keyed off ROLE_SCOPE (shared/authz/permissions.py) — the natural scope_kind
    each role is normally bound at — rather than a second, hand-rolled mapping.
    org_admin's natural scope is "organization": tenant-wide by design, always
    covers everything, no query needed.

    FAILS CLOSED for anything ROLE_SCOPE does not resolve to organization/
    business_unit/project (defensive only — `currentApproverRole` is always
    org_admin, bu_admin, project_admin, or an AGENT_OWNER_ROLE delivery role in
    practice, never "custom" or "scrum_master").

    project_admin (and every delivery role — AGENT_OWNER_ROLE names one for
    agent_access stage two) falls back to ANY live binding of `role` within the
    request's own business unit when the request names no specific project at
    all. `user_onboarding` is the one type that reaches this today: it is
    raisable by a contributor/developer, tier-routes to project_admin first, and
    genuinely carries no projectId — onboarding is a business-unit-level act.
    Refusing outright here would make that request permanently undecidable by
    anyone, a real regression this function must not cause. The fallback is
    still strictly narrower than the unscoped check it replaces (rules out every
    OTHER business unit), just not narrowed to one project when no single
    project exists to narrow to.
    """
    scope_kind = ROLE_SCOPE.get(role)
    if scope_kind == "organization":
        return True
    now = datetime.now(tz=timezone.utc)
    if scope_kind == "business_unit":
        workspace_id = request.get("workspaceId")
        if not workspace_id:
            return False
        row = (
            await db.execute(
                text(
                    f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                    "  AND rb.role_name = :role AND rb.scope_kind = 'business_unit' "
                    "  AND rb.scope_id = CAST(:sid AS uuid)"
                ),
                {"u": decider_id, "role": role, "sid": workspace_id, "now": now},
            )
        ).first()
        return row is not None
    if scope_kind == "project":
        project_id = request.get("projectId")
        if project_id:
            row = (
                await db.execute(
                    text(
                        f"SELECT 1 FROM role_bindings rb WHERE {live_binding()} "
                        "  AND rb.role_name = :role AND rb.scope_kind = 'project' "
                        "  AND rb.scope_id = CAST(:sid AS uuid)"
                    ),
                    {"u": decider_id, "role": role, "sid": project_id, "now": now},
                )
            ).first()
            return row is not None
        workspace_id = request.get("workspaceId")
        if not workspace_id:
            return False
        row = (
            await db.execute(
                text(
                    "SELECT 1 FROM role_bindings rb "
                    "  JOIN projects p ON p.id = rb.scope_id AND rb.scope_kind = 'project' "
                    f"WHERE {live_binding()} AND rb.role_name = :role "
                    "  AND p.workspace_id = CAST(:wid AS uuid)"
                ),
                {"u": decider_id, "role": role, "wid": workspace_id, "now": now},
            )
        ).first()
        return row is not None
    return False
```

- [ ] **Step 4: Run, confirm all seven pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k decider_covers_scope -v`
Expected: 7 PASS.

- [ ] **Step 5: Live-verify**

This function is not yet called from any HTTP path (Task 3 wires it in), so
"live-verify" here means confirming it behaves correctly against REAL data rather
than only the `org` fixture's synthetic tenant. Restart the backend cleanly first
(`cd backend && uv run uvicorn process_api:app --host 127.0.0.1 --port 8001
--reload`, backgrounded, after killing whatever currently owns port 8001). Then, in
a throwaway script (written OUTSIDE `backend/` — e.g. under
`.superpowers/sdd/2026-08-30-approval-workflow-b-gate-integrity/` — since the
dev server's `--reload` watches the whole `backend/` directory and a scratch
`.py` file inside it triggers unwanted reloads mid-test; add `backend/` to
`sys.path` from the script), call `decider_covers_scope` directly against real
tenant `8d5bd6a3-7e07-46ce-8416-cada90dead79`: confirm it returns `True` for
org_admin `424bd6e3-3b62-496f-928d-7cc8748ad54e` with an empty `request` dict, and
confirm it returns `False` for that same org_admin's id if you pass `role="bu_admin"`
(they hold no such binding) — proving the function reads real bindings, not a
hardcoded truth table. Clean up nothing (read-only against real data).

- [ ] **Step 6: Commit**

```bash
git add backend/shared/services/governance_requests.py backend/tests/test_governance_requests.py
git commit -m "feat: decider_covers_scope — verify a decider's role binding covers the request's own project/business unit"
```

---

### Task 3: Wire `decider_covers_scope` into `decide()` — the actual authorization fix

**Files:**
- Modify: `backend/shared/services/governance_requests.py:611-616`
- Test: `backend/tests/test_governance_requests.py` (extend)

**Interfaces:**
- Consumes: `decider_covers_scope` (Task 2).
- Produces: `decide()`'s hardened check — every later task and the final review
  build on this being live.

- [ ] **Step 1: Write the two failing regression tests (the already-known bugs)**

```python
@pytest.mark.asyncio
async def test_cross_bu_assignment_refuses_the_wrong_units_bu_admin(org):
    """Finding 5 (sub-project A's baseline audit), closed: a bu_admin of a
    DIFFERENT business unit than the one the request names must not be able to
    decide it, even though the role NAME matches."""
    other_bu = str(_uuid.uuid4())
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO workspaces (id, organization_id, slug, display_name) "
            "VALUES (:i, :o, 'lending', 'Lending')"
        ), {"i": other_bu, "o": org["org"]})
    wrong_admin = f"wrong-bu-{_uuid.uuid4()}"
    right_admin = f"right-bu-{_uuid.uuid4()}"
    await _bind(org, wrong_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])
    await _bind(org, right_admin, "bu_admin", scope_kind="business_unit", scope_id=other_bu)
    project_admin = f"pa-{_uuid.uuid4()}"
    await _bind(org, project_admin, "project_admin", scope_kind="project", scope_id=org["project"])

    async with get_db_session_for_tenant(org["org"]) as s:
        raised = await svc.create_request(
            s, tenant_id=org["org"], initiator_id=project_admin,
            initiator_name="PA", initiator_role="project_admin", request_type="cross_bu_assignment",
            title="Borrow a Lending contributor", description="Need help for a sprint.",
            workspace_id=other_bu, target_ref="someone",
            payload={"userId": "someone", "email": "someone@example.com"},
            system_raised=True,
        )
    request_id = raised["id"]

    # wrong_admin (the REQUESTING project's own unit's admin, not the LENDING
    # unit's) must be refused even though their role name is bu_admin and the
    # role-name check alone would have let them through before this task.
    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(NotYourQueue):
            await svc.decide(
                s, request_id=request_id, decider_id=wrong_admin, decider_name="Wrong",
                decider_role="bu_admin", decision="approve",
            )

    # right_admin (Lending's own bu_admin, the unit actually named by workspace_id)
    # must still be able to decide it — the false-negative safety net.
    async with get_db_session_for_tenant(org["org"]) as s:
        decided = await svc.decide(
            s, request_id=request_id, decider_id=right_admin, decider_name="Right",
            decider_role="bu_admin", decision="approve",
        )
    assert decided["status"] == "approved"


@pytest.mark.asyncio
async def test_agent_access_stage_two_refuses_the_wrong_projects_owner(org):
    """Finding 4 / Task 8's own scoping gap (sub-project A), closed: an architect
    bound to a DIFFERENT project than the request names must not be able to
    decide its design-phase stage two, even though the role name matches."""
    other_project = str(_uuid.uuid4())
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind) "
            "VALUES (:i, :w, :t, 'Other project', 'github')"
        ), {"i": other_project, "w": org["bu"], "t": org["org"]})
    ba = f"ba-{_uuid.uuid4()}"
    project_admin = f"pa-{_uuid.uuid4()}"
    wrong_architect = f"wrong-arch-{_uuid.uuid4()}"
    right_architect = f"right-arch-{_uuid.uuid4()}"
    await _bind(org, ba, "ba", scope_kind="project", scope_id=org["project"])
    await _bind(org, project_admin, "project_admin", scope_kind="project", scope_id=org["project"])
    await _bind(org, wrong_architect, "architect", scope_kind="project", scope_id=other_project)
    await _bind(org, right_architect, "architect", scope_kind="project", scope_id=org["project"])

    async with get_db_session_for_tenant(org["org"]) as s:
        raised = await svc.create_request(
            s, tenant_id=org["org"], initiator_id=ba,
            initiator_name="BA", initiator_role="ba", request_type="agent_access",
            title="Design agent access", description="Covering while Architect is out.",
            workspace_id=org["bu"], project_id=org["project"], phase="design",
        )
    async with get_db_session_for_tenant(org["org"]) as s:
        await svc.decide(
            s, request_id=raised["id"], decider_id=project_admin, decider_name="PA",
            decider_role="project_admin", decision="approve",
        )

    async with get_db_session_for_tenant(org["org"]) as s:
        with pytest.raises(NotYourQueue):
            await svc.decide(
                s, request_id=raised["id"], decider_id=wrong_architect, decider_name="Wrong",
                decider_role="architect", decision="approve",
            )

    async with get_db_session_for_tenant(org["org"]) as s:
        decided = await svc.decide(
            s, request_id=raised["id"], decider_id=right_architect, decider_name="Right",
            decider_role="architect", decision="approve",
        )
    assert decided["status"] == "approved"
```

Check `create_request`'s exact return shape and parameter names against the CURRENT
file before using them verbatim above — it has been touched by sub-project A's
Tasks 2, 3, 5, 8 and this plan's illustrative call may have drifted from the real
signature by the time you implement this. Same caution for `_bind`'s exact
parameter names (already used identically in Task 2's tests above, so if those
passed, this shape is confirmed correct).

- [ ] **Step 2: Run, confirm both fail at the "wrong decider" assertion**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k "refuses_the_wrong" -v`
Expected: FAIL — both `wrong_admin`/`wrong_architect` currently succeed (no
`NotYourQueue` raised), because the role-name-only check does not catch them yet.

- [ ] **Step 3: Wire the check into `decide()`**

In `backend/shared/services/governance_requests.py`, the existing check at lines
611-616 reads:

```python
    # 3 ── is it yours to answer?
    if decider_role != request["currentApproverRole"]:
        raise NotYourQueue(
            "This request is waiting on the "
            f"{(request['currentApproverRole'] or 'nobody').replace('_', ' ')}."
        )
```

Change to:

```python
    # 3 ── is it yours to answer?
    if decider_role != request["currentApproverRole"]:
        raise NotYourQueue(
            "This request is waiting on the "
            f"{(request['currentApproverRole'] or 'nobody').replace('_', ' ')}."
        )
    # 3b ── AND does your OWN binding actually cover it, not just your role name?
    # Same exception, same message: from the caller's point of view this is a
    # stricter form of "not your queue," not a new error class to learn.
    if not await decider_covers_scope(
        db, decider_id=decider_id, role=decider_role, request=request
    ):
        raise NotYourQueue(
            "This request is waiting on the "
            f"{(request['currentApproverRole'] or 'nobody').replace('_', ' ')}."
        )
```

Read the surrounding function (lines 577-620ish, may have shifted slightly since
this plan was written — Task 6/8's edits from sub-project A already touched
nearby code) to confirm this is still the exact right insertion point, immediately
after the existing role-name check and before `now = datetime.now(tz=timezone.utc)`.

- [ ] **Step 4: Run, confirm both pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k "refuses_the_wrong" -v`
Expected: 2 PASS.

- [ ] **Step 5: Run the full governance test suite — confirm no regressions**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py tests/test_enterprise_rbac_catalog.py tests/test_governance_routing.py -v`
Expected: every existing test still passes. Every existing test that decides a
request uses the CORRECT decider for that request (that is what they are testing),
so none of them should newly fail — if one does, it is either revealing a real gap
in Task 1's audit (a type/role combination that does not actually have the scope
field the new check expects) or a bug in the wiring; do not silence the failing
test to make the suite green.

- [ ] **Step 6: Live-verify both closed bugs against the real running backend**

Restart the backend cleanly (kill whatever owns port 8001 first). As real data,
reproduce exactly what sub-project A's Task 1 baseline audit already reproduced
once (its findings doc has the exact live-trace steps for both bugs) — but this
time confirm the WRONG decider now gets `403`/`409`-shaped refusal (whatever HTTP
status `NotYourQueue` maps to — check `_http()`'s exception mapping in
`shared/routers/governance_requests.py` if unsure) instead of `200`, and the RIGHT
decider still gets `200`. Use tenant `8d5bd6a3-7e07-46ce-8416-cada90dead79` and
its real business units/projects/users (the same ids used throughout sub-project A
— reread its plan/ledger for the specific known-good ids if you need them, or
query fresh ones directly). Confirm via a direct DB query afterward that the wrong
decider's attempt produced NO write (no `cross_bu_grants` row, no
`role_bindings.extra_agents` change) and the right decider's did.

- [ ] **Step 7: Commit**

```bash
git add backend/shared/services/governance_requests.py backend/tests/test_governance_requests.py
git commit -m "fix: decide() now verifies the decider's role binding covers the request's own scope, not just the role name"
```

---

### Task 4: Regression coverage for a third tier-routed type, the project-less fallback, and org_admin

**Files:**
- Test: `backend/tests/test_governance_requests.py` (extend)

**Interfaces:**
- Consumes: `decider_covers_scope` (Task 2), the wired check (Task 3).
- Produces: nothing new for later tasks — this is the spec's remaining required
  coverage (§7, bullets 2/4/5), closing out this sub-project's test list.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_connector_access_refuses_a_project_admin_from_another_project(org):
    """A THIRD type, not one of the two already-known bugs — proves the fix is
    genuinely general, not narrowly patching cross_bu_assignment and agent_access."""
    other_project = str(_uuid.uuid4())
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO projects (id, workspace_id, tenant_id, display_name, provider_kind) "
            "VALUES (:i, :w, :t, 'Other project', 'github')"
        ), {"i": other_project, "w": org["bu"], "t": org["org"]})
        await s.execute(text(
            "INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id) "
            "VALUES (CAST(:t AS uuid), 'connector', 'slack', CAST(:w AS uuid))"
        ), {"t": org["org"], "w": org["bu"]})
    dev = f"dev-{_uuid.uuid4()}"
    wrong_pa = f"wrong-pa-{_uuid.uuid4()}"
    right_pa = f"right-pa-{_uuid.uuid4()}"
    await _bind(org, dev, "developer", scope_kind="project", scope_id=org["project"])
    await _bind(org, wrong_pa, "project_admin", scope_kind="project", scope_id=other_project)
    await _bind(org, right_pa, "project_admin", scope_kind="project", scope_id=org["project"])

    c = TestClient(process_api.app)
    dev_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=dev, tenant_id=org["org"], permissions=["artifact:view", "run:create"],
    )}
    raised = c.post(
        "/governance-approvals", headers=dev_headers,
        json={
            "type": "connector_access", "title": "Slack access", "description": "For releases.",
            "priority": "normal", "workspaceId": org["bu"], "projectId": org["project"],
            "targetId": "slack", "accessLevel": "write",
        },
    )
    assert raised.status_code == 201, raised.text

    wrong_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=wrong_pa, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    wrong_decide = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=wrong_headers,
        json={"decision": "approve"},
    )
    assert wrong_decide.status_code >= 400, wrong_decide.text

    right_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=right_pa, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    right_decide = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=right_headers,
        json={"decision": "approve"},
    )
    assert right_decide.status_code == 200, right_decide.text


@pytest.mark.asyncio
async def test_user_onboarding_project_less_decision_falls_back_to_the_business_unit(org):
    """Closes spec §7's project-less-fallback requirement. Approving at ANY tier
    is a TERMINAL decision for a plain tier-routed type (unlike agent_access's
    special-cased two-stage auto-advance) — this test decides the request once,
    at project_admin, and checks only that the decision itself was authorized."""
    contributor = f"contrib-{_uuid.uuid4()}"
    await _bind(org, contributor, "contributor", scope_kind="business_unit", scope_id=org["bu"])
    same_unit_pa = f"pa-{_uuid.uuid4()}"
    await _bind(org, same_unit_pa, "project_admin", scope_kind="project", scope_id=org["project"])

    c = TestClient(process_api.app)
    contrib_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=contributor, tenant_id=org["org"], permissions=["artifact:view", "member:manage"],
    )}
    raised = c.post(
        "/governance-approvals", headers=contrib_headers,
        json={
            "type": "user_onboarding", "title": "Onboard someone", "description": "New QA.",
            "priority": "normal", "workspaceId": org["bu"], "onboardEmail": "gate-b-verify@example.invalid",
            # deliberately NO projectId — a contributor raising user_onboarding never has
            # one, matching Task 1's audited shape.
        },
    )
    assert raised.status_code == 201, raised.text
    assert raised.json()["currentApproverRole"] == "project_admin"

    pa_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=same_unit_pa, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=pa_headers,
        json={"decision": "approve"},
    )
    # The project-less fallback: same_unit_pa holds no binding on ANY specific
    # project named by this request (there isn't one), but IS a project_admin
    # somewhere inside the request's own business unit — must succeed. (The
    # effect itself is a no-op below org_admin tier — _apply_user_onboarding's
    # own, unrelated, pre-existing design — so this only asserts the DECISION
    # was authorized, not that anyone got onboarded.)
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_user_onboarding_reaches_org_admin_who_needs_no_scope_binding(org):
    """Closes spec §7's org_admin-unscoped requirement, via a REAL climb up the
    tier ladder — escalate (not decide) advances a plain tier-routed type, since
    only agent_access auto-advances on approval. The initiator may escalate their
    own request (governance_requests.py's own router comment: "open to the
    initiator too") — used here rather than adding a second project_admin/bu_admin
    just to escalate their own already-covered tiers."""
    contributor = f"contrib-{_uuid.uuid4()}"
    await _bind(org, contributor, "contributor", scope_kind="business_unit", scope_id=org["bu"])
    org_admin = f"org-{_uuid.uuid4()}"
    await _bind(org, org_admin, "org_admin", scope_kind="organization", scope_id=org["org"])

    c = TestClient(process_api.app)
    contrib_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=contributor, tenant_id=org["org"],
        permissions=["artifact:view", "member:manage", "governance:decide"],
    )}
    raised = c.post(
        "/governance-approvals", headers=contrib_headers,
        json={
            "type": "user_onboarding", "title": "Onboard someone", "description": "New QA.",
            "priority": "normal", "workspaceId": org["bu"], "onboardEmail": "gate-b-verify-2@example.invalid",
        },
    )
    assert raised.status_code == 201, raised.text
    req_id = raised.json()["id"]

    escalate_once = c.post(f"/governance-approvals/{req_id}/escalate", headers=contrib_headers, json={})
    assert escalate_once.status_code == 200, escalate_once.text
    assert escalate_once.json()["currentApproverRole"] == "bu_admin"

    escalate_twice = c.post(f"/governance-approvals/{req_id}/escalate", headers=contrib_headers, json={})
    assert escalate_twice.status_code == 200, escalate_twice.text
    assert escalate_twice.json()["currentApproverRole"] == "org_admin"

    # org_admin: no role_bindings row at all needed beyond what create_access_token
    # already grants via ORG_WIDE_PERMISSIONS — this is the unscoped case, entirely
    # unaffected by this plan's fix.
    org_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=org_admin, tenant_id=org["org"], permissions=["admin:*"],
    )}
    final_decide = c.post(
        f"/governance-approvals/{req_id}/decide", headers=org_headers,
        json={"decision": "approve"},
    )
    assert final_decide.status_code == 200, final_decide.text
```

Confirm `user_onboarding`'s real escalation shape (does a contributor's raise
really land on `project_admin` first, then `bu_admin`, then `org_admin` — three
hops, matching `REQUEST_ESCALATION_CHAIN`?) against the CURRENT `routing.py`
before trusting the `currentApproverRole`/escalation assertions above verbatim;
adjust them to match reality if routing has shifted since this plan was written,
but do not change what is being tested (the project-less fallback succeeding at
project_admin; a real climb to org_admin who then needs no scope binding).

- [ ] **Step 2: Run, confirm each fails or errors appropriately**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k "connector_access_refuses_a_project_admin or user_onboarding_project_less_decision or user_onboarding_reaches_org_admin" -v`
Expected: the `connector_access` test should already PASS if Task 3 landed
correctly (this task adds coverage for a type Task 3 did not itself test, it does
not change behavior) — if it fails, Task 3's fix has a gap, stop and fix Task 3
first rather than patching around it here. The `user_onboarding_project_less_decision`
test should also already pass if Task 1's audit and Task 2's fallback are
correct; if it fails, the project-less fallback has a bug — this is the one
genuinely new behavior this plan's design added beyond the literal bug reports,
so treat a failure here as seriously as Task 3's own regression tests. The
`user_onboarding_reaches_org_admin` test exercises `/escalate`, not `/decide`,
for its first two steps — it should pass regardless of this plan's changes (this
plan never touches `escalate()`), and only its FINAL `/decide` call is actually
new-behavior-relevant; a failure there points at the `org_admin` branch of
`decider_covers_scope`, not at escalation.

- [ ] **Step 3: Run the full suite one more time**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py tests/test_enterprise_rbac_catalog.py tests/test_governance_routing.py -v`
Expected: PASS, no regressions.

- [ ] **Step 4: Live-verify**

Restart the backend cleanly. Live-run the `user_onboarding` project-less fallback
scenario against real tenant `8d5bd6a3-7e07-46ce-8416-cada90dead79` data (a real
contributor, a real project_admin who administers SOME project in the same
business unit but not the specific one — there is not one, which is the point —
confirm the decide succeeds), and confirm a project_admin from a DIFFERENT real
business unit in the same tenant gets refused for the same request. Clean up any
scratch rows/requests created.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_governance_requests.py
git commit -m "test: cover connector_access cross-project refusal, user_onboarding's project-less fallback, and org_admin's unscoped path"
```

---
