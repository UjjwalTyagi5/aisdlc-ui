import { z } from "zod";

/** A native (Tier 0) tool — built-in, read-only, never removable. */
export const NativeTool = z.object({
  tool: z.string(),
  capability: z.string(),
});
export type NativeTool = z.infer<typeof NativeTool>;

/** A curated (Tier 1) tool — platform-shipped, toggled on/off per agent. */
export const CuratedTool = z.object({
  key: z.string(),
  display_name: z.string(),
  capability: z.string(),
  default_on: z.boolean(),
  enabled: z.boolean(),
});
export type CuratedTool = z.infer<typeof CuratedTool>;

/** A BYO (Tier 2) MCP server assigned to an agent for this project. */
export const AssignedByo = z.object({
  id: z.string(),
  server_name: z.string(),
  capabilities: z.array(z.string()),
});
export type AssignedByo = z.infer<typeof AssignedByo>;

export const AgentCapability = z.object({
  agent_id: z.string(),
  name: z.string(),
  required: z.array(z.string()),
  optional: z.array(z.string()),
  native: z.array(NativeTool),
  curated: z.array(CuratedTool),
  assigned_byo: z.array(AssignedByo),
});
export type AgentCapability = z.infer<typeof AgentCapability>;

export const AvailableByo = z.object({
  id: z.string(),
  server_name: z.string(),
  transport: z.string(),
  capabilities: z.array(z.string()),
});
export type AvailableByo = z.infer<typeof AvailableByo>;

export const ProjectCapabilities = z.object({
  agents: z.array(AgentCapability),
  available_byo: z.array(AvailableByo),
});
export type ProjectCapabilities = z.infer<typeof ProjectCapabilities>;

export const CuratedToggleResult = z.object({
  agent_id: z.string(),
  disabled: z.array(z.string()),
});
export type CuratedToggleResult = z.infer<typeof CuratedToggleResult>;
