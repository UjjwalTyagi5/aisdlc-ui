/**
 * Session → access scope. The bridge between "who is signed in" and "which
 * Business Units and projects they may see".
 *
 * Every list endpoint that can leak across a scope boundary calls
 * `resolveSessionScope(session)` and filters by the result. Nothing else in the
 * app should reach into the membership stores to answer that question, so there
 * is exactly one place where the boundary is drawn and exactly one place to
 * change when the backend starts issuing the resolved scope itself.
 *
 * IDENTITY RESOLUTION, in priority order:
 *   1. `session.identityId` — the authoritative seam (see `lib/auth/types.ts`).
 *   2. the session email, matched against the seeded roster — covers the
 *      email+password local-auth path, which has no persona map.
 *   3. no match: an organization-scoped role still governs everything (its
 *      authority is not a membership row), and anyone else sees nothing.
 *
 * Step 3's asymmetry is deliberate. Failing OPEN for an unresolvable
 * contributor would reinstate exactly the leak this module exists to close, so
 * an unknown delivery-tier person gets an empty scope and an explicit
 * "no access yet" empty state rather than the whole tenant.
 *
 * Presentation only — `hasPermission()` stays the action gate and the backend
 * stays the enforcement boundary (PRD §14.10).
 */
import { ROLE_META } from "@/lib/roles";
import {
  type AccessScope,
  emptyScope,
  listBindingsForIdentity,
  orgWideScope,
  resolveAccessScope,
} from "@/lib/mock/access-scope";
import { getIdentityByEmail } from "@/lib/mock/workspace-fixtures";

import { effectivePlatformRole } from "./effective-role";
import type { Session } from "./types";

export type { AccessScope } from "@/lib/mock/access-scope";

/** The identity a session acts as, or null when no seeded person matches. */
export function sessionIdentityId(session: Session | null): string | null {
  if (!session) return null;
  if (session.identityId) return session.identityId;
  const email = session.user?.email;
  if (!email) return null;
  return getIdentityByEmail(email)?.id ?? null;
}

/**
 * The Business Units and projects this session may see.
 *
 * Server-safe and synchronous — route handlers call it straight after
 * `getSession()`; the `/api/auth/access-scope` endpoint serialises it for the
 * browser so client components never re-derive it from fixtures.
 */
export function resolveSessionScope(session: Session | null): AccessScope {
  if (!session) return emptyScope(null);

  const role = effectivePlatformRole(session);
  const identityId = sessionIdentityId(session);

  // The `admin:*` wildcard is the Organization Admin's, and it passes every
  // permission check — so it must pass the scope check too, or an org admin
  // would hold every verb over an empty set of nouns.
  if (session.permissions?.includes("admin:*")) {
    return identityId
      ? orgWideScope(identityId, listBindingsForIdentity(identityId))
      : orgWideScope(null);
  }

  if (!identityId) {
    return role && ROLE_META[role].scope === "organization"
      ? orgWideScope(null)
      : emptyScope(null);
  }

  return resolveAccessScope(identityId, role);
}
