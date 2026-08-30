# Approval Workflow C — New Request Entry Points — Design

## 1. The problem

Sub-projects A and B made the governance-request system's approve-side machinery correct:
every raisable request type now has a real, reachable, transactionally-sound effect, and
`decide()` correctly scopes who may act on it. This sub-project addresses the other end —
places in the product where a person hits a wall (locked feature, insufficient budget, no
membership on a project) with **no path to actually ask for what they need**, even though
the governance system already has a request type built for exactly that ask.

A dedicated research pass (Explore agent, this session) surveyed every existing "Request X"
entry point, every locked/no-access UI state, and cross-referenced all 17 backend request
types against what the frontend actually wires a button to. Findings, and this spec's
response to each, are below. Two findings originally suspected as live bugs (from sub-project
B's own parked notes) turned out on direct investigation to be non-issues — documented in
§3 rather than silently dropped, matching the precedent set by sub-project A's Task 10.

## 2. Confirmed real gaps, addressed in this plan

### 2.1 A Project Admin whose project is over budget has no way to ask for more

`frontend/app/(app)/projects/[id]/cost/page.tsx` tells a Project Admin at/over their cap:
*"Request headroom to continue; it escalates one tier at a time."* No button anywhere on
or reachable from that page does this. The only `budget_increase` UI in the entire app is
`backend/shared/routers/workspaces.py`'s `POST /workspaces/{id}/budget-increase-request` +
its frontend button on `frontend/app/(app)/workspaces/[id]/page.tsx` — both **workspace-only**.

This is not a UI oversight alone — it is the reachable half of a bug sub-project A's Task 1
already found and explicitly parked: `_apply_budget_increase`'s project-scoped branch
(`backend/shared/governance/effects.py:213-224`, "A Project Admin whose project has exhausted
its total budget raises this against their PROJECT") has been **dead code since it shipped**,
because nothing ever calls `create_request` with `request_type="budget_increase"` and a
`project_id`. This sub-project makes that branch reachable for the first time.

**Fix:** a new backend endpoint, `POST /projects/{id}/budget-increase-request`, mirroring
`request_budget_increase` almost exactly (same `BudgetIncreaseIn` body shape, same `cost:view`
floor, same `create_request` call) but targeting the project: `workspace_id=` the project's
own workspace (required by `create_request`'s signature), `project_id=str(project.id)`,
`target_ref=str(project.id)`. Routing is already correct with no code change needed —
`budget_increase` is tier-routed (absent from `TYPE_ROUTED`), so a Project Admin's raise
climbs to their BU Admin automatically via `next_approver_role`, exactly as the page's own
copy already promises.

A new "Request more budget" button on the project cost page, visible when the project is
over/near its cap and the viewer is that project's Project Admin, calling the new endpoint
with an amount field (mirroring the BU Admin's existing dialog on `workspaces/[id]/page.tsx`
as closely as possible — same amount-entry UX, no need to invent a new pattern).

### 2.2 The Orchestrator's "you're not a member" state has no path forward

`frontend/components/orchestrator/cockpit.tsx:410-419` tells a delivery-role user who reaches
an agent on a project they aren't a member of: *"ask its Project Admin to add you"* — with no
link, not even to `/approvals`. This is the closest real match in the whole app to the
platform's own example of what governance requests are for ("a person asking for something
not yet available"), and it dead-ends completely. `access_request` — raisable by every tier,
generic, exactly this ask — is never wired here.

**Fix:** add a `RequestAccessButton` (type: `access_request`) to this branch. `cockpit.tsx`
already resolves `project.id` and `project.workspaceId` earlier in the same component
(confirmed at lines 339 and surrounding) — both are in scope at the render point, so the
request lands correctly scoped to the specific project, not the generic type-less catch-all
the survey found `access_request` reduced to everywhere else it might be used.

### 2.3 `RestrictedAccess` has no action slot at all

`frontend/components/auth/restricted-access.tsx` and the `ApiErrorState` it wraps
(`frontend/components/feedback/api-error-state.tsx`) render a lock icon and a message with
**no children/action prop whatsoever** — confirmed by reading both components in full. Of the
dozen-plus pages that render a `RestrictedAccess` wall, most are legitimately admin-only
surfaces where "requesting access" doesn't map to anything sensible (the audit log, the role
composer, tenant settings) — see §3.2 for the full list and reasoning for excluding them.
Two are not:

- `frontend/app/(app)/admin/models/page.tsx:322` — gated on `model:manage`. A Contributor or
  Developer landing here (e.g. via a stale link) has a real, sensible ask: `access_request`,
  generic, no specific target — there is no narrower type for "let me see the Models admin
  page," and `access_request`'s whole purpose (per sub-project A's spec) is exactly this
  catch-all case.
- `frontend/app/(app)/cost/page.tsx:23` — gated on `cost:view`, which the page's own existing
  comment notes a Developer holds `artifact:view` but not. Same shape: `access_request`,
  no specific target, workspace-scoped from whatever context is available (org-wide cost page
  has no single project to name).

**Fix:** add one optional prop, `action?: React.ReactNode`, to `ApiErrorState` (rendered
below the existing message, only in the `forbidden` branch — never for a genuine error, where
"request access" makes no sense) and thread it through `RestrictedAccess`. Wire it at exactly
these two call sites with a `RequestAccessButton` (`type: "access_request"`, workspace-scoped
where a workspace is knowable from context, otherwise left unset — matching how `access_request`
is already used at other tier-routed call sites in the codebase). No other `RestrictedAccess`
call site is touched.

### 2.4 `model_credential`'s button on `/admin/models` produces an unapprovable request

`frontend/components/app/model-availability-card.tsx`'s `audience === "project"` button
(rendered from `frontend/app/(app)/admin/models/page.tsx`, `scope === "project"` — i.e. a
Project Admin viewing this BU-oriented page) fires `type: "model_credential"` with only
`workspaceId`, never `projectId`. Confirmed by reading `admin/models/page.tsx` in full: its
`scope` derivation is entirely business-unit-based (`useScopedBusinessUnits`) — the page has
**no project-identifying state anywhere**, for any role, so there is no `projectId` to thread
through even if the component accepted one. This is not a missed prop; the page's own data
model cannot supply what the effect needs.

Per sub-project A's own Task 1 audit (already on record): `model_credential` raised this way
tier-escalates to `bu_admin` (a Project Admin's own raise always climbs one tier), and
`_apply_model_credential` refuses outright with no `project_id` — so this exact button
produces a request that reaches its approver and can **never be approved**, a silent dead
end the requester has no way to discover except by asking their BU Admin why nothing
happened.

**Fix:** when `audience === "project"`, replace the "Request access" button with a short
inline note ("Request this from your project's own Models page") linking to
`/projects/[id]/models` — but since THIS page doesn't know which project either, the link
goes to the Projects list (`/projects`) rather than guessing a specific one, with copy that
makes the destination clear ("open your project's Models page to request this"). This is a
redirect to the already-correct entry point (`frontend/app/(app)/projects/[id]/models/page.tsx`,
confirmed to set `projectId` correctly), not a new flow.

## 3. Findings investigated and explicitly NOT addressed

### 3.1 `agent-readiness-section.tsx`'s connector_access button — not a bug

Sub-project B's ledger listed this (via an earlier, less precise pass) as a likely gap. Direct
investigation this session found its **sole call site** is `frontend/app/(app)/catalogue/page.tsx`
— a deliberately global, project-agnostic page with no route param and no project context
anywhere in scope. The button correctly omits `projectId` because there genuinely is no
project to name from that page. No code change needed; this finding is retired, not silently
dropped — recorded here per this branch's established practice (mirrors sub-project A's
Task 10 removal note) so nobody re-opens it as a mystery later.

### 3.2 The other ten `RestrictedAccess` walls — parked, admin-only surfaces

`admin/models/[provider]/page.tsx` (deliberately WIP-locked for every role including
org_admin, per its own doc comment — not a governance gap at all), `projects/[id]/approvals/page.tsx`,
`approvals/page.tsx` (this page itself hosts the generic raise-request fallback — walling it
off with a request button would be circular), `users/page.tsx` (names colleagues by email;
"request access to see your colleagues" reads oddly and low real-world hit rate), `traces/page.tsx`,
`traces/[id]/page.tsx`, `audit/page.tsx`, `admin/audit/page.tsx`, `activity/page.tsx`,
`settings/page.tsx`, `admin/roles/page.tsx`, `admin/access/roles/page.tsx`,
`integrations/page.tsx`, `integrations/[kind]/page.tsx` — all genuinely admin/governance-only
surfaces where "let me in" isn't a request with a sensible approver-side action (approving
"give me access to the audit log" isn't something any of the built request types models, and
inventing one is out of proportion to the actual gap). Parked, not fixed — worth a second
look only if user feedback shows real friction here, which nothing in this survey found
evidence of.

### 3.3 `RaiseRequestDialog`'s Project-selector default — parked

The generic dialog's Project field defaulting to "Not project-specific" for every type is a
real, systemic UX softness, but every CONCRETE instance the survey found where this actually
produces a bad outcome is independently fixed by §2's targeted work (the cost page and
Orchestrator buttons now supply real project context at the point of raising; the
`admin/models` model_credential case is redirected rather than raised project-less). Changing
the dialog's own default would be a product decision about every OTHER type too (is "not
project-specific" ever the right default? for which types?) — out of proportion to fix here
without becoming its own design exercise. Parked for a future pass if it resurfaces as a real
problem beyond the cases already closed.

### 3.4 `project_creation`'s presence in the raisable-type picker — parked

Raisable by Project Admin per `routing.ts`, but the real "create a project" flow
(`create-project-dialog.tsx`) creates the project directly and the server produces the
pending-approval state — nobody hand-raises a standalone `project_creation` request through
the generic picker in practice, and doing so would produce an orphaned request with no real
project attached. Low real-world risk (a determined user picking this from a dropdown gets a
request nobody can act on, not a security issue) — worth a one-line cleanup (removing it from
the picker) but not urgent enough to hold up this sub-project. Noted for whoever next touches
`routing.ts`'s raisable-type tables.

### 3.5 `project-model-selection-card.tsx` — already mitigated

Its own "ask your BU Admin" copy has no button, but the parent page
(`projects/[id]/models/page.tsx`) already carries a page-level "Request a model" button
covering the same ask. Inconvenient (the copy and the button are visually separated) but not
a dead end. No fix needed.

## 4. Scope for this sub-project

**In scope:**
- New backend endpoint `POST /projects/{id}/budget-increase-request` + its frontend button
  on the project cost page (§2.1) — the only piece of this sub-project that adds a new
  backend route; everything else is frontend-only.
- `RequestAccessButton` wired into the Orchestrator's "not a member" state (§2.2).
- `ApiErrorState`/`RestrictedAccess` gain an optional `action` slot, wired at exactly two
  call sites (§2.3).
- `model-availability-card.tsx`'s `audience === "project"` button replaced with a redirect
  (§2.4).
- A live-verified regression test for §2.1's new endpoint proving the previously-dead
  project-scoped effect branch in `_apply_budget_increase` is now genuinely reachable
  end-to-end (raise → BU Admin approves → project's `monthly_budget_usd` actually changes) —
  this is the same "trace an actual UI button to a database write" discipline sub-project A's
  Global Constraints established.

**Out of scope, explicitly (§3 covers the reasoning for each):**
- The ten parked `RestrictedAccess` walls.
- `RaiseRequestDialog`'s Project-selector default behavior itself.
- `project_creation`'s raisable-type picker entry.
- Any change to `agent-readiness-section.tsx` (confirmed not a bug).
- Sub-project D (break-glass) — unrelated, separate sub-project.

## 5. Risk and rollback

§2.1's new endpoint is additive (a new route, no existing route touched) and reuses
`create_request`'s existing, already-correct `budget_increase` handling — the only genuinely
new logic is which scope field (`project_id` vs `workspace_id`) gets set, mirroring
`_apply_budget_increase`'s own already-shipped, already-tested branch selection. §2.2-2.4 are
all additive UI changes (new button, new optional prop, a link replacing a button) with no
change to any existing request-raising behavior for a case that already worked.
