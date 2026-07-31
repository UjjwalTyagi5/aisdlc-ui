/**
 * Agent identity for Agent Studio. Keyed by the backend `agent_id` returned by
 * /agent-profiles/summary — note this uses `code_review` (not the `review`
 * phase enum). All 13 agents from the Phase enum (lib/schemas/enums.ts) are
 * configurable here — the 8 shared by every track, plus the 5 track-specific
 * ones (discovery, strategy, migration_mapping, validation, data_engineering)
 * used by Tracks 3/4/5 (lib/tracks.ts::agentsForTrack). The summary schema
 * carries no display name, so labels live here.
 */
export const AGENT_ORDER = [
  "requirements",
  "design",
  "development",
  "code_review",
  "security",
  "testing",
  "deployment",
  "documentation",
  "discovery",
  "strategy",
  "migration_mapping",
  "validation",
  "data_engineering",
] as const;

export type AgentId = (typeof AGENT_ORDER)[number];

export const AGENT_LABEL: Record<string, string> = {
  requirements: "Requirements",
  design: "Design",
  development: "Development",
  code_review: "Code Review",
  security: "Security",
  testing: "Testing",
  deployment: "Deployment",
  documentation: "Documentation",
  discovery: "Discovery & Assessment",
  strategy: "Strategy",
  migration_mapping: "Migration Mapping",
  validation: "Validation",
  data_engineering: "Data Engineering",
};

export function agentLabel(agentId: string): string {
  return AGENT_LABEL[agentId] ?? agentId;
}

const rank = (id: string) => {
  const i = AGENT_ORDER.indexOf(id as AgentId);
  return i === -1 ? AGENT_ORDER.length : i;
};

/** Sort summary entries into canonical pipeline order (backend order varies). */
export function byPipelineOrder<T extends { agent_id: string }>(a: T, b: T): number {
  return rank(a.agent_id) - rank(b.agent_id);
}

/** The Testing agent's instruction slots are disabled — its prompt state
 * machine doesn't yet accept custom layers (deferred to Phase 5). */
export const CUSTOM_INSTRUCTIONS_UNSUPPORTED = new Set<string>(["testing"]);

/** Client-side field caps — mirrored from the backend lint limits. */
export const FIELD_CAPS = {
  prompt_prepend: 4000,
  prompt_append: 4000,
  output_contract_extra: 2000,
} as const;

export type ProfileField = keyof typeof FIELD_CAPS;
