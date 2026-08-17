# RBAC & Auth — what was implemented

Companion to `docs/rbac-auth-design.md`, which set out seven tasks and recorded what
did not exist on 2026-08-12. This document records what now does, and — where the
build deliberately departs from that spec — why.

Every claim below was checked against the code on 2026-08-16, not against the plan.
File paths are given so each one can be re-verified.

---

## The seven tasks

| # | Task | Status | Where |
|---|---|---|---|
| 1 | `can_perform(user, permission, resource)` + scope chain | **Done** | `shared/authz/can_perform.py`, migration `0003` |
| 2 | Seed built-in roles + startup health check | **Done** | `shared/authz/catalog.py`, `process_api.py:554` |
| 3 | Auth middleware on all routes + enforcement | **Done** | `shared/authz/dependency.py`, `shared/authz/audit.py`, `tests/test_rbac_matrix.py` |
| 4 | Self-approval blocking | **Done** | `shared/services/approval_requests.py`, migration `0007` |
| 5 | Microsoft Entra ID SSO | **Partial** | `shared/routers/org.py`, migration `0009` — schema + settings API done, Entra login exchange not wired |
| 6 | Fallback approval routing | **Deliberately dropped** | migration `0008` — see below |
| 7 | Custom roles with subset validation | **Done** | `shared/routers/custom_roles.py`, migration `0004` |

### The three decisions the design doc left open

All three were resolved, and each went the way the design doc recommended:

- **403 body** → generic. `require_permission` raises a body that does not name the
  missing permission or confirm the resource exists. The detail goes to `audit_events`
  instead. The one exception is custom-role creation, which names the excess
  permissions — those are the caller's *own* permissions described back to them, so
  there is nothing to disclose, and an opaque refusal across fifteen ticked checkboxes
  is unusable.
- **`permissions_json` vs the join table** → the normalized `role_permissions` join
  table was kept. The FK onto the `permissions` catalogue makes an unknown permission
  string unrepresentable, and the boot-time drift check supplies the tamper-resistance
  that JSON was wanted for.
- **10 vs 12 roles** → all kept. `ROLE_TIER` in `shared/authz/permissions.py` carries
  thirteen keys: twelve assignable roles plus `custom`, the marker for custom-role
  bindings. `contributor` is load-bearing — it is the two-step onboarding handover —
  and `scrum_master` is process visibility with no approval authority.

### Task 6 was dropped on purpose

Migration `0008_drop_approval_fallback` removes the `fallback_used` column that `0007`
had added in anticipation of routing that substitutes a Project Admin when nobody holds
a request's `target_role`.

That behaviour is not wanted: **a request nobody can action stays pending and visible
rather than being handed to someone else.** The column went with it, because left in
place it would sit at `false` forever while implying a substitution mechanism exists.
`target_role` stays — "who should decide this" is meaningful whether or not anyone
currently holds the role, and it is what makes an unactioned request diagnosable rather
than merely stuck.

---

## Authentication

Three modes, selected by `NEXT_PUBLIC_AUTH_MODE` (`frontend/lib/auth/mode.ts`):

- **`local`** — email + password against FastAPI `POST /auth/login`. Passwords are
  passlib + bcrypt with `deprecated="auto"`, so hashes re-upgrade on verify.
- **`auth0`** — OIDC broker, dormant, kept for SSO.
- **`mock`** — role-picker session so the app boots with no auth backend. The default.

`local` and `mock` both persist a resolved, permission-enriched `Session` in the
`sdlc_session` cookie, so `getSession()` needs no round-trip
(`frontend/lib/auth/session.ts`). `frontend/middleware.ts` is the guard; only `/login`,
`/api/auth`, and static prefixes are public.

Permissions are resolved once at login by `shared/authz/resolver.py` and baked into the
JWT. Enforcement reads the claim from `request.state`, so the common path costs no DB
hit.

### SSO configuration (task 5)

Migration `0009_org_settings` moves enterprise identity config off environment
variables, where it was a property of the *deployment* rather than of the organization.
`GET /org/settings` is readable by any member — MFA policy and session lifetime govern
their own login, and the SSO client id is not a credential. `PATCH /org/settings/sso`
writes it.

**The client secret is not stored.** The column is `entra_client_secret_ref`, a pointer
into the secret store (Key Vault in production, a Fernet-encrypted row locally). A
column that *could* hold the secret would eventually hold it — someone writes the value
"just for now" and it is then in every backup, replica, and psql screenshot. Making the
column a reference removes the option rather than discouraging it.

**What is not done:** the Entra token exchange itself — validating an Entra token,
extracting the UPN, and enforcing `mfa_required` via the `amr` claim. The schema and the
settings API are in place; the login path still goes through the generic OIDC plumbing.
This was the largest item in the plan and the one needing a sandbox Azure tenant.

---

## Authorization: three separate questions

The build keeps these apart deliberately, because no one of them answers another.

| Question | Module | Answer |
|---|---|---|
| What may they **do**? | `shared/authz/permissions.py` | a permission set |
| **Which** units/projects? | `shared/authz/read_scope.py` | the readable BU/project set |
| **Who** are they on the ladder? | `shared/authz/effective_role.py` | a tier, for request routing |

`read_scope` exists because **a count discloses as much as a row**: telling a BU Admin
"the organization has 47 people" is a fact about units they cannot open. Anything
returning totals computes them from the allowed set rather than filtering rows after
the fact.

`effective_role` exists because "one tier above the requester" cannot be computed from
a permission set — two roles can hold `member:manage` and sit on different rungs.
**Highest standing wins**: somebody bound `bu_admin` in one unit and `developer` on
another unit's project is a BU Admin for routing, or they could file from their lowest
rung and have their own tier decide it.

### `can_perform` — the scope chain (task 1)

```
organization  ->  business_unit  ->  project  ->  workstream
```

Given a resource, the chain from it up to the organization is built and the user's
active assignments are matched against every level. A role held at **any ancestor**
applies — that is what lets an Org Admin act on a business unit's resources without a
per-unit assignment. The reverse never happens: a project-scoped role does not reach the
unit above it, because the project is not an ancestor of the unit.

`workstream` is the fourth level, added by migration `0003` along with the extended
`scope_kind` CHECK. That migration also adds `role_bindings.expires_at` and
`granted_by`, which is what makes a temporary elevation expressible at all — `status` is
a flag someone must remember to flip; an expiry is enforced by the clock.

> **Corrected 2026-08-17.** The clock enforces it *here* and nowhere else. The login-time
> resolver that produces the JWT claim every `require_permission` reads
> (`shared/authz/resolver.py:61-72`) filters neither `status` nor `expires_at`, and the ORM
> model does not even declare those columns. A temporary elevation therefore does not end,
> and a deactivated binding keeps granting, until the token expires. See finding 5 in
> `docs/rbac-audit-2026-08-17.md`.

**Deny is the default and every failure returns it.** No tenant, unknown resource,
resource in another tenant, no assignment, expired, deactivated, or a role that lacks
the permission — all DENY, one failure value. A resolver that raises on "not found" and
returns `False` on "no permission" invites callers to treat the exception as an error
case and fall through.

---

## The role catalogue and its two tables

`shared/authz/catalog.py` is the single declarative description of the built-in roles —
name, label, description, tier, default scope, permissions. Previously those facts lived
in three files and nothing caught you if you edited two.

### Boot-time drift check (task 2)

`process_api.py:554` calls `assert_rbac_catalog()` before anything else touches
bindings. `roles` / `permissions` / `role_permissions` are global — no `tenant_id`, no
RLS — so a direct `INSERT` there escalates every holder of that role **in every
tenant**. The app refuses to start on any difference, logging the diff first.
`RBAC_CATALOG_AUTOREPAIR` allows reconciliation instead of failure.

### `role_permissions` vs `role_permission_overrides`

Migration `0011` exists because `role_permissions` *looks* editable and is not — it is
reconciled from code on every boot, so an edit from the Roles page would work perfectly
until the next restart and then silently revert.

- `role_permissions` — the **shipped default**, owned by code, reconciled at boot, what
  "reset to defaults" restores to. Global.
- `role_permission_overrides` — owned by the org's admin, never touched by the seeder.
  Tenant-scoped, because one org retuning its Developer role must not change another's.

**Effective = the override when one exists, the default otherwise**, merged in
`shared/authz/role_permissions.py` — one function, because two callers read permissions
(`resolve_permissions_for_user` at login, `can_perform._permissions_at` per scoped
check) and if only the first applied overrides you would get "works on the dashboard but
not on the project page", which takes a day to find.

The merge is **whole-set replacement**, not a delta. A delta has to answer "what happens
when the shipped default later gains a permission this org removed", and every answer is
a surprise.

### Tier separation

A person must never hold both governance and delivery tier **within one scope** — that
is self-approval. Holding governance in one scope and delivery in another is legitimate
and common. Enforced by `_assert_no_tier_conflict` in `shared/authz/grant.py`; it cannot
be a table constraint because the rule is scoped, not global.

`grant.py` is structurally incapable of crossing tenants, because the write goes through
`get_db_session_for_tenant` and FORCE RLS enforces the policy.

> **Corrected 2026-08-17.** This section used to claim grant.py was the *only* write path
> into `role_bindings` and was operator-only. Neither is true: `POST /projects/{id}/members`
> calls it from a request handler (correctly), and `PATCH`/`DELETE` on the same router plus
> `project_scoped.py` write the table directly, bypassing `_assert_no_tier_conflict` and
> `record_rbac_change`. See finding 7 in `docs/rbac-audit-2026-08-17.md`.

---

## Enforcement (task 3)

`require_permission(perm)` in `shared/authz/dependency.py` is the gate. `public()` is
the explicit opt-out marker.

**Fail-open is structurally impossible.** `assert_all_routes_protected(app)` runs
unconditionally at boot — including local dev, because a forgotten authz dependency must
be a boot failure *everywhere*. Every `APIRoute` must be permission-protected,
`public()`-marked, or on the documented allowlist; an unmarked route makes the app refuse
to start with a `RuntimeError` naming it.

### Audit

`shared/authz/audit.py` writes two kinds of record:

- `record_rbac_change` — a binding created or removed, a role defined. Takes **the
  caller's session**, so the grant and its audit row commit or roll back together. An
  audit write in its own transaction can record something that never happened.
- `record_access_denied` — the 403 trail. Best-effort in its own short-lived session,
  because the refusal happens in a dependency before any route session exists. Denials
  are kept for the same reason a door log keeps failed badge swipes: one is noise, forty
  in a minute from one account is the only warning you will get.

Migrations `0005` and `0006` restore append-only enforcement and the canonical query
index, both lost in the `0001` squash. Append-only is a **REVOKE** rather than a trigger,
so it is a privilege of the role and not a rule the application can be talked out of —
even a SQL-injection foothold running as `sdlc_app` cannot rewrite history it is not
granted to rewrite.

---

## Custom roles (task 7)

`shared/routers/custom_roles.py`. The catalogue check alone only ever asked whether a
permission *exists*. The added rule is **no escalation**: a creator cannot put a
permission into a custom role they do not themselves hold. Without it the endpoint was a
straightforward privilege escalation — `role:manage` is held by a BU Admin, and any
catalogue permission could be packaged into a role and then assigned, including to
themselves.

The creator's permissions are re-resolved from the database, not read from the token.
`admin:*` passes everything by design — the wildcard *is* the full catalogue.

Migration `0004` adds `scope_kind` / `scope_id` / `created_by`, so a role has an owner
and a bound on where it may be assigned. An org-scoped role needs org-wide authority; a
unit-scoped one needs write access to that unit.

---

## Approvals and governance requests

Two lanes, two tables, deliberately not merged.

**`approval_requests`** (`0007`) models a **gate**: something an agent produced, paused
for a human, one terminal decision. `initiator_id` is `NOT NULL` — it is the column that
makes the self-approval rule possible, and a request with no initiator would silently
bypass it. The check lives in the **service layer**
(`shared/services/approval_requests.py`), not the controller, so the route, a future
queue consumer, and any internal caller all hit it.

**`governance_requests`** (`0010`) is the other lane: raised *by* a person, routed
upward through tiers until someone can grant it, carrying a type, priority, attachments,
a timeline, and for one type a second decision stage. Reusing `approval_requests` would
have meant widening `request_type` from two values to sixteen, adding five
always-NULL-for-gates columns, and relaxing the status CHECK that currently makes
"decided but no decider" unrepresentable. **The two lanes route differently on purpose,
and a shared table is how they stop routing differently.**

`governance_request_events` is append-only — `sdlc_app` gets INSERT and SELECT and
nothing else — so "who escalated this and when" survives even a bug in the service layer.

---

## The frontend half

`frontend/lib/auth/permissions.ts` mirrors the backend's `has_permission` exactly,
including the `admin:*` wildcard. **This is UX gating, not a security boundary** — a user
who tampers with client state to reveal a hidden action still gets a 403 from FastAPI on
the actual call.

> **Corrected 2026-08-17, now fixed.** That last sentence was false as written. The session
> cookie was unsigned base64 and `httpOnly: false`, and `lib/bff/jwt.ts` re-signed its
> `permissions` array into the JWT FastAPI trusts — so tampering with client state did not
> get a 403, it got `admin:*`. The BFF no longer mints tokens on the local path: the
> backend-issued token is stored `httpOnly` and forwarded unchanged, which is what makes
> the sentence true. See finding 1 in `docs/rbac-audit-2026-08-17.md`.

`shared/authz/permissions.py` carries an explicit **mirror contract**: the frontend is
the spec (it was designed first and the UI gates off these exact strings), and
`test_enterprise_rbac_catalog.py` asserts the seeded DB matches the backend module.
Drift between code and database is caught mechanically; drift against the frontend is
caught by review.

`frontend/lib/auth/access-scope.ts` is the single place the scope boundary is drawn.
Identity resolves in priority order: `session.identityId`, then session email against the
seeded roster, then no match. That last branch **fails closed** — an unresolvable
delivery-tier person gets an empty scope and an explicit "no access yet" state, because
failing open would reinstate exactly the leak the module exists to close. An
organization-scoped role still governs everything, since its authority is not a
membership row.

`frontend/lib/auth/effective-role.ts` prefers `session.platformRole` when the backend
issues it and infers from permissions otherwise. The authoritative value wins the moment
it exists, with no code change — which is why that field must not be deleted for looking
unused.

---

## Schema map

| Migration | Adds | Task |
|---|---|---|
| `0003_scope_chain_and_expiry` | `workstreams`; `role_bindings.expires_at`, `.granted_by` | 1 |
| `0004_custom_role_scope` | `custom_roles.scope_kind`, `.scope_id`, `.created_by` | 7 |
| `0005_audit_append_only` | REVOKE UPDATE/DELETE on `audit_events` | 3 |
| `0006_audit_query_index` | `ix_audit_tenant_run_created` | 3 |
| `0007_approval_requests` | `approval_requests` | 4 |
| `0008_drop_approval_fallback` | drops `fallback_used` | 6 (dropped) |
| `0009_org_settings` | `org_settings` | 5 |
| `0010_governance_requests` | `governance_requests`, `governance_request_events` | — |
| `0011_role_permission_overrides` | `role_permission_overrides` | — |

Every tenant-scoped table gets the full RLS lifecycle: `tenant_id` anchor, ENABLE +
FORCE, and both USING and WITH CHECK policies. FORCE alone leaves a table wide open, so
none of the statements may be skipped.

---

## Tests

| File | Covers |
|---|---|
| `test_can_perform.py` | the scope-chain walk, expiry, deactivation, deny-by-default |
| `test_rbac_matrix.py` | each matrix action requires its own permission, not a stricter or looser one |
| `test_enterprise_rbac_catalog.py` | seeded DB matches `shared/authz/permissions.py` |
| `test_rbac_catalog_seed.py` | catalogue seeding |
| `test_rbac_audit.py` | RBAC change + denial records |
| `test_custom_role_subset.py` | the no-escalation rule |
| `test_custom_roles.py` | custom-role CRUD and scope ownership |
| `test_role_permission_overrides.py` | the override merge and reset |
| `test_approval_requests.py` | self-approval blocking |
| `test_governance_requests.py` | the request lane and its routing |
| `test_org_settings.py` | SSO settings read/write, secret-ref handling |
| `test_read_scope.py`, `test_project_run_scope.py`, `test_agent_run_scope.py`, `test_project_scoped.py` | scope filtering on aggregating and project-scoped endpoints |
| `test_local_auth.py`, `test_jwt_auth.py` | login and token verification |

**Status caveat:** the backend suite as a whole is not green — it carries inherited
failures unrelated to this work, so "all backend tests pass" is not a claim this document
makes. The frontend suite is green at 313 tests.

---

## Known gaps

1. **Entra token exchange** (task 5) — schema and settings API done; validating an Entra
   token, extracting the UPN, and enforcing `mfa_required` from the `amr` claim are not.
   Needs a sandbox Azure tenant.
2. **`session_timeout_minutes` is stored but not yet enforced** on the frontend session
   cookie, which still hard-codes eight hours.
3. **Password hardening**, both optional and neither a blocker: argon2id (passlib
   supports it, and `deprecated="auto"` migrates existing bcrypt hashes on next login),
   and a Key Vault pepper so a stolen DB dump alone cannot be cracked offline.

---

See also: `docs/rbac-audit-2026-08-17.md` (an audit of this work against the code, which
found two critical holes and twelve smaller ones — read it before trusting any "Done" in
the table above), `docs/rbac-auth-design.md` (the original seven-task assessment),
`docs/rbac-auth-plan.md` (scope-enforcement gaps found during the fixture migration),
`docs/rbac-tables.md`, and `docs/route-inventory.md`.
