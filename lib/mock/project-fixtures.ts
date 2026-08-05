/**
 * Project creation/listing — mutates the SAME `PROJECTS` array `mocks/
 * fixtures.ts` already exports (imported by both the Next.js route handlers
 * and the MSW handlers — see [[msw-dual-runtime-mutation-rule]]). Plain data
 * + functions, server-safe. This is the DUMMY-DATA source; a real backend
 * project service replaces the route-handler bodies, not these shapes.
 */
import { PROJECTS } from "@/mocks/fixtures";
import type {
  Project,
  ProjectCreateInput,
  ProjectDeliveryStatus,
  ToolAccessMode,
} from "@/lib/schemas/project";
import type { PlatformRole } from "@/lib/roles";
import { agentsForTrack } from "@/lib/tracks";
import { addProjectMember } from "@/lib/mock/project-membership-fixtures";
import { createGovernanceApproval } from "@/lib/mock/governance-approval-fixtures";
import { getWorkspace } from "@/lib/mock/workspace-fixtures";

export interface ProjectCreator {
  role: PlatformRole | null;
  displayName: string;
  userRef: { id: string; name: string; email?: string; initials: string };
}

/**
 * Creates a project, deriving its pipeline from the track's real agent
 * roster (agentsForTrack) — the previous mock handler hardcoded a fixed
 * 6-phase pipeline regardless of track, which silently disagreed with the
 * optimistic preview `create-project-dialog.tsx` already builds correctly.
 *
 * A Project Admin's creation starts `pending_approval` and opens a
 * governance approval routed to the owning Business Unit's Admin; Org Admin
 * and BU Admin create directly into `active` (PRD: they're at or above the
 * approving tier already). Contributors assigned at creation time are added
 * via the same onboarding primitive as the Members page.
 */
export function createProjectRecord(input: ProjectCreateInput, creator: ProjectCreator): Project {
  const slug = input.name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const now = new Date().toISOString();
  const roster = agentsForTrack(input.track);
  const needsApproval = creator.role === "project_admin";

  const created: Project = {
    id: slug as Project["id"],
    tenantId: "ws_acme" as Project["tenantId"],
    name: input.name,
    slug,
    description: input.description ?? null,
    workspaceId: input.workspaceId,
    approvalStatus: needsApproval ? "pending_approval" : "active",
    // Every project starts here — nothing has been delivered at creation time,
    // whatever the approval outcome. The Project Admin moves it on.
    deliveryStatus: "not_started",
    approvalDecidedBy: null,
    approvalDecidedAt: null,
    approvalReason: null,
    template: input.template,
    track: input.track,
    archived: false,
    owners: [creator.userRef as Project["owners"][number]],
    pipeline: roster.map((phase, i) => ({
      phase,
      status: i === 0 ? "draft" : "queued",
      updatedAt: i === 0 ? now : undefined,
    })),
    mcpServers: input.mcp_servers,
    connectors: input.connectors,
    toolAccessModes: input.tool_access_modes,
    // Null (the default) = inherit the Business Unit's cap, not "zero".
    monthlyBudgetUsd: input.monthlyBudgetUsd ?? null,
    budgetStartDate: input.budgetStartDate ?? null,
    budgetEndDate: input.budgetEndDate ?? null,
    monthlySpendUsd: 0,
    lastActivityAt: now,
    createdAt: now,
  };
  PROJECTS.unshift(created);

  for (const c of input.contributors ?? []) {
    addProjectMember(created.id, {
      email: c.email,
      roleName: c.roleName,
      extraAgents: c.extraAgents,
    });
  }

  if (needsApproval) {
    const workspace = getWorkspace(input.workspaceId);
    createGovernanceApproval({
      type: "project_creation",
      workspaceId: input.workspaceId,
      workspaceName: workspace?.displayName ?? input.workspaceId,
      projectId: created.id,
      projectName: created.name,
      title: `New project: ${created.name}`,
      summary: `${creator.displayName} requested a new ${input.template.replace("_", " ")} project on ${input.track.replace("_", " ")}${workspace ? ` in ${workspace.displayName}` : ""}.`,
      requestedBy: creator.displayName,
      targetRef: created.id,
    });
  }

  return created;
}

export function getProjectById(id: string): Project | undefined {
  return PROJECTS.find((p) => p.id === id);
}

export interface ProjectUpdatePatch {
  name?: string;
  description?: string;
  mcp_servers?: Record<string, string[]>;
  connectors?: Record<string, string[]>;
  tool_access_modes?: Record<string, ToolAccessMode>;
  monthlyBudgetUsd?: number | null;
  budgetStartDate?: string | null;
  budgetEndDate?: string | null;
  deliveryStatus?: ProjectDeliveryStatus;
}

export function updateProjectRecord(id: string, patch: ProjectUpdatePatch): Project | undefined {
  const project = PROJECTS.find((p) => p.id === id);
  if (!project) return undefined;
  if (patch.deliveryStatus !== undefined) project.deliveryStatus = patch.deliveryStatus;
  if (patch.name !== undefined) project.name = patch.name;
  if (patch.description !== undefined) project.description = patch.description;
  if (patch.mcp_servers !== undefined) project.mcpServers = patch.mcp_servers;
  if (patch.connectors !== undefined) project.connectors = patch.connectors;
  if (patch.tool_access_modes !== undefined) project.toolAccessModes = patch.tool_access_modes;
  if (patch.monthlyBudgetUsd !== undefined) project.monthlyBudgetUsd = patch.monthlyBudgetUsd;
  if (patch.budgetStartDate !== undefined) project.budgetStartDate = patch.budgetStartDate;
  if (patch.budgetEndDate !== undefined) project.budgetEndDate = patch.budgetEndDate;
  project.lastActivityAt = new Date().toISOString();
  return project;
}

export function setProjectArchived(id: string, archived: boolean): Project | undefined {
  const project = PROJECTS.find((p) => p.id === id);
  if (!project) return undefined;
  project.archived = archived;
  project.lastActivityAt = new Date().toISOString();
  return project;
}

/**
 * Archiving is the closest thing this app has to "delete" a project. The
 * Project Admin (its owner) and Org Admin can do it directly; a BU Admin
 * archiving a project they don't personally own needs the Org Admin's
 * approval first (lib/governance.ts::GOVERNANCE_APPROVER_ROLE.project_archive)
 * — restoring is unaffected, only archiving a currently-active project goes
 * through this gate.
 */
export function requestOrArchiveProject(
  id: string,
  requester: { role: PlatformRole | null; displayName: string },
): { project: Project; pendingApproval: boolean } | undefined {
  const project = PROJECTS.find((p) => p.id === id);
  if (!project) return undefined;

  if (requester.role === "bu_admin" && !project.archived) {
    const workspace = project.workspaceId ? getWorkspace(project.workspaceId) : undefined;
    createGovernanceApproval({
      type: "project_archive",
      workspaceId: project.workspaceId ?? "",
      workspaceName: workspace?.displayName ?? project.workspaceId ?? "",
      projectId: project.id,
      projectName: project.name,
      title: `Archive project: ${project.name}`,
      summary: `${requester.displayName} requested to archive "${project.name}".`,
      requestedBy: requester.displayName,
      targetRef: project.id,
    });
    return { project, pendingApproval: true };
  }

  project.archived = true;
  project.lastActivityAt = new Date().toISOString();
  return { project, pendingApproval: false };
}

export function activateProject(id: string, decidedBy: string): Project | undefined {
  const project = PROJECTS.find((p) => p.id === id);
  if (!project) return undefined;
  project.approvalStatus = "active";
  project.approvalDecidedBy = decidedBy;
  project.approvalDecidedAt = new Date().toISOString();
  return project;
}

export function rejectProjectCreation(
  id: string,
  decidedBy: string,
  reason?: string,
): Project | undefined {
  const project = PROJECTS.find((p) => p.id === id);
  if (!project) return undefined;
  project.approvalStatus = "rejected";
  project.approvalDecidedBy = decidedBy;
  project.approvalDecidedAt = new Date().toISOString();
  project.approvalReason = reason ?? null;
  return project;
}
