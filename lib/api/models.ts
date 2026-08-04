import { z } from "zod";

import { type GrantVisibility } from "@/lib/schemas/grant";
import {
  CatalogProvider,
  ModelAllowEntry,
  ModelAvailability,
  ModelGrantMatrix,
  ModelOptions,
  ModelProvider,
  OrgModelGrant,
  ProjectModelSelection,
  VerifyResult,
} from "@/lib/schemas/model";

import { api } from "./client";

export const getModelCatalog = () => api("/model/catalog", { schema: z.array(CatalogProvider) });

/** null/omitted = org-wide providers; a workspace id = that BU's scoped providers. */
/** Every connection the caller may see — org-wide AND unit-scoped. */
export const listAllModelProviders = () =>
  api("/model/providers", { query: { scope: "all" }, schema: z.array(ModelProvider) });

export const listModelProviders = (workspaceId?: string | null) =>
  api("/model/providers", {
    query: { workspaceId: workspaceId || undefined },
    schema: z.array(ModelProvider),
  });

/** The organization's catalogue policy: which models exist and who they reach. */
export const getOrgModelGrants = () =>
  api("/model/allowed/org", { schema: z.array(OrgModelGrant) });

export const setOrgModelGrants = (entries: OrgModelGrant[]) =>
  api("/model/allowed/org", { method: "PUT", body: { entries }, schema: z.array(OrgModelGrant) });

/** What one Business Unit may use — derived from the grants, read-only to it. */
export const getBuAllowedModels = (workspaceId: string) =>
  api("/model/allowed/bu", { query: { workspaceId }, schema: z.array(ModelAllowEntry) });

/** The Org Admin's per-unit grant, from Business Unit creation / management.
 *  Only `specific` models move; global ones already reach every unit. */
export const setBuModelGrants = (workspaceId: string, entries: ModelAllowEntry[]) =>
  api("/model/allowed/bu", {
    method: "PUT",
    query: { workspaceId },
    body: { entries },
    schema: z.array(ModelAllowEntry),
  });

/** The same set, plus whether each model still needs a key at this level. */
export const getModelAvailability = (workspaceId: string) =>
  api("/model/availability", { query: { workspaceId }, schema: z.array(ModelAvailability) });

/** The last tier: what one project selected from what its BU was granted. */
export const getProjectModelSelection = (projectId: string) =>
  api("/model/allowed/project", { query: { projectId }, schema: ProjectModelSelection });

export const setProjectModelSelection = (
  projectId: string,
  input: { selected: ModelAllowEntry[]; defaultKey?: string | null },
) =>
  api("/model/allowed/project", {
    method: "PUT",
    query: { projectId },
    body: input,
    schema: ProjectModelSelection,
  });

export interface ModelSpecInput {
  model_id: string;
  /** USD per 1M tokens — required for custom providers. */
  input_price_per_million?: number | null;
  output_price_per_million?: number | null;
  /** Per-model usage limits (null/undefined = no limit). */
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  cost_limit_usd?: number | null;
}

export const addModelProvider = (body: {
  provider: string;
  display_name: string;
  api_key: string;
  /** Optional custom endpoint (OpenAI-compatible / self-hosted / gateway). */
  api_base?: string;
  /** Preferred: rich model specs with pricing (custom flow). */
  models?: ModelSpecInput[];
  /** Back-compat: bare model ids (preset flow). */
  enabled_models?: string[];
  /** null = org-wide (Org Admin's own onboarding); a workspace id scopes it
   *  to that BU (BU Admin onboards active; Project Admin onboards pending
   *  that BU Admin's approval). */
  workspaceId: string | null;
  /** Org-wide onboarding only: how far the models being credentialed here
   *  should reach. The grant is written in the same act so a key can't land
   *  without anyone being able to use what it unlocks. */
  visibility?: GrantVisibility;
  businessUnitIds?: string[];
}) => api("/model/providers", { method: "POST", body, schema: ModelProvider });

export const verifyModelProvider = (id: string) =>
  api(`/model/providers/${encodeURIComponent(id)}/verify`, {
    method: "POST",
    schema: VerifyResult,
  });

export const updateModelProvider = (
  id: string,
  body: { display_name?: string; enabled_models?: string[] },
) => api(`/model/providers/${encodeURIComponent(id)}`, { method: "PATCH", body, schema: ModelProvider });

export const deleteModelProvider = (id: string) =>
  api(`/model/providers/${encodeURIComponent(id)}`, { method: "DELETE" });

export const setModelDefault = (offeringId: string) =>
  api("/model/default", { method: "PUT", body: { offering_id: offeringId } });

export const getModelOptions = () => api("/model/options", { schema: ModelOptions });

/**
 * The Organization Admin's grant matrix — every catalogue model, whether it is
 * granted, whether anyone still has to key it, and which units it reaches.
 * One request rather than an availability call per Business Unit.
 */
export const getModelGrantMatrix = () =>
  api("/model/grant-matrix", { schema: ModelGrantMatrix });
