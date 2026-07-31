/**
 * Dummy per-project agent capability data — plain data, server-safe
 * (imported by the app/api/capabilities/projects/[id]/agents route
 * handlers). This is the DUMMY-DATA source; a real capabilities service
 * replaces the route-handler bodies, not these shapes.
 */
import { agentsForTrack } from "@/lib/tracks";
import { PHASE_LABEL } from "@/lib/agents";
import { getProjectById } from "@/lib/mock/project-fixtures";
import { getMcpServer, listMcpServers } from "@/lib/mock/mcp-fixtures";
import type { Phase } from "@/lib/schemas/enums";
import type { AgentCapability, CuratedTool, ProjectCapabilities } from "@/lib/schemas/capabilities";

function phaseToAgentId(phase: Phase): string {
  return phase === "review" ? "code_review" : phase;
}

/** Every agent gets the same small curated catalogue in this mock — a real
 *  backend would vary it per agent type. */
const CURATED_TEMPLATE: Omit<CuratedTool, "enabled">[] = [
  { key: "web_search", display_name: "Web search", capability: "read", default_on: true },
  { key: "code_search", display_name: "Code search", capability: "read", default_on: true },
];

// (projectId, agentId) -> set of disabled curated tool keys.
const CURATED_DISABLED = new Map<string, Set<string>>();
const curatedKey = (projectId: string, agentId: string) => `${projectId}::${agentId}`;

export function getProjectCapabilitiesData(projectId: string): ProjectCapabilities | undefined {
  const project = getProjectById(projectId);
  if (!project) return undefined;

  const availableByo = listMcpServers(true).map((s) => ({
    id: s.id,
    server_name: s.server_name,
    transport: s.transport,
    capabilities: s.tools_snapshot.map((t) => t.name),
  }));

  const agents: AgentCapability[] = agentsForTrack(project.track).map((phase) => {
    const agentId = phaseToAgentId(phase);
    const disabled = CURATED_DISABLED.get(curatedKey(projectId, agentId)) ?? new Set<string>();
    const curated: CuratedTool[] = CURATED_TEMPLATE.map((c) => ({
      ...c,
      enabled: !disabled.has(c.key),
    }));

    const assignedIds = project.mcpServers?.[agentId] ?? [];
    const assigned_byo = assignedIds
      .map((id) => {
        const server = getMcpServer(id);
        if (!server) return null;
        return {
          id: server.id,
          server_name: server.server_name,
          capabilities: server.tools_snapshot.map((t) => t.name),
        };
      })
      .filter((x): x is NonNullable<typeof x> => x !== null);

    return {
      agent_id: agentId,
      name: PHASE_LABEL[phase],
      required: [],
      optional: [],
      native: [{ tool: "file_read", capability: "read" }],
      curated,
      assigned_byo,
    };
  });

  return { agents, available_byo: availableByo };
}

export function setCuratedDisabled(
  projectId: string,
  agentId: string,
  disabled: string[],
): { agent_id: string; disabled: string[] } {
  CURATED_DISABLED.set(curatedKey(projectId, agentId), new Set(disabled));
  return { agent_id: agentId, disabled };
}
