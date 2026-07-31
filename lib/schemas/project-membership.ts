import { z } from "zod";

import { Identity, MembershipStatus } from "./workspace";
import { Timestamp } from "./primitives";

/**
 * Project-scoped role assignment — the data model that was missing
 * entirely: `projects/[id]/members/page.tsx` used to display Business Unit
 * membership through the lens of a project's track, with no way to say
 * "this person is the BA on THIS project specifically." A project member is
 * a binding of (person, project, role) — parallel to `Membership`
 * (person, workspace, role) in `lib/schemas/workspace.ts`, never a property
 * of the person.
 *
 * Agent access is never stored separately — it's always derived at read
 * time from `AGENT_OWNERSHIP[roleName][phase]` (`lib/roles.ts`) given this
 * binding, so assigning someone as `ba` here is the only step needed for
 * them to get BA's agent access on this project.
 */
export const ProjectMember = z.object({
  membershipId: z.string(),
  projectId: z.string(),
  identity: Identity,
  role: z.string(),
  status: MembershipStatus,
  addedAt: Timestamp,
});
export type ProjectMember = z.infer<typeof ProjectMember>;

export const ProjectMemberCreateInput = z.object({
  /** An existing person's email, or a brand-new one to onboard. */
  email: z.string().email(),
  displayName: z.string().optional(),
  roleName: z.string().min(1),
});
export type ProjectMemberCreateInput = z.infer<typeof ProjectMemberCreateInput>;
