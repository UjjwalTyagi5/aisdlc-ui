import { z } from "zod";

import { IntegrationAccessRow } from "@/lib/schemas/integration-access";

import { api } from "./client";

export type {
  IntegrationAccessRow,
  AccessUnitEntry,
  AccessProjectEntry,
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
    },
    schema: z.object({ ok: z.boolean(), changed: z.boolean().optional() }),
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
