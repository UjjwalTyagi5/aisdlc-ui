import type { Capability, Role } from "./types";

/**
 * Role → capabilities. This is the authoritative matrix for UI gating.
 * Server enforcement is separate — never trust the client.
 */
const PERMISSIONS: Record<Role, readonly Capability[]> = {
  admin: [
    "project:create",
    "project:update",
    "project:delete",
    "run:trigger",
    "run:approve",
    "run:reject",
    "connector:install",
    "connector:revoke",
    "audit:view",
    "audit:export",
    "settings:update",
    "user:invite",
    "user:remove",
  ],
  member: [
    "project:create",
    "project:update",
    "run:trigger",
    "run:approve",
    "run:reject",
    "connector:install",
    "settings:update",
  ],
  viewer: [],
};

export function can(role: Role, capability: Capability): boolean {
  return PERMISSIONS[role].includes(capability);
}

export function capabilitiesFor(role: Role): readonly Capability[] {
  return PERMISSIONS[role];
}
