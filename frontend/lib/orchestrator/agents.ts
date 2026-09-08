import { z } from "zod";

/**
 * The nine agents, and nothing else.
 *
 * Split out of `protocol.ts` in Phase 4 so `deliverables.ts` can name an agent
 * without importing the event union, and `protocol.ts` can fold the deliverable
 * event into that union — which would otherwise be a cycle between the two files.
 * `protocol.ts` re-exports both names, so nothing that already imports them from
 * there had to change.
 *
 * `plan` is the PROJECT MANAGER agent. The id is internal and stays `plan` because
 * it appears in route paths and the API contract; the label is what changed, and it
 * is never "Plan agent" or "PM agent" anywhere a user can read it.
 */
export const ORCHESTRATOR_AGENT_IDS = [
  "requirements",
  "design",
  "plan",
  "development",
  "code_review",
  "security",
  "testing",
  "deployment",
  "documentation",
] as const;

export const OrchestratorAgentId = z.enum(ORCHESTRATOR_AGENT_IDS);
export type OrchestratorAgentId = z.infer<typeof OrchestratorAgentId>;
