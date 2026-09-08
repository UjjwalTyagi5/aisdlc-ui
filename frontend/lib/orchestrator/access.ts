import type { PlatformRole } from "@/lib/roles";

/**
 * Who may use the Orchestrator.
 *
 * Only the Project Admin, and for a specific reason: the Orchestrator reaches
 * every agent in the project, so anyone who could drive it would effectively hold
 * every agent's access. That is exactly the `use`-tier leak the one-agent-one-role
 * change removed. Project Admin is the only role that already owns all nine
 * (`AGENT_OWNERSHIP.project_admin` is ALL_OWNER), so granting it here adds nothing
 * it did not already have.
 *
 * `org_admin` and `bu_admin` are excluded despite outranking Project Admin
 * elsewhere: the governance tier holds no agent access at all. They decide who may
 * run agents; they do not run them.
 *
 * Per-project overrides (`agent_access_overrides`) deliberately do NOT open the
 * Orchestrator. They grant one extra agent to one person; the Orchestrator is all
 * nine at once, which is a different question with a different answer.
 */
export function canUseOrchestrator(
  role: PlatformRole | null | undefined,
): boolean {
  return role === "project_admin";
}
