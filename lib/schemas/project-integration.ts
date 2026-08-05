import { z } from "zod";

import { Timestamp } from "./primitives";

/**
 * The two kinds of thing a project can be approved for. Kept as one union
 * rather than two parallel APIs because the project's question is the same
 * either way — "what may I use, and does it need a credential from me" — and
 * splitting it would put the same screen behind two shapes.
 */
export const ProjectIntegrationKind = z.enum(["connector", "mcp"]);
export type ProjectIntegrationKind = z.infer<typeof ProjectIntegrationKind>;

/**
 * A credential a PROJECT holds against an integration it was granted.
 *
 * Distinct from the connection's own credential, which the onboarding admin
 * owns. This is the project's half: a service account, a bot token, a
 * per-team API key — the thing that identifies THIS project to a tool the
 * organization has already approved. It is why consumption can move to the
 * project without every project sharing one identity.
 *
 * There is no secret in this record. The platform runs without a backend
 * ([[no-backend-static-frontend]]), so there is nowhere to put one;
 * `hasSecret` records that a value was entered, which is the only part of a
 * secret a UI should ever show anyway.
 */
export const ProjectIntegrationCredential = z.object({
  id: z.string(),
  projectId: z.string(),
  /**
   * WHOSE credential this is.
   *
   * A credential authenticates a PERSON against a tool, not a project: a repo
   * bot, a board account, a database role are each somebody's. Keyed on the
   * project alone, the second contributor to configure Jira silently replaced
   * the first — same key, same row — and neither could tell.
   *
   * The project still scopes it (you configure it inside a project, and it is
   * only usable there), but the identity owns it.
   */
  ownerId: z.string(),
  kind: ProjectIntegrationKind,
  /** A connector kind (`jira`) or an MCP server id (`mcp_postgres`). */
  targetId: z.string(),
  /** What this credential is, in the team's words — "Payments CI bot". */
  label: z.string().max(120),
  /** The account/principal it authenticates as, when the tool exposes one. */
  account: z.string().max(200).nullable(),
  hasSecret: z.boolean(),
  updatedBy: z.string(),
  updatedAt: Timestamp,
});
export type ProjectIntegrationCredential = z.infer<typeof ProjectIntegrationCredential>;

export const ProjectIntegrationCredentialInput = z.object({
  kind: ProjectIntegrationKind,
  targetId: z.string().min(1),
  label: z.string().min(1, "Name this credential").max(120),
  account: z.string().max(200).nullable().optional(),
  /** Write-only. Never returned; presence flips `hasSecret`. */
  secret: z.string().max(4000).optional(),
});
export type ProjectIntegrationCredentialInput = z.infer<typeof ProjectIntegrationCredentialInput>;

/**
 * One row of a project's Integrations screen: an approved integration, plus
 * whatever credential the project has configured against it.
 */
export const ProjectIntegration = z.object({
  kind: ProjectIntegrationKind,
  /** Connector kind or MCP server id. */
  id: z.string(),
  name: z.string(),
  description: z.string().nullable().optional(),
  /** The delivery stages it is wired to. Empty for an MCP server. */
  stages: z.array(z.string()).default([]),
  /** Whether this integration expects a project-specific credential at all. */
  needsProjectCredential: z.boolean(),
  credential: ProjectIntegrationCredential.nullable(),
});
export type ProjectIntegration = z.infer<typeof ProjectIntegration>;
