# RBAC audit — 2026-08-17

Checked against the code on branch `chore/backend-cleanup-rbac`, not against the design
docs. Every claim below names a file and a line so it can be re-verified or refuted.

Companion to `docs/rbac-auth-design.md` (the original seven-task assessment) and
`docs/rbac-auth-implementation.md` (what was built). That second document is accurate
about *intent*; three of its claims no longer hold against the code, and each is flagged
where it appears below.

---

## Summary

**The backend's authorization design is strong, and the frontend session seam makes all of
it bypassable.**

The backend has deny-by-default route gating with a boot scan that refuses to start on an
unguarded route, a real four-level scope chain, override-aware permission resolution
single-sourced through one function, RLS with FORCE on every tenant-scoped table, an
append-only audit trail, and a no-escalation check on custom roles. Very little of that
survives contact with the way the BFF mints its token.

Two things are worth stating up front because they are usually where the rot is, and here
they are clean: **the 34-string permission catalogue and the 13-role permission matrix are
byte-identical between backend and frontend.** No drift. The mirror contract described in
`shared/authz/permissions.py:9-17` is being held.

| # | Severity | Finding |
|---|---|---|
| 1 | **CRITICAL** | Session cookie is unsigned; the BFF re-signs it into a trusted JWT |
| 2 | **CRITICAL** | No role-rank check on grants — a Project Admin can become Org Admin |
| 3 | HIGH | Five per-project routers never check the `{project_id}` they are given — **fixed** |
| 4 | HIGH | Audit, cost and traces are not narrowed to the caller's units — **fixed** |
| 5 | HIGH | Expiry and deactivation are not enforced on the login path — **fixed** |
| 6 | HIGH | A permission change does not take effect until the token expires — **fixed** |
| 7 | MEDIUM | `role_bindings` written outside `grant.py`, losing the tier check and audit |
| 8 | MEDIUM | Nine catalogue permissions are declared and enforced nowhere |
| 9 | MEDIUM | `effective_by_role` called without a tenant id on the scoped path |
| 10 | MEDIUM | Two governance writes gated on delivery permissions |
| 11 | MEDIUM | Rosters and user detail readable by anyone signed in |
| 12 | MEDIUM | Client gates that fail open, and one that never fires |
| 13 | LOW | Two stale mirrors, one of them load-bearing |
| 14 | LOW | `require_any_permission` writes no denial audit record |
| — | bug | `project:update` is not a real permission — Settings tab is dead for admins — **fixed** |

---

## 1. CRITICAL — The session cookie is unsigned, and the BFF re-signs it into a trusted JWT

A complete authentication and authorization bypass. Four links:

1. `frontend/lib/auth/mock.ts:113-129` — `encodeSession`/`decodeSession` are plain base64
   of JSON. No signature, no MAC. `decodeSession` validates only that `user.id` and `role`
   are present.
2. `frontend/app/api/auth/login/route.ts:78-84` — the **real** local-auth path, not just
   mock, stores that blob as `sdlc_session` with `httpOnly: false`, and **discards the
   backend's own signed token**: `LoginResponse.token` exists
   (`frontend/lib/auth/local.ts:8`) and `buildLocalSession` never reads it. Same pattern at
   `app/api/auth/register/route.ts:106` and `app/api/auth/mock/signin/route.ts:39,56`.
3. `frontend/lib/bff/jwt.ts:50-58` — `mintBffToken` signs `sub`, `tenant_id` and
   `permissions` **verbatim from that decoded cookie**, using the real `JWT_SECRET_KEY`.
4. `backend/process_api.py:777` — the HS256 branch does
   `request.state.permissions = claims.get("permissions", [])`. The signature verifies, so
   the claim is trusted wholesale.

**Attack.** Set `sdlc_session` to
`base64({"user":{"id":"x"},"role":"admin","tenant":{"id":"<tenant uuid>"},"permissions":["admin:*"]})`.
`middleware.ts:50` checks only that the cookie *exists*. The BFF mints a valid token
carrying `admin:*`, which `has_permission` (`shared/authz/permissions.py:253`) treats as a
wildcard over every `require_permission`, and which makes `is_org_wide`
(`shared/authz/read_scope.py:26`) return true so every scope filter is skipped. The result
is full organization admin — and because `tenant_id` is attacker-chosen, it reaches any
tenant whose UUID is known.

`httpOnly: false` independently turns any XSS into the same outcome. The comment on that
line says "XSS-mitigated via CSP"; CSP does not stop a user forging their own cookie.

The enterprise OIDC branch (`process_api.py:745-764`) is immune — it re-resolves
permissions from the DB per request and trusts no claim, and `bearerForRequest`
(`frontend/lib/bff/client.ts:45-51`) forwards the real IdP token. Only the HS256/local path
is affected, which is the path in use.

> This contradicts `rbac-auth-implementation.md:266-270`: *"a user who tampers with client
> state to reveal a hidden action still gets a 403 from FastAPI on the actual call."*
> They do not — the tampered state is what mints the token.

**Fix.** Make the backend the only issuer: store the token `POST /auth/login` already
returns in an `httpOnly` cookie, forward it as the Bearer token, and delete the minter.
See the implementation notes at the end.

---

## 2. CRITICAL — No role-rank check on grants: a Project Admin can make themselves Org Admin

Three facts that compose into an escalation:

- `shared/authz/resolver.py:33-87` unions permissions across every binding **ignoring
  `scope_kind`** — its own docstring says so. A binding at *project* scope contributes its
  role's full permission set to the token.
- `shared/authz/permissions.py:239` — `ALL_ROLES` is every key of `_ROLE_PERMISSIONS`,
  including `org_admin`, whose set is `["admin:*"]`.
- The grant endpoints validate only membership in that list:
  `shared/routers/admin.py:240` (`POST /admin/assignments`) and
  `shared/routers/project_members.py:224` (`POST /projects/{id}/members`). `grant_role`
  (`shared/authz/grant.py:122-130`) likewise checks `role_name in ALL_ROLES` and accepts
  any `scope_kind`.

So anyone holding `member:manage` — `bu_admin` and `project_admin` both do — can add a
person, including themselves, as `org_admin` on a project or unit they legitimately
administer. On the next token mint they hold `admin:*` organization-wide.

`admin.py:236-238` names the risk in a comment — *"granting org_admin there escalates
further"* — but the fix applied was `assert_can_write_workspace`, which constrains **where**
you may grant, not **what**. The rule that already exists for custom roles
(`shared/routers/custom_roles.py:80`: requested permissions must be a subset of the
creator's own, re-resolved from the DB rather than read from the token) was never extended
to built-in role grants.

**Fix.** `shared/authz/grant_guard.py::assert_can_grant_role`, called from every grant
path.

**And a correction to the obvious fix.** Reusing the custom-role subset rule verbatim —
"you may grant only permissions you hold" — is wrong here, and the regression test caught
it: `developer` carries `skill:edit` and `project_admin` does not, so a strict subset rule
stops a Project Admin staffing a developer, the most ordinary act on the endpoint.

The escalation was never about specialist delivery permissions. Handing a QA engineer
`artifact:approve_testing` confers no power over the person who handed it out. What made
this a hole was conferring **authority over access itself**. So the shipped rule compares
only `ESCALATING_PERMISSIONS` — `admin:*`, `role:manage`, `member:manage`,
`settings:manage`, `workspace:manage` — which refuses `org_admin` and `bu_admin` to a
Project Admin while leaving every delivery role grantable. Granting your own role stays
permitted: a Project Admin appointing a co-admin is delegation of authority already held,
and forbidding it would make every role a single point of failure.

---

## 3. HIGH — Five per-project routers take `{project_id}` from the path and never check it

`shared/routers/dev_workspace.py:61`, `code_review_workspace.py:50`,
`security_workspace.py:47`, `deployment_workspace.py:87`, `documentation_workspace.py:101`
are **writes** whose only gate is the `artifact:view` floor applied at include time
(`process_api.py:1022-1030`). None of the five imports `can_perform`, `visible_project_ids`
or `administered_workspace_ids`.

`artifact:view` is held by `contributor`, whose entire designed purpose is holding nothing
until a real role is assigned (`permissions.py:89`). So the weakest role in the system can
clone a repo into a server workspace, prepare a security scan, or prepare a deploy against
**any project id in the tenant** — and read another business unit's source tree via
`GET /dev/{project_id}/workspace/tree` (`:120`), `.../file` (`:141`), `.../changes` (`:158`)
and `/prs` (`:196`).

`shared/routers/runs.py` solved exactly this problem with the `_get_run_or_404` chokepoint
(`:914-955`), which enforces tenant *and* `can_perform`/`visible_project_ids` for all
sixteen run routes. These five never got the equivalent.

**Fixed.** `shared/authz/project_scope.py::require_project_access`, attached as a
**router-level** dependency on all five. The five carry 24 routes between them and none
took a `db` session, so a per-handler check would have meant 24 signature changes plus the
standing risk that route 25 forgets; the router-level form covers every current route and
every future one. It resolves slugs as well as UUIDs — the frontend's project routes are
slug-based, and a UUID-only guard would have 404'd real traffic.

Pinned by `backend/tests/test_project_workspace_scope.py`.

---

## 4. HIGH — The audit trail, cost and traces are not narrowed to the caller's units

`read_scope.py:7-10` states the rule the codebase is supposed to follow: *"a count
discloses as much as a row."* Four surfaces do not:

- **`GET /audit`** — `shared/routers/audit.py:42`; the query at `:81-88` filters
  `tenant_id` plus an **optional, caller-supplied** `workspace_id`. Gated on `audit:view`,
  held by `bu_admin` and `security_engineer` — either sees the whole tenant's audit trail
  by simply omitting the filter.
- **`GET /cost/summary`** (`cost.py:120`) and **`GET /cost/allocations`** (`cost.py:257`) —
  `tenant_id` only. `cost:view` is held by `bu_admin`, `project_admin` and
  `security_engineer`. Note `shared/routers/spend.py:93` *does* call
  `allowed_workspace_ids` on the same data, so the treatment is inconsistent within the
  codebase.
- **`GET /traces/*`** (`traces.py:298,329,377,449`) — scoped by a Langfuse `tenant:<id>`
  tag with no unit or project narrowing.
- **`GET /projects/{id}/artifacts`** (`artifacts.py:50`) and **`GET /artifacts/{id}`**
  (`:106`) — tenant-only, unlike `runs.py:164`.

**Fixed**, each in the shape its data source allows:

- **`GET /audit`** now filters on the caller's units, and on **both payload shapes**.
  Resource events carry `workspace_id` / `project_id`; RBAC events
  (`shared/authz/audit.py`) carry `scope_kind` + `scope_id` instead. Filtering on the
  first alone would have hidden a unit admin's own grants and revocations — the events
  they are most accountable for. Anything matching neither is organization-level and
  stays hidden. A foreign `workspace_id` is refused with 404 rather than silently
  rescoped, mirroring `spend.py`.
- **`GET /cost`** issues one Langfuse query per unit the caller may see and sums them.
  Langfuse ANDs its tag filter, so there is no single tag set meaning "these three
  workspaces", and its daily-metrics endpoint returns figures already aggregated —
  post-filtering is not available. Per-unit totalling is the only shape that computes the
  answer *from* the allowed set. An org-wide caller keeps the single query.
- **`GET /cost/budgets`** narrows units and projects, and the org rollup —
  `allocatedUsd` and spend — is computed from the visible set. The org's own **cap**
  stays visible: it is the ceiling a unit admin allocates under, and they cannot read
  their own headroom without it.
- **`GET /traces/*`** filters rows **before** any aggregate is computed, so the metric
  cards are totals over the allowed set rather than a trimmed view of the
  organisation's. `standalone` traces — chat with no project attached — are dropped for a
  scoped caller: they carry no attribution, and a prompt should not default to visible.
  `project-summary` refuses a foreign project rather than answering with zeroes, which
  are themselves a fact about it.
- **Artifact reads** (`/projects/{id}/artifacts`, `/artifacts/{id}`) check the project is
  visible. The tenant join stopped a cross-tenant read and did nothing about a
  cross-project one.

Pinned by `backend/tests/test_aggregate_scope.py`.

Two existing tests had to change, both for the same reason and both worth noting: their
callers held `cost:view` with **no bindings**, which used to be indistinguishable from
org-wide. They now assert the aggregation math with an explicitly org-wide caller, which
is what a tenant-wide total legitimately requires.

Still open: `GET /traces` applies its filter to the page Langfuse returned, so a scoped
caller can get a short page. Pushing the predicate down is not possible — projects are a
tag, not a queryable column — and the alternative is a full-tenant fetch per request.

---

## 5. HIGH — Expiry and deactivation are not enforced on the path that matters

`can_perform._permissions_at` (`shared/authz/can_perform.py:194-197`) correctly filters
`status = 'active' AND (expires_at IS NULL OR expires_at > :now)`. But `can_perform` is
reached only by the routes that have moved onto it.

The gate every other route uses reads the JWT claim, and that claim comes from
`shared/authz/resolver.py:61-72` — a plain
`select(RoleBinding.role_name).where(RoleBinding.user_id == user_id)` with **no status
filter and no expiry filter**. The custom-role join at `:78-85` has neither either.
`read_scope.py:49-63,81-88` uses `status <> 'deactivated'` — so an `invited`,
not-yet-accepted binding grants scope — and checks no expiry at all.

The ORM cannot express it: `RoleBinding` (`shared/models/orm.py:293-359`) declares no
`expires_at` and no `granted_by`, and its `scope_kind` CHECK still lists only three values.
The columns migration `0003_scope_chain_and_expiry` added were never mirrored into the
model. `grant.py:159-186` writes them via raw SQL, so they exist in the database and are
simply never read by the login path.

Net effect: **a temporary elevation never ends, and deactivating a binding does nothing
until the token expires.**

> This contradicts `rbac-auth-implementation.md:133`: *"an expiry is enforced by the
> clock."* True of `can_perform` only.

**Fixed.** `expires_at` and `granted_by` added to the ORM model (and the `scope_kind`
CHECK extended to four levels, matching migration 0003), so the resolver's query can
filter on them at all. The liveness rule now lives in exactly one place —
`read_scope.live_binding()` — and every authorization reader uses it: `resolver.py`,
`read_scope.py`, `effective_role.roles_held`, `project_scope`, and `grant.py`'s
tier-conflict check. It had been written four different ways.

`status = 'active'` rather than `<> 'deactivated'` throughout, matching `can_perform`.
Nothing writes `'invited'` today, so the two are equivalent in practice; the strict form
means an unaccepted invitation grants nothing on the day something does.

Three copies of `_assert_can_write_project` were collapsed onto the shared one while doing
this — all three carried the same stale predicate.

Pinned by `backend/tests/test_binding_liveness.py`, including the property that made this
a bug rather than a gap: both permission readers now give the same answer about the same
binding.

Still open: the roster and count queries (`admin.py:194`, `org.py:219`,
`project_members.py:144`) show expired bindings as current members. That is display, not
authorization, and changing it changes what rosters show — a product call.

---

## 6. HIGH — A permission change does not take effect until the token expires

`PUT /admin/role-permissions` (`shared/routers/role_permissions.py:212-227`) rewrites
`role_permission_overrides` but issues no revocation. The JTI denylist exists and is
checked on every request (`process_api.py:735-743`) and is called by nothing.
`GET /auth/me` (`shared/routers/auth_local.py:161-166`) echoes the stale claim, so the UI
never notices either.

With the 8-hour session cookie (`frontend/lib/auth/mock.ts:7`) and the 60-minute minted
token, a permission *removed* from a role stays live for the rest of the holder's session.
Logged as P1 in `docs/rbac-auth-plan.md` phase 2; still open.

**Fixed**, though not with the denylist. It cannot enumerate a user's live tokens — it
records a jti when one is *revoked*, never when one is minted — so there is no set to walk.

`shared/authz/token_epoch.py` stores one integer per user: when their access last
*reduced*. Tokens now carry `iat`; a token minted before that instant is refused with a
401. One Redis GET, no database round trip on the hot path, and it invalidates every token
that user holds without needing to know what they are.

Three decisions worth keeping:

- **Reductions bump, grants do not.** Granting only widens what a user may do, so an older
  token is stale in the harmless direction and catches up at next sign-in. Bumping there
  would buy nothing and would refuse a token minted in the same second as the grant.
- **The recorded instant rounds up to the next second.** `iat` is second-granular, so a
  revocation recorded in the same second as the mint would compare `N < N` and let the
  token through — mint-then-revoke inside one second is what an admin correcting a mistake
  produces. Found by a test that failed intermittently before the rounding went in.
- **401, not 403.** The request is not forbidden; the token is out of date. A 403 would
  tell someone who had just been granted a role that they still lacked it.

Fail-open on Redis errors, matching the JTI denylist: the alternative makes Redis a hard
dependency of every authenticated request, so a blip takes the product down rather than
briefly widening a window that used to be an hour by default.

Bumped from `revoke_role`, the role-permission override write and its reset branch (which
bumps every holder of the role, not just the editor), custom-role edit and delete, and the
cross-BU loan-end delete. Pinned by `backend/tests/test_token_epoch.py` and
`backend/tests/test_revocation_takes_effect.py`.

---

## 7. MEDIUM — `role_bindings` is written outside `grant.py`, losing the tier check and the audit row

`grant.py:9-10` and `rbac-auth-implementation.md:185` both state that grant.py is the only
write path into `role_bindings` and that it is operator-only. Neither is true now:

- `project_members.py:311-317` — `PATCH /projects/{id}/members/{membership_id}` sets
  `role_name` with a direct `UPDATE`. This bypasses `_assert_no_tier_conflict`, so the
  governance/delivery separation can be edited around: add someone as `developer`, then
  PATCH them to a governance role in the same scope. It also writes no
  `record_rbac_change`, so a role change leaves no trail.
- `project_members.py:345-351` and `project_scoped.py:455` — deletes with no audit row.

`POST /projects/{id}/members` (`:252`) does correctly route through `grant_role`.

**Fix.** Route the PATCH role change through `grant.py` and add `record_rbac_change` to the
deletes.

---

## 8. MEDIUM — Nine catalogue permissions are declared and enforced nowhere

`agent:invoke` is granted to nine roles and is in the catalogue (`permissions.py:219`), and
there is **no** `require_permission("agent:invoke")` call site anywhere in the backend.
Every `/sdlc/agent/*` router — the actual agent execution surface — is gated on the
`artifact:view` floor (`process_api.py:852-878`).

The same is true of `run:view`, `eval:view`, `connector:request`, `skill:promote`,
`skill:approve`, `skill:import`, `skill:edit:project`, and `artifact:export` (one call
site, `artifacts.py:163`). These render as checkboxes on the Roles & Access page that
change nothing when ticked or cleared.

**Fix.** Either enforce them at the routes that correspond to them, or remove them from the
catalogue. A permission the UI offers and the enforcement path ignores is worse than
neither.

---

## 9. MEDIUM — `effective_by_role` called without a tenant id on the scoped path

`can_perform.py:209` calls `effective_by_role(session)` with no `tenant_id`. `overrides()`
(`shared/authz/role_permissions.py:52-68`) drops its `WHERE tenant_id` predicate when the
argument is `None`, relying entirely on the session GUC. Because the merge is keyed on role
name alone and is whole-set replacement, a session that bypasses RLS would let one tenant's
override for `developer` wholly replace another tenant's defaults.

`resolver.py:73` passes the tenant id. This sibling does not, and the docstring at
`role_permissions.py:56-58` already acknowledges the hazard.

**Fix.** Pass the tenant id. One-line change.

---

## 10. MEDIUM — Two governance writes gated on delivery permissions

- `shared/routers/model.py:371` — `PUT /model/options/allowed/project` decides which models
  a project may use, gated at router level on `run:create` (`model.py:18`), a permission
  held by `developer`, `ba`, `architect` and `data_engineer`. It reads as `model:manage`.
- `shared/routers/workspaces.py:420` — `POST /{workspace_id}/budget-increase-request`. The
  docstring at `:444` claims the floor is `cost:view`; the decorator carries no dependency
  at all, so the effective gate is the router's `artifact:view`. Any signed-in user can
  raise a budget request against any unit, and there is no `assert_can_write_workspace`.

---

## 11. MEDIUM — Rosters and user detail are readable by anyone signed in

- `project_members.py:199-206` — `GET /projects/{id}/members`. `_project_or_404` checks the
  tenant only, so any signed-in user reads any project's roster including every member's
  email address. The docstring says *"readable by anyone who can see the project"*; nothing
  checks that they can see it.
- `frontend/app/(app)/users/[id]/page.tsx` — **no client gate at all.** It renders a
  person's email and every role binding they hold. The `/users` index requires
  `member:manage` (`users/page.tsx:146`); `/users/{id}` is directly navigable.

---

## 12. MEDIUM — Client gates that fail open, and one that never fires

- `app/(app)/admin/models/[provider]/page.tsx:386` and
  `app/(app)/integrations/[kind]/page.tsx:50-52` both gate on
  `role !== null && role !== "org_admin"`. A session whose role infers to `null`
  (`lib/auth/effective-role.ts:93`) — for instance a freshly registered user with
  `permissions: []` — renders the page instead of the denial.
- `effectivePlatformRole` inference can never produce `contributor`: a contributor holds
  exactly `["artifact:view"]`, which falls through to `return "custom"`
  (`effective-role.ts:91`). But `lib/nav.ts:582` hard-blocks the sidebar on
  `role === "contributor"`, so that block only fires when the backend issues `platformRole`
  explicitly. On the inference path a contributor sees the full Build group.
- `middleware.ts` performs no route-level authorization — every admin route is reachable by
  any authenticated user until the page body renders its denial.

---

## 13. LOW — Two stale mirrors, one of them load-bearing

- `frontend/lib/auth/types.ts:111-120` — the `Permission` union claims to mirror the
  backend's `ALL_PERMISSIONS` *"EXACTLY … 9 strings"*. The backend catalogue is **34**.
  Twenty-six are missing, including `member:manage`, `role:manage`, `agent:invoke`,
  `governance:decide` and all five `skill:*`. `hasPermission` takes `string`, so this is a
  loss of typo-safety rather than a runtime bug — but `lib/auth/permissions.ts:60-72` uses
  `satisfies Permission`, and the three branches added later (`review`, `security`,
  `documentation`) had to **omit** `satisfies` because they do not typecheck against it.
  No test asserts the union covers the catalogue.
- `shared/authz/catalog.py::_ROLE_LABELS` claims to have eliminated the duplicated label
  map, but `shared/routers/admin.py:49-63` still defines its own copy and `list_roles`
  (`:162`) reads from *that* one. Two labels already differ: `ba` is "Business Analyst" vs
  the frontend's "BA (Business Analyst)", and `qa` is "QA Engineer" vs "QA / Tester".

---

## 14. LOW — `require_any_permission` writes no denial audit record

`shared/authz/dependency.py:157` raises 403 without calling `record_access_denied`, which
`require_permission` does at `:103`. Denials on those routes are absent from the trail the
audit design exists to provide.

---

## Not security, but broken: `project:update` is not a real permission

`frontend/components/app/project-tabs.tsx:69` and
`frontend/app/(app)/projects/[id]/settings/page.tsx:146` pass `"project:update"` to
`hasPermission()`. That string exists only in the **legacy** `Capability` union
(`lib/auth/capabilities.ts:10`) and appears in neither `PERMISSION_CATALOG` nor the backend
`_PERMISSION_CATALOG`. Only `admin:*` satisfies it — so **a Project Admin and a Business
Unit Admin see no Settings tab and cannot manage a project budget.**

The settings page mixes both models on adjacent lines: `:138` uses
`useCan("project:update")` (legacy, coarse role) and `:146` uses
`hasPermission(session, "project:update")` (M7.2). They return different answers for the
same string.

Related: the legacy `<RequireRole capability="run:trigger">` on eight phase pages resolves
against the coarse `admin|member|viewer` matrix, derived heuristically in
`lib/auth/local.ts:26-34`. A `qa` or `security_engineer` holds `run:view` but not
`run:create`, derives to `viewer`, and therefore sees no run button anywhere. **Still
open** — the settings page was converted off that model, the eight phase pages were not.

### Fixed

`project:update` is now a real permission, in both catalogues and granted to `bu_admin`
and `project_admin`, and `PATCH /projects/{id}` enforces it — that route previously
demanded `workspace:manage`, which only `bu_admin` holds, so the Project Admin who is made
to choose a budget at creation could not change the figure afterwards. Widening WHO may
edit made WHICH project mandatory, since `_get_or_404` scopes by tenant alone:
`assert_can_administer_project` is the other half.

The settings page now reads one gate for the whole page instead of two that disagreed —
`useCan` (legacy coarse role, which derived a Business Unit Admin to `viewer` and disabled
every field) and `hasPermission` (right model, empty string). Archive/restore stays on
`workspace:manage`, matching its own route: removing a project from a unit is the unit's
call, not the project's.

Pinned by `backend/tests/test_project_update_permission.py`.

**Operational note.** Adding a permission to `_ROLE_PERMISSIONS` changes the shipped
default, and the boot guard treats any DB/code difference as tampering. The local database
has been reconciled; **other environments will refuse to start until they are**, either by
booting once with `RBAC_CATALOG_AUTOREPAIR=true` or by running `seed_rbac_catalog`. See
`docs/rbac-tables.md`.

While fixing this I found `tests/agent_skills/test_agent_skills_router.py` red on 14 tests
for the same root cause: those routes moved off `project:update` onto `skill:edit` and the
test's permission constant never followed. Corrected — the router was already right.

---

## Verified correct

Stated explicitly so it does not get re-litigated:

- The 34-string permission catalogue matches exactly between
  `shared/authz/permissions.py:202-236` and `frontend/lib/auth/permission-catalog.ts`.
- The 13-role permission matrix matches exactly, role for role and count for count.
- `ROLE_TIER` and `ROLE_SCOPE` agree with the frontend's `ROLE_META` on all 13 roles.
- `has_permission` and `hasPermission` mirror each other, including the no-implication rule
  (`connector:manage` does not satisfy `connector:view`).
- The override merge is genuinely single-sourced in `role_permissions.py::effective_by_role`
  and both permission readers call it.
- `assert_all_routes_protected` (`dependency.py:260`) really does make a forgotten authz
  dependency a boot failure, in every environment.
- The 403 response body correctly discloses nothing.
- `frontend/lib/auth/access-scope.ts:72-77` genuinely fails closed for an unresolvable
  delivery-tier identity.

---

## Fixed so far

Findings **1**, **2**, **3**, **4**, **5**, **6**, and the `project:update` bug. Regression tests:

| Finding | Test |
|---|---|
| 1 — forgeable session | `frontend/__tests__/auth/session-forgery.test.ts` |
| 2 — grant rank | `backend/tests/test_grant_rank.py` |
| 3 — per-project scope | `backend/tests/test_project_workspace_scope.py` |
| 4 — aggregate scope | `backend/tests/test_aggregate_scope.py` |
| 5 — binding liveness | `backend/tests/test_binding_liveness.py` |
| 6 — stale tokens | `backend/tests/test_token_epoch.py`, `backend/tests/test_revocation_takes_effect.py` |
| `project:update` | `backend/tests/test_project_update_permission.py` |

See `docs/rbac-auth-implementation.md` for the updated description of the mechanisms
behind 1 and 2.

**Still open: 7–14.** All six HIGH and CRITICAL findings are closed. What remains is a
MEDIUM tail: permissions declared and never enforced (8), a missing tenant id on one
override read (9), two governance writes on delivery gates (10), roster and user-detail
reads open to anyone signed in (11), client gates that fail open (12), and two stale
mirrors (13).

**Deployment note for finding 6.** The stale-token check needs `REDIS_URL` set. Without
it the module logs once and disables itself, and revocation reverts to the old
wait-for-expiry behaviour — no error, no refusal, so it will not announce itself. Redis is
already required for the JTI denylist and the worker pool, but this is a third thing that
quietly degrades without it.
