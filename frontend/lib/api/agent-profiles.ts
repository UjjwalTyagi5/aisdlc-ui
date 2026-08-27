import { z } from "zod";

import {
  type AgentProfileDraftInput,
  AgentProfileSummary,
  AgentProfileVersion,
  AgentProfileVersionList,
  LintViolation,
  LintViolationError,
  ProfilePreview,
  type ProfileScope,
} from "@/lib/schemas/agent-profiles";
import { EvaluationResult } from "@/lib/schemas/agent-studio-eval";
import { GovernanceApproval } from "@/lib/schemas/governance-approval";

import { api, ApiRequestError } from "./client";

/** Chain ids so the cascade can resolve inheritance past the requested tier. */
export interface ProfileChainIds {
  workspaceId?: string | null;
  projectId?: string | null;
  userId?: string | null;
}

/**
 * All 8 agents, pipeline order, with active/latest version + draft counts.
 * `scopeId` is required for "workspace"/"project" scope, omit (or pass null)
 * for "org" scope — matches `_validate_scope` in the backend router. `chain`
 * carries the ancestor ids (org → workspace → project → user) needed to
 * resolve `inherited_from` when the requested tier has no active version of
 * its own.
 */
export const getAgentProfilesSummary = (
  scope: ProfileScope,
  scopeId?: string | null,
  chain?: ProfileChainIds,
) =>
  api("/agent-profiles/summary", {
    query: {
      scope,
      scope_id: scopeId,
      workspace_id: chain?.workspaceId,
      project_id: chain?.projectId,
      user_id: chain?.userId,
    },
    schema: AgentProfileSummary,
  });

/** Version history for one agent at a scope, newest first. */
export const listAgentProfileVersions = (
  agentId: string,
  scope: ProfileScope,
  scopeId?: string | null,
) =>
  api("/agent-profiles/versions", {
    query: { agent_id: agentId, scope, scope_id: scopeId },
    schema: AgentProfileVersionList,
  });

/**
 * Create a new draft version. On lint failure the backend returns 422 with
 * violations — use `getLintViolations(err)` on the caught error to extract them.
 */
export const createAgentProfileDraft = (input: AgentProfileDraftInput) =>
  api("/agent-profiles/draft", {
    method: "POST",
    body: input,
    schema: AgentProfileVersion,
  });

export const publishAgentProfile = (id: string) =>
  api(`/agent-profiles/${encodeURIComponent(id)}/publish`, {
    method: "POST",
    schema: AgentProfileVersion,
  });

export const unpublishAgentProfile = (id: string) =>
  api(`/agent-profiles/${encodeURIComponent(id)}/unpublish`, {
    method: "POST",
    schema: AgentProfileVersion,
  });

/** Run the deterministic golden-task rubric against a draft — a precondition for
 *  propose(). See EvaluationResult's docstring. */
export const evaluateAgentProfile = (id: string) =>
  api(`/agent-profiles/${encodeURIComponent(id)}/evaluate`, {
    method: "POST",
    schema: EvaluationResult,
  });

/** Resolve the full layer stack (vendor..draft) for a not-yet-saved draft body. */
export const previewAgentProfile = (input: AgentProfileDraftInput) =>
  api("/agent-profiles/preview", {
    method: "POST",
    body: input,
    schema: ProfilePreview,
  });

/** Loose fallback in case a future BFF hop ever unwraps the `detail` envelope. */
const FlatLintViolations = z.object({ violations: z.array(LintViolation) });

/**
 * Extract lint violations from a caught draft/preview error. The backend has
 * no global exception handler, so a 422 lint failure reaches the wire as raw
 * FastAPI output — `{detail: {violations: [...]}}` (see LintViolationError) —
 * not the app-wide {code,message,details} ApiError envelope. That means
 * `ApiRequestError.details` is never populated for this error; read
 * `.rawBody` instead (see lib/api/client.ts). Returns null when `err` isn't a
 * 422 violation error.
 */
export function getLintViolations(err: unknown): LintViolation[] | null {
  if (!(err instanceof ApiRequestError) || err.status !== 422) return null;

  const nested = LintViolationError.safeParse(err.rawBody);
  if (nested.success) return nested.data.detail.violations;

  const flat = FlatLintViolations.safeParse(err.rawBody ?? err.details);
  if (flat.success) return flat.data.violations;

  return null;
}

/**
 * Propose publishing a saved draft at a tier the viewer doesn't own (see
 * AGENT_DEFAULT_OWNER_ROLE, lib/governance.ts) — creates a GovernanceApproval
 * routed to that tier's owner instead of publishing directly. `id` is the
 * draft version's id, already created via `createAgentProfileDraft`.
 */
export const proposeAgentProfilePublish = (
  id: string,
  input: {
    scope: Exclude<ProfileScope, "user">;
    agentId: string;
    agentLabel: string;
    workspaceId?: string;
    workspaceName?: string;
    projectId?: string;
    projectName?: string;
  },
) =>
  api(`/agent-profiles/${encodeURIComponent(id)}/propose`, {
    method: "POST",
    body: input,
    schema: GovernanceApproval,
  });
