# Model Gateway: Org → BU → Project Cascade (Design)

Date: 2026-08-11
Owner: Sarthak
Scope: `foundation_tasks.xlsx` item #21 ("Build BU model assignment ... and project model
selection ... Build getProjectModel(project_id) ... Block selection of providers not in
the BU assignment"), first of four Model Gateway tasks (#20–23).

## 1. Context

The backend already has a real, working BYOK model gateway core
(`backend/shared/services/model_resolver.py`, `model_config.py`, `model_rate_limit.py`,
`budget_guard.py`) — it resolves tenant → provider/model/API key from Postgres
(`model_providers` / `model_offerings`), enforces per-offering RPM/TPM/cost limits and
hierarchical monthly budgets, and is wired into every agent. This is not new work; it is
effectively a direct carry-forward of the same architecture in the older
`sdlc_product/platform/backend` project (same file names, same design, same fail-closed
philosophy).

What's missing, and what this design covers, is the **governance cascade** the PRD
requires (§12.2, §42.5):

> Organization Admin onboards a provider + credentials + org-wide guardrails → Business
> Unit Admin decides which of the org's onboarded providers/models a unit's projects may
> use, sets unit defaults/limits → Project Admin selects the actual model(s) from what the
> unit made available, sets a project-level limit → Builder uses the selected model in
> agent runs.

Today the backend is **deliberately tenant-wide flat**: any `model_providers` row a tenant
owns is usable by any project in that tenant (comments in `model_config.py` explicitly
document this — an earlier `workspace_id` filter was removed rather than fixed). The
frontend's admin Models UI, however, already has a fully-designed type-level contract for
the cascade (`frontend/lib/schemas/model.ts`, `frontend/lib/api/models.ts`) — org grants
with `global`/`specific`-BU visibility, credential-level granularity, per-BU availability,
a grant matrix, and project-level selection with BU-default inheritance — currently served
entirely by fixture data (`frontend/lib/mock/model-fixtures.ts`, marked `DUMMY-DATA SEAM`
in every `app/api/model/*` route). This design makes that contract real.

### RBAC dependency (explicitly out of scope, flagged not hidden)

Live permission enforcement (`shared/authz/dependency.py: require_permission`) checks a
flat permission list baked into the JWT at login. The DB tables for per-workspace role
assignment (`user_workspace_roles`, seeded `org_admin`/etc. in migration 0011) exist, but
the code's own comment states per-workspace resolution is "additive when multi-workspace
ships in 7.3+" — i.e., not live. There is no backend concept of "BU Admin of unit A" vs
"BU Admin of unit B" today; the frontend's `org_admin`/`bu_admin`/`project_admin` roles are
a client-side inference from a coarse permission list, standing in for RBAC work owned by
Ujjwal/Arif (`foundation_tasks.xlsx` #8–#10: `canPerform()`, scoped role resolution).

**Decision:** build the full data/cascade layer now, gated by the existing `model:manage`
permission (today's only real gate). This will function correctly for a single admin
acting in one workspace at a time. It will **not** yet stop a `model:manage` holder from
editing a BU they aren't really assigned to — that hard boundary lands when scoped RBAC
ships. Every enforcement point where this matters is marked `# TODO(scoped-rbac)`.

## 2. Data model

### 2.1 New table: `org_model_grants`

The only place a model enters the org's catalogue for use beyond its onboarding provider.
A `global` grant reaches every BU automatically; a `specific` one reaches only the named
units.

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| tenant_id | uuid | RLS anchor |
| provider | text | catalog provider slug |
| model_id | text | |
| credential_id | uuid, nullable, FK → model_providers.id | null = "any key the provider holds" |
| visibility | text ('global'\|'specific') | default 'global' |
| business_unit_ids | jsonb (array of workspace ids) | meaningful only when visibility='specific' |
| created_by | text | |
| created_at | timestamptz | |

Unique on `(tenant_id, provider, model_id, credential_id)` — the same model can be granted
twice under two different keys (e.g. a shared platform key + a unit's own EU-region key)
without one overwriting the other.

### 2.2 New table: `project_model_selections`

What one project actually uses, versus what it may use.

| column | type | notes |
|---|---|---|
| id | uuid pk | |
| tenant_id | uuid | RLS anchor |
| project_id | uuid, unique | FK → projects.id |
| selected | jsonb (array of {provider, model_id, credential_id}) | |
| default_key | text, nullable | identifies which `selected` entry is the project's default |
| updated_at | timestamptz | |

No row for a project (or an explicit empty/`using_defaults` state) means "inherit the BU's
full allowed set" — the UI must never present an inherited set as a choice someone made.

### 2.3 `model_providers` additions

- `approval_status` text, default `'active'` — values `active` \| `pending_approval` \|
  `rejected`.
- `approval_decided_by`, `approval_decided_at`, `approval_reason` — nullable.
- Reuse the existing (currently unused) `workspace_id`: `NULL` = org-wide/centrally
  credentialed; a value = that BU's own onboarded connection.

`create_provider` changes: `api_key` becomes optional (a provider can be registered with
no key yet — `hasKey=false`); accepts `workspace_id`; org-wide onboarding
(`workspace_id=None`) additionally accepts `visibility`/`business_unit_ids` and writes the
matching `org_model_grants` row in the same transaction (mirrors
`frontend/lib/api/models.ts: addModelProvider`'s contract exactly — a key can't land
without anyone being able to use what it unlocks).

`approval_status` is always set to `'active'` today regardless of who calls it — see the
RBAC dependency note above. The columns exist so the workflow is wireable later without
another migration.

## 3. Service layer

New module `backend/shared/services/model_grants.py` (kept separate from
`model_config.py`, which stays focused on provider CRUD/verify — one clear purpose per
unit):

- `get_org_grants(tenant_id) -> list[dict]`
- `set_org_grants(tenant_id, entries) -> list[dict]` — validates each `credential_id`
  belongs to an existing provider.
- `get_bu_allowed(tenant_id, workspace_id) -> list[dict]` — global grants ∪ specific grants
  naming this BU, filtered to offerings that are actually enabled.
- `set_bu_grants(tenant_id, workspace_id, entries)` — moves only `specific`-visibility
  grants for this unit (global ones already reach it and can't be edited per-unit).
- `get_availability(tenant_id, workspace_id) -> list[dict]` — `get_bu_allowed` plus
  `centrallyCredentialed` / `locallyCredentialed` flags.
- `get_project_selection(tenant_id, project_id) -> dict` — `{inherited, inheritedFrom,
  selected, usingDefaults, defaultKey}`.
- `set_project_selection(tenant_id, project_id, selected, default_key)` — rejects (raises
  `NotAllowedForUnitError`) any entry not in the project's BU-allowed set.
- `get_grant_matrix(tenant_id) -> dict` — one-pass join: catalog × grants × BU reach ×
  credential status.
- `effective_project_offerings(tenant_id, project_id) -> set[str] | None` — the function
  the resolver calls. Returns `None` ("no governance configured, stay fully open") when the
  tenant has zero `org_model_grants` rows; otherwise the set of eligible `offering_id`s
  (the project's own selection if not using defaults, else the BU's allowed set).

## 4. API surface (`shared/routers/model.py`)

All new endpoints require `model:manage` **except** the project-selection pair. Per the
PRD's own permission matrix (§14.11), Project Admin selects models via Project Settings
but does not hold `model:manage` — mirroring the existing split in this same file, where
`model_options_router` already gates the run-creator `/model/options` endpoint behind the
weaker `run:create` instead. `allowed/project` follows that precedent (`run:create`), not
`model:manage`.

| Method & path | Permission | Behavior |
|---|---|---|
| `GET /model/allowed/org` | model:manage | `get_org_grants` |
| `PUT /model/allowed/org` | model:manage | `set_org_grants` |
| `GET /model/allowed/bu?workspaceId=` | model:manage | `get_bu_allowed` |
| `PUT /model/allowed/bu?workspaceId=` | model:manage | `set_bu_grants` |
| `GET /model/availability?workspaceId=` | model:manage | `get_availability` |
| `GET /model/allowed/project?projectId=` | run:create | `get_project_selection` |
| `PUT /model/allowed/project?projectId=` | run:create | `set_project_selection` → 400 on out-of-grant entry |
| `GET /model/grant-matrix` | model:manage | `get_grant_matrix` |
| `GET /model/options` (existing, modified) | now filtered through `effective_project_offerings` for the active project instead of flat tenant-wide |
| `GET /model/providers` (existing, modified) | supports `scope=all` (every connection: org-wide + every BU) vs `workspaceId=` (org-wide + that BU's own) |
| `POST /model/providers` (existing, modified) | accepts `workspace_id`, optional `api_key`, `visibility`/`business_unit_ids` for org-wide onboarding |

## 5. Resolver change (`model_resolver.py`)

`resolve_model_for_run` already threads a `project_id` (explicit param or the
`_RUN_PROJECT` contextvar) for budget checks. Extend the same value to also gate offering
eligibility: call `effective_project_offerings(tenant_id, project_id)` and, when it returns
non-`None`, intersect `_load_enabled`'s candidate rows against it before choosing an
offering. An offering outside the set is treated exactly like a disabled one —
`ModelNotEnabledError` if explicitly requested, otherwise skipped when picking a default.

**Backward compatibility:** a tenant with zero `org_model_grants` rows resolves exactly as
today (fully open, tenant-wide). The cascade only starts constraining once an Org Admin
creates at least one explicit grant — this is what keeps every existing dev/test
project/fixture working unmodified after this ships.

## 6. Error handling

- `PUT /model/allowed/project` with an out-of-grant entry → `400
  {code: "not_allowed_for_unit"}` (new, small addition alongside the existing
  `InvalidModelError` / `ModelNotEnabledError` families).
- A run whose resolved offering falls outside its project's effective set → existing
  `ModelNotEnabledError` (already 422-mapped at the router) — reused, not reinvented.
- Narrowing/deleting an org grant that a project currently has selected is **not**
  hard-blocked at write time (a UI warning concern, not a backend one) — the resolver
  naturally fails closed on the next run once the offering drops out of the effective set.

## 7. Testing

New `backend/shared/tests/test_model_grants.py`, following the existing style in
`test_m2_verification.py` / `test_litellm_parity.py`:

- Global grant reaches every BU; specific grant reaches only named BUs.
- `set_project_selection` rejects an offering the BU wasn't granted (400).
- A project with `usingDefaults=true` inherits the BU set live (no stale copy) when the
  BU's grant changes.
- Resolver: a run in Project A cannot resolve an offering only granted to Project B's BU,
  once that tenant has ≥1 grant row.
- Backward-compat regression guard: a tenant with zero grant rows resolves exactly as
  today.
- Grant matrix: one row per (model, credential) pair; `centrallyCredentialed` correct when
  an org-wide connection exists.

## 8. Known gaps (deliberate, not hidden)

1. No real enforcement that a BU Admin can only edit their own BU's grants/selections —
   everyone holding `model:manage` can touch every BU today. Marked `# TODO(scoped-rbac)`
   at each spot; resolved when Ujjwal/Arif's scoped RBAC (`canPerform`,
   `user_workspace_roles` enforcement) ships.
2. The Project-Admin-onboards-pending-BU-approval workflow has its DB columns
   (`approval_status` etc.) but no reachable path to actually produce `pending_approval` —
   same underlying reason.
3. `ModelCallWrapper` (timeout/retry/no-training param — task #20) and per-call cost caps
   with `GET /cost-summary` (task #22) and model version tracking (task #23) are separate
   tasks, out of scope here.
