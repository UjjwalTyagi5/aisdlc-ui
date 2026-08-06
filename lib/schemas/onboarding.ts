import { z } from "zod";

/**
 * Org-scope onboarding — the Organization Admin admitting someone to the
 * organisation. Project scope reuses `POST /api/projects/:id/members` directly
 * (`lib/schemas/project-membership.ts`) — same underlying `findOrCreateIdentity`
 * primitive, no duplicate logic.
 *
 * `role` is one of `lib/roles.ts::ORG_ASSIGNABLE_ROLES` and nothing else; the
 * route rejects the rest rather than trusting the picker.
 */
export const OnboardingRequest = z.object({
  email: z.string().email(),
  displayName: z.string().optional(),
  /**
   * OPTIONAL, and conditionally so — required for a `contributor` (who must
   * belong somewhere for that unit's admin to be asked for a role), optional
   * for a `bu_admin` (who can be appointed before anyone decides which unit
   * they run). The conditional half is enforced server-side; expressing it in
   * the schema as a refinement would make the client's error message the
   * authority on a rule the server owns.
   */
  workspaceId: z.string().min(1).optional(),
  role: z.string().min(1),
});
export type OnboardingRequest = z.infer<typeof OnboardingRequest>;

export const OnboardingResult = z.object({
  identityId: z.string(),
  email: z.string().nullable(),
  displayName: z.string(),
  initials: z.string(),
  /** Null when a Business Unit Admin was appointed without a unit. */
  workspaceId: z.string().nullable(),
  role: z.string(),
  /** Null in that same case: with no unit there is no membership to have a
   *  status, and reporting "invited" would name a membership that does not
   *  exist. */
  membershipStatus: z.literal("invited").nullable(),
  /** True when the unit's admin was notified that a Contributor is waiting on
   *  them — false when the unit has no admin appointed yet, which the caller
   *  surfaces rather than silently dropping. */
  notifiedBusinessUnitAdmin: z.boolean(),
});
export type OnboardingResult = z.infer<typeof OnboardingResult>;
