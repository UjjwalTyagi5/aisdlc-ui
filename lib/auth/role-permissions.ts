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
    "member:manage",
    "role:manage",
    "connector:manage",
    "project:create",
    "model:manage",
    "audit:view",
    "cost:view",
    "workspace:manage",
  ],
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
