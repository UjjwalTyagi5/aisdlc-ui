import { z } from "zod";

import type { Phase, ProjectId } from "@/lib/schemas";
import { ProjectCapabilities, CuratedToggleResult } from "@/lib/schemas/capabilities";
import { AgentAccessOverride, type AgentAccessOverrideInput } from "@/lib/schemas/agent-access";

import { api } from "./client";

/** Per-agent capability view for a project: native, curated (with on/off), assigned BYO. */
export const getProjectCapabilities = (id: ProjectId) =>
  api(`/capabilities/projects/${encodeURIComponent(id)}/agents`, {
    schema: ProjectCapabilities,
  });

/** Write the agent's disabled-curated list (the full set, not a delta). */
export const setAgentCuratedDisabled = (
  id: ProjectId,
  agentId: string,
  disabled: string[],
) =>
  api(
    `/capabilities/projects/${encodeURIComponent(id)}/agents/${encodeURIComponent(agentId)}/curated`,
    { method: "PUT", body: { disabled }, schema: CuratedToggleResult },
  );

/** Per-project overrides of a role's agent access — on top of that role's
 *  global default (lib/roles.ts::AGENT_OWNERSHIP or a custom role's own
 *  agentAccess). See lib/schemas/agent-access.ts. */
export const listAgentAccessOverrides = (id: ProjectId) =>
  api(`/projects/${encodeURIComponent(id)}/agent-access-overrides`, {
    schema: z.array(AgentAccessOverride),
  });

export const setAgentAccessOverride = (id: ProjectId, input: AgentAccessOverrideInput) =>
  api(`/projects/${encodeURIComponent(id)}/agent-access-overrides`, {
    method: "PUT",
    body: input,
    schema: AgentAccessOverride,
  });

export const removeAgentAccessOverride = (id: ProjectId, role: string, phase: Phase) =>
  api(`/projects/${encodeURIComponent(id)}/agent-access-overrides`, {
    method: "DELETE",
    query: { role, phase },
  });
