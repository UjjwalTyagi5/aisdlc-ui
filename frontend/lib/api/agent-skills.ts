import {
  type SkillCreateInput,
  SkillDeleteResult,
  SkillDetail,
  SkillList,
  type SkillOrigin,
  type SkillProposeInput,
  type SkillScope,
  SkillToggleResult,
  type SkillToggleInput,
  type SkillUpdateInput,
  SkillVersionList,
} from "@/lib/schemas/agent-skills";
import { EvaluationResult } from "@/lib/schemas/agent-studio-eval";
import { GovernanceApproval } from "@/lib/schemas/governance-approval";

import { api } from "./client";

/** Chain ids so the cascade can resolve inheritance past the requested tier —
 *  mirrors ProfileChainIds in lib/api/agent-profiles.ts. */
export interface SkillChainIds {
  workspaceId?: string | null;
  projectId?: string | null;
}

/**
 * Merged vendor + custom skills for an agent at a scope, including any inherited
 * from an ancestor tier (see origin_scope on each item). `scopeId` is required for
 * "workspace"/"project" scope, omit (or pass null) for "org". `chain` supplies the
 * ancestor ids needed to resolve inheritance — omit to get today's exact-scope-only
 * behavior.
 */
export const listAgentSkills = (
  agentId: string,
  scope: SkillScope,
  scopeId?: string | null,
  chain?: SkillChainIds,
) =>
  api("/agent-skills", {
    query: {
      agent_id: agentId,
      scope,
      scope_id: scopeId,
      workspace_id: chain?.workspaceId,
      project_id: chain?.projectId,
    },
    schema: SkillList,
  });

/** Full detail (incl. markdown body) for one skill. */
export const getAgentSkill = (
  origin: SkillOrigin,
  skillKey: string,
  agentId: string,
  scope: SkillScope,
  scopeId?: string | null,
) =>
  api(
    `/agent-skills/${encodeURIComponent(origin)}/${encodeURIComponent(skillKey)}`,
    {
      query: { agent_id: agentId, scope, scope_id: scopeId },
      schema: SkillDetail,
    },
  );

/**
 * Create a v1 custom skill (active + enabled). On lint failure the backend
 * returns 422 with violations — use `getLintViolations(err)` from
 * lib/api/agent-profiles.ts on the caught error to extract them (the BFF POST
 * route forwards the raw 422 body via ApiRequestError.rawBody).
 */
export const createAgentSkill = (input: SkillCreateInput) =>
  api("/agent-skills", {
    method: "POST",
    body: input,
    schema: SkillDetail,
  });

/**
 * Publish a new active version of a custom skill. Same 422 lint-violation
 * passthrough as create — read via getLintViolations on the caught error.
 */
export const updateAgentSkill = (skillKey: string, input: SkillUpdateInput) =>
  api(`/agent-skills/${encodeURIComponent(skillKey)}`, {
    method: "PUT",
    body: input,
    schema: SkillDetail,
  });

/** Enable/disable a vendor or custom skill. */
export const toggleAgentSkill = (input: SkillToggleInput) =>
  api("/agent-skills/toggle", {
    method: "POST",
    body: input,
    schema: SkillToggleResult,
  });

/**
 * Propose a change to a custom skill at a tier the viewer doesn't own —
 * creates a GovernanceApproval routed to that tier's owner instead of writing
 * directly. The backend resolves its own target version server-side (the
 * newest inactive version at this scope); the client cannot name one.
 */
export const proposeAgentSkill = (skillKey: string, input: SkillProposeInput) =>
  api(`/agent-skills/${encodeURIComponent(skillKey)}/propose`, {
    method: "POST",
    body: input,
    schema: GovernanceApproval,
  });

/** Run the deterministic golden-task rubric against the newest pending draft of
 *  this skill_key at this scope — a precondition for proposeAgentSkill(). Unlike
 *  proposeAgentSkill, NOT restricted to the caller's own draft (see the sub-project
 *  4 spec's R3 self-evaluation-blocked rule). */
export const evaluateAgentSkill = (
  skillKey: string,
  input: { agent_id: string; scope: SkillScope; scope_id?: string | null },
) =>
  api(`/agent-skills/${encodeURIComponent(skillKey)}/evaluate`, {
    method: "POST",
    body: input,
    schema: EvaluationResult,
  });

/** Soft-delete a custom skill (custom only). */
export const deleteAgentSkill = (
  skillKey: string,
  agentId: string,
  scope: SkillScope,
  scopeId?: string | null,
) =>
  api(`/agent-skills/${encodeURIComponent(skillKey)}`, {
    method: "DELETE",
    query: { agent_id: agentId, scope, scope_id: scopeId },
    schema: SkillDeleteResult,
  });

/** Version history for a custom skill, newest first. */
export const listAgentSkillVersions = (
  skillKey: string,
  agentId: string,
  scope: SkillScope,
  scopeId?: string | null,
) =>
  api(`/agent-skills/${encodeURIComponent(skillKey)}/versions`, {
    query: { agent_id: agentId, scope, scope_id: scopeId },
    schema: SkillVersionList,
  });
