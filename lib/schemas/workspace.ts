import { z } from "zod";

import {
  IdentityId,
  MembershipId,
  OrganizationId,
  WorkspaceId,
} from "./ids";
import { Timestamp } from "./primitives";
import { BudgetWindowFields } from "./budget-window";

/**
 * Enterprise hierarchy + identity contracts (frontend-first; this file IS the
 * backend spec). See docs/superpowers/plans/2026-06-15-workspaces-users-frontend-first-plan.md.
 *
 *   Organization (tenant, hard boundary)
 *     └─ Workspace (business-unit boundary; the active context)
 *          └─ Project → Run → Artifact …
 *   Identity (one per human; SSO is the source of truth)
 *     └─ LinkedIdentity (github / azure_devops / jira — linked + verified, never email-matched)
 *   Membership { identity, workspace, role }  — per-workspace roles
 *   Actor { human | agent | service | external } — stamped on every action
 */

// ───────── Organization ─────────

export const OrganizationStatus = z.enum(["active", "suspended"]);
export type OrganizationStatus = z.infer<typeof OrganizationStatus>;

export const Organization = z.object({
  id: OrganizationId,
  slug: z.string(),
  displayName: z.string(),
  status: OrganizationStatus,
  region: z.enum(["us", "eu"]).optional(),
  plan: z.enum(["trial", "pilot", "enterprise"]).optional(),
  createdAt: Timestamp,
});
export type Organization = z.infer<typeof Organization>;

// ───────── Workspace ─────────

export const WorkspaceStatus = z.enum(["active", "archived"]);
export type WorkspaceStatus = z.infer<typeof WorkspaceStatus>;

/** Data-classification of the work a workspace holds — drives model/connector policy. */
export const DataClassification = z.enum([
  "public",
  "internal",
  "confidential",
  "restricted",
]);
export type DataClassification = z.infer<typeof DataClassification>;

export const Workspace = z.object({
  id: WorkspaceId,
  organizationId: OrganizationId,
  slug: z.string(),
  displayName: z.string(),
  /** What this workspace represents — the business unit / delivery org. */
  businessUnit: z.string().nullable().optional(),
  costCenter: z.string().nullable().optional(),
  dataClassification: DataClassification.default("internal"),
  status: WorkspaceStatus,
  /**
   * Org-Admin-owned active/inactive marker — deliberately NOT the same axis as
   * `status`.
   *
   * `status` is the archive lifecycle: archiving retires a unit and removes it
   * from the switcher. This flag is an operational label the Org Admin sets at
   * creation and can flip at any time ("this unit is stood up but not trading
   * yet", "this unit is winding down"). It is presentational only — nothing is
   * gated on it, an inactive unit keeps working exactly as before. Only the Org
   * Admin may set it (see `app/(app)/workspaces/[id]/page.tsx`).
   *
   * Defaults to `true` so units created before this existed keep parsing.
   */
  isActive: z.boolean().default(true),
  memberCount: z.number().int().nonnegative(),
  projectCount: z.number().int().nonnegative(),
  monthlySpendUsd: z.number().nonnegative(),
  /** Monthly USD cost cap; null = inherit org / unlimited (migration 0032). */
  monthlyBudgetUsd: z.number().nonnegative().nullable().optional(),
  /** The period that cap is valid for — see lib/schemas/budget-window.ts. */
  ...BudgetWindowFields,
  /** The active `bu_admin` membership's display name — null if none has been
   *  appointed yet (see PRD §15.2, create-workspace-dialog.tsx). */
  buAdminName: z.string().nullable().optional(),
  createdAt: Timestamp,
});
export type Workspace = z.infer<typeof Workspace>;

export const WorkspaceList = z.array(Workspace);

// ─── WorkspaceMemberOut — matches the FastAPI backend shape ─────────────────
// Distinct from the frontend-first `WorkspaceMember` (which uses the full
// Identity contract). This is the simpler backend-aligned shape.
export const WorkspaceMemberOut = z.object({
  userId: z.string(),
  email: z.string().nullable().optional(),
  displayName: z.string().nullable().optional(),
  initials: z.string(),
  roleName: z.string(),
  joinedAt: z.string(),
});
export type WorkspaceMemberOut = z.infer<typeof WorkspaceMemberOut>;
export const WorkspaceMemberList = z.array(WorkspaceMemberOut);

export const WorkspaceCreateInput = z.object({
  displayName: z.string().min(2, "Name must be at least 2 characters").max(80),
  businessUnit: z.string().max(120).optional(),
  costCenter: z.string().max(64).optional(),
  dataClassification: DataClassification.default("internal"),
  /**
   * Optional at creation and null by default — the Org Admin may leave the cap
   * unset and let the unit's own Admin set it later (PRD §34.5 budget
   * cascade). Null means "inherit org / unlimited", not "zero".
   */
  monthlyBudgetUsd: z.number().nonnegative().nullable().optional(),
  ...BudgetWindowFields,
  isActive: z.boolean().default(true),
});
export type WorkspaceCreateInput = z.infer<typeof WorkspaceCreateInput>;

/** Result of re-appointing a unit's admin — POST /workspaces/:id/admin. */
export const BusinessUnitAdminChange = z.object({
  workspaceId: z.string(),
  admin: z.object({
    identityId: z.string(),
    userId: z.string(),
    email: z.string().nullable(),
    displayName: z.string(),
    initials: z.string(),
  }),
  /** Who was demoted to make room, or null if the unit had no admin yet. */
  replacedDisplayName: z.string().nullable(),
});
export type BusinessUnitAdminChange = z.infer<typeof BusinessUnitAdminChange>;

// ───────── Identity & linked external accounts ─────────

/** External systems a human identity can be linked to (for cross-system attribution). */
export const ExternalSystem = z.enum(["github", "azure_devops", "jira", "gitlab"]);
export type ExternalSystem = z.infer<typeof ExternalSystem>;

/** How a link was established — only `oauth`/`scim` are trusted for compliance. */
export const LinkProvenance = z.enum(["scim", "oauth", "admin"]);
export type LinkProvenance = z.infer<typeof LinkProvenance>;

export const LinkedIdentity = z.object({
  id: z.string(),
  identityId: IdentityId,
  system: ExternalSystem,
  /** Stable external id (GitHub node id, ADO descriptor, Jira accountId) — NOT the handle. */
  externalId: z.string(),
  handle: z.string().nullable().optional(),
  verified: z.boolean(),
  provenance: LinkProvenance,
  linkedAt: Timestamp.nullable().optional(),
});
export type LinkedIdentity = z.infer<typeof LinkedIdentity>;

/** One canonical record per human. SSO subject is the source of truth. */
export const Identity = z.object({
  id: IdentityId,
  ssoSubject: z.string(),
  email: z.string().nullable(),
  displayName: z.string(),
  initials: z.string(),
  idpSource: z.string().nullable().optional(),
  avatarUrl: z.string().nullable().optional(),
  links: z.array(LinkedIdentity).default([]),
});
export type Identity = z.infer<typeof Identity>;

// ───────── Membership (per-workspace role) ─────────

export const MembershipStatus = z.enum(["active", "invited", "deactivated"]);
export type MembershipStatus = z.infer<typeof MembershipStatus>;

export const Membership = z.object({
  id: MembershipId,
  identityId: IdentityId,
  workspaceId: WorkspaceId,
  role: z.string(),
  status: MembershipStatus,
  invitedAt: Timestamp.nullable().optional(),
});
export type Membership = z.infer<typeof Membership>;

/** A member as shown in a workspace member list — identity + their role here (frontend-first shape). */
export const WorkspaceMember = z.object({
  membershipId: MembershipId,
  identity: Identity,
  role: z.string(),
  status: MembershipStatus,
});
export type WorkspaceMember = z.infer<typeof WorkspaceMember>;

// ───────── Actor (who/what did an action) ─────────

export const ActorType = z.enum(["human", "agent", "service", "external"]);
export type ActorType = z.infer<typeof ActorType>;

export const Actor = z.object({
  type: ActorType,
  id: z.string(),
  displayName: z.string(),
  identityId: IdentityId.nullable().optional(),
});
export type Actor = z.infer<typeof Actor>;

// ───────── Invite ─────────

export const InviteStatus = z.enum(["pending", "accepted", "revoked"]);
export type InviteStatus = z.infer<typeof InviteStatus>;

export const Invite = z.object({
  id: z.string(),
  email: z.string(),
  workspaceId: WorkspaceId,
  role: z.string(),
  status: InviteStatus,
  expiresAt: Timestamp.nullable().optional(),
});
export type Invite = z.infer<typeof Invite>;
