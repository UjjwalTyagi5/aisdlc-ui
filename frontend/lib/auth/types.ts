/** Coarse role model for MVP-0 per DEVELOPMENT_PLAN_MVP.md §2. */
export type Role = "admin" | "member" | "viewer";

/**
 * Fine-grained capabilities. UI uses these — never the raw role — so the
 * matrix in `capabilities.ts` is the single place we change when a
 * permission shifts.
 */
export type Capability =
  | "project:create"
  | "project:update"
  | "project:delete"
  | "run:trigger"
  | "run:approve"
  | "run:reject"
  | "connector:install"
  | "connector:revoke"
  | "audit:view"
  | "audit:export"
  | "settings:update"
  | "user:invite"
  | "user:remove";

export interface SessionUser {
  id: string;
  name: string;
  email: string;
  avatarUrl?: string;
  initials: string;
}

export interface SessionTenant {
  id: string;
  name: string;
  plan: string;
}

export interface Session {
  user: SessionUser;
  tenant: SessionTenant;
  role: Role;
  /** Which auth backend produced this session. */
  mode: "mock" | "auth0" | "local";
  /**
   * Coarse tier. Always "org" — the platform (Application Team) tier was removed
   * along with multi-org support, and nothing branches on this any more.
   *
   * "platform" stays in the union only because sessions minted before the removal
   * are still sitting in browser cookies and `decodeSession` does not validate the
   * field; typing it away would not delete those cookies, it would just make the
   * type lie about what can be read back.
   */
  tier?: "platform" | "org";
  /**
   * DB-resolved effective permission strings (REQ-M7-12 frontend half,
   * milestone-7.2 plan 05). Sourced server-side from the canonical backend
   * resolver via `GET /auth/permissions` (Auth0 mode) or role-seeded mock
   * data (mock mode) — see `getSession()` / `buildMockSession`.
   *
   * Typed as `string[]` (NOT the `Permission` union) so unknown server-issued
   * strings never break decoding; `Permission` exists for call-site ergonomics
   * and to mirror the backend vocabulary for documentation/typo-safety.
   *
   * This is ADDITIVE to the legacy `role`/`Capability`/`can()` model — both
   * coexist; this field exists to support `hasPermission` gating against the
   * M7.2 RBAC vocabulary without ripping out the MVP-0 capability matrix.
   */
  permissions: string[];
  /**
   * The platform role this session acts as — one of the twelve (PRD §33.1 plus Scrum Master)
   * (`org_admin`, `bu_admin`, `project_admin`, `ba`, `architect`, `developer`,
   * `qa`, `security_engineer`, `devops_engineer`, `data_engineer`, `scrum_master`, `custom`).
   *
   * FORWARD-COMPATIBLE SEAM. The backend does not issue this yet, so it is
   * optional and normally absent. `effectivePlatformRole()` reads it when
   * present and falls back to inferring the role from `permissions` when it
   * is not — so the day the backend starts emitting it, the inference is
   * bypassed automatically with no code change and no redeploy coupling.
   *
   * Typed as `string` rather than the `PlatformRole` union so an unrecognised
   * server-issued value can never break session decoding; the resolver
   * validates it against the catalogue before trusting it.
   */
  platformRole?: string;
  /**
   * The person this session acts as, as an identity id in the membership
   * stores — the key that resolves WHICH Business Units and projects the
   * viewer may see (`lib/mock/access-scope.ts`).
   *
   * FORWARD-COMPATIBLE SEAM, exactly like `platformRole` above: the backend
   * does not issue it yet, so it is optional, typed `string` rather than a
   * branded id, and `resolveSessionScope()` falls back to matching on the
   * session's email before giving up. `permissions` says what KIND of action
   * the viewer may take; this is what decides on WHICH scope — the two are
   * independent, and neither substitutes for the other.
   *
   * Presentation only. It decides what the UI fetches and shows; the backend
   * remains the enforcement boundary.
   */
  identityId?: string;
}

/**
 * A HAND-MAINTAINED SUBSET of the permission vocabulary — not the whole of it.
 *
 * This used to claim it mirrored the backend's `ALL_PERMISSIONS` "EXACTLY … 9
 * strings". It never did after the catalogue grew: the backend carries 35 and this
 * lists ten. The drift is already visible in `lib/auth/permissions.ts`, where the
 * `approvePermissionForPhase` branches added later had to omit their `satisfies
 * Permission` because the strings do not typecheck here.
 *
 * `hasPermission` takes `string`, so this costs typo-safety rather than correctness.
 * The authoritative list is `lib/auth/permission-catalog.ts`
 * (`ALL_GRANTABLE_PERMISSIONS`), which IS in exact agreement with the backend.
 * Reconciling the two is finding 13 in `docs/rbac-audit-2026-08-17.md`.
 *
 * `admin:*` is the wildcard honored by both backend `has_permission` and
 * frontend `hasPermission` — granting it passes every permission check.
 */
export type Permission =
  | "admin:*"
  | "artifact:view"
  | "artifact:approve_requirements"
  | "artifact:approve_design"
  | "artifact:approve_development"
  | "artifact:approve_testing"
  | "artifact:approve_deployment"
  | "connector:manage"
  | "project:update"
  | "run:create";
