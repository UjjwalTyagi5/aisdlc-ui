import type { PlatformRole } from "@/lib/roles";

/**
 * Role → M7.2 permission strings, one entry per platform role (PRD §33.1 plus
 * Scrum Master). Single source of truth for "what does this role hold" —
 * `mocks/handlers.ts`'s assignable role catalogue and `lib/auth/mock.ts`'s
 * mock-sign-in session builder both read from here, so the two can never
 * drift apart. Mirrors the role × permission matrix (§14.11): governance
 * tier never holds agent-invoke/approval permissions; Developer never holds
 * an approval permission at all (self-approval is structurally impossible,
 * not merely discouraged, per §14.10).
 */
export const ROLE_PERMISSIONS: Record<PlatformRole, readonly string[]> = {
  org_admin: ["admin:*"],
  bu_admin: [
    // The read-only floor. It was missing here while `contributor` — a strictly
    // weaker role — had it, so every backend route behind the `artifact:view`
    // dependency (workspaces, projects, runs, audit, cost, traces) refused a
    // Business Unit Admin: they could administer a unit they could not open.
    // Governance roles do not run agents (PRD §14.8) — that is why `agent:invoke`
    // and `approve` stay absent — but being unable to READ was never the intent.
    "artifact:view",
    "member:manage",
    "role:manage",
    // Both halves, explicitly. `hasPermission` mirrors the backend's exact
    // membership test — `required in perms or "admin:*" in perms` — with no
    // implication from manage to view, so holding only `connector:manage` did
    // not satisfy the Integrations page's `connector:view` gate. The Business
    // Unit Admin runs their unit's connections (PRD §15.2), and every delivery
    // role already lists `connector:view`; this role was the sole exception,
    // and the only one whose sidebar link led to a permission error.
    "connector:manage",
    "connector:view",
    "project:create",
    "model:manage",
    "audit:view",
    "cost:view",
    "workspace:manage",
  ],
  // Onboarded, placed in a unit, and holding nothing until that unit's admin
  // assigns a real role. `artifact:view` is the read-only floor — enough to
  // sign in and see the shell rather than an error page, and deliberately not
  // enough to open an agent, raise a run or read a cost figure.
  contributor: ["artifact:view"],
  project_admin: [
    "member:manage",
    "project:create",
    "model:manage",
    "run:create",
    "run:view",
    "run:cancel",
    "artifact:view",
    "artifact:export",
    "agent:invoke",
    "approve",
    // Documentation's acceptance is automatic and AGENT_OWNER_ROLE.documentation is
    // project_admin — the fallback approver on every agent. It is the override for a
    // stage no delivery role owns.
    "artifact:approve_documentation",
    "connector:view",
    "connector:manage",
    "cost:view",
    "trace:view",
  ],
  ba: [
    "run:create",
    "run:view",
    "artifact:view",
    "artifact:export",
    "agent:invoke",
    "approve",
    "artifact:approve_requirements",
    "connector:view",
  ],
  architect: [
    "run:create",
    "run:view",
    "artifact:view",
    "artifact:export",
    "agent:invoke",
    "approve",
    "artifact:approve_design",
    "artifact:approve_development",
    // AGENT_OWNER_ROLE.review is `architect`, so the Code Review gate routes here.
    // Without this the gate routed to a role that could not pass it, and only an
    // `admin:*` holder could — a sign-off with nobody behind it.
    "artifact:approve_code_review",
    "connector:view",
  ],
  developer: [
    "run:create",
    "run:view",
    "artifact:view",
    "artifact:export",
    "agent:invoke",
    "connector:view",
    "skill:edit",
  ],
  qa: [
    "run:view",
    "artifact:view",
    "artifact:export",
    "agent:invoke",
    "approve",
    "artifact:approve_testing",
    "connector:view",
  ],
  security_engineer: [
    "run:view",
    "artifact:view",
    "artifact:export",
    "agent:invoke",
    "approve",
    // The Security gate is this role's own (AGENT_OWNER_ROLE.security). It held the
    // generic `approve` but not this, and `has_permission` is an exact membership
    // test with no implication from the generic to the specific — so the Security
    // Engineer could not sign off Security.
    "artifact:approve_security",
    "connector:view",
    "audit:view",
    "cost:view",
    "trace:view",
  ],
  devops_engineer: [
    "run:view",
    "artifact:view",
    "artifact:export",
    "agent:invoke",
    "approve",
    "artifact:approve_deployment",
    "connector:view",
  ],
  data_engineer: [
    "run:create",
    "run:view",
    "artifact:view",
    "artifact:export",
    "agent:invoke",
    "approve",
    "connector:view",
  ],
  scrum_master: ["run:view", "artifact:view", "artifact:export", "agent:invoke", "connector:view"],
  custom: ["artifact:view"],
};
