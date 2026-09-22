import {
  BusinessUnitTechStacks,
  ProjectTechStack,
  TechStack,
  TechStackCatalog,
  type TechStackCreateInput,
  TechStackDeleted,
  type TechStackFieldsInput,
} from "@/lib/schemas/tech-stacks";

import { api } from "./client";

/** Categories and suggestions for the editor's chip inputs. */
export const getTechStackCatalog = () => api("/tech-stacks/catalog", { schema: TechStackCatalog });

/** A Business Unit's live stacks (default first) and whether the caller manages them. */
export const listBusinessUnitTechStacks = (workspaceId: string) =>
  api("/tech-stacks", { query: { workspace_id: workspaceId }, schema: BusinessUnitTechStacks });

/** A project's options, selection and the stack its agents follow now. */
export const getProjectTechStack = (projectId: string) =>
  api(`/projects/${encodeURIComponent(projectId)}/tech-stack`, { schema: ProjectTechStack });

/** 422 carries `violations` — read them with `getLintViolations(err)` (lib/api/agent-profiles). */
export const createTechStack = (input: TechStackCreateInput) =>
  api("/tech-stacks", { method: "POST", body: input, schema: TechStack });

export const updateTechStack = (id: string, input: TechStackFieldsInput) =>
  api(`/tech-stacks/${encodeURIComponent(id)}`, { method: "PATCH", body: input, schema: TechStack });

export const deleteTechStack = (id: string) =>
  api(`/tech-stacks/${encodeURIComponent(id)}`, { method: "DELETE", schema: TechStackDeleted });

export const setDefaultTechStack = (id: string, isDefault: boolean) =>
  api(`/tech-stacks/${encodeURIComponent(id)}/default`, {
    method: "PUT",
    body: { is_default: isDefault },
    schema: TechStack,
  });

/** `null` = follow the Business Unit default. */
export const selectProjectTechStack = (projectId: string, techStackId: string | null) =>
  api(`/projects/${encodeURIComponent(projectId)}/tech-stack`, {
    method: "PUT",
    body: { tech_stack_id: techStackId },
    schema: ProjectTechStack,
  });
