import { z } from "zod";

import {
  Project,
  type ProjectCreateInput,
  type ProjectId,
  type ProjectDeliveryStatus,
  type ToolAccessMode,
  paginated,
} from "@/lib/schemas";

import { api } from "./client";

export const IngestBoardResult = z.object({
  ingested: z.number(),
  board_project: z.string().optional(),
  run_id: z.string().optional(),
});
export type IngestBoardResult = z.infer<typeof IngestBoardResult>;

export const BoardProjects = z.object({
  projects: z.array(z.object({ name: z.string(), key: z.string() })),
  selected: z.string().nullable(),
  /** The resolved board provider (e.g. "jira", "azure_devops") — drives the dialog title. */
  provider: z.string().optional(),
  /** All board providers assigned to the Requirements stage (for the provider picker). */
  available_providers: z.array(z.string()).optional(),
});
export type BoardProjects = z.infer<typeof BoardProjects>;

/** Discover the board projects available on a provider (defaults to the first). */
export const getBoardProjects = (id: ProjectId, provider?: string) =>
  api(`/projects/${encodeURIComponent(id)}/board-projects`, {
    query: provider ? { provider } : undefined,
    schema: BoardProjects,
  });

/** Pull work items from a chosen board project (+ provider) into structured stories. */
export const ingestBoard = (id: ProjectId, boardProject?: string, provider?: string) =>
  api(`/projects/${encodeURIComponent(id)}/ingest-board`, {
    method: "POST",
    body: { board_project: boardProject, provider },
    schema: IngestBoardResult,
  });

export const listProjects = (query?: {
  search?: string;
  archived?: boolean;
  page?: number;
  pageSize?: number;
}) =>
  api("/projects", {
    query,
    schema: paginated(Project),
  });

export const getProject = (id: ProjectId) =>
  api(`/projects/${encodeURIComponent(id)}`, { schema: Project });

export const createProject = (input: ProjectCreateInput) =>
  api("/projects", { method: "POST", body: input, schema: Project });

export const archiveProject = (id: ProjectId) =>
  api(`/projects/${encodeURIComponent(id)}/archive`, { method: "POST", schema: Project });

export const restoreProject = (id: ProjectId) =>
  api(`/projects/${encodeURIComponent(id)}/restore`, { method: "POST", schema: Project });

export interface ProjectUpdatePatch {
  name?: string;
  description?: string;
  /** Stage→MCP-server mapping {agent_id: [mcp_server_id, ...]}. */
  mcp_servers?: Record<string, string[]>;
  /** Stage→connector-kind mapping {agent_id: [connector_kind, ...]}. */
  connectors?: Record<string, string[]>;
  /** Access mode per assigned tool — see ToolAccessMode (lib/schemas/project.ts). */
  tool_access_modes?: Record<string, ToolAccessMode>;
  /** Monthly USD cost cap; 0/null clears it (inherit workspace). Migration 0032. */
  monthlyBudgetUsd?: number | null;
  /** Validity period of the cap — see lib/schemas/budget-window.ts. */
  budgetStartDate?: string | null;
  budgetEndDate?: string | null;
  /** Human-set delivery state. Project / BU / Org Admin only — the API 403s
   *  this field (and the cap above) for anyone else. */
  deliveryStatus?: ProjectDeliveryStatus;
}

export const updateProject = (id: ProjectId, patch: ProjectUpdatePatch) =>
  api(`/projects/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
    schema: Project,
  });
