# Approval Workflow Sub-Project A: Effect Reachability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every request type in `routing.REQUEST_TYPES` either really grants the
thing it was approved for, or is a deliberate, documented exception — verified by
tracing an actual UI button through to a database write, not by reading an effect
function in isolation.

**Architecture:** The root defect is plumbing, not missing effect code:
`connector_access` and `model_provider_access` already have `_apply_*`
functions in `shared/governance/effects.py` that are correct in isolation,
but every real UI path that raises them goes through `POST
/governance-approvals`, whose `RequestCreateIn` schema has no field for which
connector/provider is being asked about — so `target_ref`/`payload.targetId`
never arrives and approval always fails with `EffectNotAvailable`. This plan
(1) adds typed target fields to the request schema, mirroring the existing
`phase` field's exact pattern, (2) wires every "Request X" button to send
them — discovering along the way that the UI raising `model_provider_access`
never has a specific connection row id in scope, only a `(provider,
model_id)` pair, so that effect needs a real code change (resolve by
provider kind) on top of the plumbing, not just a reachable `target_ref`
(Task 5), (3) THEN gives `model_credential`, `mcp_server`, and `agent_access`
real effects (now that they're reachable), and (4) builds a dedicated raise
path + effect for `user_onboarding` (which had neither).
`role_assignment` — the fifth type the spec named — turned out on live
tracing to already be fully correct (see Task 10's removal note); no work
was needed. Ordering matters: tasks 2-5
(plumbing + the model_provider_access fix) must land and be verified before
tasks 6-9 (the remaining effects), because those are provably unreachable
without it.

**Tech Stack:** FastAPI + SQLAlchemy (async), Next.js + React Query + Zod,
pytest (backend, incl. live-DB tests), vitest + RTL (frontend). Live verification
against the real running dev stack (`127.0.0.1:8001` + local Postgres) is a
first-class requirement of this plan, not optional polish — see Global
Constraints.

**Spec:** `docs/superpowers/specs/2026-08-29-approval-workflow-a-effect-reachability-design.md`

## Global Constraints

- **Every task ends with a LIVE verification**, not just a green `pytest` run:
  hit the real running backend at `127.0.0.1:8001` (or restart it if not
  running — `cd backend && uv run uvicorn process_api:app --host 127.0.0.1
  --port 8001 --reload`, backgrounded) against the real local Postgres, raise
  the request as the correct role, decide it as the correct role, then
  directly query the database (or the decide response's `effectNote`) to
  confirm the real-world write happened. This is how the central bug in this
  plan was found, and skipping it is exactly the mistake that let
  `connector_access`/`model_provider_access` be marked "working" when they
  were not.
- **`access_request` is out of scope for a real effect, by design** (spec
  §3.4) — it is the platform's generic catch-all with no addressable target.
  Do not touch it in this plan.
- **Sub-projects B (gate integrity), C (new request-button pages), D
  (break-glass) are out of scope.** Do not add new "Request" buttons to pages
  that have none today (e.g. Cost & Budget) — that is sub-project C. Do not
  touch `runs.py`'s agent-gate approval endpoints — that is sub-project B.
- New typed request fields follow the existing `phase` field's exact
  precedent (`RequestCreateIn`/`RequestCreateInput`, merged into `payload`
  inside `service.create_request`) — never a raw passthrough `payload: dict`
  from the client, which would let a client stuff arbitrary keys an effect
  function might trust.
- Every backend test that touches `governance_requests`/`role_bindings`/
  `model_providers`/etc. uses this repo's live-DB convention:
  `get_db_session_for_tenant` + raw `text()` INSERTs + a random UUID tenant
  per test (see `backend/tests/agent_skills/test_skill_store_inheritance.py`,
  `backend/tests/test_model_grants.py`). Never mock the database.
- Reject reasons and error messages follow this repo's established
  `EffectNotAvailable(request_type, "<sentence a person reads>")` shape —
  see any existing `_apply_*` function in `effects.py` for tone (a full
  sentence naming what's missing and, where useful, what to do instead).

---

### Task 1: Live baseline audit + close the mcp_server raisable-type drift

**Why first:** before touching any code, establish ground truth for every
request type by tracing it live — this is the exact discipline the spec's
central finding came from, and it may surface bugs beyond the ones already
known. It also closes one already-found, freshly-introduced bug: today's
`mcp_server` commit (557a86db) added `mcp_server` to the FRONTEND's
`BU_ADMIN_RAISABLE` list (`frontend/lib/requests/routing.ts:186-196`) but not
the BACKEND's `_BU_ADMIN_RAISABLE` (`backend/shared/governance/routing.py:240-247`)
— a Business Unit Admin sees "Request an MCP server" and gets a 403 on submit.
The backend module's own docstring claims `tests/test_governance_routing.py`
pins this mirror; that file does not exist (confirmed via `find backend -iname
"test_governance_routing*"` — no match). This task writes it for real.

**Files:**
- Modify: `backend/shared/governance/routing.py:240-247` (`_BU_ADMIN_RAISABLE`)
- Create: `backend/tests/test_governance_routing.py`
- Create: `backend/.superpowers-findings/2026-08-29-request-type-baseline-audit.md`
  (a plain findings doc, not code — every request type's live-traced status
  before this plan's fixes; later tasks reference it, and the final
  whole-branch review reads it to confirm every finding was addressed or
  explicitly parked)

**Interfaces:**
- Produces: the corrected `_BU_ADMIN_RAISABLE` tuple (now includes
  `"mcp_server"`), and the baseline audit findings doc later tasks and the
  final review read.

- [ ] **Step 1: Reproduce the drift**

With the backend running (`cd backend && uv run uvicorn process_api:app
--host 127.0.0.1 --port 8001 --reload`, backgrounded if not already up),
mint a BU Admin token and attempt to raise an `mcp_server` request:

```python
import asyncio
from config.auth.jwt import create_access_token
import httpx

async def main():
    tenant = "8d5bd6a3-7e07-46ce-8416-cada90dead79"  # or any live tenant with a BU Admin
    # substitute a real bu_admin user_id + workspace_id from your local DB
    token = create_access_token(user_id="<bu-admin-user-id>", tenant_id=tenant, permissions=["artifact:view", "member:manage"])
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8001") as c:
        r = await c.post("/governance-approvals", headers={"Authorization": f"Bearer {token}"}, json={
            "type": "mcp_server", "title": "Request the Postgres MCP server",
            "description": "Need it for the pipeline agent.", "priority": "normal",
            "workspaceId": "<a real workspace id this bu_admin administers>",
        })
        print(r.status_code, r.text)

asyncio.run(main())
```

Expected right now: `403`, `TYPE_NOT_RAISABLE`.

- [ ] **Step 2: Write the failing regression test**

```python
# backend/tests/test_governance_routing.py
"""Pins the mirror between backend routing.py and frontend requests/routing.ts —
the docstring in routing.py has claimed this file exists since before this test
was written; a mismatch here is a silent 403 in production, found this way once
already (mcp_server absent from _BU_ADMIN_RAISABLE while present in the frontend
copy, see plan task 1)."""
from shared.governance import routing


def test_bu_admin_raisable_includes_mcp_server():
    """mcp_server is tier-routed like connector_access, its closest sibling —
    a Business Unit Admin who lacks an MCP server must be able to ask for one,
    exactly as they already can for a connector."""
    assert "mcp_server" in routing.raisable_types_for("bu_admin")


def test_every_raisable_list_only_names_real_types():
    """A typo in any *_RAISABLE tuple would silently make a type unraisable by
    anyone rather than erroring — this is the cheap net for that."""
    for role in ("bu_admin", "project_admin", None):
        for t in routing.raisable_types_for(role):
            assert t in routing.REQUEST_TYPES, f"{t!r} in raisable_types_for({role!r}) is not a real REQUEST_TYPES entry"
```

- [ ] **Step 3: Run it, confirm the first assertion fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_routing.py -v`
Expected: `test_bu_admin_raisable_includes_mcp_server` FAILS (mcp_server not in the tuple); `test_every_raisable_list_only_names_real_types` PASSES.

- [ ] **Step 4: Fix the drift**

In `backend/shared/governance/routing.py`, add `"mcp_server"` to
`_BU_ADMIN_RAISABLE` (line ~240), next to `"connector_access"`, with a comment
mirroring the frontend's own (same reasoning: same ask, other half of the
estate, tier-routed to Org Admin like its neighbour):

```python
_BU_ADMIN_RAISABLE: tuple[str, ...] = (
    "model_provider_access",
    "user_onboarding",
    "connector_access",
    # Same ask as connector_access, about the other half of the same estate:
    # granting an MCP server to a business unit is the Org Admin's alone.
    # Mirrors frontend/lib/requests/routing.ts::BU_ADMIN_RAISABLE — see that
    # file's comment for the full reasoning.
    "mcp_server",
    "budget_increase",
    "access_request",
    "other",
)
```

- [ ] **Step 5: Run the test again, confirm both pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_routing.py -v`
Expected: PASS.

- [ ] **Step 6: Re-run the live reproduction from Step 1**

Expected now: `201`, a real request row created.

- [ ] **Step 7: Live-trace every request type — write the findings doc**

For each of the 17 entries in `routing.REQUEST_TYPES`, using the running
backend + a live tenant (create one via the pattern in
`backend/tests/test_project_scoped.py`'s `org` fixture if you don't have a
convenient existing tenant), do the minimum needed to observe the REAL current
behavior — not a re-read of the code:

1. Raise it as the correct role (per `raisable_types_for`, or via the
   system-raised path it actually uses if it's in `SYSTEM_RAISED`).
2. Decide it (approve) as the correct role, per `initial_approver_role`.
3. Query the database directly for whatever the effect claims to write, or
   confirm the `EffectNotAvailable` message if one fires.

Write each type's real, observed result to
`backend/.superpowers-findings/2026-08-29-request-type-baseline-audit.md` as
one row: `type | raised as | decided as | observed result | matches effects.py's
claimed behavior? Y/N`. This file is NOT deleted at the end of this plan — it
is the evidence trail the final whole-branch review reads. If this step
surfaces a bug beyond the ones this plan already targets, add a ruling line at
the bottom of the file: either fix it inline in this task (if it's a
one-line/low-risk fix, same bar as the mcp_server drift above) or park it
explicitly with a one-sentence reason it belongs to a later sub-project (most
likely: anything touching `runs.py`'s agent-gate approvals belongs to
sub-project B).

- [ ] **Step 8: Commit**

```bash
git add backend/shared/governance/routing.py backend/tests/test_governance_routing.py backend/.superpowers-findings/2026-08-29-request-type-baseline-audit.md
git commit -m "fix: close mcp_server BU-Admin raisable-type drift; live-baseline every request type"
```

---

### Task 2: Plumbing — typed target fields end to end

**Files:**
- Modify: `backend/shared/routers/governance_requests.py:51-70` (`RequestCreateIn`), `:137-162` (`create_request` route)
- Modify: `backend/shared/services/governance_requests.py:301-319` (`create_request` service signature + body)
- Modify: `frontend/lib/schemas/governance-approval.ts:288-307` (`RequestCreateInput`)
- Modify: `frontend/components/requests/raise-request-dialog.tsx` (`RaiseRequestPrefill` interface, `mutationFn`)
- Test: `backend/tests/test_governance_requests.py` (extend)

**Interfaces:**
- Produces: `RequestCreateIn`/`RequestCreateInput` gain `targetId:
  Optional[str]`, `providerModel: Optional[{provider: str, modelId: str}]`,
  `onboardEmail: Optional[EmailStr]`. `service.create_request` gains matching
  kwargs (`target_id`, `provider_model`, `onboard_email`), each merged into
  `payload` (or, for `targetId`, into `target_ref` directly — see Step 3)
  exactly like `phase` already is.
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write the failing backend test**

Add to `backend/tests/test_governance_requests.py`, using this file's own
established fixtures/helpers (`org`, `_bind`, an inline `TestClient` +
`create_access_token` — there is no shared `_client`/`_headers` function in
this file; every HTTP-level test builds these inline, e.g.
`test_self_approval_blocked_over_http`):

```python
@pytest.mark.asyncio
async def test_connector_access_request_carries_target_id(org):
    """A client-raised connector_access request must record WHICH connector
    it's about — without this, _apply_connector_access can never find a
    target to grant (the bug this plan exists to fix)."""
    dev = f"dev-{_uuid.uuid4()}"
    await _bind(org, dev, "developer", scope_kind="project", scope_id=org["project"])

    c = TestClient(process_api.app)
    headers = {"Authorization": "Bearer " + create_access_token(
        user_id=dev, tenant_id=org["org"], permissions=["artifact:view", "run:create"],
    )}
    r = c.post(
        "/governance-approvals", headers=headers,
        json={
            "type": "connector_access", "title": "Slack access",
            "description": "Need it for the release channel.", "priority": "normal",
            "workspaceId": org["bu"], "projectId": org["project"],
            "targetId": "slack",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["payload"]["targetId"] == "slack"


@pytest.mark.asyncio
async def test_model_provider_access_request_carries_provider_kind(org):
    """A client-raised model_provider_access request must record WHICH
    provider kind it's about. NOTE: this type does NOT carry a specific
    model_providers row id — the UI that raises it (model-availability-card.tsx)
    only ever has a (provider, model_id) pair in scope, never a connection's
    row id (that id is knowable only from Model Management's admin view, which
    a BU Admin raising this request is not looking at). The effect (Task 5)
    resolves the real row server-side, by provider kind, at decide time —
    this is a genuine design correction from the plan's first draft, which
    incorrectly assumed a row id was available here."""
    bu_admin = f"bu-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])

    c = TestClient(process_api.app)
    headers = {"Authorization": "Bearer " + create_access_token(
        user_id=bu_admin, tenant_id=org["org"], permissions=["artifact:view", "model:manage"],
    )}
    r = c.post(
        "/governance-approvals", headers=headers,
        json={
            "type": "model_provider_access", "title": "Onboard Anthropic",
            "description": "Need Claude for the security agent.", "priority": "normal",
            "workspaceId": org["bu"], "providerModel": {"provider": "anthropic", "modelId": "claude-sonnet-5"},
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["payload"]["providerModel"]["provider"] == "anthropic"
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k "target_id or provider_kind" -v`
Expected: FAIL — `targetId`/`providerModel` are not recognized fields (422
from Pydantic, extra field forbidden or silently dropped depending on model
config — check which and assert accordingly if the model allows extra
fields today).

- [ ] **Step 3: Add the fields to the backend schema and route**

In `backend/shared/routers/governance_requests.py`, extend `RequestCreateIn`:

```python
class RequestCreateIn(BaseModel):
    type: str
    title: str = Field(min_length=4, max_length=160)
    description: str = Field(min_length=10, max_length=4000)
    priority: str = "normal"
    workspaceId: str = Field(min_length=1)
    projectId: Optional[str] = None
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=10)
    phase: Optional[str] = None
    # WHAT is being asked for, when the type needs a specific one — a
    # connector kind, an MCP server row id, or a model provider id. Content,
    # not routing, exactly like `phase` above: the requester names the thing,
    # the platform still derives who decides it.
    targetId: Optional[str] = Field(default=None, max_length=255)
    # connector_access's access level (read/write/read_write) — required by
    # _apply_connector_access's existing payload.get("access") read (see that
    # function in effects.py). Only meaningful alongside targetId for this
    # one type; unused by mcp_server (which has no level, per migration 0024
    # — see _apply_connector_access's own "no level on the row" comment).
    accessLevel: Optional[str] = Field(default=None, max_length=16)
    # model_credential's target: a project needs BOTH a provider and a model
    # id, not one opaque id.
    providerModel: Optional[dict[str, str]] = None
    # user_onboarding's target: who is being asked about.
    onboardEmail: Optional[str] = Field(default=None, max_length=320)
```

In the same file's `create_request` route (~line 137), pass the new fields
through:

```python
        return await service.create_request(
            db,
            tenant_id=tenant_id,
            initiator_id=actor_id,
            initiator_name=actor_name,
            initiator_role=actor_role,
            request_type=body.type,
            title=body.title,
            description=body.description,
            workspace_id=body.workspaceId,
            project_id=body.projectId,
            priority=body.priority,
            attachments=[a.model_dump() for a in body.attachments],
            phase=body.phase,
            target_id=body.targetId,
            access_level=body.accessLevel,
            provider_model=body.providerModel,
            onboard_email=body.onboardEmail,
        )
```

- [ ] **Step 4: Thread the new kwargs through the service function**

In `backend/shared/services/governance_requests.py`, extend `create_request`'s
signature (~line 301) and body (~line 373-381):

```python
async def create_request(
    db: AsyncSession,
    *,
    tenant_id: str,
    initiator_id: str,
    initiator_name: str,
    initiator_role: Optional[str],
    request_type: str,
    title: str,
    description: Optional[str],
    workspace_id: str,
    project_id: Optional[str] = None,
    priority: str = "normal",
    attachments: Optional[list[dict[str, Any]]] = None,
    phase: Optional[str] = None,
    target_ref: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    system_raised: bool = False,
    # New: the client-supplied target, for the types that need one. Distinct
    # from `target_ref` above (which system-raised callers pass directly) —
    # this is the client-facing counterpart, folded into target_ref/payload
    # below exactly like `phase` already is.
    target_id: Optional[str] = None,
    access_level: Optional[str] = None,
    provider_model: Optional[dict[str, str]] = None,
    onboard_email: Optional[str] = None,
) -> dict[str, Any]:
```

And in the body, right after the existing `agent_access`/`phase` block
(~line 375-381), add:

```python
    # connector_access and mcp_server read payload.targetId (see
    # _apply_connector_access) — merged the same way phase is, never a raw
    # passthrough.
    if target_id and request_type in ("connector_access", "mcp_server"):
        payload = {**(payload or {}), "targetId": target_id}
    # connector_access alone also carries an access level — _apply_
    # connector_access's project-tier branch reads payload["access"].
    if access_level and request_type == "connector_access":
        payload = {**(payload or {}), "access": access_level}
    # model_credential AND model_provider_access both carry a (provider,
    # model_id) pair — never a model_providers row id. Neither the project
    # Model Management view nor the BU availability card ever has a specific
    # connection's row id in scope (see plan Task 3's corrected design note);
    # model_provider_access's effect resolves the real row server-side, by
    # provider kind, at decide time.
    if provider_model and request_type in ("model_credential", "model_provider_access"):
        payload = {**(payload or {}), "providerModel": provider_model}
    if onboard_email and request_type == "user_onboarding":
        payload = {**(payload or {}), "onboardEmail": onboard_email}
```

- [ ] **Step 5: Run the backend tests, confirm they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k "target_id or provider_kind" -v`
Expected: PASS.

- [ ] **Step 6: Extend the frontend schema**

In `frontend/lib/schemas/governance-approval.ts`, extend `RequestCreateInput`
(~line 288):

```typescript
export const RequestCreateInput = z.object({
  type: GovernanceApprovalType,
  title: z.string().min(4, "Give the request a title").max(160),
  description: z.string().min(10, "Describe what you need and why").max(4000),
  priority: RequestPriority.default("normal"),
  workspaceId: z.string().min(1, "Choose a business unit"),
  projectId: z.string().nullable().optional(),
  attachments: z.array(RequestAttachment).max(10).default([]),
  phase: Phase.optional(),
  /**
   * WHAT is being asked for, when the type needs one — a connector kind, an
   * MCP server row id, or a model provider id. Same content-not-routing rule
   * as `phase`.
   */
  targetId: z.string().max(255).optional(),
  /** connector_access's access level (read/write/read_write) — only
   *  meaningful alongside targetId for this one type. */
  accessLevel: z.string().max(16).optional(),
  /** model_credential's target: a project's ask needs both a provider and a
   *  model id. */
  providerModel: z.object({ provider: z.string(), modelId: z.string() }).optional(),
  /** user_onboarding's target: who is being asked about. */
  onboardEmail: z.string().email().optional(),
});
export type RequestCreateInput = z.infer<typeof RequestCreateInput>;
```

- [ ] **Step 7: Extend `RaiseRequestPrefill` and the dialog's mutation**

In `frontend/components/requests/raise-request-dialog.tsx`, extend the
`RaiseRequestPrefill` interface (~line 86) with the same four optional
fields, and thread them through the `createRequest` call in `mutationFn`
(~line 194-204) exactly like `workspaceId`/`projectId` already are — pass
`prefill?.targetId`/`prefill?.accessLevel`/`prefill?.providerModel`/
`prefill?.onboardEmail` through to `createRequest`'s input object. Read the
existing `seeded`/`prefill` plumbing in this file first (the
`React.useEffect` at ~line 144 that seeds state on open) and add matching
`React.useState`/seed-on-open handling for whichever of the four fields a
given prefill carries — do not invent a second seeding mechanism.

- [ ] **Step 8: Run the frontend typecheck**

Run: `cd frontend && npm run typecheck`
Expected: PASS (no type errors from the new optional fields).

- [ ] **Step 9: Live-verify the plumbing directly (no UI yet — that's tasks 3-4)**

With the backend running, repeat Task 1 Step 1's shape but for
`connector_access` with a real `targetId`, and confirm via a direct DB query
that the created row's `payload` column actually contains it:

```python
import asyncio, asyncpg

async def main():
    conn = await asyncpg.connect("postgresql://postgres:sarthak@localhost:5433/sdlc_product")
    row = await conn.fetchrow("SELECT payload, target_ref FROM governance_requests WHERE type = 'connector_access' ORDER BY created_at DESC LIMIT 1")
    print(dict(row))
    await conn.close()

asyncio.run(main())
```

Expected: `payload` contains `{"targetId": "slack"}` (or whatever was sent).

- [ ] **Step 10: Commit**

```bash
git add backend/shared/routers/governance_requests.py backend/shared/services/governance_requests.py backend/tests/test_governance_requests.py frontend/lib/schemas/governance-approval.ts frontend/components/requests/raise-request-dialog.tsx
git commit -m "feat: thread a real target through client-raised governance requests"
```

---

### Task 3: Wire Model Management's request buttons to a real target

**Files:**
- Modify: `frontend/components/app/model-availability-card.tsx`
- Test: `frontend/__tests__/app/model-availability-card.test.tsx` (extend, or create if none exists — check first)

**Interfaces:**
- Consumes: `RaiseRequestPrefill.targetId`/`.providerModel` from Task 2.

- [ ] **Step 1: Read the two `RequestAccessButton` call sites**

Both live in `frontend/components/app/model-availability-card.tsx`: the
zero-rows case (~line 142, only reachable when `ungranted.length === 0`, so
it names no specific model and needs no target — leave it as-is) and the
`ungranted.map(...)` disclosure list (~line 205), which is the one this task
fixes. Both are driven by the SAME component-level `requestType` constant
(`audience === "bu" ? "model_provider_access" : "model_credential"`, defined
near the top of the component) — so both request types are raised from
identical `ungranted` catalog rows, each shaped `{provider, model_id, label,
providerLabel}` (confirmed against the `ungranted` derivation earlier in this
file). Neither this component nor its caller ever holds a specific
`model_providers` row id — only the catalog pair. This is why Task 2's
`providerModel` field (not a row-id `targetId`) is the correct target for
BOTH request types raised from here, unlike the plan's first draft.

- [ ] **Step 2: Write the failing frontend test**

Create `frontend/__tests__/app/model-availability-card.test.tsx`, following
`__tests__/app/provider-detail-rbac-gate.test.tsx`'s established pattern
(vitest + jsdom, RTL, `QueryClientProvider` wrapper, `vi.mock` on the API
client module):

```typescript
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/models", () => ({
  getModelAvailability: vi.fn().mockResolvedValue([]), // empty granted rows -> ungranted disclosure renders
}));

// A plain stub, not a rendered dialog — RequestAccessButton always mounts
// RaiseRequestDialog (open={false} until clicked), so its prefill prop is
// observable on render without needing to open anything. This is a simpler,
// more idiomatic mock than spy-wrapping the real component: it directly
// mirrors provider-detail-rbac-gate.test.tsx's "stub the module, assert on
// what reached it" style rather than inventing a new technique.
const raiseRequestDialogSpy = vi.fn();
vi.mock("@/components/requests/raise-request-dialog", () => ({
  RaiseRequestDialog: (props: unknown) => {
    raiseRequestDialogSpy(props);
    return null;
  },
}));

import { ModelAvailabilityCard } from "@/components/app/model-availability-card";

afterEach(cleanup);

function renderCard(props: Partial<React.ComponentProps<typeof ModelAvailabilityCard>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ModelAvailabilityCard
        workspaceId="ws-1"
        workspaceName="Payments"
        audience="bu"
        catalog={[{
          provider: "anthropic",
          label: "Anthropic",
          models: [{ model_id: "claude-sonnet-5", label: "Claude Sonnet 5" }],
        }] as never}
        {...props}
      />
    </QueryClientProvider>,
  );
}

describe("model availability card — request prefill", () => {
  it("carries providerModel (not a row id) when requesting an ungranted model", async () => {
    renderCard();
    const disclosure = await screen.findByText(/more model/i);
    await userEvent.click(disclosure);
    const lastCall = raiseRequestDialogSpy.mock.calls.at(-1)?.[0] as { prefill?: unknown };
    expect(lastCall?.prefill).toMatchObject({
      type: "model_provider_access",
      providerModel: { provider: "anthropic", modelId: "claude-sonnet-5" },
    });
  });
});
```

The exact `catalog`/`ungranted` shape used above must be confirmed against
this file's real types (`CatalogProvider`, the `ungranted` derivation logic
above line 142) before finalizing — the fixture here is illustrative of the
shape, not guaranteed byte-exact; adjust field names to match what actually
compiles.

- [ ] **Step 3: Run it, confirm it fails**

Run: `cd frontend && npm test -- model-availability-card`
Expected: FAIL — `prefill` has no `providerModel`, only `{type, title, description, workspaceId}`.

- [ ] **Step 4: Wire the real target into both `ungranted` prefills**

In the `ungranted.map(...)` block (~line 205), add
`providerModel: { provider: m.provider, modelId: m.model_id }` to the
`RaiseRequestPrefill` object — the SAME field for both audiences, since it's
one shared render path; only `requestType` (already computed) differs between
them.

- [ ] **Step 5: Run the test, confirm it passes**

Run: `cd frontend && npm test -- model-availability-card`
Expected: PASS.

- [ ] **Step 6: Live-verify end to end**

Start the frontend (`cd frontend && npm run dev`, backgrounded, on
`localhost:3000`) and the backend if not already running. As a real BU Admin
account in the local DB, open Model Management, click "Request access" on a
provider not yet active for the unit, submit, then query the database
(`SELECT payload, target_ref FROM governance_requests WHERE type IN
('model_credential','model_provider_access') ORDER BY created_at DESC LIMIT
1`) and confirm a real id landed, not null/the workspace id.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/app/model-availability-card.tsx frontend/__tests__/app/model-availability-card.test.tsx
git commit -m "fix: Model Management's request buttons now name a real target"
```

---

### Task 4: Wire the Integrations page's request buttons to a real target

**Files:**
- Modify: `frontend/app/(app)/integrations/page.tsx`
- Test: `frontend/__tests__/app/integrations-page.test.tsx` (extend or create — check first)

**Interfaces:**
- Consumes: `RaiseRequestPrefill.targetId` from Task 2.

- [ ] **Step 1: Locate the three `requestPrefill` object literals**

Per earlier reading, these are at approximately lines 509-513 (connector
tiles, `type: "connector_access"`), 545-550 (the MCP section-level "Request an
MCP server" button, `type: "mcp_server"`), and 581-585 (per-row MCP tiles,
`type: "mcp_server"`) in `frontend/app/(app)/integrations/page.tsx`. All three
currently set only `{type, title, description}`.

- [ ] **Step 2: Write the failing test**

Following this repo's established RTL pattern for this page (check
`frontend/__tests__/app/` for an existing integrations test to match style —
if none exists, create one scoped to just this behavior): render the page
with a known connector kind and a known MCP server row present but not
granted; click each "Request access" button; assert the resulting prefill
carries `targetId` equal to the connector's `kind` string (for connectors) or
the MCP row's `id` (for MCP servers) — not just present in the title text.

- [ ] **Step 3: Run it, confirm it fails**

Run: `cd frontend && npm test -- integrations`
Expected: FAIL.

- [ ] **Step 4: Add `targetId` to all three prefills**

Connector tiles (~line 509): `targetId: c.kind, accessLevel: "read"` — a
sensible default level for a fresh ask (matching the level a Project Admin's
own manual project-tier grant defaults to; confirm against
`_apply_connector_access`'s `is_access_level` check that `"read"` is a valid
member before finalizing).
MCP section button (~line 545): this one has NO specific server in view yet
(it's "stand one up", per the existing comment at line 532-539) — leave it
without a `targetId`; its effect (Task 7) must handle the no-target case
gracefully (see Task 7's error-message requirement).
MCP per-row tiles (~line 581): `targetId: r.id`.

- [ ] **Step 5: Run the test, confirm it passes**

Run: `cd frontend && npm test -- integrations`
Expected: PASS.

- [ ] **Step 6: Live-verify end to end**

Same shape as Task 3 Step 6, for a connector kind and a real MCP server row.
Confirm the database row's `payload.targetId` matches.

- [ ] **Step 7: Commit**

```bash
git add "frontend/app/(app)/integrations/page.tsx" frontend/__tests__/app/integrations-page.test.tsx
git commit -m "fix: Integrations page's connector/MCP request buttons now name a real target"
```

---

### Task 5: Verify connector_access is reachable; fix + verify model_provider_access

**Why its own task:** `connector_access` needs no new production code — tasks
2-4 already made `_apply_connector_access` reachable, and this task proves it
with a permanent regression test plus a live trace. `model_provider_access`
needs one more real change on top of the plumbing: `_apply_model_provider_access`
currently reads a specific `model_providers` ROW id from `target_ref`, but
Task 2/3's corrected design means the request only ever carries a
`(provider, model_id)` PAIR — the UI that raises it never has a row id in
scope (see Task 3's design note). This task updates the effect to resolve the
real row server-side, by provider kind, rather than expecting one it can
never receive. Treat a green result on both here as the gate before starting
any of tasks 6-9.

**Files:**
- Test: `backend/tests/test_governance_requests.py` (extend)

**Interfaces:**
- Consumes: everything from tasks 2-4.

- [ ] **Step 1: Write the end-to-end regression tests**

```python
@pytest.mark.asyncio
async def test_connector_access_request_grants_on_approval(org):
    """The exact bug this plan fixes: raised with a real targetId, approved,
    and the grant must actually land in project_connector_access.

    connector_access is TIER-ROUTED (absent from routing.TYPE_ROUTED), so a
    Developer's request lands on their Project Admin, not the Org Admin —
    confirm this against routing.py before changing the shape below. With
    `projectId` set, `_apply_connector_access` takes its PROJECT branch,
    which requires the connector already granted to the business unit
    (`integration_grants`) — seeded directly here rather than through a
    second request, since that grant is a precondition of this test, not
    what it's testing.
    """
    dev = f"dev-{_uuid.uuid4()}"
    await _bind(org, dev, "developer", scope_kind="project", scope_id=org["project"])
    project_admin = f"pa-{_uuid.uuid4()}"
    await _bind(org, project_admin, "project_admin", scope_kind="project", scope_id=org["project"])
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO integration_grants (tenant_id, kind, target_ref, workspace_id) "
            "VALUES (CAST(:t AS uuid), 'connector', 'slack', CAST(:w AS uuid))"
        ), {"t": org["org"], "w": org["bu"]})

    c = TestClient(process_api.app)
    dev_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=dev, tenant_id=org["org"], permissions=["artifact:view", "run:create"],
    )}
    raised = c.post(
        "/governance-approvals", headers=dev_headers,
        json={
            "type": "connector_access", "title": "Slack access", "description": "For releases.",
            "priority": "normal", "workspaceId": org["bu"], "projectId": org["project"],
            "targetId": "slack", "accessLevel": "read",
        },
    )
    assert raised.status_code == 201, raised.text
    assert raised.json()["currentApproverRole"] == "project_admin"

    pa_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=project_admin, tenant_id=org["org"],
        permissions=["artifact:view", "governance:decide"],
    )}
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=pa_headers,
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text
    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(text(
            "SELECT 1 FROM project_connector_access WHERE tenant_id = CAST(:t AS uuid) "
            "  AND project_id = CAST(:p AS uuid) AND kind = 'connector' AND target_ref = 'slack'"
        ), {"t": org["org"], "p": org["project"]})).first()
    assert row is not None, "connector_access approval did not grant project_connector_access"
```

```python
@pytest.mark.asyncio
async def test_model_provider_access_request_activates_on_approval(org):
    """Raise with a (provider, model_id) pair, no row id — the effect must
    find the matching inactive org-wide connection itself and activate it.
    model_provider_access IS type-routed straight to org_admin
    (routing.GOVERNANCE_APPROVER_ROLE), so a single decide call suffices —
    no escalation needed."""
    async with get_db_session_superuser() as s:
        await s.execute(text(
            "INSERT INTO model_providers (id, tenant_id, workspace_id, provider, display_name, "
            "  secret_ref, status, created_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), NULL, 'anthropic', 'Anthropic', "
            "  '', 'unverified', 'seed')"
        ), {"t": org["org"]})
    bu_admin = f"bu-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])
    org_admin = f"org-{_uuid.uuid4()}"
    await _bind(org, org_admin, "org_admin", scope_kind="organization", scope_id=org["org"])

    c = TestClient(process_api.app)
    bu_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=bu_admin, tenant_id=org["org"], permissions=["artifact:view", "model:manage"],
    )}
    raised = c.post(
        "/governance-approvals", headers=bu_headers,
        json={
            "type": "model_provider_access", "title": "Onboard Anthropic",
            "description": "Need Claude for the security agent.", "priority": "normal",
            "workspaceId": org["bu"], "providerModel": {"provider": "anthropic", "modelId": "claude-sonnet-5"},
        },
    )
    assert raised.status_code == 201, raised.text
    assert raised.json()["currentApproverRole"] == "org_admin"

    org_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=org_admin, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=org_headers,
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text
    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(text(
            "SELECT status FROM model_providers WHERE tenant_id = CAST(:t AS uuid) "
            "  AND provider = 'anthropic' AND workspace_id IS NULL"
        ), {"t": org["org"]})).first()
    assert row is not None and row.status == "active"


@pytest.mark.asyncio
async def test_model_provider_access_refuses_when_ambiguous(org):
    """Two inactive anthropic connections exist for this tenant: the effect
    must refuse rather than guess which one the requester meant."""
    # Seed TWO inactive, org-wide model_providers rows for "anthropic" (same
    # INSERT as above, called twice), raise + approve exactly as above,
    # assert the decide call returns a 4xx with code EFFECT_UNAVAILABLE and a
    # message naming the ambiguity — never a silent pick of either row.
```

`model_providers` columns above (`secret_ref` required NOT NULL,
`created_by`, etc.) must be confirmed against the real ORM/migration before
finalizing — cross-check `backend/shared/models/orm.py::ModelProvider`
(already read once earlier in this plan's research) rather than assuming the
insert above is byte-exact.

- [ ] **Step 2: Run, confirm all three fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k "grants_on_approval or activates_on_approval or refuses_when_ambiguous" -v`
Expected: `grants_on_approval` (connector_access) PASSES already — tasks 2-4
alone were sufficient for it, confirming the plan's claim that type needed no
effect change. Both `model_provider_access` tests FAIL — the effect still
expects a row id.

- [ ] **Step 3: Update `_apply_model_provider_access` to resolve by provider kind**

In `backend/shared/governance/effects.py`, replace the function's target
resolution (the `target = request.get("targetRef")` block) with a
provider-kind lookup:

```python
async def _apply_model_provider_access(db: AsyncSession, request: dict[str, Any]) -> str:
    """Activate the org-wide model provider connection the request named.

    THE REQUEST NAMES A PROVIDER KIND, NEVER A CONNECTION ROW — the UI that
    raises this (model-availability-card.tsx) only ever has a (provider,
    model_id) catalog pair in scope; a specific model_providers row id is
    knowable only from Model Management's admin view, which this request's
    raiser (a Business Unit Admin) is not looking at. This resolves the real
    row itself: an inactive, org-wide (workspace_id IS NULL) connection for
    the named provider kind. Exactly one match activates cleanly; zero or
    more than one refuses rather than guessing — a silent pick of either row
    when two exist would activate a connection nobody specifically agreed to.
    """
    payload = request.get("payload") or {}
    provider = (payload.get("providerModel") or {}).get("provider")
    if not provider:
        raise EffectNotAvailable("model_provider_access", "This request names no provider.")

    candidates = (
        await db.execute(
            text(
                "SELECT id FROM model_providers WHERE tenant_id = CAST(:t AS uuid) "
                "  AND provider = :p AND workspace_id IS NULL AND status <> 'active'"
            ),
            {"t": request["tenantId"], "p": provider},
        )
    ).fetchall()
    if not candidates:
        raise EffectNotAvailable(
            "model_provider_access",
            f"No inactive {provider} connection exists yet — an Organization Admin "
            "needs to onboard one from Model Management before this can be granted.",
        )
    if len(candidates) > 1:
        raise EffectNotAvailable(
            "model_provider_access",
            f"{len(candidates)} inactive {provider} connections exist — approve this "
            "directly from Model Management instead, where the right one can be picked.",
        )
    target = str(candidates[0].id)

    result = await db.execute(
        text(
            "UPDATE model_providers SET status = 'active', updated_at = now() "
            "WHERE id = CAST(:p AS uuid)"
        ),
        {"p": target},
    )
    if not result.rowcount:
        raise EffectNotAvailable("model_provider_access", "That provider no longer exists.")
    logger.info(
        "governance: model provider activated request=%s provider=%s id=%s",
        request["id"], provider, target,
    )
    return f"{provider} activated."
```

- [ ] **Step 4: Run, confirm all three pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k "grants_on_approval or activates_on_approval or refuses_when_ambiguous" -v`
Expected: PASS.

- [ ] **Step 5: Live-verify against the running stack**

Full manual trace, as a real user in the browser at `localhost:3000`: raise a
connector request from the Integrations page, approve it as an Org Admin
account, confirm the connector tile now shows granted. Repeat for a model
provider from Model Management — onboard an inactive Anthropic connection
first if none exists locally, then raise+approve the request from a BU Admin
account and confirm it activates.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/governance/effects.py backend/tests/test_governance_requests.py
git commit -m "fix: model_provider_access resolves its target by provider kind, not an unreachable row id; verify connector_access reachability"
```

---

### Task 6: Real effect — `model_credential`

**Files:**
- Modify: `backend/shared/governance/effects.py`
- Test: `backend/tests/test_governance_requests.py` (extend)

**Interfaces:**
- Consumes: `payload.providerModel` from Task 2; `set_project_selection`,
  `get_project_selection` from `backend/shared/services/model_grants.py`
  (already exist, unmodified).
- Produces: `_apply_model_credential(db, request) -> str`, dispatched from
  `apply_on_approve`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_model_credential_request_selects_model_for_project(org):
    """Approving must add the requested (provider, model_id) to the project's
    selection — the same write set_project_selection already performs by hand.
    Requires the model already reachable to the project's BU (get_bu_allowed) —
    see model_grants.py's NotAllowedForUnitError for why. Seeded directly as a
    GLOBAL org_model_grants row (reaches every unit, including this one) —
    the simplest real precondition, matching set_org_grants's own INSERT
    shape rather than going through set_bu_grants's specific-visibility path,
    which this test has no need to exercise.

    model_credential is TIER-ROUTED (absent from routing.TYPE_ROUTED), so a
    Developer's request lands on their Project Admin directly — one decide
    call, no escalation."""
    async with get_db_session_for_tenant(org["org"]) as s:
        await s.execute(text(
            "INSERT INTO org_model_grants "
            "  (id, tenant_id, provider, model_id, credential_id, visibility, business_unit_ids, created_by) "
            "VALUES (gen_random_uuid(), CAST(:t AS uuid), 'openai', 'gpt-4.1', NULL, 'global', '[]', 'seed')"
        ), {"t": org["org"]})
    dev = f"dev-{_uuid.uuid4()}"
    await _bind(org, dev, "developer", scope_kind="project", scope_id=org["project"])
    project_admin = f"pa-{_uuid.uuid4()}"
    await _bind(org, project_admin, "project_admin", scope_kind="project", scope_id=org["project"])

    c = TestClient(process_api.app)
    dev_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=dev, tenant_id=org["org"], permissions=["artifact:view", "run:create"],
    )}
    raised = c.post(
        "/governance-approvals", headers=dev_headers,
        json={
            "type": "model_credential", "title": "Need GPT-4.1", "description": "For the design agent.",
            "priority": "normal", "workspaceId": org["bu"], "projectId": org["project"],
            "providerModel": {"provider": "openai", "modelId": "gpt-4.1"},
        },
    )
    assert raised.status_code == 201, raised.text
    assert raised.json()["currentApproverRole"] == "project_admin"

    pa_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=project_admin, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=pa_headers,
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text
    from shared.services.model_grants import get_project_selection
    selection = await get_project_selection(org["org"], org["project"])
    assert any(e["provider"] == "openai" and e["model_id"] == "gpt-4.1" for e in selection["selected"])
```

- [ ] **Step 2: Run, confirm it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k model_credential -v`
Expected: FAIL — request decides fine (still `_DECISION_IS_THE_OUTCOME`), but
`selected` never gains the entry.

- [ ] **Step 3: Implement the effect**

In `backend/shared/governance/effects.py`, remove `"model_credential"` from
`_DECISION_IS_THE_OUTCOME` (~line 84), add a dispatch branch in
`apply_on_approve` (~line 111, beside `model_provider_access`), and add the
function:

```python
async def _apply_model_credential(db: AsyncSession, request: dict[str, Any]) -> str:
    """Add the requested (provider, model_id) to the project's selection —
    the exact write set_project_selection already performs when a Project
    Admin does this by hand (Model Management). Reuses that function rather
    than reimplementing its reachability checks (NotAllowedForUnitError etc.):
    a request approved for a model the project's BU never made reachable
    should fail the same way the manual path does, not silently succeed
    through a second, looser route.
    """
    from shared.services.model_grants import (
        NotAllowedForUnitError,
        get_project_selection,
        set_project_selection,
    )

    payload = request.get("payload") or {}
    pm = payload.get("providerModel") or {}
    provider, model_id = pm.get("provider"), pm.get("modelId")
    project_id = request.get("projectId")

    if not project_id:
        raise EffectNotAvailable("model_credential", "This request names no project.")
    if not provider or not model_id:
        raise EffectNotAvailable(
            "model_credential", "This request names no provider or model to select."
        )

    current = await get_project_selection(request["tenantId"], project_id)
    already = any(
        e["provider"] == provider and e["model_id"] == model_id for e in current["selected"]
    )
    if already:
        return f"{provider}/{model_id} was already selected for this project."

    next_selection = [*current["selected"], {"provider": provider, "model_id": model_id}]
    try:
        await set_project_selection(
            request["tenantId"], project_id, next_selection, current.get("defaultKey")
        )
    except NotAllowedForUnitError as exc:
        raise EffectNotAvailable("model_credential", str(exc))

    logger.info(
        "governance: model_credential applied request=%s project=%s model=%s/%s",
        request["id"], project_id, provider, model_id,
    )
    return f"{provider}/{model_id} selected for this project."
```

- [ ] **Step 4: Run, confirm it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k model_credential -v`
Expected: PASS.

- [ ] **Step 5: Live-verify**

Against the running stack, as a real Developer/BA on a real project: raise a
model_credential request for a model the project's BU already allows but the
project hasn't selected, approve it as the Project Admin, confirm on the
project's Model settings that it's now selected.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/governance/effects.py backend/tests/test_governance_requests.py
git commit -m "feat: model_credential approval selects the model for the project"
```

---

### Task 7: Real effect — `mcp_server`

**Files:**
- Modify: `backend/shared/governance/effects.py`
- Test: `backend/tests/test_governance_requests.py` (extend)

**Interfaces:**
- Consumes: `payload.targetId` from Task 2/4. Note from research: GodOfDecay's
  557a86db commit (read in full before starting this task —
  `git show 557a86db`) changed only the FRONTEND's presentation of MCP
  servers to look like connectors; it did not merge the `mcp_server` request
  TYPE into `connector_access`, and neither did it touch `effects.py` or
  `governance_requests.py`. The two types remain genuinely separate end to
  end (separate `TYPE_ROUTED`/raisable-list entries — confirmed in
  `routing.py`). Build `_apply_mcp_server` as its own function; do not
  attempt a thin wrapper around `_apply_connector_access` — the two request
  types are deliberately kept distinct at every other layer, and a wrapper
  here would be the one place they secretly weren't.
- Produces: `_apply_mcp_server(db, request) -> str`.

- [ ] **Step 1: Read the manual "enable/permit an MCP server" write path first**

Before writing the effect, find how a BU Admin/Org Admin manually
enables/permits an MCP server today (the routes `AddMcpServerDialog`
ultimately calls, and whatever unit-level "enable" action exists — search
`backend/shared/routers/` for the MCP server grant table, likely something
alongside `integration_grants` given 557a86db's own commit message describes
MCP servers as "governed identically — granted to units, consumed by
projects" to connectors). Mirror that exact write, the same way Task 6
mirrored `set_project_selection` rather than inventing a new table.

- [ ] **Step 2: Write the failing test**

```python
@pytest.mark.asyncio
async def test_mcp_server_request_grants_on_approval(org):
    """Same shape as connector_access's test in Task 5 — approving must
    actually grant the server to the business unit, not just record
    agreement. Use the `org`/`_bind`/inline-TestClient pattern
    `test_connector_access_request_grants_on_approval` (Task 5) already
    establishes — same fixture, same helpers, this whole file's convention.
    """
    # Mirror test_connector_access_request_grants_on_approval's structure,
    # with type="mcp_server" and a real MCP server row's id as targetId (seed
    # one first via whatever table Step 1 found). mcp_server is tier-routed
    # like connector_access (confirm in routing.py before assuming the same
    # project_admin-as-approver shape applies here too). Assert the grant
    # lands in whichever table Step 1's manual-path research found.
```

- [ ] **Step 3: Run, confirm it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k mcp_server_request_grants -v`
Expected: FAIL.

- [ ] **Step 4: Implement `_apply_mcp_server`**

Remove `"mcp_server"` from `_DECISION_IS_THE_OUTCOME`, add the dispatch
branch, and write the function mirroring whichever exact table/write Step 1
found — same shape as `_apply_connector_access`'s reach-check-then-insert
pattern (`EffectNotAvailable` if `payload.targetId` is empty, matching the
"stand one up" no-target case from Task 4 Step 4 with a clear message like
`"This request doesn't yet name which server — ask the requester to specify
one, or register it directly."`).

- [ ] **Step 5: Run, confirm it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k mcp_server_request_grants -v`
Expected: PASS.

- [ ] **Step 6: Live-verify**

Same shape as Task 5 Step 3, for an MCP server.

- [ ] **Step 7: Commit**

```bash
git add backend/shared/governance/effects.py backend/tests/test_governance_requests.py
git commit -m "feat: mcp_server approval actually grants the server"
```

---

### Task 8: Real effect — `agent_access` (final decision)

**Files:**
- Modify: `backend/shared/governance/effects.py`
- Modify: `backend/shared/services/governance_requests.py` (the stage-two
  decide path, ~line 590-639, where `next_agent_access_stage` advances the
  request — read this block in full first, it already has real logic for
  stage transitions and this task's effect must fire only at the FINAL
  decision, not stage one)
- Test: `backend/tests/test_governance_requests.py` (extend)

**Interfaces:**
- Consumes: `payload.phase` (already set by the existing `phase` mechanism),
  `requestedById`, `projectId` — all already present on every `agent_access`
  request, no new field needed from Task 2.
- Produces: `_apply_agent_access(db, request) -> str`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_agent_access_request_grants_extra_agent_on_final_approval(org):
    """Two-stage: Project Admin approves stage one (no grant yet — the ask
    isn't decided), the agent's owner approves stage two (the real grant
    lands in role_bindings.extra_agents)."""
    ba = f"ba-{_uuid.uuid4()}"
    await _bind(org, ba, "ba", scope_kind="project", scope_id=org["project"])
    project_admin = f"pa-{_uuid.uuid4()}"
    await _bind(org, project_admin, "project_admin", scope_kind="project", scope_id=org["project"])
    architect = f"arch-{_uuid.uuid4()}"
    await _bind(org, architect, "architect", scope_kind="project", scope_id=org["project"])

    c = TestClient(process_api.app)
    ba_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=ba, tenant_id=org["org"], permissions=["artifact:view", "run:create"],
    )}
    raised = c.post(
        "/governance-approvals", headers=ba_headers,
        json={
            "type": "agent_access", "title": "Access to the Design agent", "description": "Covering while Architect is out.",
            "priority": "normal", "workspaceId": org["bu"], "projectId": org["project"], "phase": "design",
        },
    )
    assert raised.status_code == 201, raised.text
    req_id = raised.json()["id"]

    pa_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=project_admin, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    stage_one = c.post(
        f"/governance-approvals/{req_id}/decide", headers=pa_headers,
        json={"decision": "approve"},
    )
    assert stage_one.status_code == 200, stage_one.text
    # confirm no grant yet
    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(text(
            "SELECT extra_agents FROM role_bindings WHERE user_id = :u AND scope_kind = 'project' AND scope_id = CAST(:p AS uuid)"
        ), {"u": ba, "p": org["project"]})).first()
    assert not row.extra_agents

    arch_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=architect, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    stage_two = c.post(
        f"/governance-approvals/{req_id}/decide", headers=arch_headers,
        json={"decision": "approve"},
    )
    assert stage_two.status_code == 200, stage_two.text
    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(text(
            "SELECT extra_agents FROM role_bindings WHERE user_id = :u AND scope_kind = 'project' AND scope_id = CAST(:p AS uuid)"
        ), {"u": ba, "p": org["project"]})).first()
    assert row.extra_agents and "design" in row.extra_agents
```

- [ ] **Step 2: Run, confirm it fails at the last assertion**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k agent_access_request_grants -v`
Expected: FAIL at the stage-two `extra_agents` assertion (stage one's
no-grant assertion already passes today).

- [ ] **Step 3: Implement `_apply_agent_access`**

Remove `"agent_access"` from `_DECISION_IS_THE_OUTCOME`, add the dispatch
branch. This effect must fire ONLY when the decision is final (stage two, or
stage one if `next_agent_access_stage` says there is no stage two — e.g.
Documentation, whose owner IS the Project Admin). Read
`governance_requests.py`'s existing stage-transition block (~590-639) to see
exactly how it currently determines "is this the final decision" before
`apply_on_approve` is ever called, and make sure this effect is invoked at
the same point that block already treats as final — do not duplicate that
stage logic inside `effects.py`.

```python
async def _apply_agent_access(db: AsyncSession, request: dict[str, Any]) -> str:
    """Grant the requester the extra agent access their final approver just
    signed off on — the same field the manual 'grant extra agent access'
    admin action already writes (PRD §43.2 step 3), just reached through the
    two-stage request instead of an admin acting directly.
    """
    payload = request.get("payload") or {}
    phase = payload.get("phase")
    user_id = request.get("requestedById")
    project_id = request.get("projectId")

    if not phase:
        raise EffectNotAvailable("agent_access", "This request names no agent.")
    if not user_id or not project_id:
        raise EffectNotAvailable(
            "agent_access", "This request names no person or project to grant access on."
        )

    row = (
        await db.execute(
            text(
                "SELECT extra_agents FROM role_bindings WHERE user_id = :u "
                "  AND scope_kind = 'project' AND scope_id = CAST(:p AS uuid)"
            ),
            {"u": user_id, "p": project_id},
        )
    ).first()
    if row is None:
        raise EffectNotAvailable(
            "agent_access", "This person no longer holds a role on this project."
        )
    current = list(row.extra_agents or [])
    if phase in current:
        return f"{phase} was already granted."
    current.append(phase)

    await db.execute(
        text(
            "UPDATE role_bindings SET extra_agents = CAST(:a AS jsonb) "
            "WHERE user_id = :u AND scope_kind = 'project' AND scope_id = CAST(:p AS uuid)"
        ),
        {"a": json.dumps(current), "u": user_id, "p": project_id},
    )
    logger.info(
        "governance: agent_access granted request=%s user=%s project=%s phase=%s",
        request["id"], user_id, project_id, phase,
    )
    return f"Granted access to the {phase} agent."
```

Confirm `request["requestedById"]` is the actual dict key
`_row_to_dict`/`get_request` uses (cross-check against `governance_requests.py`'s
`_row_to_dict` function — it may be `requestedById` per the camelCase
convention seen elsewhere in that function, verify before assuming).

- [ ] **Step 4: Run, confirm it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k agent_access_request_grants -v`
Expected: PASS.

- [ ] **Step 5: Live-verify**

Via `RequestAgentAccessDialog` in the running app: raise, approve as Project
Admin, approve as the agent's owner, confirm the person can now open that
agent.

- [ ] **Step 6: Commit**

```bash
git add backend/shared/governance/effects.py backend/tests/test_governance_requests.py
git commit -m "feat: agent_access final approval actually grants extra_agents"
```

---

### Task 9: Dedicated raise path + real effect — `user_onboarding`

**Why this one's bigger:** unlike the other four, `user_onboarding` has NO
dedicated raise entry point today (confirmed: only the generic dialog, no
email field anywhere) and its real action (`onboard()` in
`backend/shared/routers/onboarding.py`) is Org-Admin-only. This task builds
both the entry point and the effect, and extracts `onboard()`'s three-act
body into a reusable function so the new effect doesn't duplicate it.

**Files:**
- Modify: `backend/shared/routers/onboarding.py` (extract the shared logic)
- Modify: `backend/shared/governance/effects.py`
- Create: `frontend/components/app/request-onboarding-dialog.tsx` (mirrors
  `request-agent-access-dialog.tsx`'s shape — small, single-purpose, one
  field)
- Modify: wherever a non-Org-Admin should see this new dialog (likely the
  Users page, gated to whoever can see it but not onboard directly — check
  `app/(app)/users/page.tsx` for the existing "Onboard someone" button's
  permission gate and add this as the alternative shown to roles that fail
  that gate)
- Test: `backend/tests/test_governance_requests.py` (extend)

**Interfaces:**
- Consumes: `payload.onboardEmail` from Task 2.
- Produces: `_onboard_person(db, *, tenant_id: str, email: str, display_name:
  Optional[str], workspace_id: Optional[str], role: str, actor_id:
  Optional[str]) -> dict[str, Any]` in `onboarding.py` — the route's existing
  body, extracted verbatim (Step 2), called by both the existing route and
  the new effect (Step 5). `_apply_user_onboarding(db, request) -> str` in
  `effects.py`.

- [ ] **Step 1: Read `onboarding.py`'s `onboard()` route in full**

Read the entire function body (starts ~line 97 per earlier research) — the
three acts (idempotent account creation, workspace bind, `role_assignment`
sub-request) and every validation it performs (`ORG_ASSIGNABLE` role check,
`assert_can_grant_role`, email/name normalization). This task extracts this
body into a plain function the route becomes a thin wrapper around — same
principle as Task 6 reusing `set_project_selection` rather than
reimplementing it.

- [ ] **Step 2: Extract the shared function**

Refactor `onboard()`'s body into a new top-level async function in the same
file (keep it in `onboarding.py`, not a new module — this logic has exactly
one other caller and belongs beside its original), taking the same inputs the
route currently reads off `body`/`request.state`, returning the same dict
shape the route currently returns. The route becomes:

```python
@onboarding_router.post("/onboarding", status_code=201)
async def onboard(
    body: OnboardIn, request: Request, db: AsyncSession = Depends(get_db_session)
) -> dict[str, Any]:
    return await _onboard_person(
        db, tenant_id=_tenant_id(request), email=body.email,
        display_name=body.displayName, workspace_id=body.workspaceId,
        role=body.role, actor_id=getattr(request.state, "user_id", None),
    )
```

Run the EXISTING onboarding tests before touching anything further to
establish a passing baseline, then again immediately after the extraction, to
confirm the refactor is behavior-preserving before any new code is added:

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_onboarding_e2e_flow.py tests/test_onboarding.py -v` (confirm exact test file names first via `find backend/tests -iname "*onboard*"`)
Expected: PASS, identically, both before and after the extraction.

- [ ] **Step 3: Write the failing governance-effect test**

```python
@pytest.mark.asyncio
async def test_user_onboarding_request_onboards_on_org_admin_approval(org):
    """A BU Admin's ask routes DIRECTLY to Org Admin — user_onboarding is
    tier-routed (absent from routing.TYPE_ROUTED), and next_approver_role for
    a bu_admin requester is org_admin, one hop, no escalation needed. Chosen
    over a Developer/Contributor raiser specifically to keep this test to a
    single decide call; a lower-tier raiser's multi-hop path to org_admin is
    covered by Task 1's live baseline trace instead, not duplicated here."""
    bu_admin = f"bu-{_uuid.uuid4()}"
    await _bind(org, bu_admin, "bu_admin", scope_kind="business_unit", scope_id=org["bu"])
    org_admin = f"org-{_uuid.uuid4()}"
    await _bind(org, org_admin, "org_admin", scope_kind="organization", scope_id=org["org"])

    c = TestClient(process_api.app)
    bu_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=bu_admin, tenant_id=org["org"], permissions=["artifact:view", "member:manage"],
    )}
    raised = c.post(
        "/governance-approvals", headers=bu_headers,
        json={
            "type": "user_onboarding", "title": "Onboard a new contributor",
            "description": "We need another QA on this project.", "priority": "normal",
            "workspaceId": org["bu"], "onboardEmail": "new.qa@example.com",
        },
    )
    assert raised.status_code == 201, raised.text
    assert raised.json()["currentApproverRole"] == "org_admin"

    org_headers = {"Authorization": "Bearer " + create_access_token(
        user_id=org_admin, tenant_id=org["org"], permissions=["artifact:view", "governance:decide"],
    )}
    decided = c.post(
        f"/governance-approvals/{raised.json()['id']}/decide", headers=org_headers,
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text
    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(text("SELECT id FROM users WHERE email = 'new.qa@example.com'"))).first()
    assert row is not None
```

- [ ] **Step 4: Run, confirm it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k user_onboarding_request_onboards -v`
Expected: FAIL — still `_DECISION_IS_THE_OUTCOME`, no account created.

- [ ] **Step 5: Implement `_apply_user_onboarding`**

Remove `"user_onboarding"` from `_DECISION_IS_THE_OUTCOME`, add the dispatch
branch:

```python
async def _apply_user_onboarding(db: AsyncSession, request: dict[str, Any]) -> str:
    """Onboard the person the request named — the exact three acts
    onboarding.py's route already performs (idempotent account, workspace
    bind, a role_assignment sub-request for the unit's admin), reused via
    _onboard_person rather than duplicated.

    ORG-ADMIN ONLY, regardless of who technically holds `currentApproverRole`
    at this decision — onboarding.py's own docstring is explicit that this is
    "the Organization Admin's half of the handover", and a Project or BU
    Admin approver genuinely lacks the standing to create an account. A
    request decided below org_admin therefore records agreement only (same
    as before this task) until it actually escalates that far — mirroring
    _apply_connector_access's identical unit-tier guard.
    """
    payload = request.get("payload") or {}
    email = payload.get("onboardEmail")
    if not email:
        raise EffectNotAvailable("user_onboarding", "This request names no email to onboard.")

    if request.get("currentApproverRole") != "org_admin":
        return None  # decision-is-the-outcome until it reaches the tier that can act

    from shared.routers.onboarding import _onboard_person  # noqa: PLC0415

    result = await _onboard_person(
        db, tenant_id=request["tenantId"], email=email, display_name=None,
        workspace_id=request.get("workspaceId"), role="contributor",
        actor_id=request.get("decidedBy"),
    )
    logger.info("governance: user_onboarding applied request=%s email=%s", request["id"], email)
    return f"{email} onboarded to this business unit."
```

Confirm `_onboard_person`'s exact parameter names against what Step 2 actually
produced before finalizing this call — do not assume the signature sketched
here is exact; it depends on how `onboard()`'s real body reads its inputs.

- [ ] **Step 6: Run, confirm it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_governance_requests.py -k user_onboarding_request_onboards -v`
Expected: PASS.

- [ ] **Step 7: Build the dedicated raise dialog**

Create `frontend/components/app/request-onboarding-dialog.tsx`, closely
mirroring `request-agent-access-dialog.tsx`'s shape (small, one real field —
an email input plus a short justification textarea, `createRequest({type:
"user_onboarding", onboardEmail, ...})`). Wire it into `app/(app)/users/page.tsx`
next to (not replacing) the existing Org-Admin-only "Onboard someone" button
— shown to whichever roles can raise `user_onboarding`
(`raisable_types_for`/`canRaiseType`, same guard `RequestAccessButton` already
uses) but cannot onboard directly.

- [ ] **Step 8: Live-verify**

As a Developer in the running app: open the new dialog from Users, submit a
real email, escalate/approve through to an Org Admin account, confirm the
account now exists and can sign in (or at minimum exists in the `users`
table with the right `tenant_id`).

- [ ] **Step 9: Commit**

```bash
git add backend/shared/routers/onboarding.py backend/shared/governance/effects.py backend/tests/test_governance_requests.py frontend/components/app/request-onboarding-dialog.tsx "frontend/app/(app)/users/page.tsx"
git commit -m "feat: user_onboarding gets a real raise path and effect"
```

---

### Task 10 — REMOVED: `role_assignment` is already correctly built

**This task does not exist.** The plan's first draft (following the spec,
which followed the original static audit) assumed clicking Approve on a
`role_assignment` request throws `EffectNotAvailable` as a bare error. Live
tracing this before finalizing the plan (the same discipline Task 1 applies
to everything) found otherwise:
`frontend/components/app/governance-approval-row.tsx:77,138-152` already
special-cases `approval.type === "role_assignment"` with an "Assign role"
button that opens `AssignBusinessUnitRoleDialog` — never the generic
Approve/Reject `ApprovalCard`. That dialog's save action calls `PATCH
/workspaces/{id}/members/{userId}`
(`frontend/lib/api/workspaces.ts::updateWorkspaceMemberRole`), which is
exactly the endpoint `complete_role_assignment`
(`backend/shared/services/governance_requests.py:742`) hooks into to
auto-close the request. The payload the row's button depends on
(`target.userId`) is populated correctly at raise time
(`backend/shared/routers/onboarding.py:237`,
`payload={"userId": user_id, "email": email}`). The whole loop was already
closed before this plan started.

**What replaces it:** Task 1's live baseline audit (Step 7) includes
`role_assignment` in its full trace and records this as a CONFIRMED-WORKING
row rather than a finding requiring a fix — do the live click-through there
(onboard a contributor, confirm the request lands with an "Assign role"
button rather than Approve/Reject, assign the role, confirm the request
disappears from the queue) as part of that task's existing per-type trace,
not as a separate task. No code changes, no commit, for this item.

---

## After all tasks: final whole-branch review

Per this branch's established process, dispatch a final review on the most
capable available model, base = the commit before Task 1 through HEAD,
emphasizing: (1) every finding in
`backend/.superpowers-findings/2026-08-29-request-type-baseline-audit.md`
(Task 1) was either fixed in a later task or explicitly parked with a reason;
(2) no task's live-verification step was skipped — check each task's ledger
entry names an actual observed result, not just "tests pass"; (3) the
`user_onboarding` extraction (Task 9) didn't change `onboard()`'s existing
behavior for the Org-Admin-direct path; (4) `access_request` was genuinely
left untouched, matching spec §3.4's ruling; (5) `role_assignment` really is
already correct as documented in Task 10's removal note — re-verify this
live once during the review rather than trusting the plan's own claim,
matching the exact discipline that caught the plan's own two design errors
(model_provider_access's row-id assumption, and this one) before
implementation started. Fix Critical/Important findings directly in the main
session (established fallback for this branch).
