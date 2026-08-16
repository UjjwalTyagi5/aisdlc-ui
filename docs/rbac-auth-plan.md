# RBAC & Auth — state of play and plan

Assessed 2026-08-12 against the running code, not the design docs.

---

## 1. What is built and working

### Authentication

| Capability | Where | State |
|---|---|---|
| Local email + password sign-in | `backend/shared/routers/auth_local.py` | **Done** |
| Self-serve sign-up (grants nothing) | same, `POST /auth/register` | **Done** |
| Single-org bootstrap + seeded org admin | `backend/shared/auth/bootstrap.py` | **Done** |
| Password hashing (bcrypt via passlib) | `backend/shared/auth/passwords.py` | **Done** |
| Change password | `POST /auth/change-password` | **Done** |
| HS256 JWT minted per request by the BFF | `frontend/lib/bff/jwt.ts` → `client.ts` | **Done** |
| Session cookie (`sdlc_session`, 8h) | `frontend/lib/auth/mock.ts` | **Done** |
| JWT middleware + exempt paths | `backend/process_api.py` | **Done** |
| JTI denylist (Redis) | `backend/shared/auth/denylist.py` | Built, **unused by logout** |
| OIDC / Auth0 SSO | `config/auth/providers.py`, `frontend/lib/auth/auth0.ts` | Built, **flag off** |
| SCIM provisioning | `backend/scim/router.py` | Built, **flag off** |

### Authorization

| Capability | Where | State |
|---|---|---|
| Permission catalogue (leaf strings + wildcards) | `shared/authz/permissions.py` | **Done** |
| 12 platform roles + role→permission map | same, mirrored in `frontend/lib/roles.ts` | **Done** |
| `role_bindings` at org / business_unit / project scope | `shared/models/orm.py` | **Done** |
| Permission resolution from bindings | `shared/authz/resolver.py` | **Done**, fail-closed |
| `require_permission` dependency | `shared/authz/dependency.py` | **Done** |
| Boot scan: every route protected | `assert_all_routes_protected` | **Done** — passes |
| Row-level security per tenant (FORCE RLS) | migration `0001_baseline` | **Done** |
| Idempotent grant / revoke + tier-conflict guard | `shared/authz/grant.py` | **Done** |
| Custom roles (tenant-defined) | `shared/routers/custom_roles.py` | **Done** (backend) |
| Org-wide read predicate | `shared/authz/read_scope.py` | **Done** (new) |
| Unit-scoped write guard | `assert_can_write_workspace` | **Done** (new) |

### Fixed during the fixture migration

Five authorization holes, each previously masked by a frontend filter:

1. `GET /workspaces` listed every sibling unit to a BU Admin
2. `POST /workspaces` let a BU Admin create sibling units
3. `POST /admin/assignments` let a BU Admin grant **any role, including `org_admin`**, in any unit
4. `DELETE /admin/assignments` let them revoke a sibling unit admin's role
5. `POST /admin/members` let them create members inside any unit

Pinned by `backend/tests/test_read_scope.py`.

---

## 2. The gaps, in priority order

### P0 — Scope is not enforced on resource endpoints

**This is the central gap.** The backend enforces two of the three questions:

- *Are you authenticated?* → JWT middleware ✅
- *May you do this verb?* → `require_permission` ✅
- *On WHICH business unit / project?* → **mostly not enforced**

Tenant isolation (RLS) stops cross-org reads, but **within** an organization most
resource endpoints return everything in the tenant. The frontend was doing the
narrowing from membership fixtures, so removing fixtures removes the enforcement.

Confirmed unscoped:

| Endpoint | Scoping today | Should be |
|---|---|---|
| `GET /projects` | tenant + **active workspace** | + caller's project bindings |
| `GET /runs` | tenant only — no `role_bindings` reference at all | + caller's projects |
| `GET /artifacts`, `/traces`, `/cost`, `/conversations` | tenant only (assumed — verify each) | + caller's projects |

> **Known regression:** wiring `/api/projects` to the backend dropped the frontend's
> `canReadProject` filter. A contributor bound to one project now sees every project
> in the active unit. Introduced by this migration; fix is item 1 below.

### P0 — `/auth/access-scope` is entirely fixture-backed

`frontend/app/api/auth/access-scope/route.ts` resolves the viewer's units and
projects from `lib/mock/access-scope.ts`. This is the layer the sidebar, every
scoped list, every scope indicator and the My Access page read. **The frontend's
whole notion of "which units and projects am I in" is currently fabricated.**

There is no backend endpoint returning a caller's own bindings.

### P1 — Permissions are frozen in the session cookie for 8 hours

Permissions are resolved once at login and baked into `sdlc_session`
(`MOCK_COOKIE_MAX_AGE = 60*60*8`). Nothing re-checks them. **Revoking a role has no
effect until the user signs in again** — observed live: deleting an `org_admin`
binding left the badge and access intact until sign-out.

For a permissions system this is the most user-visible correctness gap.

### P1 — Role changes are not audited

`shared/authz/grant.py` and `shared/routers/admin.py` write **no `AuditEvent`**.
Granting and revoking roles are the highest-leverage writes on the platform and
they leave no trail. The `audit_events` table and `GET /audit` already exist.

### P2 — Logout does not revoke anything

`/api/auth/logout` deletes the cookie. The JTI denylist is built but never called.
Largely mitigated because the BFF mints a short-lived token per request from the
cookie — but a leaked token stays valid until expiry.

### P2 — Custom roles are backend-only end to end

`shared/routers/custom_roles.py` exists and the frontend has
`/api/admin/custom-roles`, but the UI path is unverified against real data.

### P2 — SSO and SCIM are built but dark

`ENABLE_OIDC=false`, `ENABLE_SCIM` off. Neither has been exercised against a real
tenant. Enterprise readiness claims depend on both.

### P3 — Absent entirely

MFA · password policy / rotation · account lockout on repeated failure ·
active-session listing and remote revoke · break-glass / emergency access ·
per-role rate limiting.

---

## 3. Plan

### Phase 1 — Close the scope gap (P0)

1. **`GET /auth/me/scope`** (new backend endpoint) — return the caller's real
   bindings: org-wide flag, business unit ids + names, project ids + names, and the
   role at each. Shape must match `frontend/lib/schemas/access-scope.ts` so the
   frontend seam becomes a passthrough.
2. **Wire `/api/auth/access-scope`** to it and delete `lib/mock/access-scope.ts`.
3. **Add project-scope filtering** to `GET /projects` and `GET /runs` — org-wide
   callers see all; everyone else sees units they are bound to plus projects they
   are bound to. Reuse `read_scope.py`.
4. **Audit the remaining resource endpoints** (`artifacts`, `traces`, `cost`,
   `conversations`, `capabilities`) and apply the same filter.
5. **Regression tests** per endpoint: org-wide sees all, scoped sees own, unbound
   sees none.

*Fixes the known `/projects` regression and makes the frontend's scoping real.*

### Phase 2 — Make revocation take effect (P1)

6. **Re-resolve permissions on navigation.** `GET /auth/me` already returns live
   permissions; have the session read path call it and refresh the cookie when it
   differs. Alternative, more correct and more work: server-side sessions keyed by
   id, permissions read per request.
7. **Shorten the cookie** to ~1h regardless, as defence in depth.
8. **Call the JTI denylist on logout** so an issued token dies with the session.

### Phase 3 — Accountability (P1)

9. **Audit every RBAC write** — grant, revoke, member create, custom-role change,
   model grant change. Actor, target, scope, before/after, timestamp.
10. **Surface it** on the existing audit page, filterable by actor and by target.

### Phase 4 — Enterprise auth (P2)

11. Exercise **OIDC** end to end against a real Auth0 tenant; verify the BFF
    forwards the RS256 token instead of minting HS256.
12. Exercise **SCIM** provisioning / deprovisioning; confirm a deactivated user is
    denied at next request, not at next login.
13. Verify **custom roles** end to end against real data.

### Phase 5 — Hardening (P3)

14. Password policy + lockout on repeated failure.
15. MFA.
16. Session listing + remote revoke.

---

## 4. Suggested order

Phase 1 first, without exception — it is both a live regression and the foundation
everything else assumes. Phase 2 next, because a permissions system where
revocation takes eight hours will not survive its first audit. Phase 3 is small and
buys most of the compliance story. Phases 4–5 are scheduled work, not urgent.

Related: `docs/route-inventory.md` for the full frontend → backend route mapping.
