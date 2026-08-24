# Model Management RBAC Redesign — Design Spec

**Status:** approved by user, proceeding to implementation.
**Branch:** `worktree-model-management-rbac-redesign` (worktree at
`.claude/worktrees/model-management-rbac-redesign`), based on `origin/main` @ `25965a8a`
(includes PR #19).

## 1. Problem

Today's Model Management flow conflates three distinct decisions into one dialog
(`AddModelDialog`, opened by "Add provider"):
1. Whether a Business Unit may use a provider at all.
2. Which specific models under that provider are permitted.
3. The actual credential (API key) that makes a model callable.

A Business Unit Admin can currently register **any** provider from scratch — nothing
gates them to providers the Org Admin actually approved for their unit. This is the
one problem the user named directly: "he should not have the ability to add a model
... only of the provider that has been given to him."

The user wants the same three-tier shape the **connector system** already has and
enforces correctly: Org Admin grants *access to a kind*, a unit-level admin brings
their own credential for it, a project consumes what's been made available to it.

## 2. Decisions made (confirmed with user during brainstorming; #1 and #2 amended mid-implementation — see the note after #5)

1. ~~Org Admin grants are provider-level only, no per-model picking~~ — **AMENDED, see #5.**
2. ~~Org-wide keys survive as a separate Org Admin action~~ — **REVERSED, see #5.**
3. **Existing data gets a migration script**, not left to rot — see §7.
4. **BU Admin pushes keys to specific projects; Project Admin only picks a master**
   among what's been pushed (not free self-service selection from the whole BU
   catalogue, which is today's behavior).
5. **AMENDMENT (mid-implementation, after Tasks 1-4 were already built and reviewed):**
   Org Admin's flow is **provider grant PLUS per-model curation, both keyless** — not
   provider-only as #1 originally said:
   - Granting a provider to a BU (Tasks 1-4's `integration_grants` / `kind='model_provider'`
     mechanism) is **unchanged and still required** — it's still the access gate BU Admin's
     key-adding checks against.
   - **Restored**, not retired: Org Admin ALSO picks which specific models of a granted
     provider a BU may use — the same curation `org_model_grants` already did before this
     redesign started (§4's original "retire from the write path" framing is **reversed**;
     `org_model_grants` keeps its write path, unmodified, exactly as it worked before Task 1).
     Reasoning: `org_model_grants` never carried a credential (`credential_id` is
     nullable and keys live in `model_providers`), so restoring the org-admin write path
     to it does not reopen the "org admin holds keys" problem this whole redesign exists
     to close — it's a genuinely separate, keyless axis (WHICH models) layered on top of
     the new WHETHER-the-provider-is-reachable-at-all gate.
   - Clicking a provider (Org Admin view) shows a matrix of which BUs have which specific
     models enabled, not just which BUs hold the provider grant — both facts, together.
   - **The separate "add an org-wide key" action from original decision #2 is removed
     entirely.** Org Admin never adds, holds, or sees a credential anywhere in the
     product, full stop — confirmed explicitly when this amendment was made. Every real
     key, including what would have been a global/centrally-credentialed one, must now
     be added at the BU level. (A `model_providers` row with `workspace_id IS NULL` and
     a key remains structurally possible in the schema/backend — nothing in this
     amendment removes that capacity from the data model or from `resolve_model_for_run`
     — but there is no UI action for an Org Admin to create one anymore.)
   - **Consequence for BU Admin's "Add key" flow (§5, Task 10):** the model picker in
     that flow must be constrained to models the Org Admin actually curated for this BU
     (via the restored `org_model_grants` mechanism) — not every model the provider
     happens to offer. This preserves the cascade's original invariant: "what a unit may
     use is DERIVED from these grants, never a list they curate" (the exact phrase
     `org_model_grants`'s own migration docstring uses) — the invariant this amendment
     restores rather than breaks.

## 3. Data model

### 3.1 Reuse `integration_grants`, don't build a parallel table

`integration_grants` (migration `0015_integration_grants`) already is exactly "which
Business Units may use which X," generalized over a `kind` column (`'connector'` |
`'mcp'`) plus a `target_ref` (text, so it fits both a connector slug and an MCP
server's uuid). Its own docstring states the reasoning this redesign needs verbatim:
*"They are the same decision made about two kinds of thing, and splitting them would
mean every reader unions two queries and every writer picks a table."* A provider
grant (`kind='model_provider'`, `target_ref='anthropic'`) is the same shape again.

**Migration `00NN_model_provider_grants_kind.py`:**
```sql
ALTER TABLE integration_grants DROP CONSTRAINT ck_integration_grant_kind;
ALTER TABLE integration_grants ADD CONSTRAINT ck_integration_grant_kind
  CHECK (kind IN ('connector', 'mcp', 'model_provider'));
```
No new table, no new RLS policies, no new indexes — everything on `integration_grants`
already covers the new kind for free.

**Reuse the existing helpers directly**, passing `kind="model_provider"`:
- `shared/authz/connector_grants.py::granted_target_refs(db, tenant_id, workspace_id, kind="model_provider")`
  → the set of provider kinds this BU may use.
- `shared/authz/connector_grants.py::unit_is_granted(...)` → single-provider check.

Both already take `kind` as a parameter (default `"connector"`), added in PR #19
specifically because a picker checking a whole catalogue needs one batched query —
exactly what the BU Admin's provider grid needs.

**Grant/revoke**: new thin functions in the same module, `grant_provider(db, *,
tenant_id, workspace_id, provider, granted_by)` / `revoke_provider(...)`, each a
one-row upsert/delete against `integration_grants` with `kind='model_provider'` — no
new pattern, just parameterizing what already exists for connectors.

### 3.2 `model_providers` (existing table) — new constraint at creation

- Org-wide creation (`workspace_id IS NULL`): backend route **unchanged** (Task 4
  deliberately left this branch untouched) — still technically keyless-or-keyed at the
  API level. Per §2 amendment 5, there is simply **no Org Admin UI action that calls
  this path anymore** — it remains reachable only as a backend/data-model capability,
  not a product feature. Nothing in this spec asks for it to be removed from the
  backend; removing an unused-by-the-UI capability is out of scope and not requested.
- BU-scoped creation (`workspace_id = <bu>`): **new required check** — the handler
  must find a `model_provider`-kind `integration_grants` row for `(workspace_id,
  provider)` before allowing creation. No grant, no key.
- `api_key` becomes **required** (not optional) specifically on the BU-scoped
  creation path. Org-wide creation keeps the optional/keyless path as-is.

### 3.3 `org_model_grants` — write path RESTORED per §2 amendment 5 (originally this
section said "retired"; reversed mid-implementation before any task touched it)

Per-model grants stay something the Org Admin curates, exactly as before this
redesign — `getOrgModelGrants`/`setOrgModelGrants` (`GET`/`PUT /model/allowed/org`)
are **unmodified by this plan**, not new work. What's new is the *sequencing* the
Org Admin's UI now presents: curating models for a BU is only meaningful (and only
shown) for a provider that BU already holds a `model_provider`-kind `integration_grants`
row for (§3.1) — the two layers compose (provider access gate, then model curation
within it) rather than either replacing the other. `getModelGrantMatrix` continues to
read `org_model_grants` for the Org Admin's oversight table exactly as it always has.

**Per §2 amendment 5**: BU Admin's "Add key" model picker (§5, Task 10) must be
constrained to models `org_model_grants` actually curated for this BU under the
provider being keyed — read via the existing BU-allowed derivation
(`getBuAllowedModels`/`GET /model/allowed/bu`, unmodified), not every model the
provider's catalog happens to list.

### 3.4 `ProjectModelSelection` — reused, but who writes `selected` changes

Schema is unchanged (`inherited`, `selected: ModelAllowEntry[]`, `usingDefaults`,
`defaultKey`). What changes: a new BU Admin action, `assignKeyToProject(projectId,
credentialId)`, appends to a project's `selected` (today only the project itself
writes this via `setProjectModelSelection`). Project Admin's own write narrows to
picking `defaultKey` among `selected` — still `setProjectModelSelection`, just with
`selected` now typically pre-populated by their BU Admin rather than self-chosen.

## 4. Backend API changes

All new/changed routes live in `shared/routers/model.py`, under the existing
`/model` prefix. Permission model mirrors connectors' `view`/`manage` split rather
than introducing new permission strings — `model:manage` already gates the whole
router; the new work is **resource-scoped enforcement** via `_require_scoped`
(already in this file) plus a `granted_target_refs` check, exactly how connectors
already gate `set_connector_credentials`.

| Route | Method | Who | What's new |
|---|---|---|---|
| `/model/providers/grants` | GET | org_admin | List every `(provider, workspace)` grant — powers the "which BUs have this provider" dropdown. |
| `/model/providers/grants` | PUT | org_admin | Set the full grant set for one provider (`{provider, workspaceIds: [...]}`) — upsert/delete against `integration_grants`, mirrors `setBuConnectorGrants`'s shape exactly. |
| `/model/providers` (POST) | — | bu_admin (BU-scoped), org_admin (org-wide) | **Changed**: BU-scoped calls now 403 unless `granted_target_refs` includes this provider; `api_key` required when `workspaceId` is set. |
| `/model/providers/{id}/assign` | POST | bu_admin | **New.** `{projectId}` — appends this credential to that project's `ProjectModelSelection.selected`. Scoped: project must belong to caller's BU (`_require_scoped`). |
| `/model/allowed/project` (PUT) | — | project_admin | **Unchanged endpoint**, narrower expected use — `defaultKey` picks among what's already in `selected`. |

`GET /model/providers` (existing, BU-scoped view) gains a `granted: boolean` per
provider-kind-not-yet-onboarded entry, same shape as `Connector.granted` — so the BU
Admin's grid can render "granted, not yet keyed" tiles for providers they have access
to but haven't added a key for yet, matching the Integrations page's own pattern.

## 5. Frontend flow per role

### Org Admin (`/admin/models`) — REVISED per §2 amendment 5

- **"Add provider" button removed** from this role's view. **No "Add an org-wide key"
  action either** (the original decision #2 secondary action is gone — Org Admin never
  touches a credential anywhere in the product).
- Provider cards **no longer link to the credential-adding flow**. Click → inline
  dropdown/expand (not a page nav, per the user's explicit description) showing:
  - Every BU currently granted this provider, with a toggle to grant/revoke per BU
    (Task 1-4's `integration_grants`/`kind='model_provider'` mechanism) — unchanged
    from the pre-amendment design.
  - **Restored**: for each granted BU, which specific models of this provider are
    curated for it (`org_model_grants`, unmodified backend), with controls to
    enable/disable individual models per BU — the same model-level picking the
    product already had before this redesign, just now presented *after* the
    provider-grant step rather than bundled into one "Add provider" dialog. No key
    field appears anywhere in this control — model curation is exactly as keyless as
    the provider grant itself.
- Grant matrix / governance summary stays, reads from BOTH the new provider-grant
  table (which BUs can reach this provider at all) and `org_model_grants` (which
  models within that reach are actually curated) — both facts shown together, not one
  replacing the other.

### BU Admin (`/admin/models`)
- Provider grid shows **only providers the org granted this BU** (`granted_target_refs`
  filtered). No "Add provider" — button becomes **"Add key"**.
- "Add key" dialog: provider is fixed to the one being added to (no provider picker —
  contrast with today's dialog where provider is step 1 of 4). Model(s) picker stays,
  **now constrained to models the Org Admin curated for this BU** (§3.4 amendment) —
  not every model the provider's catalog lists. **API key field becomes required**,
  not optional — with a **"Test"** button next to
  it that fires the existing live-probe verify path (`verifyModelProvider`, already
  built) before Save is enabled, rather than only verifying after save as today.
- Clicking a provider (once at least one key exists) navigates to a keys-list page —
  same shape as today's org-admin detail page (`/admin/models/[provider]`), listing
  every key this BU added by name, plus a **new "Assign to project"** action per key
  that opens a project picker scoped to this BU's own projects.

### Project Admin (project Settings → Model tab)
- Shows keys assigned to this project (pushed by their BU Admin) — **no self-service
  browsing of the BU's full catalogue** (today's behavior removed per decision #4).
- Existing `defaultKey` picker UI stays — becomes the primary control on this tab
  rather than a secondary one.

## 6. RBAC chain, re-verified top to bottom (per user's explicit request)

Walking one model, start to finish, under the new design:
1. **Org Admin** grants BU "Payments" access to provider `anthropic` — writes one
   `integration_grants` row. No model, no key exists yet. Nobody can run anything.
2. **BU Admin (Payments)** opens Models, sees `anthropic` as an available-to-add tile
   (`granted_target_refs` includes it). Adds a key, names it "Payments prod", picks
   `claude-sonnet-5`, key is required, Test passes, Save. This writes one
   `model_providers` row (`workspace_id = Payments`) + one `model_offerings` row.
   Creation handler re-checks the grant server-side (not just UI-gated) — a direct
   API call from a BU Admin without the grant still 403s.
3. **BU Admin** assigns that credential to project "Core ledger" — appends to
   `ProjectModelSelection.selected` for that project.
4. **Project Admin (Core ledger)** opens Settings → Model, sees "Payments prod" in
   their assigned list, sets it as `defaultKey`.
5. **At run time**, `resolve_model_for_run(tenant_id, model_id, offering_id,
   project_id)` (unchanged by this redesign — `backend/shared/services/
   model_resolver.py`) resolves the offering the same way it already does: by
   explicit `offering_id` if the caller passes one (this is where a project's
   `defaultKey`/chosen offering_id feeds in, at whatever call site already threads it
   today — confirmed unchanged, this redesign only changes how a project's selection
   *set* gets populated, not how the run-time resolver consumes it), else the tenant's
   default. **No change needed in model_resolver.py** — the redesign is entirely
   about who may populate `model_providers` / `ProjectModelSelection`, not about how
   a resolved key gets used once it's there.
6. **A different BU** (Lending), never granted `anthropic`: their BU Admin's Models
   page never shows an Anthropic tile at all (filtered by their own
   `granted_target_refs`), and a direct API call attempting to add an Anthropic key
   under Lending's workspace_id 403s at the creation handler, independent of what the
   UI shows.

This chain has one property the current per-model system doesn't: **grant and
credential are strictly ordered** (grant must exist before a key can be added), where
today a BU Admin can add a key for a provider that was never granted at all — the bug
the user opened this whole design conversation to fix.

## 7. Migration of existing data

One-off script, `backend/scripts/migrate_org_model_grants_to_provider_grants.py`,
run once per environment after this ships:
1. For every distinct `(tenant_id, provider)` pair with at least one active
   `org_model_grants` row: if `visibility='global'`, no action (nothing to migrate —
   a global grant already means every BU can use it, which the redesign preserves
   via org-wide `model_providers` rows, unaffected). If `visibility='specific'`,
   write one `integration_grants` row (`kind='model_provider'`) per business_unit_id
   named on the grant.
2. Existing `model_providers` rows (org-wide or BU-scoped, keyed or not) are
   **untouched** — they keep working exactly as before; this migration only backfills
   the *grant* layer so BU Admins don't lose access to providers they already had a
   key for.
3. `org_model_grants` rows themselves are **not deleted** — kept for `getModelGrantMatrix`'s
   historical read and as an audit trail of the old system, per decision #3 (write a
   script, don't silently drop data).

## 8. Testing

- Backend: new tests for `grant_provider`/`revoke_provider`/`granted_target_refs(kind="model_provider")`
  mirroring the existing connector-grant test file's structure exactly (same test
  names, same scenarios: grant exists → allowed, no grant → 403, cross-tenant
  isolation).
- Backend: `POST /model/providers` BU-scoped creation — test the new 403-without-grant
  case, and that `api_key` is now enforced required on that path (400 without it).
- Backend: migration script — a test seeding one `specific` org_model_grants row and
  asserting the resulting `integration_grants` row matches.
- Frontend: update/replace `AddModelDialog` tests for the new BU-Admin "Add key" flow
  (required key, Test button gating Save) and the Org Admin grant-toggle dropdown.
- Full RBAC chain (§6) as one end-to-end test: org grants → BU keys → BU assigns to
  project → project sets default → `resolve_model_for_run` returns that exact
  offering; plus the negative case (ungranted BU's direct API call to add a key 403s).

## 9. Explicitly out of scope

- Any change to `resolve_model_for_run` / `model_resolver.py` — confirmed unaffected,
  see §6 step 5.
- Any change to the connector system itself (only its pattern is reused).
- A UI for Org Admin to un-grant a provider that has active BU keys under it (revoke
  is possible via the API from day one; a "this will break N existing keys" warning
  in the UI is a reasonable fast-follow, not blocking this pass).
