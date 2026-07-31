import { z } from "zod";

/**
 * Org- and Business-Unit-scope onboarding (org_admin inviting into either
 * tier). Project scope reuses `POST /api/projects/:id/members` directly
 * (`lib/schemas/project-membership.ts`) — same underlying `findOrCreateIdentity`
 * primitive, no duplicate logic.
 */
export const OnboardingRequest = z.object({
  email: z.string().email(),
  displayName: z.string().optional(),
  workspaceId: z.string().min(1),
  role: z.string().min(1),
});
export type OnboardingRequest = z.infer<typeof OnboardingRequest>;

export const OnboardingResult = z.object({
  identityId: z.string(),
  email: z.string().nullable(),
  displayName: z.string(),
  initials: z.string(),
  workspaceId: z.string(),
  role: z.string(),
  membershipStatus: z.literal("invited"),
});
export type OnboardingResult = z.infer<typeof OnboardingResult>;
