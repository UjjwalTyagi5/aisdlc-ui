import { z } from "zod";

import {
  ConnectorAccessLevel,
  IntegrationAccessRow,
  ProjectIntegrationAccess,
} from "@/lib/schemas/integration-access";

import { api } from "./client";

export type {
  IntegrationAccessRow,
  AccessUnitEntry,
  AccessProjectEntry,
  ConnectorAccessLevel,
  ProjectIntegrationAccess,
} from "@/lib/schemas/integration-access";

/** Every integration, with the units that hold it and the projects using it. */
export const listIntegrationAccess = () =>
  api("/integrations/access", { schema: z.array(IntegrationAccessRow) });

export interface RevokeAccessInput {
  kind: "connector" | "mcp";
  /** Connector kind or MCP server id. */
  id: string;
  level: "unit" | "project";
  workspaceId?: string;
  projectId?: string;
  /** Display names, carried only so the success toast can name what changed. */
  unitName?: string;
  projectName?: string;
}

/**
 * Give access at one level or the other — the mirror of `revokeIntegrationAccess`.
 *
 * `workspaceId` gives a Business Unit the integration (Organization Admin
 * only). `projectId` turns it on for a project the unit already holds it for,
 * across that project's whole pipeline (either admin tier).
 */
export const grantIntegrationAccess = (input: {
  kind: "connector" | "mcp";
  id: string;
  workspaceId?: string;
  projectId?: string;
  /**
   * Read, write, or both. Omitted means the server's default, which is `read` —
   * least privilege, so widening is always somebody's explicit choice.
   */
  access?: z.infer<typeof ConnectorAccessLevel>;
  unitName?: string;
  projectName?: string;
}) =>
  api("/integrations/access", {
    method: "POST",
    query: {
      kind: input.kind,
      id: input.id,
      workspaceId: input.workspaceId,
      projectId: input.projectId,
      access: input.access,
    },
    schema: z.object({
      ok: z.boolean(),
      changed: z.boolean().optional(),
      access: ConnectorAccessLevel.optional(),
      /**
       * Levels that are permitted but partly hollow — a write-only board can
       * create items but cannot comment on one, because that needs an id only a
       * read can supply. Surfaced to the admin who chose the level rather than
       * logged, since they are the only person who can act on it.
       */
      warnings: z.array(z.string()).default([]),
    }),
  });

/**
 * Take access away at one level or the other. `unit` is the Organization
 * Admin's — the unit loses it entirely; `project` is either admin tier's — the
 * project stops using it while the unit keeps the grant.
 */
export const revokeIntegrationAccess = (input: RevokeAccessInput) =>
  api("/integrations/access", {
    method: "DELETE",
    query: {
      kind: input.kind,
      id: input.id,
      level: input.level,
      workspaceId: input.workspaceId,
      projectId: input.projectId,
    },
    schema: z.object({ ok: z.boolean(), changed: z.boolean().optional() }),
  });


// ── the project rung ─────────────────────────────────────────────────────────
//
// A unit's grant is the ceiling; a project may sit at or below it. These three
// mirror `backend/shared/routers/project_connector_access.py`, whose refusals the
// UI surfaces rather than pre-empting — the server is the authority on whether a
// level is allowed, and duplicating that rule here would give it somewhere to drift.

/** What this project's unit was granted, and what the project actually gets. */
export const listProjectIntegrationAccess = (projectId: string) =>
  api(`/projects/${projectId}/integrations/access`, {
    schema: z.array(ProjectIntegrationAccess),
  });

/**
 * Narrow one integration for this project.
 *
 * Refused with 403 `exceeds_grant` when it asks for more than the unit holds —
 * refused rather than silently narrowed, so somebody who asked for write is told
 * they did not get it instead of believing they did.
 */
export const setProjectIntegrationAccess = (input: {
  projectId: string;
  kind: "connector" | "mcp";
  targetId: string;
  access: z.infer<typeof ConnectorAccessLevel>;
}) =>
  api(`/projects/${input.projectId}/integrations/access`, {
    method: "PUT",
    body: { kind: input.kind, targetId: input.targetId, access: input.access },
    schema: z.object({
      ok: z.boolean(),
      unitAccess: ConnectorAccessLevel,
      projectAccess: ConnectorAccessLevel,
      effectiveAccess: ConnectorAccessLevel,
      warnings: z.array(z.string()).default([]),
    }),
  });

/**
 * Undo a narrowing — the project goes back to inheriting its unit's level.
 * NOT a revoke: revoking is the unit's grant going away, a rung up.
 */
export const clearProjectIntegrationAccess = (input: {
  projectId: string;
  kind: "connector" | "mcp";
  targetId: string;
}) =>
  api(`/projects/${input.projectId}/integrations/access`, {
    method: "DELETE",
    query: { kind: input.kind, targetId: input.targetId },
    schema: z.object({
      ok: z.boolean(),
      cleared: z.boolean(),
      effectiveAccess: ConnectorAccessLevel.nullable(),
    }),
  });
