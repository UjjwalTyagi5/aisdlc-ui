# Approval Workflow Sub-Project C: New Request Entry Points — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** every confirmed real dead-end this sub-project's research found — a place where a
person hits a wall with no way to ask for what they need, even though the governance system
already has a request type built for exactly that ask — gets a real, working path forward.

**Architecture:** four independent frontend fixes plus one new backend endpoint. The backend
piece (Task 1) makes reachable, for the first time, a project-scoped effect branch
(`_apply_budget_increase`'s `project_id` path) that has been dead code since it shipped in
sub-project A — nothing has ever called `create_request` with `request_type="budget_increase"`
and a `project_id`. Tasks 2-5 are frontend-only: a new button wired to Task 1's endpoint, a
small reusable "action" slot added to the existing `ApiErrorState`/`RestrictedAccess`
components and wired at two genuinely request-worthy walls, a button added to the
Orchestrator's one true "ask someone to add me" dead end, and one existing button (which
produces an un-approvable request today) replaced with a redirect to the page where the
identical ask already works correctly.

**Tech Stack:** FastAPI + SQLAlchemy (async), Next.js + React Query + Zod. Live verification
against the real running dev stack (`127.0.0.1:8001` + local Postgres) is required for Task 1
(it makes a previously-unreachable database write reachable for the first time) — see Global
Constraints.

**Spec:** `docs/superpowers/specs/2026-08-30-approval-workflow-c-request-entry-points-design.md`

## Global Constraints

- **Task 1 ends with a LIVE verification**, not just a green `pytest` run: hit the real
  running backend at `127.0.0.1:8001` (restart it cleanly first — kill whatever's on port
  8001, then `cd backend && uv run uvicorn process_api:app --host 127.0.0.1 --port 8001
  --reload`, backgrounded), raise a real `budget_increase` request against a real project,
  approve it as the correct BU Admin, then directly query the database to confirm the
  project's `monthly_budget_usd` actually changed. This is the exact discipline that found
  sub-project A's central bug — do not skip it for a change that touches money.
- **Run any test command synchronously, in the foreground of your own turn.** Do not
  background a long-running test command and end your turn waiting for a notification — this
  mistake happened twice during sub-project B and left uncommitted work stranded both times.
- **Sub-projects A and B's already-shipped code is not to be modified in this plan**, except
  by calling into it (`create_request`, `_apply_budget_increase` — both already correct and
  already tested; this plan only makes a new caller reach them).
- **Do not touch any of the ten `RestrictedAccess` call sites parked in the spec's §3.2** —
  only `admin/models/page.tsx` and `cost/page.tsx` get the new action slot wired in.
- **Do not change `RaiseRequestDialog`'s Project-selector default behavior** — parked in the
  spec's §3.3, out of scope for this plan.
- New backend endpoint follows `POST /workspaces/{id}/budget-increase-request`'s exact
  existing shape (`backend/shared/routers/workspaces.py:427-501`) — same `BudgetIncreaseIn`
  body, same `cost:view` floor, same `create_request` call shape — the only difference is
  which scope field (`project_id` vs `target_ref`/`workspace_id`) gets set.

---

### Task 1: Backend — `POST /projects/{id}/budget-increase-request`

**Files:**
- Modify: `backend/shared/routers/projects.py` (new route + reused `BudgetIncreaseIn` model)
- Test: `backend/tests/test_project_scoped.py` (extend)

**Interfaces:**
- Produces: `POST /projects/{project_id}/budget-increase-request` → `201`, same response
  shape `create_request` already returns (a `GovernanceApproval`-schema dict). Task 2 (frontend)
  calls this exact route.

- [ ] **Step 1: Write the failing test**

Read `backend/shared/routers/projects.py`'s imports and existing patterns first — confirm
`_get_or_404`, `can_perform`, `governance_service` (imported as `from shared.services import
governance_requests as governance_service`), `require_permission`, `actor_display_name`,
`effective_platform_role` are all already imported (they are, as of this plan's writing —
confirm before assuming, since sub-projects A/B may have touched this file too).

```python
@pytest.mark.asyncio
async def test_project_budget_increase_request_reaches_the_bu_admin_and_applies(org):
    """The exact bug sub-project A's Task 1 parked: _apply_budget_increase's project_id
    branch (effects.py) has been dead code since it shipped — nothing has ever called
    create_request with request_type="budget_increase" and a real project_id. This
    proves the new endpoint makes that branch genuinely reachable end to end."""
    pa = await _user(org, "projadmin")
    bua = await _user(org, "buadmin")
    await _bind_project_admin(org, pa)
    await _bind_bu_admin(org, bua)

    c = TestClient(process_api.app)
    r = c.post(
        f"/projects/{org['project']}/budget-increase-request",
        headers=_headers(pa, org["org"], ["cost:view", "artifact:view"]),
        json={"requestedAmountUsd": 500, "reason": "Ran out mid-sprint."},
    )
    assert r.status_code == 201, r.text
    req_id = r.json()["id"]
    # Tier-routed (budget_increase is absent from routing.TYPE_ROUTED): a Project
    # Admin's raise climbs to their BU Admin, exactly as the cost page's own copy
    # already promises ("it escalates one tier at a time").
    assert r.json()["currentApproverRole"] == "bu_admin"

    from shared.services import governance_requests as gov
    async with get_db_session_for_tenant(org["org"]) as s:
        await gov.decide(s, request_id=req_id, decider_id=bua, decider_name="Bua",
                         decider_role="bu_admin", decision="approve")

    async with get_db_session_for_tenant(org["org"]) as s:
        row = (await s.execute(
            text("SELECT monthly_budget_usd FROM projects WHERE id = CAST(:p AS uuid)"),
            {"p": org["project"]},
        )).first()
    assert float(row.monthly_budget_usd) == 500.0
```

Read `test_project_scoped.py`'s `org` fixture (`test_project_scoped.py:29-50`) and
`_bind_project_admin`/`_bind_bu_admin` helpers (added in sub-project B's final-review fix
round, `test_project_scoped.py:576-590`) before writing this — confirm the exact `org` dict
keys (`org["org"]`, `org["payments"]`, `org["project"]`) match what's actually there, since
this plan's own draft used `org["bu"]` initially and the real key is `org["payments"]`
(fixed in sub-project B's final-review round — verify against the current file, not this
paragraph).

- [ ] **Step 2: Run, confirm it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped.py -k budget_increase_request_reaches -v`
Expected: FAIL — `404 Not Found` (the route doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

In `backend/shared/routers/projects.py`, add near the project's other single-project mutation
routes (after `get_project`, `~line 308`, or wherever the file's own convention groups
project-scoped POST routes — follow the existing file's own organization, don't force a
specific line number):

```python
@projects_router.post("/{project_id}/budget-increase-request", status_code=201)
async def request_project_budget_increase(
    project_id: str,
    request: Request,
    body: BudgetIncreaseIn,  # reuse workspaces.py's model — same shape, no new schema needed
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """The Project Admin's half of the budget cascade (mirrors
    workspaces.py::request_budget_increase, the Business Unit Admin's half).

    THE PROJECT, NOT THE WORKSPACE. `_apply_budget_increase` (shared/governance/effects.py)
    has always supported a project-scoped target — "A Project Admin whose project has
    exhausted its total budget raises this against their PROJECT" — but nothing has ever
    called create_request with a project_id for this type, so that branch has been dead
    code since it shipped (sub-project A, Task 1, parked finding). This is that caller.

    The floor is cost:view, not project:update, matching workspaces.py's own reasoning
    exactly: asking is not changing, and whoever can see a cap about to bind is who should
    be able to raise it.
    """
    tenant_id = request.state.tenant_id
    project = await _get_or_404(db, project_id, tenant_id)
    if not await can_perform(
        db, user_id=_user_id(request), permission="cost:view",
        tenant_id=str(tenant_id), resource_kind="project", resource_id=str(project.id),
    ):
        raise HTTPException(status_code=404, detail="Project not found")

    from shared.authz.effective_role import actor_display_name, effective_platform_role  # noqa: PLC0415
    from shared.services import governance_requests as governance_service  # noqa: PLC0415
    from shared.services.governance_requests import GovernanceError  # noqa: PLC0415

    amount = body.requestedAmountUsd
    current = float(project.monthly_budget_usd) if project.monthly_budget_usd is not None else None
    detail = (
        f"{project.display_name} is asking to move its monthly cap "
        + (f"from {current:.0f} " if current is not None else "")
        + f"to {amount:.0f} USD."
        + (f" {body.reason}" if body.reason else "")
    )

    try:
        return await governance_service.create_request(
            db,
            tenant_id=str(tenant_id),
            initiator_id=getattr(request.state, "user_id", "") or "",
            initiator_name=await actor_display_name(db, request),
            initiator_role=await effective_platform_role(db, request),
            request_type="budget_increase",
            title=f"Budget increase: {project.display_name} — {amount:.0f} USD/month",
            description=detail,
            workspace_id=str(project.workspace_id),
            project_id=str(project.id),
            target_ref=str(project.id),
            payload={"requestedAmountUsd": amount, "previousAmountUsd": current},
            priority="high",
        )
    except GovernanceError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code, "message": str(exc)}
        )
```

Confirm `create_request`'s exact current parameter names (`project_id` vs `projectId`,
keyword-only or not) against `backend/shared/services/governance_requests.py` before using
this snippet verbatim — it has been touched by both prior sub-projects.

- [ ] **Step 4: Run, confirm it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped.py -k budget_increase_request_reaches -v`
Expected: PASS.

- [ ] **Step 5: Run the full test_project_scoped.py suite — confirm no regressions**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped.py -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Live-verify**

Restart the backend cleanly. Against real tenant `8d5bd6a3-7e07-46ce-8416-cada90dead79` (or a
freshly-seeded one via the test pattern, if cleaner): raise a real `budget_increase` request
via `POST /projects/{project_id}/budget-increase-request` as a real Project Admin, approve it
as the real BU Admin, and directly query `projects.monthly_budget_usd` to confirm it changed.
Clean up any scratch state afterward.

- [ ] **Step 7: Commit**

```bash
git add backend/shared/routers/projects.py backend/tests/test_project_scoped.py
git commit -m "feat: a Project Admin can request more budget for their own project, not just a Business Unit Admin for their unit"
```

---

### Task 2: Frontend — "Request more budget" button on the project cost page

**Files:**
- Modify: `frontend/app/(app)/projects/[id]/cost/page.tsx`
- Create: `frontend/lib/api/projects.ts` — add `requestProjectBudgetIncrease` (or the
  equivalent existing file if project API calls live elsewhere — check
  `frontend/lib/api/projects.ts` first; it likely already has `getProject`/`updateProject`
  from this same page's own imports).
- Test: whatever this repo's convention is for this page (check for an existing
  `__tests__/app/*cost*` file first; if none exists, a new lightweight RTL test asserting the
  button appears when over-cap and the viewer is `project_admin`, and is absent otherwise, is
  sufficient — do not invent a broader test suite for a page that has none today).

**Interfaces:**
- Consumes: `POST /projects/{id}/budget-increase-request` (Task 1).
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Add the API client function**

In `frontend/lib/api/projects.ts`, alongside the file's existing project API calls:

```typescript
export const requestProjectBudgetIncrease = (
  id: string,
  body: { requestedAmountUsd: number; reason?: string },
) =>
  api(`/projects/${encodeURIComponent(id)}/budget-increase-request`, {
    method: "POST",
    body,
    schema: GovernanceApproval,
  });
```

Check the exact `api()` helper signature and `GovernanceApproval` schema import path against
`frontend/lib/api/workspaces.ts:56-64`'s existing, identical-shape `requestBudgetIncrease` —
copy its import style exactly rather than guessing.

- [ ] **Step 2: Add the button and an inline amount-entry form to the cost page**

`frontend/app/(app)/projects/[id]/cost/page.tsx` already has a proven inline expand-to-edit
pattern in its own `ProjectBudgetCard` component (`:255-370`ish — an `editing` boolean toggling
between a compact button and an inline `$ [amount input] [Save] [X]` row). That pattern is for
DIRECT edits (BU/Org Admin, gated on `canSetCap`) — a genuinely different flow from a Project
Admin's REQUEST — so do not extend `ProjectBudgetCard` itself; add a small, separate,
sibling component in the same file following the identical inline-toggle SHAPE (own `useState`
for `open`/amount/reason, not a modal `Dialog` — matching this file's own established taste for
inline over modal).

In the cap-state block (`:154-181`, the `{cap > 0 && ratio >= 0.8 && ...}` block), inside the
`ratio &gt;= 1` branch specifically (`:166-171`, the "over its total cap" copy), add:

```tsx
{ratio >= 1 && role === "project_admin" && (
  <ProjectBudgetIncreaseRequest projectId={id} />
)}
```

New component, defined alongside `ProjectBudgetCard` in the same file:

```tsx
function ProjectBudgetIncreaseRequest({ projectId }: { projectId: ProjectId }) {
  const [open, setOpen] = React.useState(false);
  const [amount, setAmount] = React.useState("");
  const [reason, setReason] = React.useState("");

  const mutation = useMutation({
    mutationFn: () =>
      requestProjectBudgetIncrease(projectId, {
        requestedAmountUsd: Number(amount),
        reason: reason.trim() || undefined,
      }),
    onSuccess: () => {
      toast.info("Sent for approval", {
        description: "Your Business Unit Admin needs to approve this before the budget changes.",
      });
      setOpen(false);
      setAmount("");
      setReason("");
    },
    onError: (e) =>
      toast.error("Couldn't send request", {
        description: e instanceof Error ? e.message : undefined,
      }),
  });

  if (!open) {
    return (
      <Button size="sm" variant="outline" className="h-7 shrink-0 font-mono text-[11px]" onClick={() => setOpen(true)}>
        Request more budget
      </Button>
    );
  }

  const parsed = Number(amount);
  const valid = amount.trim() !== "" && Number.isFinite(parsed) && parsed > 0;

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <span className="text-muted-foreground font-mono text-[13px]">$</span>
      <Input
        type="number" min={0} step="1" autoFocus value={amount}
        onChange={(e) => setAmount(e.target.value)}
        placeholder="New monthly cap"
        className="border-line-soft h-8 w-40 font-mono text-[12px]"
      />
      <Input
        value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder="Reason (optional)"
        className="border-line-soft h-8 w-56 font-mono text-[12px]"
      />
      <Button
        size="sm" className="h-7" disabled={!valid || mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        {mutation.isPending ? <Loader2 className="size-3.5 animate-spin" /> : "Send"}
      </Button>
      <Button size="sm" variant="ghost" className="h-7 w-7 p-0" onClick={() => setOpen(false)}>
        <X className="size-3.5" aria-hidden />
      </Button>
    </div>
  );
}
```

`Input`, `Button`, `Loader2`, `X`, `toast`, `useMutation` are all already imported in this file
(used by `ProjectBudgetCard` and the page's other mutations) — no new imports needed beyond
`requestProjectBudgetIncrease` from Step 1.

- [ ] **Step 3: Manual verification**

Start the frontend (`cd frontend && npm run dev`) and backend if not already running. As a
real Project Admin on a project at/over its cap, confirm the button appears, opens the
dialog, and a submitted request shows up in `/approvals` for the correct BU Admin. As a
Developer/Contributor on the same project, confirm the button does NOT appear.

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/api/projects.ts "frontend/app/(app)/projects/[id]/cost/page.tsx"
git commit -m "feat: project cost page gets a real 'Request more budget' button, matching what it already promises"
```

---

### Task 3: Frontend — `ApiErrorState`/`RestrictedAccess` action slot, wired at two walls

**Files:**
- Modify: `frontend/components/feedback/api-error-state.tsx`
- Modify: `frontend/components/auth/restricted-access.tsx`
- Modify: `frontend/app/(app)/admin/models/page.tsx`
- Modify: `frontend/app/(app)/cost/page.tsx`
- Test: extend or create RTL coverage for `RestrictedAccess`'s new prop (check
  `frontend/__tests__/components/` for an existing `restricted-access` test first).

**Interfaces:**
- Produces: `ApiErrorState`'s `action?: React.ReactNode` prop, threaded through
  `RestrictedAccess`'s own new `action?: React.ReactNode` prop.

- [ ] **Step 1: Add the `action` prop to `ApiErrorState`**

In `frontend/components/feedback/api-error-state.tsx`, add to `ApiErrorStateProps`
(`:9-19`):

```typescript
export interface ApiErrorStateProps {
  error?: ApiError | null;
  title?: string;
  description?: React.ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
  /** Rendered below the message, ONLY in the forbidden branch — "request access"
   *  makes no sense for a genuine error, only for a permission limit. */
  action?: React.ReactNode;
}
```

Render it inside the component body, in the existing `forbidden` conditional region (near
the retry-button section at the end of the component, `:98-104` roughly — but gated on
`forbidden`, not on `!forbidden` the way the retry button is):

```tsx
{forbidden && action}
```

- [ ] **Step 2: Thread it through `RestrictedAccess`**

In `frontend/components/auth/restricted-access.tsx`:

```typescript
export function RestrictedAccess({
  title = "Access restricted",
  description,
  action,
}: {
  title?: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-4xl p-4 md:p-8">
      <ApiErrorState title={title} description={description} action={action} />
    </div>
  );
}
```

- [ ] **Step 3: Wire it at `admin/models/page.tsx`'s `model:manage` wall**

At `frontend/app/(app)/admin/models/page.tsx:320-324` (the `!hasPermission(session,
"model:manage")` branch):

```tsx
if (!hasPermission(session, "model:manage") || !scope) {
  return (
    <RestrictedAccess
      description="Model providers require the model:manage permission."
      action={
        <RequestAccessButton
          prefill={{
            type: "access_request",
            title: "Access to Model Management",
            description: "Requesting access to the Model Management admin page.",
          }}
        />
      }
    />
  );
}
```

Add the `RequestAccessButton` import (`@/components/requests/request-access-button`) at the
top of the file if not already present.

- [ ] **Step 4: Wire it at `cost/page.tsx`'s `cost:view` wall**

At `frontend/app/(app)/cost/page.tsx:20-24` (the `!hasPermission(session, "cost:view")`
branch), same pattern:

```tsx
if (!hasPermission(session, "cost:view")) {
  return (
    <RestrictedAccess
      description="Cost visibility requires the cost:view permission. Ask your admin for access."
      action={
        <RequestAccessButton
          prefill={{
            type: "access_request",
            title: "Access to Cost & Budget",
            description: "Requesting access to view cost and budget data.",
          }}
        />
      }
    />
  );
}
```

- [ ] **Step 5: Confirm `access_request` is actually raisable by whoever hits these walls**

Before finalizing, check `frontend/lib/requests/routing.ts`'s raisable-type tables (mirroring
`backend/shared/governance/routing.py`'s `_CONTRIBUTOR_RAISABLE`) to confirm `access_request`
is raisable by every role likely to hit these two specific walls (a Developer/Contributor
lacking `model:manage` or `cost:view`) — it is, per the spec's own research (`access_request`
is in all three `*_RAISABLE` lists), but verify directly against the current file rather than
trusting this plan's summary, since raisable-type tables are exactly the kind of thing that
drifts.

- [ ] **Step 6: Manual verification**

As a Developer (who holds neither `model:manage` nor `cost:view`), visit `/admin/models` and
`/cost` directly and confirm the "Request access" button now appears on both walls and opens
the raise dialog correctly.

- [ ] **Step 7: Commit**

```bash
git add frontend/components/feedback/api-error-state.tsx frontend/components/auth/restricted-access.tsx "frontend/app/(app)/admin/models/page.tsx" "frontend/app/(app)/cost/page.tsx"
git commit -m "feat: RestrictedAccess can offer a real request-access path, wired at the Model Management and Cost admin walls"
```

---

### Task 4: Frontend — Orchestrator's "not a member" state gets a real request button

**Files:**
- Modify: `frontend/components/orchestrator/cockpit.tsx`

**Interfaces:**
- Consumes: nothing from earlier tasks in this plan.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Add the import**

At the top of `frontend/components/orchestrator/cockpit.tsx` (alongside its existing
component imports, `:13-18`):

```typescript
import { RequestAccessButton } from "@/components/requests/request-access-button";
```

- [ ] **Step 2: Add the button to the "not a member" branch**

At `cockpit.tsx:410-419` (inside the `!reachesAnyAgent ? ... : (...)` conditional's `else`
branch — the "Read-only — you're not a member of {project}" copy):

```tsx
<>
  Read-only — you&apos;re not a member of{" "}
  <span className="text-foreground font-medium">
    {project?.name ?? "this project"}
  </span>
  . Reaching an agent elsewhere isn&apos;t standing to run it on this team&apos;s
  work; ask its Project Admin to add you, or{" "}
  <RequestAccessButton
    variant="ghost"
    size="sm"
    label="request access"
    prefill={{
      type: "access_request",
      title: `Access to ${project?.name ?? "this project"}`,
      description: "Requesting to join this project as a contributor.",
      projectId: project?.id ? String(project.id) : undefined,
      workspaceId: project?.workspaceId ?? undefined,
    }}
  />
  .
</>
```

Check `RequestAccessButton`'s actual prop shape (`variant`/`size`/`label` — confirmed present
at `frontend/components/requests/request-access-button.tsx:32-44`) and adjust the inline
JSX to read naturally alongside the surrounding sentence — this is prose with an embedded
button, not a standalone CTA, so the exact wording/layout should match the file's own tone,
not be pasted verbatim without reading how it renders.

- [ ] **Step 3: Manual verification**

As a Developer/delivery-role user reaching an agent on a project they are not a member of,
confirm the "request access" affordance appears in the Orchestrator's read-only banner and
opens the raise dialog with the project correctly prefilled (check the raised request's
`projectId` in the network tab or by inspecting the created request afterward — it must
carry the real project, not land project-less the way the generic dialog's default would).

- [ ] **Step 4: Commit**

```bash
git add frontend/components/orchestrator/cockpit.tsx
git commit -m "feat: Orchestrator's 'not a member' dead end now offers a real access_request path, correctly scoped to the project"
```

---

### Task 5: Frontend — redirect the un-approvable `model_credential` button on `/admin/models`

**Files:**
- Modify: `frontend/components/app/model-availability-card.tsx`

**Interfaces:**
- Consumes: nothing from earlier tasks in this plan.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Add the `Link` import**

`frontend/components/app/model-availability-card.tsx` does not currently import
`next/link` (confirmed by reading its imports). Add:

```typescript
import Link from "next/link";
```

- [ ] **Step 2: Replace both `model_credential` buttons with a redirect note**

There are exactly two `RequestAccessButton` call sites sharing the same `requestType`
variable (`requestType = audience === "bu" ? "model_provider_access" : "model_credential"`,
`:73`) — the empty-state one (`:142-151`, inside `rows.length === 0`) and the
ungranted-catalogue one (`:205-219`, inside the `ungranted.map(...)`). Both need the same
`audience === "project"` carve-out, since both currently render an un-approvable
`model_credential` request when this component is used from `admin/models/page.tsx`.

Empty-state button (`:141-151`) — replace the `RequestAccessButton` with:

```tsx
{audience === "project" ? (
  <Link
    href="/projects"
    className="text-brand-bright text-[11.5px] underline underline-offset-2"
  >
    Open your project&apos;s own Models page to request this
  </Link>
) : (
  <RequestAccessButton
    label="Request model access"
    prefill={{
      type: requestType,
      title: "Model access",
      description: `${workspaceName} holds no models. Which we need, and what for:`,
      workspaceId,
    }}
  />
)}
```

Ungranted-catalogue button (`:205-219`) — same carve-out, keeping the rest of the `<li>` row
(the model name/provider spans at `:199-204`) unchanged, only swapping the trailing
`RequestAccessButton`:

```tsx
{audience === "project" ? (
  <Link
    href="/projects"
    className="text-brand-bright shrink-0 text-[11px] underline underline-offset-2"
  >
    Request from your project&apos;s Models page
  </Link>
) : (
  <RequestAccessButton
    prefill={{
      type: requestType,
      title: `${m.label} access`,
      description: `Requesting ${m.label} (${m.providerLabel}) for ${workspaceName}. It isn't granted to us today.`,
      workspaceId,
      providerModel: { provider: m.provider, modelId: m.model_id },
    }}
  />
)}
```

Note the `description` in this second snippet is fixed to the `audience === "bu"` wording
only, since the `audience === "project"` branch no longer reaches `RequestAccessButton` at
all — delete the `audience === "bu" ? ... : ...` ternary that previously produced the
project-audience description text, since it's now dead.

- [ ] **Step 3: Add one sentence to the `requestType` derivation's comment**

At `:65-73`, the existing comment explaining the bu/project distinction gets one addition
explaining WHY `audience === "project"` no longer gets a button here: this page
(`admin/models`) has no per-project state for any role — its `scope` derivation is entirely
business-unit-based (`useScopedBusinessUnits`) — so a request raised from here can never
carry the `project_id` `_apply_model_credential` requires to actually apply. See
`projects/[id]/models/page.tsx` for the correct, already-working, already-project-scoped
entry point for this exact ask.

- [ ] **Step 4: Manual verification**

As a Project Admin viewing `/admin/models` (which resolves `scope === "project"` for that
role per the page's own logic), confirm the ungranted-model rows now show the redirect link
instead of a button, and that clicking it lands on `/projects`. Separately confirm
`/projects/[id]/models` (the correct entry point) still shows its own, unaffected
`RequestAccessButton` for the same ask.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/app/model-availability-card.tsx
git commit -m "fix: admin/models' model_credential button produced an unapprovable request (no project_id it could ever carry) — redirect to the project's own Models page instead"
```

---
