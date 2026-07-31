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
  /** Coarse tier — "platform" (Application Team) vs "org" (tenant). Local auth only. */
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
 * The full M7.2 permission vocabulary, mirrored EXACTLY from the backend's
 * canonical `ALL_PERMISSIONS` (agentic_app/shared/authz/permissions.py) —
 * single source of truth, no dual maintenance (D-01). 9 strings.
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
  | "run:create";
