# RBAC & Auth — your 7 tasks vs. the current build

Checked against the running schema and code on 2026-08-12. Nothing below is taken
from design docs; every "exists" claim was verified in `orm.py`, the migrations, or
the routers.

---

## 0. Password storage — already enterprise-grade

`shared/auth/passwords.py` uses **passlib + bcrypt** with `deprecated="auto"`, so
hashes re-upgrade on verify. Plaintext is never stored or logged. Nothing to fix.

Two optional hardenings, not blockers:
- Move to **argon2id** (passlib supports it; `deprecated="auto"` migrates existing
  bcrypt hashes on next successful login, so it is a config change plus a dependency).
- Add a **pepper** from Key Vault, so a stolen DB dump alone cannot be cracked offline.

---

## 1. `canPerform(userId, permission, resource)`

**Exists:** `resolve_permissions_for_user(user_id, tenant_id)` → a flat permission
set for the whole tenant. `require_permission(perm)` checks that flat set.

**Missing:** the resource argument and the scope-chain walk. Today a permission held
anywhere in the tenant passes everywhere in the tenant. There is no
`canPerform(user, permission, resource)` anywhere in the codebase.

**Also missing — `workstream` does not exist.** There is no `workstreams` table, and
`role_bindings.scope_kind` is constrained to `('organization','business_unit','project')`.
The chain you describe is four levels; the system models three.

**Elevation with expiry does not exist.** `role_bindings` has `status`
(`active|invited|deactivated`) but **no `expires_at`**, so "elevated role within
expiry / expired elevation" cannot be expressed or tested.

### Work
- New table `workstreams`, extend the `scope_kind` CHECK to include `'workstream'`.
- `ALTER TABLE role_bindings ADD COLUMN expires_at timestamptz NULL, ADD COLUMN granted_by varchar(255) NULL`.
- New `shared/authz/can_perform.py` implementing the walk: resource → workstream →
  project → business_unit → organization, returning DENY on any miss, treating
  `expires_at < now()` as absent.
- The four unit tests you named, plus: no-binding-anywhere → deny; expired
  organization binding → deny; deactivated binding → deny.

> **Semantics to confirm:** walking *up* from the resource means an organization-scope
> assignment does reach a BU resource — which your own test case requires ("org admin
> access to BU resource: allow"). What must not happen is the reverse: a project-scope
> role granting anything at BU or org level. That is the reading I have designed to.

---

## 2. Seed built-in roles + startup health check

**Exists:** roles and their permissions are seeded by migration `0001_baseline` into
normalized `roles` + `role_permissions` tables, with the authoritative baseline
hardcoded in `shared/authz/permissions.py::_ROLE_PERMISSIONS`.

**Missing:** the **startup health check**. Nothing today compares the DB to the
hardcoded baseline, so a direct `INSERT INTO role_permissions` would silently
escalate every holder of that role. This is the single cheapest high-value item in
your whole list.

### Two decisions needed

**(a) `permissions_json` vs the normalized join table.** You specify
`permissions_json`. The build uses `role_permissions` with a real FK onto a
`permissions` catalogue. **I recommend keeping the normalized form**: the FK makes an
unknown permission string unrepresentable, and "which roles grant X" stays a query
rather than a JSON scan. The startup health check gives you the tamper-resistance you
wanted from JSON either way. Say the word if you want JSON regardless.

**(b) 10 roles vs the 12 that exist.** You list 10. The build also has:
- `contributor` — **load-bearing**, do not drop. It is the two-step onboarding
  handover: an Org Admin places someone in a unit as a Contributor, and that unit's
  Admin assigns the real role.
- `scrum_master` — process visibility, no approval authority.
- `custom` — the marker for custom-role bindings.

Your 10 map 1:1 onto the rest.

### Work
- `verify_role_catalog()` called in the lifespan, raising and failing boot on any
  drift: missing role, extra permission, missing permission.
- Log the exact diff before raising — a boot failure that does not say what differs
  costs an hour every time it fires.

---

## 3. Auth middleware on all routes + CI enforcement

**Exists, and it is strong:**
- JWT verify/expiry in middleware; 401 without a token.
- `require_permission` on routes.
- **A boot scan (`assert_all_routes_protected`) that fails startup if any route is
  neither permission-gated nor explicitly marked public.** This already gives you the
  "no endpoint returns 2xx without a JWT" guarantee structurally, at boot, rather
  than by test convention. It currently passes.

**Missing:**
- 403 **response shape**. Current body is `{"detail": "Forbidden"}`.
- 403s are **not written to audit_logs** — only a Prometheus counter and a server log.
- The **CI matrix test** (every endpoint × no-JWT / wrong-scope / correct-JWT).

### Decision needed — this one is a genuine security tradeoff

You want `{error: FORBIDDEN, required_permission, scope}`. The current code
deliberately does the opposite, with an explicit comment: *"do NOT name the missing
perm in the response body; log server-side only"*. Naming the permission and scope
tells an attacker exactly what to escalate toward and confirms the resource exists.

**My recommendation:** return `{error: "FORBIDDEN", request_id}` to the client, write
`required_permission` + `scope` + actor into `audit_logs`, and let an admin resolve
the request_id. You keep the debuggability without the disclosure. If you want the
detail inline, restrict it to non-production builds.

### Work
- Audit-write in the 403 path (actor, permission, scope, route, request_id).
- CI test that enumerates `app.routes` and asserts 401/403/2xx — generated from the
  route table, so a new endpoint is covered the day it is added.

---

## 4. Self-approval blocking

**Nothing exists.** There is no `approval_requests` table. Approvals today are
*derived* from `runs.gate_pending` — there is no initiator, no approval id to POST
to, and therefore nothing to compare a caller against.

### Work — new table
```
approval_requests(
  id uuid pk, tenant_id uuid not null,          -- RLS anchor
  initiator_id varchar(255) not null,
  subject_kind varchar(32), subject_id uuid,     -- what is being approved
  target_role varchar(64) not null,              -- who should decide
  scope_kind varchar(32), scope_id uuid,         -- where that role is held
  request_type varchar(32) not null,             -- standard | specialist_required
  status varchar(16) not null default 'pending', -- pending|approved|rejected|cancelled
  fallback_used boolean not null default false,
  decided_by varchar(255), decided_at timestamptz, decision_reason text,
  created_at timestamptz not null default now()
)
```
- `POST /approvals/{id}/approve` in a **service layer**, with the
  `caller == initiator_id` check inside the service, not the controller — so the
  route, a future queue consumer and any internal caller all hit it.
- Return `400 {error: "SELF_APPROVAL_BLOCKED"}`.
- Direct HTTP test: create then self-approve, assert 400.

---

## 5. Microsoft Entra ID SSO

**Exists:** generic OIDC plumbing (`config/auth/providers.py`, `ENABLE_OIDC`,
RS256 verification, audience guard) and an Auth0 frontend path. Entra is OIDC, so
this is mostly configuration rather than new protocol work.

**Missing:** per-org configuration. Everything is env-level today — there is **no
`org_settings` table**, no MFA enforcement, no session timeout policy.

### Work — new table
```
org_settings(
  tenant_id uuid pk,
  entra_tenant_id varchar(64), entra_client_id varchar(64),
  entra_client_secret_ref varchar(255),      -- Key Vault reference ONLY, never the secret
  mfa_required boolean not null default false,
  session_timeout_minutes int not null default 480,
  updated_by varchar(255), updated_at timestamptz
)
```
- `PATCH /org/settings/sso`, gated on org-wide authority (`is_org_wide`, already built).
- Store only the vault reference; the write path puts the secret in Key Vault via the
  existing `shared/keyvault.py`.
- On login: validate the Entra token, extract UPN, mint the platform JWT with
  bindings embedded. Enforce `mfa_required` by checking the `amr` claim.
- `session_timeout_minutes` should drive the session cookie's max-age — which also
  fixes the 8-hour staleness problem in the other plan.

---

## 6. Fallback approval routing

**Nothing exists on the backend.** The frontend has a routing notion
(`lib/requests/routing.ts::initialApproverRole`) but it computes against fixtures.

Depends entirely on task 4's table.

### Work
- On create: find an active, unexpired binding for `target_role` at
  `(scope_kind, scope_id)`.
- None found and `request_type = 'standard'` → route to Project Admin, set
  `fallback_used = true`.
- None found and `request_type = 'specialist_required'` → escalate to BU Admin, notify.
- Write the routing decision to `audit_logs` either way — including *why* the
  fallback fired, which is the part that gets asked about later.
- `fallback_used` drives the FALLBACK badge; the frontend already has the badge
  vocabulary.

---

## 7. Custom roles with subset validation

**Exists:** `custom_roles` + `custom_role_permissions`, tenant-scoped under FORCE
RLS, and `POST /custom-roles` validating that every permission is in the global
catalogue and is not a wildcard.

**Missing — the important half:** there is **no check that the creator holds the
permissions they are granting**. Today anyone with `role:manage` can mint a custom
role containing any catalogue permission and assign it — a straightforward
escalation path.

**Also missing:** BU-scoped creation. There is one org-level endpoint; your spec
wants `POST /bu/{bu_id}/custom-roles` with the role owned by that unit.

### Work
- Subset check: `set(requested) ⊆ set(resolve_permissions_for_user(caller))`, with
  `admin:*` treated as the full catalogue. Reject the difference by name — here
  naming it is safe, because they are the caller's *own* permissions.
- `ALTER TABLE custom_roles ADD COLUMN scope_kind varchar(32), ADD COLUMN scope_id uuid, ADD COLUMN created_by varchar(255)`.
- A BU-owned custom role is assignable only within that unit.

---

## Summary of schema changes

| Change | Type | For task |
|---|---|---|
| `workstreams` table | new | 1 |
| `role_bindings.expires_at`, `.granted_by` | columns | 1 |
| `scope_kind` CHECK += `'workstream'` | constraint | 1 |
| `approval_requests` table | new | 4, 6 |
| `org_settings` table | new | 5 |
| `custom_roles.scope_kind`, `.scope_id`, `.created_by` | columns | 7 |

Every new table needs the standard treatment used by `0002_org_model_grants`:
`tenant_id` anchor, `ENABLE` + `FORCE ROW LEVEL SECURITY`, both USING and WITH CHECK
policies, and a role-guarded GRANT to `sdlc_app`.

---

## Recommended order

1. **Role-catalogue health check** (task 2) — hours, closes a live tamper path.
2. **Custom-role subset validation** (task 7) — hours, closes a live escalation.
3. **Audit every RBAC write + every 403** (task 3, partial) — small, and everything
   below is easier to trust once it is in.
4. **`can_perform` + `expires_at` + workstreams** (task 1) — the foundation. Do it
   before the approval work, which depends on scope resolution being real.
5. **`approval_requests` + self-approval block** (task 4).
6. **Fallback routing** (task 6) — needs 4 and 5.
7. **`org_settings` + Entra SSO + MFA** (task 5) — largest, and needs a sandbox
   Azure tenant to test against.
8. **CI 401/403/2xx matrix** (task 3, remainder) — once the shapes are settled.

Items 1–3 are quick wins that each close a real hole. Item 4 is the one piece of
genuine architecture.

Three decisions are yours before I start: the **403 body**, **`permissions_json` vs
the join table**, and **10 vs 12 roles**.

See also `docs/rbac-auth-plan.md` (scope-enforcement gaps found during the fixture
migration) and `docs/route-inventory.md`.
