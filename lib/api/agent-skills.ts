import {
  type SkillCreateInput,
  SkillDeleteResult,
  SkillDetail,
  SkillList,
  type SkillOrigin,
  type SkillScope,
  SkillToggleResult,
  type SkillToggleInput,
  type SkillUpdateInput,
  SkillVersionList,
} from "@/lib/schemas/agent-skills";

import { api } from "./client";

/**
 * Merged vendor + custom skills for an agent at a scope. `scopeId` is required
 * for "workspace"/"project" scope, omit (or pass null) for "org".
 */
export const listAgentSkills = (
  agentId: string,
  scope: SkillScope,
  scopeId?: string | null,
) =>
  api("/agent-skills", {
    query: { agent_id: agentId, scope, scope_id: scopeId },
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
